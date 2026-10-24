"""The dispatch guard refuses over-budget jobs before any agent runs (#246).

Exercises ``IssueExecutor._refuse_if_over_budget`` directly: it is the single
step every background runner (issue→PR, QA, onboard) starts with.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.orchestration.budget_gate import DispatchDecision
from embry0.safety.error_codes import ErrorCode
from embry0.services.issue_executor import IssueExecutor


def _executor(decision=None):
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()
    ex._db = None
    ex._audit_log_path = None
    ex._budget_gate = None
    if decision is not None:
        gate = MagicMock()
        gate.check = AsyncMock(return_value=decision)
        ex._budget_gate = gate
    ex._emit_audit = AsyncMock()
    return ex


@pytest.mark.asyncio
async def test_no_gate_without_a_database_allows_dispatch():
    ex = _executor()
    assert await ex._refuse_if_over_budget("job-1") is False
    ex._jobs.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_allowed_decision_does_not_touch_the_job():
    ex = _executor(DispatchDecision(allowed=True))
    assert await ex._refuse_if_over_budget("job-1", issue_id="iss-1") is False
    ex._jobs.update.assert_not_awaited()
    ex._emit_audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_refusal_fails_the_job_with_the_budget_error_code():
    ex = _executor(
        DispatchDecision(
            allowed=False,
            limit="monthly",
            message="Monthly budget cap reached: $500.00 spent this month against a $500.00 cap",
            details={"spent_usd": 500.0, "cap_usd": 500.0},
        )
    )

    assert await ex._refuse_if_over_budget("job-1", issue_id="iss-1", actor="alex") is True

    kwargs = ex._jobs.update.await_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["error_code"] == ErrorCode.BUDGET_REFUSED.value
    assert kwargs["error_message"] == "dispatch refused: budget cap reached"
    assert kwargs["finished_at"] is not None


@pytest.mark.asyncio
async def test_refusal_emits_an_audit_row_naming_the_limit():
    ex = _executor(
        DispatchDecision(allowed=False, limit="rate_limit", message="too fast", details={"limit_per_hour": 5})
    )

    await ex._refuse_if_over_budget("job-1", issue_id="iss-1", actor="alex")

    args, kwargs = ex._emit_audit.await_args
    assert args[1] == "job.budget_refused"
    assert kwargs["issue_id"] == "iss-1"
    assert kwargs["details"]["limit"] == "rate_limit"
    assert kwargs["details"]["job_id"] == "job-1"


@pytest.mark.asyncio
async def test_refusal_returns_the_issue_to_open():
    """Only `open` issues dispatch — a row left in `triaging` would swallow
    every future label trigger."""
    ex = _executor(DispatchDecision(allowed=False, limit="daily", message="capped"))

    await ex._refuse_if_over_budget("job-1", issue_id="iss-1")

    ex._issues.update.assert_awaited_once_with("iss-1", status="open")


@pytest.mark.asyncio
async def test_issue_less_refusal_skips_the_issue_write():
    ex = _executor(DispatchDecision(allowed=False, limit="daily", message="capped"))

    await ex._refuse_if_over_budget("job-1")

    ex._issues.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_actor_is_passed_through_to_the_gate():
    ex = _executor(DispatchDecision(allowed=True))
    await ex._refuse_if_over_budget("job-1", issue_id="iss-1", actor="alex")
    ex._budget_gate.check.assert_awaited_once_with(actor="alex", excluding="job-1")


@pytest.mark.asyncio
async def test_a_refused_qa_job_never_reaches_the_workflow():
    """The guard is the first step of every runner, so a refusal short-circuits
    before any sandbox or agent is created."""
    from unittest.mock import patch

    executor = IssueExecutor(
        issues_repo=MagicMock(),
        jobs_repo=AsyncMock(),
        traces_repo=MagicMock(),
        workflow_registry=MagicMock(),
        database_url="postgresql://test/test",
    )
    executor._jobs.update = AsyncMock()
    # AsyncMock's auto-child `get` would return a coroutine that
    # `(job_row or {}).get("trace_id")` then leaks un-awaited.
    executor._jobs.get = AsyncMock(return_value={"trace_id": None})
    executor._audit_log_path = None
    gate = MagicMock()
    gate.check = AsyncMock(return_value=DispatchDecision(allowed=False, limit="daily", message="capped"))
    executor._budget_gate = gate
    stream = AsyncMock(return_value=(None, False, None))

    with (
        patch.object(executor, "_execute_workflow_stream", stream),
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
    ):
        await executor._run_qa_workflow(job_id="job-1", repo="org/r", branch="feature/x", qa_overrides={})

    stream.assert_not_awaited()
    assert executor._jobs.update.await_args.kwargs["error_code"] == ErrorCode.BUDGET_REFUSED.value


@pytest.mark.asyncio
async def test_run_workflow_buckets_on_the_job_dispatcher_not_the_issue_author():
    """#246: ``issues.created_by`` is a provenance constant ("webhook-label",
    "triage"), so bucketing on it throttled the whole fleet — and triage's own
    sub-issues throttled each other. The actor is the job row's dispatcher."""
    from types import SimpleNamespace

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = MagicMock()
    executor._jobs.get = AsyncMock(return_value={"trace_id": None, "dispatched_by": "octocat"})
    executor._jobs.update = AsyncMock()
    executor._issues = MagicMock()
    executor._issues.update = AsyncMock()
    executor._db = None
    executor._audit_log_path = None
    executor._config = SimpleNamespace(environment_secret_key="k")
    executor._env_repo = None
    executor._emit_audit = AsyncMock()
    gate = MagicMock()
    gate.check = AsyncMock(return_value=DispatchDecision(allowed=False, limit="rate_limit", message="x"))
    executor._budget_gate = gate
    executor._post_tracker_outcome = AsyncMock()

    await executor._run_workflow(None, "job-1", {"title": "t", "body": "", "created_by": "webhook-label"})

    gate.check.assert_awaited_once_with(actor="octocat", excluding="job-1")


