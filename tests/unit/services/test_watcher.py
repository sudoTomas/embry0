"""Watcher/proposer (RAV-657) — parsing, guardrails, and the propose path."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import structlog

from embry0.services.log_signatures import LogEntry
from embry0.services.watcher import WatcherService, parse_proposal, parse_verdict, proposal_fingerprint
from embry0.trackers.filing import Draft

#: Fixed base timestamp for stubbed Loki entries (#289). Fixed, not NOW(), so a
#: cluster's span — and therefore its rate — is identical on every run.
_T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

# ---- parse_proposal ---------------------------------------------------------


def test_parse_no_issue():
    assert parse_proposal("NO_ISSUE") is None
    assert parse_proposal("  no_issue  ") is None
    assert parse_proposal("") is None


def test_parse_plain_json():
    p = parse_proposal('{"title": "Fix X", "body": "## Evidence\\nboom", "severity": "high"}')
    assert p == {"title": "Fix X", "body": "## Evidence\nboom", "severity": "high", "fingerprint_hint": None}


def test_parse_fenced_json_with_prose():
    text = 'Here is my finding:\n```json\n{"title": "Y", "body": "b"}\n```\nthanks'
    p = parse_proposal(text)
    assert p is not None
    assert p["title"] == "Y"
    assert p["severity"] == "medium"  # default


def test_parse_rejects_json_without_title_or_body():
    assert parse_proposal('{"title": "", "body": "x"}') is None
    assert parse_proposal('{"foo": 1}') is None
    assert parse_proposal("just prose, no json") is None


def test_fingerprint_normalizes_digits_and_case():
    a = proposal_fingerprint("Fix 500s on /api/v1/deals (23 occurrences)")
    b = proposal_fingerprint("fix 500s on /api/v1/deals (7 occurrences)")
    assert a == b
    assert a != proposal_fingerprint("Different title entirely")


# ---- run_once ---------------------------------------------------------------


def _config(**overrides):
    base = {
        "watcher_enabled": True,
        "watcher_interval_seconds": 3600,
        "watcher_startup_delay_seconds": 90,
        "watcher_loki_url": "http://loki.test:3100",
        "watcher_logql": '{compose_project="acme"}',
        "watcher_min_log_lines": 3,
        "watcher_max_log_lines": 50,
        "watcher_max_open_proposals": 2,
        "watcher_proposal_window_days": 7,
        "watcher_job_timeout_seconds": 60,
        "watcher_linear_team_id": "team-1",
        "watcher_linear_project_id": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeExecutor:
    def __init__(self):
        self.dispatched = []
        self.issues = []

    def submit_job(self, job_id, *, repo, title, body="", github_number=None, additional_context="", kind=""):
        self.issues.append({"repo": repo, "title": title, "body": body, "github_number": github_number})
        self.dispatched.append(job_id)


def _service(
    *,
    config=None,
    lines=None,
    job_result=None,
    open_proposals=0,
    fingerprint_seen=False,
    linear_issue=None,
    linear=True,
    open_issues=None,
    tracked_identifiers=None,
    identifier_states=None,
    findings=None,
    patterns=None,
    last_analysed_end=None,
    unparseable_backoff=None,
    last_tick_at=None,
    last_tick_attempt_at=None,
):
    svc = WatcherService(
        config=config or _config(),
        jobs_repo=MagicMock(
            create=AsyncMock(return_value="job-w1"),
            get=AsyncMock(return_value=job_result),
        ),
        watcher_runs_repo=MagicMock(
            create=AsyncMock(return_value="wr-1"),
            open_proposal_count=AsyncMock(return_value=open_proposals),
            fingerprint_exists=AsyncMock(return_value=fingerprint_seen),
            recent_proposal_titles=AsyncMock(return_value=[]),
            recent_findings=AsyncMock(return_value=findings or []),
            open_proposal_tickets=AsyncMock(
                return_value=[{"tracker": "linear", "key": k} for k in (tracked_identifiers or [])]
            ),
            release_ticket=AsyncMock(return_value=1),
            last_analysed_window_end=AsyncMock(return_value=last_analysed_end),
            unparseable_backoff=AsyncMock(return_value=unparseable_backoff),
            last_tick_at=AsyncMock(return_value=last_tick_at),
            last_tick_attempt_at=AsyncMock(return_value=last_tick_attempt_at),
            record_tick_attempt=AsyncMock(),
        ),
        integration_repo=MagicMock(get_telegram_credentials=AsyncMock(return_value=("", ""))),
        executor=_FakeExecutor(),
        filer=MagicMock(
            tracker="linear",
            target="team-1",
            file_draft=AsyncMock(
                return_value=Draft(key=linear_issue["identifier"], url=linear_issue.get("url", ""))
                if linear_issue
                else None
            ),
            list_open_drafts=AsyncMock(
                return_value=[
                    Draft(
                        key=i["identifier"],
                        title=i.get("title", ""),
                        url=i.get("url", ""),
                        label=i.get("state", ""),
                    )
                    for i in (open_issues or [])
                ]
            ),
            draft_states=AsyncMock(return_value=identifier_states or {}),
        )
        if linear
        else None,
        patterns_repo=MagicMock(
            upsert=AsyncMock(),
            list_recent=AsyncMock(return_value=patterns or []),
        ),
    )
    # #289: the tick reads structured entries so a cluster has a real
    # first/last seen. Stub that seam; `lines=` fixtures are unchanged, and the
    # timestamps are spaced a second apart so a cluster has a non-zero span.
    _entries = [
        LogEntry(line=_text, timestamp=_T0 + timedelta(seconds=_i), service="svc")
        for _i, _text in enumerate(lines or [])
    ]
    svc._fetch_log_entries = AsyncMock(return_value=_entries)  # bypass httpx
    svc._fetch_recent_commits = AsyncMock(return_value=[])
    svc._fetch_deploy_markers = AsyncMock(return_value=[])
    return svc


def _recorded_action(svc):
    return svc._runs.create.await_args.kwargs["action"]


@pytest.mark.asyncio
async def test_unconfigured_records_error():
    svc = _service(linear=False)
    out = await svc.run_once()
    assert out["action"] == "error"
    assert _recorded_action(svc) == "error"


@pytest.mark.asyncio
async def test_full_cap_without_a_ledger_still_looks_at_the_logs():
    # #283: a full cap used to end the tick BEFORE the logs were fetched (682
    # of 1,339 ticks were blind). With no ledger nothing can be deferred, so
    # the analysis is still not paid for — but the row says what the window held.
    svc = _service(open_proposals=2, lines=_LINES)
    out = await svc.run_once()
    assert out["action"] == "skipped_backlog"
    svc._fetch_log_entries.assert_awaited_once()
    assert svc._runs.create.await_args.kwargs["log_lines"] == len(_LINES)
    svc._jobs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_cap_on_a_quiet_window_is_a_quiet_tick():
    svc = _service(open_proposals=2)
    assert (await svc.run_once())["action"] == "skipped_quiet"


@pytest.mark.asyncio
async def test_dedup_window_threaded_into_repo_calls():
    # RAV-1073: the configured window must reach both the open-proposal cap and
    # the fingerprint dedup so stale drafts age out. Exercise the proposal path
    # (lines + a parseable verdict) so fingerprint_exists is actually called.
    job = {"job_id": "job-w1", "status": "completed", "result_summary": '{"title": "X", "body": "b"}'}
    svc = _service(
        config=_config(watcher_proposal_window_days=3),
        lines=["[c] e1", "[c] e2", "[c] e3"],
        job_result=job,
        fingerprint_seen=True,
    )
    await svc.run_once()
    assert svc._runs.open_proposal_count.await_args.kwargs["days"] == 3
    assert svc._runs.fingerprint_exists.await_args.kwargs["days"] == 3


@pytest.mark.asyncio
async def test_quiet_window_skips_without_dispatch():
    svc = _service(lines=["[c] one", "[c] two"])  # below min of 3
    out = await svc.run_once()
    assert out["action"] == "skipped_quiet"
    svc._jobs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_issue_verdict_recorded():
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job)
    out = await svc.run_once()
    assert out["action"] == "no_issue"
    svc._filer.file_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_analysis_job_records_error():
    job = {"job_id": "job-w1", "status": "failed", "result_summary": None}
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job)
    out = await svc.run_once()
    assert out["action"] == "error"


@pytest.mark.asyncio
async def test_failed_analysis_job_records_its_error_code():
    """#275: a budget stop must be tellable from any other failed tick."""
    job = {"job_id": "job-w1", "status": "failed", "error_code": "ERR_BUDGET_OVERRUN", "result_summary": None}
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job)
    out = await svc.run_once()
    assert out["action"] == "error"
    detail = svc._runs.create.await_args.kwargs["detail"]
    assert detail == "analysis job status=failed error_code=ERR_BUDGET_OVERRUN"


