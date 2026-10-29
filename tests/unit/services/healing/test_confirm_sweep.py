"""The confirm-healed sweep end to end (#312, SH-P5.4)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from embry0.services.healing.confirm_sweep import ConfirmHealedSweep
from embry0.storage.repositories.log_signatures import SignatureRow

# A fixed instant, handed to the sweep as its clock: every window below is
# measured from it, however long after import the suite reaches a test.
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def _case(**over: Any) -> dict[str, Any]:
    base = {
        "id": "hc-1",
        "repo": "owner/repo",
        "title": "Deals 500",
        "signature_id": "sig1",
        "signature": "template",
        "tracker": "linear",
        "tracker_key": "ENG-7",
        "tracker_url": "https://linear.test/ENG-7",
        "stage": "deployed",
        "merge_sha": "abc123",
        "merged_at": NOW - timedelta(hours=6),
        "deployed_at": NOW - timedelta(hours=5),
        "deploy_marker_at": NOW - timedelta(hours=5),
        "deploy_sha": "def456",
        "healed_verdict": "pending",
        "occurrence_count": 40,
    }
    base.update(over)
    return base


def _row(*, last_seen_at: datetime, occurrences: int = 400, span_hours: float = 48.0) -> SignatureRow:
    return SignatureRow(
        signature_id="sig1",
        repo="owner/repo",
        template="template",
        first_seen_at=last_seen_at - timedelta(hours=span_hours),
        last_seen_at=last_seen_at,
        occurrence_count=occurrences,
        ticks_seen=40,
        last_window_count=7,
        services=["backend"],
        sample_lines=["a line"],
    )


class _Service:
    def __init__(self) -> None:
        self.verdicts: list[tuple[str, Any, str | None]] = []
        self.evidence: list[dict[str, Any]] = []
        self.reopens: list[tuple[str, str, str | None]] = []

    async def record_verdict(self, case_id, verdict, *, evidence, event_key=None, checked_at=None):
        self.verdicts.append((case_id, verdict, event_key))
        self.evidence.append(evidence)
        return None

    async def reopen(self, case_id, *, reason, at=None, event_key=None):
        self.reopens.append((case_id, reason, event_key))
        # Where HealingCaseService.reopen lands a case: back with a person (#658).
        return {"id": case_id, "stage": "tracked"}


def _sweep(cases, row, *, comments=None, retry=(), **cfg):
    repo = SimpleNamespace(awaiting_verdict=_async(cases), not_healed_awaiting_retry=_async(list(retry)))
    signatures = SimpleNamespace(get=_async(row))
    service = _Service()

    async def _comment(key, body):
        (comments if comments is not None else []).append((key, body))

    sweep = ConfirmHealedSweep(
        cases_repo=repo,
        cases_service=service,
        signatures_repo=signatures,
        config=SimpleNamespace(watcher_repo="owner/repo", **cfg),
        commenter=_comment if comments is not None else None,
        clock=lambda: NOW,
    )
    return sweep, service


def _async(value):
    async def _call(*_a, **_k):
        if isinstance(value, Exception):
            raise value
        return value

    return _call


# ---- verdicts --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_signature_silent_since_the_deploy_is_recorded_healed() -> None:
    sweep, service = _sweep([_case()], _row(last_seen_at=NOW - timedelta(hours=7)))
    tally = await sweep.run_once()
    assert tally["healed"] == 1
    assert service.verdicts[0][0] == "hc-1"
    assert str(service.verdicts[0][1]) == "healed"


@pytest.mark.asyncio
async def test_a_signature_seen_after_the_deploy_is_recorded_not_healed() -> None:
    sweep, service = _sweep([_case()], _row(last_seen_at=NOW - timedelta(minutes=30)))
    tally = await sweep.run_once()
    assert tally["not_healed"] == 1
    assert str(service.verdicts[0][1]) == "not_healed"


@pytest.mark.asyncio
async def test_a_case_still_inside_its_window_is_not_judged_yet() -> None:
    """A loud signature still needs the 15-minute floor to pass."""
    case = _case(deploy_marker_at=NOW - timedelta(minutes=2), deployed_at=NOW - timedelta(minutes=2))
    sweep, service = _sweep([case], _row(last_seen_at=NOW - timedelta(hours=9)))
    tally = await sweep.run_once()
    assert tally["too_early"] == 1
    assert not service.verdicts, "a verdict was recorded before the window elapsed"


@pytest.mark.asyncio
async def test_a_case_without_a_signature_is_inconclusive() -> None:
    sweep, service = _sweep([_case(signature_id=None)], None)
    tally = await sweep.run_once()
    assert tally["inconclusive"] == 1
    assert str(service.verdicts[0][1]) == "inconclusive"


@pytest.mark.asyncio
async def test_the_marker_time_is_preferred_over_the_ledger_time() -> None:
    """deploy_marker_at is the deploy host's own clock; deployed_at is when we
    got round to recording it. Judging from the later one would shorten the
    observation window."""
    case = _case(deploy_marker_at=NOW - timedelta(hours=5), deployed_at=NOW - timedelta(minutes=1))
    sweep, service = _sweep([case], _row(last_seen_at=NOW - timedelta(hours=7)))
    tally = await sweep.run_once()
    assert tally["healed"] == 1, "the sweep used the ledger time and called the case too early"


# ---- idempotency and resilience -------------------------------------------


@pytest.mark.asyncio
async def test_a_second_pass_over_unchanged_state_uses_the_same_event_key() -> None:
    """The ledger drops a repeated event_key whole, so the sweep is safe to
    run on a short interval."""
    sweep, service = _sweep([_case()], _row(last_seen_at=NOW - timedelta(hours=7)))
    await sweep.run_once()
    await sweep.run_once()
    assert len({key for _, _, key in service.verdicts}) == 1


@pytest.mark.asyncio
async def test_the_event_key_changes_when_the_deploy_anchor_does() -> None:
    """record_deploy resets a verdict to pending when an earlier deploy turns
    up late; re-judging against the corrected anchor must be a new event."""
    sweep_a, service_a = _sweep([_case()], _row(last_seen_at=NOW - timedelta(hours=7)))
    await sweep_a.run_once()
    moved = _case(deploy_marker_at=NOW - timedelta(hours=9), deployed_at=NOW - timedelta(hours=9))
    sweep_b, service_b = _sweep([moved], _row(last_seen_at=NOW - timedelta(hours=10)))
    await sweep_b.run_once()
    assert service_a.verdicts[0][2] != service_b.verdicts[0][2]


@pytest.mark.asyncio
async def test_one_broken_case_does_not_stop_the_sweep() -> None:
    good = _case(id="hc-2")
    sweep, service = _sweep(
        [_case(id="hc-bad", deploy_marker_at="not-a-date"), good], _row(last_seen_at=NOW - timedelta(hours=7))
    )
    tally = await sweep.run_once()
    assert tally["checked"] == 2
    assert tally["failed"] == 1
    assert [v[0] for v in service.verdicts] == ["hc-2"]


@pytest.mark.asyncio
async def test_an_unreadable_signature_waits_rather_than_guessing() -> None:
    """Unreadable is not absent. The counter may be back next pass, so the
    honest move is to wait — and above all NOT to read an unreadable counter
    as 'the error stopped', which would close the ticket."""
    repo = SimpleNamespace(awaiting_verdict=_async([_case()]))
    signatures = SimpleNamespace(get=_async(RuntimeError("db gone")))
    service = _Service()
    sweep = ConfirmHealedSweep(
        cases_repo=repo,
        cases_service=service,
        signatures_repo=signatures,
        config=SimpleNamespace(watcher_repo="owner/repo"),
    )
    tally = await sweep.run_once()
    assert tally["too_early"] == 1
    assert not service.verdicts, "a verdict was recorded from an unreadable counter"


@pytest.mark.asyncio
async def test_unreadable_cases_end_the_pass_quietly() -> None:
    repo = SimpleNamespace(awaiting_verdict=_async(RuntimeError("db gone")))
    sweep = ConfirmHealedSweep(
        cases_repo=repo,
        cases_service=_Service(),
        signatures_repo=SimpleNamespace(get=_async(None)),
        config=SimpleNamespace(watcher_repo="owner/repo"),
    )
    assert (await sweep.run_once())["checked"] == 0


# ---- the ticket comment ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_verdict_is_commented_on_the_ticket() -> None:
    comments: list[tuple[str, str]] = []
    sweep, _ = _sweep([_case()], _row(last_seen_at=NOW - timedelta(hours=7)), comments=comments)
    await sweep.run_once()
    assert len(comments) == 1
    key, body = comments[0]
    assert key == "ENG-7"
    assert "Confirmed healed" in body
    assert "#312" in body


@pytest.mark.asyncio
async def test_the_comment_states_elapsed_and_window_in_whole_minutes() -> None:
    deployed = NOW - timedelta(hours=5)
    case = _case(deployed_at=deployed, deploy_marker_at=deployed)
    comments: list[tuple[str, str]] = []
    sweep, _ = _sweep([case], _row(last_seen_at=NOW - timedelta(hours=7)), comments=comments)
    await sweep.run_once()
    _key, body = comments[0]
    assert "Checked 300 minutes after the deploy (def456)" in body, body
    window = re.search(r"against a window of (\d+) minutes sized", body)
    assert window and int(window.group(1)) > 0, f"no window in whole minutes: {body}"


@pytest.mark.asyncio
async def test_a_failed_comment_does_not_undo_the_verdict() -> None:
    """The ledger is the record; the comment is the courtesy."""
    repo = SimpleNamespace(awaiting_verdict=_async([_case()]))
    signatures = SimpleNamespace(get=_async(_row(last_seen_at=NOW - timedelta(hours=7))))
    service = _Service()

    async def _boom(_key, _body):
        raise RuntimeError("linear down")

    sweep = ConfirmHealedSweep(
        cases_repo=repo,
        cases_service=service,
        signatures_repo=signatures,
        config=SimpleNamespace(watcher_repo="owner/repo"),
        commenter=_boom,
    )
    tally = await sweep.run_once()
    assert tally["healed"] == 1
    assert service.verdicts, "the verdict was lost because the comment failed"


@pytest.mark.asyncio
async def test_a_case_with_no_tracker_is_judged_without_a_comment() -> None:
    comments: list[tuple[str, str]] = []
    sweep, service = _sweep([_case(tracker_key=None)], _row(last_seen_at=NOW - timedelta(hours=7)), comments=comments)
    tally = await sweep.run_once()
    assert tally["healed"] == 1
    assert not comments


# ---- configuration ---------------------------------------------------------


def test_the_sweep_is_on_by_default() -> None:
    """Every other autonomy stage ships in shadow. This one only reads and
    comments, so the plan says build it early and leave it on."""
    from embry0.config import Embry0Config

    config = Embry0Config(_env_file=None, database_url="postgresql://unused")
    assert config.confirm_healed_enabled is True
    sweep = ConfirmHealedSweep(
        cases_repo=SimpleNamespace(), cases_service=None, signatures_repo=SimpleNamespace(), config=config
    )
    assert sweep.enabled is True


def test_it_can_be_turned_off() -> None:
    sweep = ConfirmHealedSweep(
        cases_repo=SimpleNamespace(),
        cases_service=None,
        signatures_repo=SimpleNamespace(),
        config=SimpleNamespace(confirm_healed_enabled=False),
    )
    assert sweep.enabled is False


# ---- found on the first live run, 2026-09-21 ------------------------------


@pytest.mark.asyncio
async def test_an_inconclusive_verdict_is_recorded_but_not_commented() -> None:
    """All five first-run verdicts were 'no signature to verify against' on
    historical tickets. A comment saying we could not tell is noise."""
    comments: list[tuple[str, str]] = []
    sweep, service = _sweep([_case(signature_id=None)], None, comments=comments)
    await sweep.run_once()
    assert str(service.verdicts[0][1]) == "inconclusive"
    assert not comments, "an inconclusive verdict was posted to the ticket"


class _LinearSync:
    """The real LinearSync surface the commenter uses — native_ids + post_comment."""

    def __init__(self, ids=None, accept=True):
        self.ids = ids if ids is not None else {"ENG-7": "uuid-7"}
        self.accept = accept
        self.posted: list[tuple[str, str]] = []

    async def native_ids(self, team_id, identifiers):
        self.team_id = team_id
        return {i: self.ids[i] for i in identifiers if i in self.ids}

    async def post_comment(self, linear_issue_id, body):
        self.posted.append((linear_issue_id, body))
        return self.accept


@pytest.mark.asyncio
async def test_the_linear_commenter_posts_to_the_native_uuid_not_the_key() -> None:
    """The first live run passed "RAV-1466" where a TrackerIssueRef was
    expected and every comment died with an AttributeError."""
    from embry0.services.healing.confirm_sweep import linear_commenter

    linear = _LinearSync()
    await linear_commenter(linear, "team-1")("ENG-7", "hello")
    assert linear.posted == [("uuid-7", "hello")]
    assert linear.team_id == "team-1"


@pytest.mark.asyncio
async def test_the_linear_commenter_raises_when_it_cannot_post() -> None:
    """So the sweep logs the failure — silence would read as 'commented'."""
    from embry0.services.healing.confirm_sweep import linear_commenter

    with pytest.raises(LookupError):
        await linear_commenter(_LinearSync(ids={}), "t")("ENG-7", "x")
    with pytest.raises(RuntimeError):
        await linear_commenter(_LinearSync(accept=False), "t")("ENG-7", "x")


def test_production_wires_the_linear_commenter() -> None:
    """Pinned at the source, like #369: the old closure constructed fine and
    failed only when a comment was sent."""
    import ast
    import pathlib

    import embry0.api.app as app_module

    tree = ast.parse(pathlib.Path(app_module.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ConfirmHealedSweep":
            commenter = next(kw.value for kw in node.keywords if kw.arg == "commenter")
            assert "linear_commenter(" in ast.unparse(commenter), ast.unparse(commenter)
            return
    pytest.fail("ConfirmHealedSweep is not constructed in app.py")


# ---- #451: a not_healed verdict earns a retry ------------------------------


def _not_healed(**over: Any) -> dict[str, Any]:
    return _case(stage="verified", healed_verdict="not_healed", reopens=0, **over)


@pytest.mark.asyncio
async def test_a_not_healed_case_with_retries_left_is_reopened() -> None:
    comments: list[tuple[str, str]] = []
    sweep, service = _sweep([], None, comments=comments, retry=[_not_healed()])
    tally = await sweep.run_once()
    assert tally["reopened"] == 1, f"a not_healed case with retries left was not reopened: {tally}"
    case_id, reason, key = service.reopens[0]
    assert case_id == "hc-1"
    assert "retry 1 of 1" in reason, reason
    assert key and "hc-1" in key
    # The ticket is told that nothing started and what a person can do (#658).
    assert any("Nothing has been dispatched" in body and "accept the case again" in body for _k, body in comments), (
        comments
    )


@pytest.mark.asyncio
async def test_the_reopen_key_is_stable_across_passes() -> None:
    """Two passes over the same failed attempt reopen it once: the ledger
    drops the repeated event_key."""
    sweep, service = _sweep([], None, retry=[_not_healed()])
    await sweep.run_once()
    await sweep.run_once()
    assert len({key for _, _, key in service.reopens}) == 1


@pytest.mark.asyncio
async def test_a_not_healed_verdict_says_whether_a_retry_follows() -> None:
    sweep, service = _sweep([_case(reopens=0)], _row(last_seen_at=NOW - timedelta(minutes=30)))
    await sweep.run_once()
    assert service.evidence[0]["retries"] == {"used": 0, "allowed": 1, "next": "reopen"}


@pytest.mark.asyncio
async def test_a_not_healed_verdict_past_the_budget_stops_and_says_why() -> None:
    comments: list[tuple[str, str]] = []
    sweep, service = _sweep([_case(reopens=1)], _row(last_seen_at=NOW - timedelta(minutes=30)), comments=comments)
    tally = await sweep.run_once()
    assert tally["not_healed"] == 1
    assert service.evidence[0]["retries"] == {"used": 1, "allowed": 1, "next": "a person decides"}, service.evidence
    assert "retries are spent" in comments[0][1], comments