@pytest.mark.asyncio
async def test_the_refusal_stored_on_the_job_carries_no_figures():
    """jobs.error_message is echoed onto the originating issue, which may be
    public. The detail stays in the audit row."""
    ex = _executor(
        DispatchDecision(
            allowed=False,
            limit="monthly",
            message="Monthly budget cap reached: $483.12 spent this month against a $500.00 cap",
            details={"spent_usd": 483.12, "cap_usd": 500.0},
        )
    )

    await ex._refuse_if_over_budget("job-1", issue_id="iss-1")

    stored = ex._jobs.update.await_args.kwargs["error_message"]
    assert stored == "dispatch refused: budget cap reached"
    assert "$" not in stored
    assert "483.12" not in stored
    # …but the operator-facing numbers are still recorded.
    assert ex._emit_audit.await_args.kwargs["details"]["spent_usd"] == 483.12
    assert "483.12" in ex._emit_audit.await_args.kwargs["details"]["message"]


@pytest.mark.asyncio
async def test_the_tracker_comment_for_a_refusal_shows_no_spend():
    """End to end: the string the gate produces, through the job row, into the
    comment the tracker channel would post on a public issue."""
    from embry0.notifications.tracker_comment import TrackerCommentChannel
    from embry0.trackers import TrackerIssueRef, TrackerRegistry

    class _Recorder:
        name = "github"
        posts: list[str] = []

        async def handle_webhook_event(self, payload, ctx):
            return {}

        async def post_issue_comment(self, ref: TrackerIssueRef, body: str) -> bool:
            self.posts.append(body)
            return True

    ex = _executor(
        DispatchDecision(
            allowed=False,
            limit="monthly",
            message="Monthly budget cap reached: $483.12 spent this month against a $500.00 cap",
            details={"spent_usd": 483.12, "cap_usd": 500.0},
        )
    )
    await ex._refuse_if_over_budget("job-1", issue_id="iss-1")
    stored = ex._jobs.update.await_args.kwargs["error_message"]

    registry = TrackerRegistry()
    tracker = _Recorder()
    tracker.posts = []
    registry.register(tracker)
    channel = TrackerCommentChannel(registry, "github")
    await channel.post_outcome(
        {"id": "iss-1", "tracker": "github", "tracker_key": "7", "repo": "o/r"},
        {"job_id": "job-1", "status": "failed", "error_message": stored, "total_cost_usd": 0.0},
    )

    body = tracker.posts[0]
    error_line = next(line for line in body.splitlines() if line.startswith("Error:"))
    assert "$" not in error_line
    assert "483.12" not in body
    assert "500.00" not in body
