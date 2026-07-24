"""Watcher/proposer (RAV-657) — parsing, guardrails, and the propose path."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.services.watcher import WatcherService, parse_proposal, proposal_fingerprint

# ---- parse_proposal ---------------------------------------------------------


def test_parse_no_issue():
    assert parse_proposal("NO_ISSUE") is None
    assert parse_proposal("  no_issue  ") is None
    assert parse_proposal("") is None


def test_parse_plain_json():
    p = parse_proposal('{"title": "Fix X", "body": "## Evidence\\nboom", "severity": "high"}')
    assert p == {"title": "Fix X", "body": "## Evidence\nboom", "severity": "high"}


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
        "watcher_loki_url": "http://loki.test:3100",
        "watcher_logql": '{compose_project="ai-quoting"}',
        "watcher_min_log_lines": 3,
        "watcher_max_log_lines": 50,
        "watcher_max_open_proposals": 2,
        "watcher_job_timeout_seconds": 60,
        "watcher_linear_team_id": "team-1",
        "watcher_linear_project_id": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeExecutor:
    def __init__(self):
        self.dispatched = []

    async def _run_workflow(self, issue_id, job_id, issue):
        return None

    def _track_task(self, coro, **kwargs):
        self.dispatched.append(kwargs.get("job_id"))
        coro.close()


def _service(
    *,
    config=None,
    lines=None,
    job_result=None,
    open_proposals=0,
    fingerprint_seen=False,
    linear_issue=None,
    linear=True,
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
        ),
        integration_repo=MagicMock(get=AsyncMock(return_value={"telegram_bot_token": "", "telegram_chat_id": ""})),
        executor=_FakeExecutor(),
        linear_sync=MagicMock(create_issue=AsyncMock(return_value=linear_issue)) if linear else None,
    )
    svc._fetch_log_lines = AsyncMock(return_value=lines or [])  # bypass httpx
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
async def test_backlog_cap_skips_before_any_work():
    svc = _service(open_proposals=2)
    out = await svc.run_once()
    assert out["action"] == "skipped_backlog"
    svc._fetch_log_lines.assert_not_awaited()
    svc._jobs.create.assert_not_awaited()


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
    svc._linear.create_issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_analysis_job_records_error():
    job = {"job_id": "job-w1", "status": "failed", "result_summary": None}
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job)
    out = await svc.run_once()
    assert out["action"] == "error"


@pytest.mark.asyncio
async def test_proposal_files_draft_and_records():
    job = {
        "job_id": "job-w1",
        "status": "completed",
        "result_summary": '{"title": "Deals search 500s", "body": "## Evidence\\nlines", "severity": "high"}',
    }
    issue = {"id": "lin-1", "identifier": "RAV-2001", "url": "https://linear.app/x/RAV-2001"}
    svc = _service(lines=["[c] e1", "[c] e2", "[c] e3"], job_result=job, linear_issue=issue)
    out = await svc.run_once()
    assert out["action"] == "proposed"
    assert out["linear_issue_identifier"] == "RAV-2001"
    kwargs = svc._linear.create_issue.await_args.kwargs
    assert kwargs["title"].startswith("[watcher] ")
    assert kwargs["team_id"] == "team-1"
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
    svc._linear.create_issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_exception_is_contained():
    svc = _service(open_proposals=0)
    svc._fetch_log_lines = AsyncMock(side_effect=RuntimeError("loki down"))
    out = await svc.run_once()
    assert out["action"] == "error"
    assert _recorded_action(svc) == "error"


@pytest.mark.asyncio
async def test_ping_skipped_when_telegram_unconfigured():
    svc = _service()
    ok = await svc._ping_human("RAV-2001", "https://linear.app/x", "title")
    assert ok is False  # skipped, not raised


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
    assert svc._jobs.create.await_args.kwargs["repo"] is None


def _now():
    return datetime.now(UTC)
