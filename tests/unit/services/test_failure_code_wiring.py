"""#281 — every place the executor writes status=failed hands its evidence
to the classifier instead of stamping ERR_UNKNOWN (or nothing)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.errors import GraphRecursionError, InvalidUpdateError

from tests.unit.services.test_issue_executor_final_status import _executor, _ok, _run
from tests.unit.services.test_run_qa_workflow_seeding import _qa_executor, _qa_state


def _err(agent, message, code=None):
    out = {"agent_type": agent, "is_error": True, "error_message": message, "output": ""}
    if code:
        out["error_code"] = code
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            {
                "current_stage": "analysis_failed",
                "agent_outputs": [_err("analysis", "Reached maximum number of turns (40)")],
                "errors": ["analysis: Claude Code returned an error result: Reached maximum number of turns (40)"],
            },
            "ERR_AGENT_MAX_TURNS",
        ),
        (
            {
                "current_stage": "developer_failed",
                "agent_outputs": [_ok("triage"), _err("developer", "Command failed with exit code 1 (exit code: 1)")],
            },
            "ERR_AGENT_PROCESS_FAILED",
        ),
        (
            {
                "current_stage": "abandoned",
                "agent_outputs": [_ok("developer"), _ok("review")],
                "errors": ["Abandoned after 3 retries"],
            },
            "ERR_ABANDONED",
        ),
        # A code the runner already diagnosed is kept, not re-derived.
        (
            {
                "current_stage": "developer_failed",
                "agent_outputs": [_err("developer", "whatever", code="ERR_SANDBOX_IMAGE_STALE")],
            },
            "ERR_SANDBOX_IMAGE_STALE",
        ),
        # ...and a node-level code outranks both.
        (
            {
                "current_stage": "failed",
                "error_code": "ERR_TRIAGE_MALFORMED",
                "agent_outputs": [_err("triage", "Reached maximum number of turns (5)")],
            },
            "ERR_TRIAGE_MALFORMED",
        ),
    ],
)
async def test_workflow_result_failures_are_classified(result, expected):
    kwargs = await _run(_executor(), {"triage_decision": {"action": "proceed"}, **result})
    assert kwargs["status"] == "failed"
    assert kwargs["error_code"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (_qa_state("failed", [("dashboard", "qa_failure"), ("billing", "boot_failure")]), "ERR_QA_FAILED"),
        (_qa_state("failed", [("dashboard", "passed"), ("billing", "infra_failure")]), "ERR_QA_INFRA"),
        (_qa_state("failed", [("dashboard", "inconclusive")]), "ERR_QA_INCONCLUSIVE"),
        (
            {"qa": {"final_status": "failed", "validation_errors": ["bad group"], "error_message": "x"}},
            "ERR_QA_CONFIG_INVALID",
        ),
        ({"qa": {"final_status": "exhausted", "error_code": "ERR_QA_IDLE"}}, "ERR_QA_IDLE"),
        ({"qa": {"final_status": "pending"}}, "ERR_QA_NO_VERDICT"),
    ],
)
async def test_standalone_qa_failures_get_a_code(state, expected):
    executor = _qa_executor()

    async def fake_execute(initial_state, *a, **k):
        return state, False, None

    with patch.object(executor, "_execute_workflow_stream", side_effect=fake_execute):
        await executor._run_qa_workflow(job_id="j1", repo="o/r", branch="b", qa_overrides={})

    _, kw = executor._jobs.update.call_args
    assert kw["status"] == "failed"
    assert kw["error_code"] == expected


@pytest.mark.asyncio
async def test_a_crashed_qa_workflow_is_classified_by_exception_type():
    from langgraph.errors import InvalidUpdateError

    executor = _qa_executor()
    boom = AsyncMock(side_effect=InvalidUpdateError("At key 'qa': Can receive only one value per step."))
    with (
        patch.object(executor, "_execute_workflow_stream", boom),
        patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
    ):
        await executor._run_qa_workflow(job_id="j1", repo="o/r", branch="b", qa_overrides={})

    _, kw = executor._jobs.update.call_args
    assert kw["status"] == "failed"
    assert kw["error_code"] == "ERR_STATE_CONCURRENT_UPDATE"


def _main_path_executor():
    """The issue→PR runner's own harness (mirrors test_budget_hard_stop)."""
    from types import SimpleNamespace

    from embry0.services.issue_executor import IssueExecutor

    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.get = AsyncMock(return_value={"trace_id": None})
    ex._jobs.update = AsyncMock()
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()
    ex._traces = MagicMock()
    ex._traces.total_cost_for_job = AsyncMock(return_value=0.0)
    ex._sandbox = MagicMock()
    ex._sandbox.destroy = AsyncMock()
    ex._db = None
    ex._database_url = ""
    ex._audit_log_path = None
    ex._config = SimpleNamespace(environment_secret_key="k", pr_dedup_enabled=False)
    ex._env_repo = None
    ex._budget_gate = None
    ex._emit_audit = AsyncMock()
    ex._post_tracker_outcome = AsyncMock()
    ex._announce_finished = AsyncMock()
    return ex


def _stream_that_dies(exc: BaseException, streamed: dict | None = None):
    async def _stream(input_value, issue_id, job_id, state_sink=None, **kw):
        if state_sink is not None and streamed:
            state_sink.update(streamed)
        raise exc

    return _stream


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (InvalidUpdateError("At key 'qa': Can receive only one value per step."), "ERR_STATE_CONCURRENT_UPDATE"),
        (GraphRecursionError("Recursion limit of 25 reached"), "ERR_GRAPH_RECURSION"),
        (RuntimeError("Sandbox initialization failed: no such image"), "ERR_SANDBOX_INIT"),
    ],
)
async def test_a_crash_on_the_main_pipeline_is_classified(exc, expected):
    """_run_workflow's `except Exception` is the single widest failure path in
    the product: every issue→PR job that dies mid-run lands here, and it
    stamped ERR_UNKNOWN on all of them."""
    ex = _main_path_executor()
    ex._execute_workflow_stream = _stream_that_dies(  # type: ignore[assignment]
        exc, {"sandbox_container_id": "ctr-1", "current_stage": "developer_complete"}
    )

    await ex._run_workflow("iss-1", "job-1", {"title": "t", "body": "", "repo": "o/r"})

    kwargs = ex._jobs.update.await_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["error_code"] == expected


@pytest.mark.asyncio
async def test_an_unclassifiable_main_pipeline_crash_says_so_in_the_logs():
    """ERR_UNKNOWN is allowed to remain, but never silently: the log line is
    what the next audit reads to close the gap."""
    import structlog.testing

    ex = _main_path_executor()
    ex._execute_workflow_stream = _stream_that_dies(  # type: ignore[assignment]
        ValueError("something nobody has clustered"), {"current_stage": "review_passed"}
    )

    with structlog.testing.capture_logs() as logs:
        await ex._run_workflow("iss-1", "job-1", {"title": "t", "body": "", "repo": "o/r"})

    assert ex._jobs.update.await_args.kwargs["error_code"] == "ERR_UNKNOWN"
    event = next(e for e in logs if e["event"] == "failure_unclassified")
    assert event["exception_class"] == "builtins.ValueError"
    assert event["stage"] == "review_passed"