@pytest.mark.asyncio
async def test_analysis_job_is_created_with_a_hard_budget():
    """#275: unattended ticks stop at their own cap whatever the fleet mode is."""
    svc = _service(
        config=_config(watcher_job_budget_usd=1.25),
        job_result={"status": "failed", "job_id": "job-w1"},
    )
    await svc._run_analysis_job("task text")
    assert svc._jobs.create.await_args.kwargs["pipeline_config"] == {
        "max_budget_usd": 1.25,
        "budget_overrun_mode": "hard",
    }


@pytest.mark.asyncio
async def test_proposal_files_draft_and_records():
    job = {
        "job_id": "job-w1",
        "status": "completed",
        "result_summary": '{"title": "Deals search 500s", "body": "## Evidence\\nlines", "severity": "high"}',
    }
    issue = {"id": "lin-1", "identifier": "ENG-2001", "url": "https://linear.app/x/ENG-2001"}
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job, linear_issue=issue)
    out = await svc.run_once()
    assert out["action"] == "proposed"
    assert out["tracker_key"] == "ENG-2001"
    kwargs = svc._filer.file_draft.await_args.kwargs
    assert kwargs["title"].startswith("[watcher] ")
    assert svc._filer.target == "team-1", "filed into the stack's own target"
    # The human gate is structural: issue creation must carry NO label input.
    assert "labels" not in kwargs and "label_ids" not in kwargs
    assert "embry0" not in kwargs["title"]
    assert "Add the `embry0` label to accept" in kwargs["description"]
    # Analysis job actually dispatched through the executor.
    assert svc._executor.dispatched == ["job-w1"]


@pytest.mark.asyncio
async def test_duplicate_fingerprint_skips_filing():
    job = {
        "job_id": "job-w1",
        "status": "completed",
        "result_summary": '{"title": "Deals search 500s", "body": "b"}',
    }
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job, fingerprint_seen=True)
    out = await svc.run_once()
    assert out["action"] == "skipped_duplicate"
    svc._filer.file_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_exception_is_contained():
    svc = _service(open_proposals=0)
    svc._fetch_log_entries = AsyncMock(side_effect=RuntimeError("loki down"))
    out = await svc.run_once()
    assert out["action"] == "error"
    assert _recorded_action(svc) == "error"


@pytest.mark.asyncio
async def test_ping_skipped_when_telegram_unconfigured():
    svc = _service()
    ok = await svc._ping_human("ENG-2001", "https://linear.app/x", "title")
    assert ok is False  # skipped, not raised


@pytest.mark.asyncio
async def test_ping_uses_raw_credentials(monkeypatch):
    """RAV-657 hotfix: the ping must read RAW creds — get() masks secrets,
    which made the original path silently skip every ping."""
    svc = _service()
    svc._integrations.get_telegram_credentials = AsyncMock(return_value=("tok-raw", "chat-1"))
    sent = {}

    async def _fake_send(token, chat_id, text):
        sent.update(token=token, chat_id=chat_id, text=text)
        return True

    monkeypatch.setattr("embry0.notifications.telegram.send_message", _fake_send)
    ok = await svc._ping_human("ENG-2001", "https://linear.app/x/ENG-2001", "Deals search 500s")
    assert ok is True
    assert sent["token"] == "tok-raw"
    assert sent["chat_id"] == "chat-1"
    assert "ENG-2001" in sent["text"] and "embry0" in sent["text"]


# ---- excerpt task assembly --------------------------------------------------


@pytest.mark.asyncio
async def test_analysis_task_carries_excerpts_and_contract():
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(lines=["[api] ERROR boom", "[ml] Traceback x", "[api] ERROR again"], job_result=job)
    await svc.run_once()
    task = svc._jobs.create.await_args.kwargs["task"]
    assert "[api] ERROR boom" in task
    assert "NO_ISSUE" in task  # output contract stated
    assert svc._jobs.create.await_args.kwargs["context"] == {"type": "none"}
    assert svc._jobs.create.await_args.kwargs["repo"] is None  # no stack repo configured
    assert svc._jobs.create.await_args.kwargs["dispatched_by"] == "embry0:watcher"  # #281


@pytest.mark.asyncio
async def test_analysis_job_carries_the_stack_repo():
    """#378: the job is about the stack, so its row says so — that is what
    routes its notifications and lists it under the repo. The workspace stays
    type=none: the excerpt rides the task, nothing is cloned."""
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(config=_config(watcher_repo="acme/backend"), lines=_LINES, job_result=job)
    await svc.run_once()
    assert svc._jobs.create.await_args.kwargs["repo"] == "acme/backend"
    assert svc._jobs.create.await_args.kwargs["context"] == {"type": "none"}
    assert svc._executor.issues[0]["repo"] == "acme/backend"


# ---- recursion guard (#378) --------------------------------------------------

_OWN_ERROR = '{"event": "watcher_tick_failed", "level": "error", "exception": "Traceback (most recent call last)"}'


@pytest.mark.asyncio
async def test_a_burst_of_the_watchers_own_errors_is_a_quiet_window():
    """embry0 watching itself sees its own tick failures. They must not be
    counted, clustered or analysed: no job, no proposal, a quiet tick."""
    job = {"job_id": "job-w1", "status": "completed", "result_summary": _PROPOSAL_TEXT}
    svc = _service(lines=[_OWN_ERROR] * 40, job_result=job, linear_issue={"identifier": "GH-1", "url": "u"})
    with structlog.testing.capture_logs() as logs:
        out = await svc.run_once()
    assert out["action"] == "skipped_quiet", out
    svc._jobs.create.assert_not_awaited()
    svc._filer.file_draft.assert_not_awaited()
    assert svc._runs.create.await_args.kwargs["log_lines"] == 0
    dropped = [e for e in logs if e["event"] == "watcher_own_lines_dropped"]
    assert dropped and dropped[0]["dropped"] == 40 and dropped[0]["kept"] == 0


@pytest.mark.asyncio
async def test_the_watchers_own_errors_never_reach_the_analysis_prompt():
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    real = ["[api] ERROR boom", "[ml] Traceback x", "[api] ERROR again"]
    svc = _service(lines=[_OWN_ERROR] * 10 + real, job_result=job)
    out = await svc.run_once()
    assert out["action"] == "no_issue", out
    task = svc._jobs.create.await_args.kwargs["task"]
    assert "watcher_tick_failed" not in task
    for line in real:
        assert line in task
    assert svc._runs.create.await_args.kwargs["log_lines"] == len(real)


_LOG_READ = (
    'INFO:     172.18.0.9:40312 - "GET /api/v1/logs/query?container=api&search=ERROR%7CException%7CTraceback'
    '&since_minutes=60 HTTP/1.1" 200 OK'
)


@pytest.mark.asyncio
async def test_the_drill_down_of_the_last_analysis_is_a_quiet_window():
    """An analysis reads logs through /logs/query; the access lines that leaves
    quote its query, error words and all. The next tick must not pay to read
    them: no job, a quiet tick."""
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(lines=[_LOG_READ] * 4, job_result=job)
    out = await svc.run_once()
    assert out["action"] == "skipped_quiet", out
    svc._jobs.create.assert_not_awaited()
    assert svc._runs.create.await_args.kwargs["log_lines"] == 0


@pytest.mark.asyncio
async def test_analysis_task_names_the_watched_repo():
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(
        config=_config(watcher_repo="acme/backend"),
        lines=["[api] ERROR boom", "[ml] Traceback x", "[api] ERROR again"],
        job_result=job,
    )
    await svc.run_once()
    task = svc._jobs.create.await_args.kwargs["task"]
    assert "from the acme/backend stack" in task
    assert "other/widgets" not in task


# ---- deploy markers ---------------------------------------------------------


