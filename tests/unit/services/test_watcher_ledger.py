"""The watcher on the healing-case ledger (#278 part 2, #283).

Mocked collaborators: what the tick asks of the ledger and in which order.
The same paths run against real Postgres in
``tests/unit/services/healing/test_watcher_ledger_db.py``.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
import structlog

from embry0.services.healing.service import TrackerRef
from embry0.services.log_signatures import LogEntry
from embry0.services.watcher import WatcherService, proposal_fingerprint
from embry0.trackers.filing import Draft

pytestmark = pytest.mark.asyncio

REPO = "acme/widgets"
_LINES = ["[c] e1", "[c] e2", "[c] e3"]
_PROPOSAL = "TITLE: Fix the breaker\nSEVERITY: high\nRATIONALE: it is loud\nBODY:\n## Evidence\nboom"
_JOB = {"job_id": "job-w1", "status": "completed", "result_summary": _PROPOSAL}
_ISSUE = {"identifier": "ENG-7", "url": "https://linear.app/x/ENG-7"}


def _config(**overrides):
    base = {
        "watcher_interval_seconds": 3600,
        "watcher_loki_url": "http://loki.test:3100",
        "watcher_logql": '{compose_project="x"}',
        "watcher_min_log_lines": 3,
        "watcher_max_log_lines": 50,
        "watcher_max_open_proposals": 2,
        "watcher_max_deferred_findings": 2,
        "watcher_proposal_window_days": 7,
        "watcher_job_timeout_seconds": 60,
        "watcher_linear_team_id": "team-1",
        "watcher_linear_project_id": "",
        "watcher_repo": REPO,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _service(
    *,
    ledger_open=0,
    legacy_open=0,
    deferred=None,
    fingerprint_seen=False,
    job=_JOB,
    lines=_LINES,
    config=None,
    ledger_keys=None,
    legacy_keys=None,
    states=None,
    full_cap_analyses=0,
):
    cases = MagicMock(
        count_open_unaccepted_watcher_cases=AsyncMock(return_value=ledger_open),
        open_unaccepted_watcher_tracker_keys=AsyncMock(return_value=ledger_keys or []),
        deferred_watcher_findings=AsyncMock(return_value=deferred or []),
    )
    recorder = MagicMock(
        finding_recorded=AsyncMock(return_value="hc-1"),
        finding_recurred=AsyncMock(),
        draft_released=AsyncMock(),
        deferred_finding_filed=AsyncMock(return_value=True),
        deferred_finding_dropped=AsyncMock(return_value=True),
    )
    svc = WatcherService(
        config=config or _config(),
        jobs_repo=MagicMock(create=AsyncMock(return_value="job-w1"), get=AsyncMock(return_value=job)),
        watcher_runs_repo=MagicMock(
            create=AsyncMock(return_value="wr-new"),
            open_proposal_count=AsyncMock(return_value=legacy_open),
            fingerprint_exists=AsyncMock(return_value=fingerprint_seen),
            recent_proposal_titles=AsyncMock(return_value=[]),
            recent_findings=AsyncMock(return_value=[]),
            open_proposal_tickets=AsyncMock(
                return_value=[{"tracker": "linear", "key": k} for k in (legacy_keys or [])]
            ),
            release_ticket=AsyncMock(return_value=1),
            last_analysed_window_end=AsyncMock(return_value=None),
            unparseable_backoff=AsyncMock(return_value=None),
            mark_filed=AsyncMock(return_value=True),
            full_cap_analyses_since=AsyncMock(return_value=full_cap_analyses),
        ),
        integration_repo=MagicMock(get_telegram_credentials=AsyncMock(return_value=("", ""))),
        executor=MagicMock(),
        filer=MagicMock(
            tracker="linear",
            target="team-1",
            file_draft=AsyncMock(return_value=Draft(key=_ISSUE["identifier"], url=_ISSUE["url"])),
            list_open_drafts=AsyncMock(return_value=[]),
            draft_states=AsyncMock(return_value=states or {}),
        ),
        cases_repo=cases,
        case_recorder=recorder,
    )
    # #289: the tick reads structured entries; stamps fixed and spaced.
    svc._fetch_log_entries = AsyncMock(
        return_value=[
            LogEntry(line=t, timestamp=datetime(2026, 9, 20, 12, 0, tzinfo=UTC) + timedelta(seconds=i), service="svc")
            for i, t in enumerate(lines)
        ]
    )
    svc._fetch_recent_commits = AsyncMock(return_value=[])
    svc._fetch_deploy_markers = AsyncMock(return_value=[])
    return svc


def _finding(case_id="hc-d1", run_id="wr-d1", **over):
    now = datetime.now(UTC)
    row = {
        "id": case_id,
        "watcher_run_id": run_id,
        "detected_at": now - timedelta(hours=3),
        "last_seen_at": now - timedelta(hours=3),
        "run_identifier": None,
        "run_job_id": "job-old",
        "run_window_start": now - timedelta(hours=4),
        "run_window_end": now - timedelta(hours=3),
    }
    row.update(over)
    return row


# ---- a filed proposal ---------------------------------------------------------


async def test_a_filed_proposal_opens_one_tracked_case_for_its_run():
    svc = _service()
    out = await svc.run_once()
    assert out["action"] == "proposed"
    assert out["run_id"] == "wr-new"
    svc._recorder.finding_recorded.assert_awaited_once_with(
        repo=REPO,
        title="Fix the breaker",
        watcher_run_id="wr-new",
        fingerprint=proposal_fingerprint("Fix the breaker"),
        # #290: these are None here because this service has no signature
        # memory wired. That is the documented degrade — the case is still
        # opened, keyed on the title hash exactly as before. With nothing
        # anchored there is no source to record either (#657).
        signature_id=None,
        signature=None,
        signature_source=None,
        parent_case_id=None,
        severity="high",
        tracker=TrackerRef("linear", "ENG-7", "https://linear.app/x/ENG-7"),
        work_nature=None,
    )


async def test_the_cap_is_asked_of_the_ledger_with_the_window_and_repo():
    svc = _service()
    await svc.run_once()
    svc._cases.count_open_unaccepted_watcher_cases.assert_awaited_once_with(days=7, repo=REPO)


async def test_a_duplicate_is_a_recurrence_keyed_on_the_tick():
    svc = _service(fingerprint_seen=True)
    out = await svc.run_once()
    assert out["action"] == "skipped_duplicate"
    svc._recorder.finding_recurred.assert_awaited_once_with(
        fingerprint=proposal_fingerprint("Fix the breaker"), repo=REPO, watcher_run_id="wr-new"
    )
    svc._recorder.finding_recorded.assert_not_awaited()


# ---- a full cap ---------------------------------------------------------------


async def test_a_full_cap_still_analyses_and_defers_the_filing():
    svc = _service(ledger_open=2, legacy_open=2)
    out = await svc.run_once()
    assert out["action"] == "deferred"
    svc._fetch_log_entries.assert_awaited_once()
    svc._jobs.create.assert_awaited_once()  # ONE analysis job, as on any tick
    svc._filer.file_draft.assert_not_awaited()
    row = svc._runs.create.await_args.kwargs
    assert row["detail"] == "Fix the breaker"
    assert row.get("tracker_key") is None
    kwargs = svc._recorder.finding_recorded.await_args.kwargs
    assert kwargs["tracker"] is None
    assert kwargs["watcher_run_id"] == "wr-new"


async def test_full_cap_and_full_deferred_queue_skips_the_paid_analysis():
    svc = _service(ledger_open=2, legacy_open=2, deferred=[_finding("hc-a"), _finding("hc-b")])
    out = await svc.run_once()
    assert out["action"] == "skipped_backlog"
    assert out["log_lines"] == len(_LINES)  # it looked
    svc._jobs.create.assert_not_awaited()  # and paid nothing
    svc._filer.file_draft.assert_not_awaited()


async def test_the_high_severity_bypass_seam_is_inert():
    svc = _service(ledger_open=2, legacy_open=2)
    assert svc._bypasses_cap({"severity": "high"}) is False
    assert (await svc.run_once())["action"] == "deferred"  # _PROPOSAL is SEVERITY: high


# ---- a slot frees -------------------------------------------------------------


async def test_a_free_slot_files_the_oldest_deferred_finding_and_only_one():
    old = dict(_JOB, result_summary="TITLE: Old finding\nSEVERITY: low\nBODY:\nold evidence")
    first, second = _finding("hc-a", "wr-a"), _finding("hc-b", "wr-b")
    svc = _service(ledger_open=1, legacy_open=1, job=old)
    svc._cases.deferred_watcher_findings = AsyncMock(side_effect=[[first, second], [second]])
    # The tick's own analysis proposes something new as well.
    svc._jobs.get = AsyncMock(side_effect=[old, _JOB])
    out = await svc.run_once()

    # Exactly one ticket was created this tick — for the OLDEST deferred finding.
    svc._filer.file_draft.assert_awaited_once()
    assert svc._filer.file_draft.await_args.kwargs["title"] == "[watcher] Old finding"
    svc._runs.mark_filed.assert_awaited_once_with("wr-a", tracker="linear", key="ENG-7", url=ANY)
    svc._recorder.deferred_finding_filed.assert_awaited_once_with(
        "hc-a", TrackerRef("linear", "ENG-7", "https://linear.app/x/ENG-7"), watcher_run_id="wr-a"
    )
    # The fresh proposal waits its turn.
    assert out["action"] == "deferred"


async def test_no_free_slot_files_nothing_deferred():
    svc = _service(ledger_open=2, legacy_open=2, deferred=[_finding()])
    await svc.run_once()
    svc._runs.mark_filed.assert_not_awaited()
    svc._recorder.deferred_finding_filed.assert_not_awaited()


async def test_a_stale_deferred_finding_is_dropped_not_filed():
    long_ago = datetime.now(UTC) - timedelta(days=8)
    svc = _service(deferred=[_finding(detected_at=long_ago, last_seen_at=long_ago)], job=None)
    svc._jobs.get = AsyncMock(return_value=_JOB)
    await svc.run_once()
    svc._recorder.deferred_finding_dropped.assert_awaited_once()
    svc._runs.mark_filed.assert_not_awaited()


async def test_a_deferred_finding_whose_proposal_is_gone_is_dropped():
    svc = _service(deferred=[_finding()])
    svc._jobs.get = AsyncMock(side_effect=[None, _JOB])
    await svc.run_once()
    svc._recorder.deferred_finding_dropped.assert_awaited_once()
    svc._runs.mark_filed.assert_not_awaited()


async def test_a_filing_whose_ledger_write_was_lost_is_repaired_not_refiled():
    svc = _service(deferred=[_finding(run_identifier="ENG-5")], lines=[])
    await svc.run_once()
    svc._filer.file_draft.assert_not_awaited()
    svc._recorder.deferred_finding_filed.assert_awaited_once_with(
        "hc-d1", TrackerRef("linear", "ENG-5"), watcher_run_id="wr-d1"
    )


async def test_a_linear_failure_leaves_the_finding_deferred():
    svc = _service(deferred=[_finding()], lines=[])
    svc._filer.file_draft = AsyncMock(return_value=None)
    await svc.run_once()
    svc._runs.mark_filed.assert_not_awaited()
    svc._recorder.deferred_finding_dropped.assert_not_awaited()


# ---- fail-open ----------------------------------------------------------------


async def test_a_ledger_that_cannot_count_falls_back_to_the_old_predicate():
    svc = _service(legacy_open=2)
    svc._cases.count_open_unaccepted_watcher_cases = AsyncMock(side_effect=RuntimeError("db down"))
    with structlog.testing.capture_logs() as logs:
        out = await svc.run_once()
    assert any(e["event"] == "watcher_cap_ledger_failed" and e["log_level"] == "error" for e in logs)
    assert out["action"] == "deferred"  # legacy says full


async def test_a_ledger_that_cannot_list_deferred_findings_does_not_stop_the_tick():
    svc = _service()
    svc._cases.deferred_watcher_findings = AsyncMock(side_effect=RuntimeError("db down"))
    with structlog.testing.capture_logs() as logs:
        out = await svc.run_once()
    assert out["action"] == "proposed"
    assert any(e["event"] == "watcher_deferred_drain_failed" for e in logs)


async def test_without_watcher_repo_the_ledger_is_off_and_the_old_cap_rules():
    svc = _service(legacy_open=2, config=_config(watcher_repo=""))
    out = await svc.run_once()
    assert out["action"] == "skipped_backlog"
    svc._cases.count_open_unaccepted_watcher_cases.assert_not_awaited()
    svc._recorder.finding_recorded.assert_not_awaited()


# ---- shadow compare -----------------------------------------------------------


async def test_disagreeing_predicates_are_logged_and_the_ledger_wins():
    svc = _service(ledger_open=1, legacy_open=2, ledger_keys=["ENG-1"], legacy_keys=["ENG-1", "ENG-2"])
    with structlog.testing.capture_logs() as logs:
        out = await svc.run_once()
    (warning,) = [e for e in logs if e["event"] == "watcher_cap_predicates_disagree"]
    assert warning["log_level"] == "warning"
    assert (warning["ledger_open"], warning["legacy_open"]) == (1, 2)
    assert warning["only_legacy"] == ["ENG-2"]
    assert warning["only_ledger"] == []
    assert out["action"] == "proposed"  # 1 < 2: the ledger's answer was used


async def test_agreeing_predicates_log_nothing():
    svc = _service(ledger_open=1, legacy_open=1)
    with structlog.testing.capture_logs() as logs:
        await svc.run_once()
    assert not [e for e in logs if e["event"] == "watcher_cap_predicates_disagree"]


# ---- reconcile ----------------------------------------------------------------


async def test_reconcile_checks_the_ledgers_keys_and_closes_a_cancelled_draft():
    svc = _service(ledger_keys=["ENG-1", "ENG-2", "ENG-3"], legacy_keys=["ENG-1"], lines=[])
    svc._filer.draft_states = AsyncMock(return_value={"ENG-1": "canceled", "ENG-2": "completed"})
    await svc.run_once()
    assert svc._filer.draft_states.await_args.args[0] == ["ENG-1", "ENG-2", "ENG-3"]
    assert {c.args[1] for c in svc._runs.release_ticket.await_args_list} == {"ENG-1", "ENG-2", "ENG-3"}
    closed = {(c.args[0].key, c.kwargs["state"]) for c in svc._recorder.draft_released.await_args_list}
    # Closing is final, so only an explicit cancel closes. Completed (#282 decides)
    # and missing (deleted, or moved to another team) just free their slot.
    assert closed == {("ENG-1", "cancelled")}


# ---- deploy markers -----------------------------------------------------------


def _with_deploys(svc, lines, record=None):
    svc._deploy_sync = MagicMock(
        record=record or AsyncMock(return_value={"recorded": 1, "undetermined": 0}),
        lookback_hours=AsyncMock(return_value=72),
    )
    svc._query_deploy_lines = AsyncMock(return_value=lines)
    return svc


async def test_every_tick_reports_the_stacks_deploy_markers_even_a_quiet_one():
    ts = int(datetime(2026, 9, 20, 8, 26, tzinfo=UTC).timestamp() * 1e9)
    svc = _with_deploys(
        _service(lines=[]),
        [
            (str(ts), "acme-deploy", "deployed 65f0a0257ee1: backend ml"),
            (str(ts + 1), "watchtower", "Session done Failed=0 Scanned=9 Updated=1"),  # no sha: not reportable
        ],
    )
    assert (await svc.run_once())["action"] == "skipped_quiet"
    repo, markers = svc._deploy_sync.record.await_args.args
    assert repo == REPO
    assert [(m.sha, m.services, m.at) for m in markers] == [
        ("65f0a0257ee1", ("backend", "ml"), datetime.fromtimestamp(ts / 1e9, tz=UTC))
    ]
    assert svc._query_deploy_lines.await_args.args[1] == 72  # looks further back than the briefing's day


async def test_a_failing_deploy_sync_never_stops_the_tick():
    ts = str(int(datetime(2026, 9, 20, 8, 26, tzinfo=UTC).timestamp() * 1e9))
    svc = _with_deploys(
        _service(),
        [(ts, "acme-deploy", "deployed 65f0a0257ee1: backend")],
        record=AsyncMock(side_effect=RuntimeError("ledger down")),
    )
    with structlog.testing.capture_logs() as logs:
        out = await svc.run_once()
    assert out["action"] == "proposed"
    assert [e["log_level"] for e in logs if e["event"] == "watcher_deploy_sync_failed"] == ["error"]


# ---- one draft per tick (review of #340, minors 2 and 4) ----------------------


def _two_deferred(svc, first, second):
    """deferred_watcher_findings answers before, then after, the filing."""
    svc._cases.deferred_watcher_findings = AsyncMock(side_effect=[[first, second], [second]])


async def test_only_one_draft_is_filed_per_tick_even_with_slots_to_spare():
    # Cap 4 with 1 draft open: filing the oldest deferred finding leaves the
    # cap at 2/4, so nothing about the CAP stops this tick's own proposal.
    # The one-draft-per-tick pace has to.
    old = dict(_JOB, result_summary="TITLE: Old finding\nSEVERITY: low\nBODY:\nold evidence")
    svc = _service(ledger_open=1, legacy_open=1, config=_config(watcher_max_open_proposals=4), job=old)
    _two_deferred(svc, _finding("hc-a", "wr-a"), _finding("hc-b", "wr-b"))
    svc._jobs.get = AsyncMock(side_effect=[old, _JOB])

    out = await svc.run_once()

    assert svc._filer.file_draft.await_count == 1
    assert svc._filer.file_draft.await_args.kwargs["title"] == "[watcher] Old finding"
    assert out["action"] == "deferred"


async def test_a_bypass_cannot_add_a_second_draft_to_a_tick_that_already_filed():
    # When #292 makes _bypasses_cap live, a high-severity proposal must still
    # wait for the next tick if this one already filed a deferred finding.
    old = dict(_JOB, result_summary="TITLE: Old finding\nSEVERITY: low\nBODY:\nold evidence")
    svc = _service(ledger_open=1, legacy_open=1, config=_config(watcher_max_open_proposals=4), job=old)
    _two_deferred(svc, _finding("hc-a", "wr-a"), _finding("hc-b", "wr-b"))
    svc._jobs.get = AsyncMock(side_effect=[old, _JOB])
    svc._bypasses_cap = lambda proposal: True  # type: ignore[method-assign]

    out = await svc.run_once()

    assert svc._filer.file_draft.await_count == 1
    assert out["action"] == "deferred"


async def test_a_bypass_still_files_through_a_full_cap_when_nothing_was_filed_yet():
    svc = _service(ledger_open=2, legacy_open=2)
    svc._bypasses_cap = lambda proposal: True  # type: ignore[method-assign]
    out = await svc.run_once()
    assert out["action"] == "proposed"
    svc._filer.file_draft.assert_awaited_once()


# ---- a draft filed just before a crash (review of #340, minor 3) --------------


async def test_a_draft_created_before_the_write_was_lost_is_adopted_not_refiled():
    # Crash between Linear's answer and mark_filed: the run row still says
    # `deferred` and the case has no tracker, so the finding comes back round.
    # Its ticket already exists — adopt it instead of filing a second one.
    svc = _service(deferred=[_finding()], lines=[])
    svc._filer.list_open_drafts = AsyncMock(
        return_value=[
            Draft(key="ENG-4", title="[watcher] Something else", label="backlog"),
            Draft(key="ENG-5", title="[watcher] Fix the breaker", label="backlog"),
        ]
    )

    await svc.run_once()

    svc._filer.file_draft.assert_not_awaited()
    svc._runs.mark_filed.assert_awaited_once_with("wr-d1", tracker="linear", key="ENG-5", url=ANY)
    svc._recorder.deferred_finding_filed.assert_awaited_once_with(
        "hc-d1", TrackerRef("linear", "ENG-5", ""), watcher_run_id="wr-d1"
    )


async def test_an_unrelated_open_ticket_is_never_adopted():
    svc = _service(deferred=[_finding()], lines=[])
    svc._filer.list_open_drafts = AsyncMock(return_value=[Draft(key="ENG-4", title="Fix the breaker", label="backlog")])
    await svc.run_once()
    svc._filer.file_draft.assert_awaited_once()  # a bare title is somebody else's ticket
    svc._runs.mark_filed.assert_awaited_once_with("wr-d1", tracker="linear", key="ENG-7", url=ANY)


async def test_a_tracker_that_cannot_be_listed_still_files():
    svc = _service(deferred=[_finding()], lines=[])
    svc._filer.list_open_drafts = AsyncMock(side_effect=RuntimeError("linear down"))
    await svc.run_once()
    svc._filer.file_draft.assert_awaited_once()


# ---- the daily analysis cap (review of #340, major 1) ------------------------


async def test_a_full_cap_stops_paying_for_analyses_once_the_daily_cap_is_reached():
    # The draft cap used to be the de-facto daily spend cap: a full cap cost
    # nothing. Now that a full-cap tick analyses, its own cap has to exist.
    svc = _service(ledger_open=2, legacy_open=2, full_cap_analyses=6)
    out = await svc.run_once()

    assert out["action"] == "skipped_analysis_cap"
    svc._jobs.create.assert_not_awaited()
    assert out["log_lines"] == len(_LINES)  # it still looked
    row = svc._runs.create.await_args.kwargs
    assert "6/6" in row["detail"]
    assert not row.get("analysed_under_full_cap")
    svc._runs.full_cap_analyses_since.assert_awaited_once_with(hours=24, repo="acme/widgets")


async def test_under_the_daily_cap_a_full_cap_tick_analyses_and_says_it_paid():
    svc = _service(ledger_open=2, legacy_open=2, full_cap_analyses=5)
    out = await svc.run_once()
    assert out["action"] == "deferred"
    svc._jobs.create.assert_awaited_once()
    assert svc._runs.create.await_args.kwargs["analysed_under_full_cap"] is True


async def test_the_daily_analysis_cap_does_not_bind_a_tick_that_can_file():
    # Nothing changes for a watcher with a free slot: that spend predates #283.
    svc = _service(full_cap_analyses=99)
    out = await svc.run_once()
    assert out["action"] == "proposed"
    svc._jobs.create.assert_awaited_once()
    assert svc._runs.create.await_args.kwargs["analysed_under_full_cap"] is False


async def test_a_full_cap_tick_that_files_nothing_new_is_not_charged_twice():
    # The queue guard fires first: no analysis, so nothing to count.
    svc = _service(ledger_open=2, legacy_open=2, deferred=[_finding("hc-a"), _finding("hc-b")], full_cap_analyses=0)
    out = await svc.run_once()
    assert out["action"] == "skipped_backlog"
    svc._jobs.create.assert_not_awaited()
    assert not svc._runs.create.await_args.kwargs.get("analysed_under_full_cap")


async def test_a_daily_cap_count_that_fails_does_not_stop_the_tick():
    svc = _service(ledger_open=2, legacy_open=2)
    svc._runs.full_cap_analyses_since = AsyncMock(side_effect=RuntimeError("db down"))
    with structlog.testing.capture_logs() as logs:
        out = await svc.run_once()
    # Fail CLOSED on spend: the one thing that must not happen is paying for
    # an analysis because the counter broke.
    assert out["action"] == "skipped_analysis_cap"
    svc._jobs.create.assert_not_awaited()
    assert [e["log_level"] for e in logs if e["event"] == "watcher_analysis_cap_unknown"] == ["error"]


async def test_deploy_markers_that_stop_parsing_are_loud_not_silent():
    # The parser is anchored ^…$: a deploy script that gains a log prefix
    # stops producing markers, and nothing else would ever say so.
    ts = str(int(datetime(2026, 9, 20, 8, 26, tzinfo=UTC).timestamp() * 1e9))
    svc = _with_deploys(
        _service(lines=[]),
        [(ts, "acme-deploy", "INFO 2026-09-20 deployed 65f0a0257ee1: backend")],
        record=AsyncMock(return_value={"recorded": 0, "undetermined": 0}),
    )
    with structlog.testing.capture_logs() as logs:
        await svc.run_once()
    (warning,) = [e for e in logs if e["event"] == "watcher_deploy_markers_unparsed"]
    assert warning["log_level"] == "warning"
    assert warning["lines"] == 1
    svc._deploy_sync.record.assert_not_awaited()  # nothing to report


async def test_a_window_with_no_deploy_lines_at_all_is_not_a_warning():
    svc = _with_deploys(_service(lines=[]), [])
    with structlog.testing.capture_logs() as logs:
        await svc.run_once()
    assert not [e for e in logs if e["event"] == "watcher_deploy_markers_unparsed"]


async def test_the_marker_lookback_follows_the_oldest_unanchored_merge():
    svc = _with_deploys(_service(lines=[]), [])
    svc._deploy_sync.lookback_hours = AsyncMock(return_value=300)
    await svc.run_once()
    assert svc._query_deploy_lines.await_args.args[1] == 300