def _deploy_marker_service(streams):
    """A service whose real _fetch_deploy_markers reads ``streams`` from a fake Loki."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = request.url.params["query"]
        return httpx.Response(200, json={"status": "success", "data": {"result": streams}})

    svc = _service(config=_config(watcher_deploy_logql='{job="acme-deploy"}'))
    svc._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc._fetch_deploy_markers = WatcherService._fetch_deploy_markers.__get__(svc)
    return svc, seen


def _ns(iso: str) -> str:
    return str(int(datetime.fromisoformat(iso).timestamp() * 1e9))


@pytest.mark.asyncio
async def test_deploy_markers_render_script_pushed_lines_verbatim():
    streams = [
        {
            "stream": {"job": "acme-deploy"},
            "values": [
                [_ns("2026-09-18T21:50:00+00:00"), "deployed 621b8c42abcd: backend frontend"],
                [_ns("2026-09-18T19:10:00+00:00"), "deployed 90d66068aaaa: ml"],
            ],
        }
    ]
    svc, seen = _deploy_marker_service(streams)
    markers = await svc._fetch_deploy_markers(datetime(2026, 9, 18, 22, 0, tzinfo=UTC))
    assert seen["query"] == '{job="acme-deploy"}'
    assert markers == [
        "deploy 2026-09-18 21:50 UTC [acme-deploy] deployed 621b8c42abcd: backend frontend",
        "deploy 2026-09-18 19:10 UTC [acme-deploy] deployed 90d66068aaaa: ml",
    ]


@pytest.mark.asyncio
async def test_deploy_markers_keep_watchtower_updates_and_drop_idle_polls():
    streams = [
        {
            "stream": {"container_name": "acme-watchtower"},
            "values": [
                [_ns("2026-07-30T13:05:00+00:00"), "Session done Failed=0 Scanned=4 Updated=0 notify=no"],
                [_ns("2026-07-30T12:58:00+00:00"), "Session done Failed=0 Scanned=4 Updated=4 notify=no"],
            ],
        }
    ]
    svc, _ = _deploy_marker_service(streams)
    markers = await svc._fetch_deploy_markers(datetime(2026, 7, 30, 14, 0, tzinfo=UTC))
    assert markers == ["deploy 2026-07-30 12:58 UTC [acme-watchtower] recreated 4 container(s)"]


@pytest.mark.asyncio
async def test_deploy_markers_bound_line_length_and_skip_blank_lines():
    streams = [
        {
            "stream": {"job": "acme-deploy"},
            "values": [
                [_ns("2026-09-18T21:50:00+00:00"), "deployed " + "x" * 500],
                [_ns("2026-09-18T21:40:00+00:00"), "   "],
            ],
        }
    ]
    svc, _ = _deploy_marker_service(streams)
    markers = await svc._fetch_deploy_markers(datetime(2026, 9, 18, 22, 0, tzinfo=UTC))
    assert len(markers) == 1
    assert markers[0].endswith("x" * (160 - len("deployed ")))
    assert not markers[0].endswith("x" * 161)


# ---- RAV-1140: context-proxy flag + drill-down prompt section ---------------


@pytest.mark.asyncio
async def test_context_proxy_on_flags_job_and_renders_drilldown():
    cfg = _config(
        context_proxy_enabled=True,
        context_proxy_container_allowlist="acme-backend, acme-frontend",
        context_proxy_query_quota=30,
    )
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(config=cfg, lines=["[api] e1", "[api] e2", "[api] e3"], job_result=job)
    await svc.run_once()
    kwargs = svc._jobs.create.await_args.kwargs
    assert kwargs["context"] == {"type": "none", "watcher_log_access": True}
    task = kwargs["task"]
    assert "mcp__context__logs_query" in task
    assert "acme-backend, acme-frontend" in task
    assert "30 queries" in task


@pytest.mark.asyncio
async def test_context_proxy_off_keeps_plain_context_and_no_drilldown():
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(lines=["[api] e1", "[api] e2", "[api] e3"], job_result=job)
    await svc.run_once()
    kwargs = svc._jobs.create.await_args.kwargs
    assert kwargs["context"] == {"type": "none"}
    assert "mcp__context__logs_query" not in kwargs["task"]


@pytest.mark.asyncio
async def test_context_proxy_enabled_without_allowlist_stays_off():
    """Enabled + empty allowlist = proxy not launched — the flag must not be
    set or init_node would try to provision against a missing proxy."""
    cfg = _config(context_proxy_enabled=True, context_proxy_container_allowlist="  ")
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(config=cfg, lines=["[api] e1", "[api] e2", "[api] e3"], job_result=job)
    await svc.run_once()
    kwargs = svc._jobs.create.await_args.kwargs
    assert kwargs["context"] == {"type": "none"}
    assert "mcp__context__logs_query" not in kwargs["task"]


def _now():
    return datetime.now(UTC)


# ---- RAV-1052: outbound webhook on the proposed path ------------------------


@pytest.mark.asyncio
async def test_proposed_emits_webhook():
    svc = _service(
        lines=["[api] err"] * 5,
        job_result={
            "job_id": "job-w1",
            "status": "completed",
            "result_summary": '{"title": "Fix X", "body": "evidence", "severity": "high"}',
        },
        linear_issue={"identifier": "ENG-1099", "url": "https://linear.app/x/ENG-1099"},
    )
    notifier = MagicMock()
    svc._webhook_notifier = notifier
    out = await svc.run_once()
    assert out["action"] == "proposed"
    notifier.emit_soon.assert_called_once()
    event, data = notifier.emit_soon.call_args.args
    assert event == "watcher.proposed"
    assert data["identifier"] == "ENG-1099"
    assert data["url"] == "https://linear.app/x/ENG-1099"
    assert data["title"] == "Fix X"
    assert data["severity"] == "high"


@pytest.mark.asyncio
async def test_no_webhook_when_nothing_proposed():
    svc = _service(lines=["[api] err"])  # below min-signal
    notifier = MagicMock()
    svc._webhook_notifier = notifier
    out = await svc.run_once()
    assert out["action"] == "skipped_quiet"
    notifier.emit_soon.assert_not_called()


@pytest.mark.asyncio
async def test_analysis_job_carries_full_task_in_issue_body():
    """RAV-1062: the workflow prompt is built from the synthetic issue's
    title+body — the full task (with the log excerpt) must ride the body.
    With body="" the analysis agent never saw a single log line."""
    svc = _service(job_result={"status": "failed", "job_id": "job-w1"})
    task = "Analyze the following production log excerpts...\n[gateway] ERROR boom\n[gateway] ERROR boom2"
    await svc._run_analysis_job(task)

    assert svc._executor.issues, "no issue dispatched"
    issue = svc._executor.issues[0]
    assert issue["title"] == "[watcher] log analysis"
    assert issue["body"] == task


# ---- RAV-1062: TITLE/SEVERITY/BODY line contract ----------------------------


def test_parse_line_format_basic():
    p = parse_proposal("TITLE: Fix the flux capacitor\nSEVERITY: high\nBODY:\n## Evidence\nboom")
    assert p == {
        "title": "Fix the flux capacitor",
        "severity": "high",
        "body": "## Evidence\nboom",
        "fingerprint_hint": None,
    }


def test_parse_line_format_body_with_json_and_fences_survives_verbatim():
    """The exact failure that motivated the contract change: a body quoting
    log lines that contain JSON (`{"detail":"Not Found"}`) and code fences —
    no escaping required, nothing lost."""
    body = (
        "## Evidence\n"
        "```\n"
        '2026-07-26 WARN poller: 404 Not Found: {"detail":"Not Found"}\n'
        "```\n"
        "## Impact\nemail ingestion dead"
    )
    p = parse_proposal(f"TITLE: Circuit breaker stuck OPEN\nSEVERITY: high\nBODY:\n{body}")
    assert p is not None
    assert '{"detail":"Not Found"}' in p["body"]
    assert p["severity"] == "high"


def test_parse_line_format_defaults_and_normalizes_severity():
    assert parse_proposal("TITLE: t\nBODY:\nb")["severity"] == "medium"  # missing
    assert parse_proposal("TITLE: t\nSEVERITY: CRITICAL\nBODY:\nb")["severity"] == "medium"  # invalid


def test_parse_line_format_strips_stray_trailing_fence():
    p = parse_proposal("TITLE: t\nSEVERITY: low\nBODY:\nreal content\n```")
    assert p["body"] == "real content"


def test_parse_line_format_rejects_empty_body():
    assert parse_proposal("TITLE: t\nSEVERITY: low\nBODY:\n   ") is None


def test_parse_json_fallback_still_works_after_contract_change():
    p = parse_proposal('{"title": "Old style", "body": "b", "severity": "low"}')
    assert p == {"title": "Old style", "body": "b", "severity": "low", "fingerprint_hint": None}


# ---- watcher hardening (post-RAV-1066/1067 incident) ------------------------


@pytest.mark.asyncio
async def test_analysis_job_pins_analysis_template():
    """Routing must be deterministic: triage's LLM classification drifted to
    job_kind=code once the task carried real log evidence, sending watcher
    ticks through developer+reviewer against an empty workspace. The job-row
    template outranks triage in plan_route (RAV-1058 increment 3)."""
    svc = _service(job_result={"status": "failed", "job_id": "job-w1"})
    await svc._run_analysis_job("task text")
    assert svc._jobs.create.await_args.kwargs["pipeline_template"] == "analysis-default"


@pytest.mark.asyncio
async def test_known_issues_fed_into_analysis_task():
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result={"status": "failed", "job_id": "job-w1"})
    svc._runs.recent_proposal_titles = AsyncMock(return_value=["msGraphProxy breaker stuck OPEN"])
    await svc.run_once()
    task = svc._jobs.create.await_args.kwargs["task"]
    assert "- msGraphProxy breaker stuck OPEN" in task
    assert "do not re-propose any of these" in task


@pytest.mark.asyncio
async def test_no_known_issues_renders_none():
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result={"status": "failed", "job_id": "job-w1"})
    await svc.run_once()
    assert "(none)" in svc._jobs.create.await_args.kwargs["task"]


def test_parse_fingerprint_line():
    p = parse_proposal(
        "TITLE: Breaker stuck OPEN for 3 days\nSEVERITY: high\nFINGERPRINT: circuitbreaker msgraphproxy open\nBODY:\nb"
    )
    assert p is not None
    assert p["fingerprint_hint"] == "circuitbreaker msgraphproxy open"


def test_fingerprint_hint_survives_title_rephrasing():
    """The RAV-1067 duplicate: same issue, reworded title. With a stable
    FINGERPRINT the dedup key is identical across runs."""
    a = parse_proposal(
        "TITLE: msGraphProxy circuit breaker stuck OPEN for 2+ days, halting mailbox polling\n"
        "SEVERITY: high\nFINGERPRINT: circuitbreaker msgraphproxy open\nBODY:\nb"
    )
    b = parse_proposal(
        "TITLE: msGraphProxy circuit breaker stuck OPEN — email polling halted for 60+ mailboxes\n"
        "SEVERITY: high\nFINGERPRINT: circuitbreaker msgraphproxy open\nBODY:\nb"
    )
    assert a is not None and b is not None
    assert proposal_fingerprint(a["fingerprint_hint"]) == proposal_fingerprint(b["fingerprint_hint"])
    # Title-derived keys would have differed:
    assert proposal_fingerprint(a["title"]) != proposal_fingerprint(b["title"])


@pytest.mark.asyncio
async def test_proposed_run_records_title_and_hint_fingerprint():
    job = {
        "job_id": "job-w1",
        "status": "completed",
        "result_summary": (
            "TITLE: Deals search 500s\nSEVERITY: high\nFINGERPRINT: deals search 500\nBODY:\n## Evidence\nlines"
        ),
    }
    issue = {"id": "lin-1", "identifier": "ENG-2001", "url": "https://linear.app/x/ENG-2001"}
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job, linear_issue=issue)
    out = await svc.run_once()
    assert out["action"] == "proposed"
    kwargs = svc._runs.create.await_args.kwargs
    assert kwargs["detail"] == "Deals search 500s"  # title fed back as a known issue next tick
    assert kwargs["fingerprint"] == proposal_fingerprint("deals search 500")


# ---- parse_verdict (RAV-1138) -----------------------------------------------


def test_parse_verdict_no_issue_with_rationale_and_memory():
    v = parse_verdict(
        "NO_ISSUE\n"
        "RATIONALE: only the known OnlyOffice healthcheck 404 noise this window\n"
        "MEMORY: onlyoffice healthcheck 404 | container healthcheck probes 404 by design\n"
    )
    assert v["proposal"] is None
    assert v["rationale"] == "only the known OnlyOffice healthcheck 404 noise this window"
    assert v["memory"] == [("onlyoffice healthcheck 404", "container healthcheck probes 404 by design")]


def test_parse_verdict_proposal_with_rationale_before_body():
    v = parse_verdict(
        "TITLE: Deals search 500s\n"
        "SEVERITY: high\n"
        "FINGERPRINT: deals search 500\n"
        "RATIONALE: 40 new 500s right after the 12:58 deploy\n"
        "BODY:\n## Evidence\nlines"
    )
    assert v["proposal"] is not None
    assert v["proposal"]["title"] == "Deals search 500s"
    assert v["rationale"] == "40 new 500s right after the 12:58 deploy"
    assert v["memory"] == []


def test_parse_verdict_caps_memory_lines():
    text = "NO_ISSUE\n" + "\n".join(f"MEMORY: slug {i} | summary {i}" for i in range(6))
    assert len(parse_verdict(text)["memory"]) == 3


def test_parse_verdict_garbage_is_unparseable_not_no_issue():
    v = parse_verdict("complete nonsense with no contract lines")
    assert v["kind"] == "unparseable"
    assert v["proposal"] is None
    assert v["rationale"] is None
    assert v["memory"] == []
    assert "NO_ISSUE" in v["reason"]


# ---- reconciliation (RAV-1106/1134) -----------------------------------------


@pytest.mark.asyncio
async def test_reconcile_releases_canceled_and_missing_drafts():
    svc = _service(
        lines=["[c] e1", "[c] e2", "[c] e3"],
        job_result={"status": "failed", "job_id": "job-w1"},
        tracked_identifiers=["ENG-1", "ENG-2", "ENG-3"],
        identifier_states={"ENG-1": "canceled", "ENG-2": "started"},  # RAV-3 deleted in Linear
    )
    await svc.run_once()
    released = {c.args[1] for c in svc._runs.release_ticket.await_args_list}
    assert released == {"ENG-1", "ENG-3"}


@pytest.mark.asyncio
async def test_reconcile_runs_before_cap_check():
    svc = _service(
        lines=["[c] e1", "[c] e2", "[c] e3"],
        open_proposals=5,  # over cap — tick will skip, but reconcile must still run
        tracked_identifiers=["ENG-9"],
        identifier_states={"ENG-9": "completed"},
    )
    out = await svc.run_once()
    assert out["action"] == "skipped_backlog"
    svc._runs.release_ticket.assert_awaited_once_with("linear", "ENG-9")


@pytest.mark.asyncio
async def test_reconcile_linear_error_fails_open():
    svc = _service(
        lines=["[c] e1", "[c] e2", "[c] e3"],
        job_result={"status": "failed", "job_id": "job-w1"},
        tracked_identifiers=["ENG-1"],
    )
    svc._filer.draft_states = AsyncMock(side_effect=RuntimeError("tracker down"))
    out = await svc.run_once()  # tick proceeds despite the reconcile failure
    svc._runs.release_ticket.assert_not_awaited()
    assert out["action"] == "error"  # from the failed analysis job, not the reconcile


# ---- briefing sections (RAV-1106/1138/1139) ---------------------------------


@pytest.mark.asyncio
async def test_briefing_injects_open_tickets_findings_patterns_and_changes():
    svc = _service(
        lines=["[c] e1", "[c] e2", "[c] e3"],
        job_result={"status": "failed", "job_id": "job-w1"},
        open_issues=[{"identifier": "ENG-42", "title": "Known msgraph flake", "state": "In Progress"}],
        findings=[
            {
                "action": "no_issue",
                "detail": None,
                "fingerprint": "healthcheck 404",
                "rationale": "probe noise",
                "created_at": datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
            }
        ],
        patterns=[{"fingerprint": "onlyoffice 404", "summary": "healthcheck by design", "occurrences": 4}],
    )
    svc._fetch_recent_commits = AsyncMock(return_value=["commit abc1234 2026-07-30T12:00:00Z fix: thing"])
    svc._fetch_deploy_markers = AsyncMock(return_value=["deploy 2026-07-30 12:58 UTC [wt] recreated 4 container(s)"])
    await svc.run_once()
    task = svc._jobs.create.await_args.kwargs["task"]
    assert "- ENG-42 [In Progress] Known msgraph flake" in task
    assert "no_issue [healthcheck 404]: probe noise" in task
    assert "- onlyoffice 404: healthcheck by design (confirmed 4x)" in task
    assert "commit abc1234" in task
    assert "recreated 4 container(s)" in task


@pytest.mark.asyncio
async def test_briefing_section_failure_degrades_to_unavailable():
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result={"status": "failed", "job_id": "job-w1"})
    svc._filer.list_open_drafts = AsyncMock(side_effect=RuntimeError("tracker down"))
    await svc.run_once()
    task = svc._jobs.create.await_args.kwargs["task"]
    assert "(unavailable)" in task  # open-tickets section degraded, tick survived


@pytest.mark.asyncio
async def test_briefing_failures_recorded_on_the_run_row_and_logged():
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job)
    svc._filer.list_open_drafts = AsyncMock(side_effect=RuntimeError("tracker down"))
    svc._fetch_recent_commits = AsyncMock(side_effect=RuntimeError("github down"))
    with structlog.testing.capture_logs() as logs:
        out = await svc.run_once()
    assert out["action"] == "no_issue"  # still fail-open for the analysis
    assert svc._runs.create.await_args.kwargs["briefing_failures"] == ["open_tickets", "commits"]
    degraded = [e for e in logs if e["event"] == "watcher_briefing_degraded"]
    assert degraded and degraded[0]["failed_sections"] == ["open_tickets", "commits"]


@pytest.mark.asyncio
async def test_healthy_briefing_records_no_failures():
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job)
    await svc.run_once()
    assert svc._runs.create.await_args.kwargs["briefing_failures"] == []


@pytest.mark.asyncio
async def test_one_failed_change_source_still_renders_the_other():
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result={"status": "failed", "job_id": "job-w1"})
    svc._fetch_recent_commits = AsyncMock(return_value=["commit abc1234 2026-07-29 fix: x"])
    svc._fetch_deploy_markers = AsyncMock(side_effect=RuntimeError("loki down"))
    await svc.run_once()
    assert "commit abc1234" in svc._jobs.create.await_args.kwargs["task"]
    assert svc._runs.create.await_args.kwargs["briefing_failures"] == ["deploys"]


# ---- memory persistence (RAV-1138) ------------------------------------------


@pytest.mark.asyncio
async def test_no_issue_persists_rationale_and_memory():
    job = {
        "job_id": "job-w1",
        "status": "completed",
        "result_summary": (
            "NO_ISSUE\nRATIONALE: benign probe noise\nMEMORY: onlyoffice 404 | healthcheck probes 404 by design"
        ),
    }
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job)
    out = await svc.run_once()
    assert out["action"] == "no_issue"
    assert svc._runs.create.await_args.kwargs["rationale"] == "benign probe noise"
    svc._patterns.upsert.assert_awaited_once()
    assert svc._patterns.upsert.await_args.args[:2] == ("onlyoffice 404", "healthcheck probes 404 by design")


@pytest.mark.asyncio
async def test_proposed_persists_rationale():
    job = {
        "job_id": "job-w1",
        "status": "completed",
        "result_summary": (
            "TITLE: Deals search 500s\nSEVERITY: high\nFINGERPRINT: deals search 500\n"
            "RATIONALE: new since the last deploy\nBODY:\n## Evidence\nlines"
        ),
    }
    issue = {"id": "lin-1", "identifier": "ENG-2001", "url": "https://linear.app/x/ENG-2001"}
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job, linear_issue=issue)
    out = await svc.run_once()
    assert out["action"] == "proposed"
    assert svc._runs.create.await_args.kwargs["rationale"] == "new since the last deploy"


@pytest.mark.asyncio
async def test_pattern_upsert_failure_does_not_fail_the_tick():
    job = {
        "job_id": "job-w1",
        "status": "completed",
        "result_summary": "NO_ISSUE\nMEMORY: slug | summary",
    }
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job)
    svc._patterns.upsert = AsyncMock(side_effect=RuntimeError("db hiccup"))
    out = await svc.run_once()
    assert out["action"] == "no_issue"


@pytest.mark.asyncio
async def test_analysis_contract_is_scoped_away_from_triage():
    """Two consecutive live ticks failed triage_malformed because TRIAGE
    followed the analysis agent's embedded NO_ISSUE contract. The contract
    block must explicitly bind only the analysis agent and tell other
    readers to use their own format."""
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result={"status": "failed", "job_id": "job-w1"})
    await svc.run_once()
    task = svc._jobs.create.await_args.kwargs["task"]
    assert "bind ONLY the ANALYSIS" in task
    assert "IGNORE this contract entirely" in task
    assert "The analysis agent's FINAL message" in task


# ---- query validation (#270) ------------------------------------------------

_ESCAPED_LOGQL = '{compose_project=\\"acme\\"} |~ \\"(?i)error\\"'
_LOKI_PARSE_ERROR = "parse error at line 1, col 18: syntax error: unexpected IDENTIFIER, expecting STRING"


def _loki(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _validating_service(handler, **config_overrides) -> WatcherService:
    svc = _service(config=_config(**config_overrides))
    svc._http = _loki(handler)
    return svc


def test_health_is_unverified_before_any_validation():
    assert _service().health == "unverified"


@pytest.mark.asyncio
async def test_validate_queries_ok_marks_healthy():
    svc = _validating_service(
        lambda r: httpx.Response(200, json={"data": {"result": []}}),
        watcher_deploy_logql='{job="acme-deploy"}',
    )
    checks = await svc.validate_queries()
    assert set(checks) == {"WATCHER_LOGQL", "WATCHER_DEPLOY_LOGQL"}
    assert svc.health == "ok"


@pytest.mark.asyncio
async def test_escaped_quote_logql_marks_unhealthy_and_logs_critical():
    svc = _validating_service(
        lambda r: httpx.Response(400, text=_LOKI_PARSE_ERROR),
        watcher_logql=_ESCAPED_LOGQL,
    )
    with structlog.testing.capture_logs() as logs:
        await svc.validate_queries()

    assert svc.health == "invalid_logql"
    critical = [e for e in logs if e["log_level"] == "critical"]
    assert len(critical) == 1
    assert critical[0]["event"] == "watcher_logql_invalid"
    assert critical[0]["env_var"] == "WATCHER_LOGQL"
    assert "parse error" in critical[0]["detail"]
    # The 9-day outage's exact cause gets named, not just Loki's column number.
    assert "backslash" in critical[0]["hint"]


@pytest.mark.asyncio
async def test_invalid_deploy_logql_alone_marks_unhealthy():
    def handler(request: httpx.Request) -> httpx.Response:
        if "deploy" in request.url.params["query"]:
            return httpx.Response(400, text=_LOKI_PARSE_ERROR)
        return httpx.Response(200, json={"data": {"result": []}})

    svc = _validating_service(handler, watcher_deploy_logql='{job=\\"acme-deploy\\"}')
    with structlog.testing.capture_logs() as logs:
        await svc.validate_queries()
    assert svc.health == "invalid_logql"
    assert [e["env_var"] for e in logs if e["log_level"] == "critical"] == ["WATCHER_DEPLOY_LOGQL"]


@pytest.mark.asyncio
async def test_unreachable_loki_is_unverified_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    svc = _validating_service(handler, watcher_logql=_ESCAPED_LOGQL)
    with structlog.testing.capture_logs() as logs:
        await svc.validate_queries()
    assert svc.health == "unverified"
    assert not [e for e in logs if e["log_level"] == "critical"]
    assert [e["event"] for e in logs if e["log_level"] == "warning"] == ["watcher_logql_unverified"]


@pytest.mark.asyncio
async def test_empty_deploy_logql_is_not_probed():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["query"])
        return httpx.Response(200, json={"data": {"result": []}})

    svc = _validating_service(handler, watcher_deploy_logql="")
    checks = await svc.validate_queries()
    assert set(checks) == {"WATCHER_LOGQL"}
    assert len(seen) == 1
    assert svc.health == "ok"


@pytest.mark.asyncio
async def test_empty_watcher_logql_is_invalid_without_asking_loki():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Loki must not be asked about an empty query")

    svc = _validating_service(handler, watcher_logql="  ")
    with structlog.testing.capture_logs() as logs:
        await svc.validate_queries()
    assert svc.health == "missing_logql"
    critical = [e for e in logs if e["log_level"] == "critical"]
    assert [(e["event"], e["env_var"]) for e in critical] == [("watcher_logql_missing", "WATCHER_LOGQL")]


@pytest.mark.asyncio
async def test_run_once_without_logql_records_error_and_skips_loki():
    """POST /watcher/run works with WATCHER_ENABLED=false, where the config
    validator does not require a query — the tick must say so, not send
    Loki an empty one."""
    svc = _service(config=_config(watcher_logql=""))
    out = await svc.run_once()
    assert out["action"] == "error"
    assert "WATCHER_LOGQL" in svc._runs.create.await_args.kwargs["detail"]
    svc._fetch_log_entries.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_loki_endpoint_is_misconfigured_with_its_own_alarm():
    svc = _validating_service(lambda r: httpx.Response(404, text="404 page not found"))
    with structlog.testing.capture_logs() as logs:
        await svc.validate_queries()
    assert svc.health == "loki_misconfigured"
    critical = [e for e in logs if e["log_level"] == "critical"]
    assert {e["event"] for e in critical} == {"watcher_loki_misconfigured"}
    assert "WATCHER_LOKI_URL" in critical[0]["hint"]
    assert not [e for e in logs if e["event"] == "watcher_logql_invalid"]


@pytest.mark.asyncio
async def test_misconfigured_loki_alarm_masks_the_url_password():
    """#535: a Loki behind basic auth is configured as user:password@host,
    and this alarm is the line an operator pastes into a bug report."""
    svc = _validating_service(
        lambda r: httpx.Response(404, text="404 page not found"),
        watcher_loki_url="https://reader:FAKE-loki-password@loki.test:3100",
    )
    with structlog.testing.capture_logs() as logs:
        await svc.validate_queries()
    [alarm] = [e for e in logs if e["event"] == "watcher_loki_misconfigured"]
    assert alarm["loki_url"] == "https://reader:****@loki.test:3100", alarm


async def _run_loop(svc: WatcherService, probe_results: list, *, iterations: int) -> list[str]:
    """Drive start() for ``iterations`` sleeps; returns the validate/tick order."""
    calls: list[str] = []
    real_validate = svc.validate_queries
    sleeps = 0

    async def validate():
        calls.append("validate")
        return await real_validate()

    async def run_once():
        calls.append("tick")
        return {"action": "skipped_low_signal"}

    async def sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps > iterations:
            raise asyncio.CancelledError

    svc.validate_queries = validate
    svc.run_once = run_once
    svc._sleep = sleep
    responses = iter(probe_results)
    svc._http = _loki(lambda r: next(responses))
    with pytest.raises(asyncio.CancelledError):
        await svc.start()
    return calls


_OK = {"data": {"result": []}}


@pytest.mark.asyncio
async def test_loop_does_not_tick_while_the_query_is_invalid_then_recovers():
    """The outage was ~24 error ticks a day. A CRITICAL next to each one is
    not a fix — an invalid query must stop the ticks, and a corrected Loki
    verdict must restart them without an orchestrator restart."""
    svc = _service(config=_config(watcher_deploy_logql=""))
    bad = httpx.Response(400, text=_LOKI_PARSE_ERROR)
    calls = await _run_loop(svc, [bad, bad, bad, bad, httpx.Response(200, json=_OK)], iterations=5)
    # startup probe + 3 blocked intervals (re-probe, no tick) + recovery
    # interval (re-probe OK → tick) + one healthy interval (tick, no probe).
    assert calls == ["validate", "validate", "validate", "validate", "validate", "tick", "tick"]
    svc._runs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_loop_does_not_tick_against_a_wrong_endpoint():
    svc = _service(config=_config(watcher_deploy_logql=""))
    calls = await _run_loop(svc, [httpx.Response(404)] * 3, iterations=2)
    assert calls == ["validate", "validate", "validate"]


@pytest.mark.asyncio
@pytest.mark.parametrize("logql", ["", "   "])
async def test_loop_does_not_tick_without_a_query(logql: str):
    svc = _service(config=_config(watcher_logql=logql, watcher_deploy_logql=""))
    calls = await _run_loop(svc, [], iterations=2)
    assert calls == ["validate", "validate", "validate"]
    assert svc.health == "missing_logql"


@pytest.mark.asyncio
async def test_loop_keeps_ticking_while_loki_is_unreachable():
    """Fail-open as before: no verdict on the query is no reason to stop."""
    svc = _service(config=_config(watcher_deploy_logql=""))
    calls = await _run_loop(svc, [httpx.Response(503)] * 3, iterations=2)
    assert calls == ["validate", "validate", "tick", "validate", "tick"]


@pytest.mark.asyncio
async def test_loop_keeps_ticking_when_only_the_deploy_query_is_invalid():
    """Deploy markers are a fail-open briefing section: a bad deploy query is
    unhealthy and alarms, but it produces no error ticks, so it stops none."""
    svc = _service(config=_config(watcher_deploy_logql='{job=\\"acme-deploy\\"}'))
    ok, bad = httpx.Response(200, json=_OK), httpx.Response(400, text=_LOKI_PARSE_ERROR)
    calls = await _run_loop(svc, [ok, bad, ok, bad], iterations=1)
    assert calls == ["validate", "validate", "tick"]
    assert svc.health == "invalid_logql"


# ---- three-way verdict + bounded retry (SH-P2.4) -----------------------------

_LINES = ["[c] e1", "[c] e2", "[c] e3"]
_PROPOSAL_TEXT = "TITLE: Fix X\nSEVERITY: low\nBODY:\n## Evidence\nboom"


def test_parse_verdict_kinds():
    assert parse_verdict("NO_ISSUE\nRATIONALE: benign")["kind"] == "no_issue"
    assert parse_verdict(_PROPOSAL_TEXT)["kind"] == "proposal"
    assert parse_verdict("")["kind"] == "unparseable"


def test_parse_verdict_names_the_missing_half_of_a_proposal():
    assert "BODY" in parse_verdict("TITLE: Fix X\nSEVERITY: low")["reason"]
    assert "TITLE" in parse_verdict("BODY:\nsome evidence")["reason"]
    assert "empty" in parse_verdict("   ")["reason"]


def test_parse_verdict_accepts_no_issue_after_a_reasoning_preamble():
    # Real deliverables: result_summary glues the agent's text blocks, so the
    # verdict token lands after earlier narration, sometimes mid-line.
    for text in (
        "I checked the 404 storm first.\n\nNO_ISSUE\nRATIONALE: all covered",
        "...to capture the full stack trace lines.NO_ISSUE\nRATIONALE: all covered",
        "## Verdict: NO_ISSUE\n\nRATIONALE: all covered",
        "All covered.\n**NO_ISSUE**",
    ):
        v = parse_verdict(text)
        assert v["kind"] == "no_issue", text


def test_parse_verdict_mention_of_no_issue_in_prose_is_not_a_verdict():
    v = parse_verdict("I'll verify the triage read before accepting NO_ISSUE — the error looks specific.")
    assert v["kind"] == "unparseable"


def test_parse_verdict_accepts_title_glued_to_preceding_narration():
    text = (
        "the one candidate not covered by existing tickets.TITLE: Fix the stall\nSEVERITY: high\nBODY:\n## Evidence\nx"
    )
    v = parse_verdict(text)
    assert v["kind"] == "proposal"
    assert v["proposal"]["title"] == "Fix the stall"
    assert v["proposal"]["severity"] == "high"


@pytest.mark.parametrize(
    "text",
    [
        "The contract wants `TITLE: x` first.\nSo far nothing.\nBODY:\nsome text",
        "see http://x:TITLE: y\nBODY:\nsome text",
        "note*TITLE: x\nBODY:\nsome text",
        "fields are SEVERITY;TITLE: x\nBODY:\nsome text",
    ],
)
def test_title_glue_accepts_only_sentence_end_punctuation(text):
    # A false positive here files a real Linear ticket.
    assert parse_verdict(text)["kind"] == "unparseable"


@pytest.mark.parametrize(
    "text",
    [
        "This window cannot be NO_ISSUE\n",
        "This is not a case for NO_ISSUE.",
        "I would normally answer NO_ISSUE",
    ],
)
def test_negated_or_narrated_no_issue_is_not_a_verdict(text):
    assert parse_verdict(text)["kind"] == "unparseable"


def test_inline_body_is_rejected_with_a_reason_that_says_so():
    v = parse_verdict("TITLE: Fix X\nBODY: the evidence is inline")
    assert v["kind"] == "unparseable"
    assert "own line" in v["reason"]


def test_empty_body_reason_still_says_empty():
    assert "empty" in parse_verdict("TITLE: Fix X\nBODY:\n   ")["reason"]


def _jobs_sequence(svc, *jobs):
    svc._jobs.create = AsyncMock(side_effect=[j["job_id"] for j in jobs])
    svc._jobs.get = AsyncMock(side_effect=list(jobs))


@pytest.mark.asyncio
async def test_unparseable_output_retries_once_then_records_error():
    svc = _service(lines=_LINES)
    _jobs_sequence(
        svc,
        {"job_id": "job-a", "status": "completed", "result_summary": "rambling, no verdict", "total_cost_usd": 1.25},
        {"job_id": "job-b", "status": "completed", "result_summary": "still rambling", "total_cost_usd": 0.75},
    )
    with structlog.testing.capture_logs() as logs:
        out = await svc.run_once()
    assert out["action"] == "error"
    assert svc._jobs.create.await_count == 2  # ONE retry, never more
    kwargs = svc._runs.create.await_args.kwargs
    assert "unparseable" in kwargs["detail"]
    assert kwargs["job_id"] == "job-b"
    assert kwargs["retried_job_id"] == "job-a"
    assert kwargs["analysis_cost_usd"] == pytest.approx(2.0)
    svc._filer.file_draft.assert_not_awaited()
    assert [e["job_id"] for e in logs if e["event"] == "watcher_analysis_unparseable"] == ["job-a", "job-b"]


@pytest.mark.asyncio
async def test_retry_that_parses_is_used_and_its_cost_is_on_the_row():
    svc = _service(lines=_LINES)
    _jobs_sequence(
        svc,
        {"job_id": "job-a", "status": "completed", "result_summary": "rambling, no verdict", "total_cost_usd": 1.0},
        {
            "job_id": "job-b",
            "status": "completed",
            "result_summary": "NO_ISSUE\nRATIONALE: benign",
            "total_cost_usd": 0.5,
        },
    )
    out = await svc.run_once()
    assert out["action"] == "no_issue"
    kwargs = svc._runs.create.await_args.kwargs
    assert kwargs["job_id"] == "job-b"
    assert kwargs["retried_job_id"] == "job-a"
    assert kwargs["analysis_cost_usd"] == pytest.approx(1.5)
    assert kwargs["rationale"] == "benign"
    # The retry tells the agent what was wrong with the first answer.
    retry_task = svc._jobs.create.await_args_list[1].kwargs["task"]
    assert "RETRY" in retry_task and "ANALYSIS DELIVERABLE CONTRACT" in retry_task


@pytest.mark.asyncio
async def test_first_attempt_that_parses_is_not_retried():
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE", "total_cost_usd": 0.4}
    svc = _service(lines=_LINES, job_result=job)
    await svc.run_once()
    assert svc._jobs.create.await_count == 1
    kwargs = svc._runs.create.await_args.kwargs
    assert kwargs["retried_job_id"] is None
    assert kwargs["analysis_cost_usd"] == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_failed_job_is_not_retried():
    svc = _service(lines=_LINES, job_result={"job_id": "job-w1", "status": "failed", "result_summary": None})
    out = await svc.run_once()
    assert out["action"] == "error"
    assert svc._jobs.create.await_count == 1


@pytest.mark.asyncio
async def test_failed_retry_job_records_error_with_both_jobs():
    svc = _service(lines=_LINES)
    _jobs_sequence(
        svc,
        {"job_id": "job-a", "status": "completed", "result_summary": "rambling", "total_cost_usd": 1.0},
        {"job_id": "job-b", "status": "failed", "result_summary": None, "total_cost_usd": 0.25},
    )
    out = await svc.run_once()
    assert out["action"] == "error"
    kwargs = svc._runs.create.await_args.kwargs
    assert "status=failed" in kwargs["detail"]
    assert kwargs["retried_job_id"] == "job-a"
    assert kwargs["analysis_cost_usd"] == pytest.approx(1.25)


@pytest.mark.asyncio
async def test_unparseable_output_persists_no_memory_patterns():
    svc = _service(lines=_LINES)
    garbage = "rambling\nMEMORY: some slug | looks benign"
    _jobs_sequence(
        svc,
        {"job_id": "job-a", "status": "completed", "result_summary": garbage},
        {"job_id": "job-b", "status": "completed", "result_summary": garbage},
    )
    await svc.run_once()
    svc._patterns.upsert.assert_not_awaited()


# ---- contiguous query window (SH-P2.4) ---------------------------------------


def _window(svc):
    kwargs = svc._runs.create.await_args.kwargs
    return kwargs["window_start"], kwargs["window_end"]


@pytest.mark.asyncio
async def test_window_starts_where_the_last_analysed_window_ended():
    last_end = datetime.now(UTC) - timedelta(minutes=95)  # interval is 60 min
    svc = _service(lines=["[c] one"], last_analysed_end=last_end)
    await svc.run_once()
    start, end = _window(svc)
    assert start == last_end
    fetch_start, fetch_end = svc._fetch_log_entries.await_args.args
    assert (fetch_start, fetch_end) == (start, end)


@pytest.mark.asyncio
async def test_first_ever_tick_looks_back_one_interval():
    svc = _service(lines=["[c] one"], last_analysed_end=None)
    await svc.run_once()
    start, end = _window(svc)
    assert end - start == timedelta(seconds=3600)


@pytest.mark.asyncio
async def test_long_outage_is_clamped_and_the_gap_is_on_the_row():
    svc = _service(
        config=_config(watcher_max_lookback_seconds=7200),
        lines=["[c] one"],
        last_analysed_end=datetime.now(UTC) - timedelta(days=3),
    )
    await svc.run_once()
    start, end = _window(svc)
    assert end - start == timedelta(seconds=7200)
    # 3 days since the last analysed window, 2h looked at → ~70h given up.
    unanalysed = svc._runs.create.await_args.kwargs["unanalysed_seconds"]
    assert abs(unanalysed - (3 * 86400 - 7200)) <= 2


@pytest.mark.asyncio
async def test_contiguous_window_records_zero_unanalysed_seconds():
    svc = _service(lines=["[c] one"], last_analysed_end=datetime.now(UTC) - timedelta(minutes=95))
    await svc.run_once()
    assert svc._runs.create.await_args.kwargs["unanalysed_seconds"] == 0


def _clamp_events(logs):
    return [(e["log_level"], e["event"]) for e in logs if e["event"].startswith("watcher_window_clamp")]


@pytest.mark.asyncio
async def test_clamp_warns_only_when_the_clamped_window_is_analysed():
    """The floor slides every tick of a long skipped/errored stretch; the logs
    behind it are only really lost on the tick that reaches a verdict."""
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    stale = datetime.now(UTC) - timedelta(days=3)

    skipped = _service(lines=["[c] one"], last_analysed_end=stale)
    with structlog.testing.capture_logs() as logs:
        assert (await skipped.run_once())["action"] == "skipped_quiet"
    assert _clamp_events(logs) == [("debug", "watcher_window_clamp_pending")]

    backlog = _service(lines=_LINES, last_analysed_end=stale, open_proposals=5)
    with structlog.testing.capture_logs() as logs:
        assert (await backlog.run_once())["action"] == "skipped_backlog"
    assert _clamp_events(logs) == [("debug", "watcher_window_clamp_pending")]

    analysed = _service(lines=_LINES, job_result=job, last_analysed_end=stale)
    with structlog.testing.capture_logs() as logs:
        assert (await analysed.run_once())["action"] == "no_issue"
    events = _clamp_events(logs)
    assert ("warning", "watcher_window_clamped") in events
    warned = next(e for e in logs if e["event"] == "watcher_window_clamped")
    assert warned["unanalysed_seconds"] > 0


# ---- line cap honesty (SH-P2.4 review A2) -------------------------------------


@pytest.mark.asyncio
async def test_excerpt_at_the_line_cap_is_recorded_as_truncated():
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(config=_config(watcher_max_log_lines=3), lines=_LINES, job_result=job)
    await svc.run_once()
    assert svc._runs.create.await_args.kwargs["log_lines_truncated"] is True


@pytest.mark.asyncio
async def test_excerpt_under_the_line_cap_is_not_truncated():
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(lines=_LINES, job_result=job)  # cap is 50
    await svc.run_once()
    assert svc._runs.create.await_args.kwargs["log_lines_truncated"] is False


# ---- unparseable back-off (SH-P2.4 review A5) ---------------------------------


@pytest.mark.asyncio
async def test_unchanged_window_is_not_reanalysed_right_after_an_unparseable_error():
    svc = _service(lines=_LINES, unparseable_backoff={"log_lines": 3, "skipped_since": 0})
    out = await svc.run_once()
    assert out["action"] == "skipped_unparseable"
    svc._jobs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_matching_lines_end_the_unparseable_backoff():
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(lines=_LINES, job_result=job, unparseable_backoff={"log_lines": 2, "skipped_since": 0})
    assert (await svc.run_once())["action"] == "no_issue"


@pytest.mark.asyncio
async def test_unparseable_backoff_expires_after_three_skipped_ticks():
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    held = _service(lines=_LINES, job_result=job, unparseable_backoff={"log_lines": 3, "skipped_since": 2})
    assert (await held.run_once())["action"] == "skipped_unparseable"
    released = _service(lines=_LINES, job_result=job, unparseable_backoff={"log_lines": 3, "skipped_since": 3})
    assert (await released.run_once())["action"] == "no_issue"


@pytest.mark.asyncio
async def test_no_backoff_state_means_analysis_runs():
    # The repo reports back-off state ONLY when the last attempt was an
    # unparseable error — a Loki error or failed job yields None here.
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(lines=_LINES, job_result=job, unparseable_backoff=None)
    assert (await svc.run_once())["action"] == "no_issue"


@pytest.mark.asyncio
async def test_blocked_stretch_does_not_use_up_the_unparseable_backoff():
    """#270 blocked intervals record nothing, and the back-off counts only
    skipped_unparseable ROWS — so a blocked stretch cannot count as withheld
    ticks and release the back-off on its own."""
    svc = _service(
        config=_config(watcher_deploy_logql=""),
        lines=_LINES,
        unparseable_backoff={"log_lines": 3, "skipped_since": 0},
    )
    svc._http = _loki(lambda r: httpx.Response(400, text=_LOKI_PARSE_ERROR))
    sleeps = 0

    async def sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 4:
            raise asyncio.CancelledError

    svc._sleep = sleep
    with pytest.raises(asyncio.CancelledError):
        await svc.start()
    assert svc.tick_blocked
    svc._runs.unparseable_backoff.assert_not_awaited()
    svc._runs.create.assert_not_awaited()
    svc._jobs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_stopped_job_is_one_job_and_an_error_tick_with_the_code():
    """#275 x SH-P2.4: a budget stop is status=failed, not garbage output —
    re-running it would spend the cap a second time."""
    job = {
        "job_id": "job-w1",
        "status": "failed",
        "error_code": "ERR_BUDGET_OVERRUN",
        "result_summary": "rambling, no verdict",
        "total_cost_usd": 2.0,
    }
    svc = _service(lines=_LINES, job_result=job)
    out = await svc.run_once()
    assert out["action"] == "error"
    assert svc._jobs.create.await_count == 1
    kwargs = svc._runs.create.await_args.kwargs
    assert kwargs["detail"] == "analysis job status=failed error_code=ERR_BUDGET_OVERRUN"
    assert kwargs["job_id"] == "job-w1"
    assert kwargs["retried_job_id"] is None
    assert kwargs["analysis_cost_usd"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_unparseable_error_detail_carries_the_prefix_the_backoff_query_matches():
    from embry0.storage.repositories.watcher_runs import UNPARSEABLE_DETAIL_PREFIX

    svc = _service(lines=_LINES)
    _jobs_sequence(
        svc,
        {"job_id": "job-a", "status": "completed", "result_summary": "rambling"},
        {"job_id": "job-b", "status": "completed", "result_summary": "rambling"},
    )
    await svc.run_once()
    assert svc._runs.create.await_args.kwargs["detail"].startswith(UNPARSEABLE_DETAIL_PREFIX)

    failed = _service(lines=_LINES, job_result={"job_id": "job-w1", "status": "failed", "result_summary": None})
    await failed.run_once()
    assert not failed._runs.create.await_args.kwargs["detail"].startswith(UNPARSEABLE_DETAIL_PREFIX)


@pytest.mark.asyncio
async def test_lookback_never_shorter_than_one_interval():
    svc = _service(
        config=_config(watcher_max_lookback_seconds=60),
        lines=["[c] one"],
        last_analysed_end=datetime.now(UTC) - timedelta(days=3),
    )
    await svc.run_once()
    start, end = _window(svc)
    assert end - start == timedelta(seconds=3600)


@pytest.mark.asyncio
async def test_future_window_end_falls_back_to_one_interval():
    svc = _service(lines=["[c] one"], last_analysed_end=datetime.now(UTC) + timedelta(hours=1))
    await svc.run_once()
    start, end = _window(svc)
    assert end - start == timedelta(seconds=3600)


@pytest.mark.asyncio
async def test_window_lookup_failure_falls_back_to_one_interval():
    svc = _service(lines=["[c] one"])
    svc._runs.last_analysed_window_end = AsyncMock(side_effect=RuntimeError("db down"))
    out = await svc.run_once()
    assert out["action"] == "skipped_quiet"
    start, end = _window(svc)
    assert end - start == timedelta(seconds=3600)


# ---- #289 log-signature clustering wiring -----------------------------------
#
# The mode ladder matters more than the clustering itself here: `shadow` must
# change nothing the model sees, or the two-week comparison against the agent's
# own grouping is comparing against something already changed.

_SPIKE = [f"ERROR DNS resolve failed for host-{i}" for i in range(30)]


def _sig_repo():
    return MagicMock(
        # #290 reads the full prior rows, not just "have I seen this id":
        # last_window_count and last_seen_at are what tell a spike from steady
        # traffic and a regression from a signature that never left.
        rows_for=AsyncMock(return_value={}),
        known_ids=AsyncMock(return_value=set()),
        record_window=AsyncMock(return_value=0),
    )


def _clustered_service(mode, *, sig_repo=None, lines=None):
    # job_result is required: without it run_once() polls the analysis job until
    # watcher_job_timeout_seconds (60s) and each test takes a minute.
    svc = _service(
        config=_config(log_clustering_mode=mode),
        lines=lines or _SPIKE,
        job_result={"job_id": "job-c1", "status": "completed", "result_summary": "NO_ISSUE"},
    )
    svc._signatures = sig_repo
    return svc


@pytest.mark.asyncio
async def test_shadow_persists_signatures_but_leaves_the_prompt_alone():
    sig = _sig_repo()
    svc = _clustered_service("shadow", sig_repo=sig)
    await svc.run_once()

    sig.record_window.assert_awaited_once()
    clusters = sig.record_window.await_args.args[0]
    assert len(clusters) == 1, "30 lines differing only by host number are one signature"
    assert clusters[0].count == 30
    # The model still reads the raw tail — shadow changes nothing it sees.
    task = svc._jobs.create.await_args.kwargs["task"]
    assert "ERROR DNS resolve failed for host-7" in task
    assert "sig=" not in task


@pytest.mark.asyncio
async def test_on_replaces_the_raw_tail_with_the_ranked_table():
    sig = _sig_repo()
    svc = _clustered_service("on", sig_repo=sig)
    await svc.run_once()

    task = svc._jobs.create.await_args.kwargs["task"]
    assert "sig=" in task
    assert "[30×]" in task
    assert "template:" in task
    # One sample survives; the other 29 near-identical lines do not.
    assert task.count("ERROR DNS resolve failed for host-") <= 3


@pytest.mark.asyncio
async def test_off_does_not_cluster_or_persist():
    sig = _sig_repo()
    svc = _clustered_service("off", sig_repo=sig)
    await svc.run_once()

    sig.record_window.assert_not_awaited()
    sig.known_ids.assert_not_awaited()
    assert "sig=" not in svc._jobs.create.await_args.kwargs["task"]


@pytest.mark.asyncio
async def test_an_unrecognised_mode_reads_as_off_not_on():
    """A typo in the environment must not quietly change what the model sees."""
    sig = _sig_repo()
    svc = _clustered_service("On!", sig_repo=sig)
    await svc.run_once()

    sig.record_window.assert_not_awaited()
    assert "sig=" not in svc._jobs.create.await_args.kwargs["task"]


@pytest.mark.asyncio
async def test_prior_rows_are_read_before_the_window_is_written():
    """Otherwise this window's own insert makes every signature look familiar:
    `new_signatures` is always 0 and, since record_window REPLACES
    last_window_count, every signature also compares equal to itself and
    nothing can ever read as spiking (#290)."""
    calls = []
    sig = MagicMock(
        rows_for=AsyncMock(side_effect=lambda *a, **k: calls.append("read") or {}),
        record_window=AsyncMock(side_effect=lambda *a, **k: calls.append("write") or 0),
    )
    await _clustered_service("shadow", sig_repo=sig).run_once()
    assert calls == ["read", "write"]


@pytest.mark.asyncio
async def test_a_persistence_failure_does_not_cost_the_tick_its_analysis():
    """Clustering is an observation. The analysis is the tick's one paid step,
    and a broken observation must never take it down."""
    sig = MagicMock(
        rows_for=AsyncMock(side_effect=RuntimeError("db gone")),
        record_window=AsyncMock(),
    )
    svc = _clustered_service("shadow", sig_repo=sig)
    out = await svc.run_once()

    assert out["action"] == "no_issue"
    svc._jobs.create.assert_awaited()


@pytest.mark.asyncio
async def test_clustering_runs_without_a_repository():
    """Legacy callers pass no signatures repo: the ranked table is still
    computed (so `on` works) and nothing is persisted."""
    svc = _clustered_service("on", sig_repo=None)
    await svc.run_once()
    assert "sig=" in svc._jobs.create.await_args.kwargs["task"]


@pytest.mark.asyncio
async def test_line_count_recorded_on_the_row_stays_the_raw_line_count():
    """Clustering must not change what the tick reports about window volume —
    `log_lines` is the comparison every earlier row is on."""
    svc = _clustered_service("on", sig_repo=_sig_repo())
    await svc.run_once()
    assert svc._runs.create.await_args.kwargs["log_lines"] == len(_SPIKE)


# ---- #360: an unregistered stack repo runs no analysis ----------------------


@pytest.mark.asyncio
async def test_a_stack_whose_repo_is_unregistered_records_an_error_before_any_spend():
    svc = _service(config=_config(watcher_repo="acme/unregistered"), lines=["[c] e1", "[c] e2", "[c] e3"])
    asked: list[str] = []

    async def _refusal(repo):
        asked.append(repo)
        return f"ERR_REPO_UNREGISTERED: repo {repo!r} is not registered in embry0"

    svc._executor.unregistered_repo_refusal = _refusal
    out = await svc.run_once()
    assert out["action"] == "error", f"an unregistered stack repo was analysed anyway: {out}"
    assert asked == ["acme/unregistered"]
    assert "ERR_REPO_UNREGISTERED" in svc._runs.create.await_args.kwargs["detail"]
    svc._fetch_log_entries.assert_not_awaited()
    svc._jobs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_registered_stack_repo_is_analysed_as_before():
    svc = _service(config=_config(watcher_repo="acme/registered"), lines=["[c] one", "[c] two"])
    svc._executor.unregistered_repo_refusal = AsyncMock(return_value=None)
    assert (await svc.run_once())["action"] == "skipped_quiet"
