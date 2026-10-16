"""The executor runs pr-review jobs (#30)."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.orchestration.budget_gate import DispatchDecision
from embry0.orchestration.restart import NOT_RESUMABLE_PIPELINES
from embry0.safety.error_codes import ErrorCode
from embry0.services._executor_run import _READ_ONLY_PIPELINES
from embry0.services.issue_executor import IssueExecutor
from embry0.storage.repositories.pr_reviews import PrReviewsRepository
from embry0.workflows.pr_review.graph import PrReviewWorkflow

REVIEW = {
    "pipeline": "pr-review",
    "review_id": "prr-1",
    "repo": "acme/widgets",
    "forge": "github",
    "number": 7,
    "title": "Add the login form",
    "head_sha": "a" * 40,
    "from_fork": False,
    "trigger": "ready",
    "depth": "full",
    "model": None,
}


class _Jobs:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.updates: list[dict[str, Any]] = []
        self.created: dict[str, Any] = {}
        self.launch: dict[str, Any] | None = None

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return self.row

    async def update(self, job_id: str, **fields: Any) -> None:
        self.updates.append(fields)

    async def create(self, **kwargs: Any) -> str:
        self.created = kwargs
        return "job-1"

    async def record_launch(self, job_id: str, launch: dict[str, Any]) -> None:
        self.launch = launch


def _executor(jobs: _Jobs) -> IssueExecutor:
    executor = IssueExecutor(
        issues_repo=MagicMock(),
        jobs_repo=jobs,  # type: ignore[arg-type]
        traces_repo=MagicMock(),
        workflow_registry=MagicMock(),
        database_url="postgresql://unused/unused",
    )
    executor._audit_log_path = None
    executor._budget_gate = SimpleNamespace(check=AsyncMock(return_value=DispatchDecision(allowed=True)))
    executor._finish_credential_uses = AsyncMock()  # type: ignore[method-assign]
    executor._cleanup_sandbox = AsyncMock()  # type: ignore[method-assign]
    executor._load_merged_env_vars = AsyncMock(return_value=[{"key": "A", "value": "1", "scope": "app"}])  # type: ignore[method-assign]
    return executor


def _row(**review: Any) -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "repo": "acme/widgets",
        "task": "Review",
        "dispatched_by": "octocat",
        "pipeline_config": {**REVIEW, **review},
    }


def test_the_pipeline_is_selected_and_never_resumed_or_given_push() -> None:
    assert isinstance(_executor(_Jobs())._select_workflow("pr-review"), PrReviewWorkflow)
    assert "pr-review" in NOT_RESUMABLE_PIPELINES, "a review fans out over a sandbox a resume would not re-enroll"
    assert "pr-review" in _READ_ONLY_PIPELINES, "a review sandbox must never be enrolled with git_push"


def test_the_dispatcher_and_the_executor_agree_on_the_contract() -> None:
    params = inspect.signature(IssueExecutor.start_pr_review_job).parameters
    assert {n: p.kind for n, p in params.items() if n != "self"} == {
        "repo": inspect.Parameter.KEYWORD_ONLY,
        "review": inspect.Parameter.KEYWORD_ONLY,
        "dispatched_by": inspect.Parameter.KEYWORD_ONLY,
    }


async def test_start_creates_the_job_and_records_how_to_launch_it_again() -> None:
    jobs = _Jobs()
    executor = _executor(jobs)
    run = AsyncMock()
    with (
        patch.object(executor, "_run_pr_review_workflow", run),
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
    ):
        job_id = await executor.start_pr_review_job(repo="acme/widgets", review=REVIEW, dispatched_by="octocat")
        await executor._tasks_by_job[job_id]

    assert job_id == "job-1"
    assert jobs.created == {
        "repo": "acme/widgets",
        "task": "Review acme/widgets#7: Add the login form",
        "pipeline_template": "pr-review",
        "pipeline_config": REVIEW,
        "sandbox_profile": None,
        "trace_id": jobs.created["trace_id"],
        "dispatched_by": "octocat",
    }
    assert "pr_url" not in jobs.created, "a review job must not claim the pull request as its own"
    assert jobs.launch == {"runner": "pr-review"}
    run.assert_awaited_once_with("job-1")


async def test_a_relaunch_runs_the_review_again() -> None:
    executor = _executor(_Jobs())
    run = AsyncMock()
    with patch.object(executor, "_run_pr_review_workflow", run):
        await executor._relaunch("job-1", {"runner": "pr-review"})
    run.assert_awaited_once_with("job-1")


def _stream(final_state: dict[str, Any] | None, seen: dict[str, Any]) -> AsyncMock:
    async def run(initial_state: dict[str, Any], **kwargs: Any) -> tuple[dict[str, Any] | None, bool, None]:
        seen.update(initial_state=initial_state, **kwargs)
        sink = kwargs.get("state_sink")
        if sink is not None:
            sink["sandbox_container_id"] = "sandbox-1"
        return final_state, False, None

    return AsyncMock(side_effect=run)


async def test_a_published_review_completes_the_job_with_its_summary() -> None:
    jobs = _Jobs(_row())
    executor = _executor(jobs)
    seen: dict[str, Any] = {}
    final = {"pr_review": {"final_status": "completed", "verdict": {"summary": "Looks careful."}}}
    with patch.object(executor, "_execute_workflow_stream", _stream(final, seen)):
        await executor._run_pr_review_workflow("job-1")

    assert jobs.updates[0]["status"] == "running"
    assert (jobs.updates[-1]["status"], jobs.updates[-1]["result_summary"]) == ("completed", "Looks careful.")
    assert seen["pipeline"] == "pr-review"
    assert seen["initial_state"]["pr_review"] == REVIEW
    assert seen["initial_state"]["agent_models_override"] == {"pr-reviewer": "claude-opus-5"}, (
        "no override means the heavy tier"
    )
    assert seen["initial_state"]["user_env_vars"] == [{"key": "A", "value": "1", "scope": "app"}]
    executor._cleanup_sandbox.assert_awaited_once_with({"sandbox_container_id": "sandbox-1"}, "job-1")


async def test_the_repos_model_override_wins() -> None:
    executor = _executor(_Jobs(_row(model="claude-sonnet-5")))
    seen: dict[str, Any] = {}
    with patch.object(
        executor, "_execute_workflow_stream", _stream({"pr_review": {"final_status": "completed"}}, seen)
    ):
        await executor._run_pr_review_workflow("job-1")
    assert seen["initial_state"]["agent_models_override"] == {"pr-reviewer": "claude-sonnet-5"}


async def test_a_fork_gets_no_env_vars() -> None:
    executor = _executor(_Jobs(_row(from_fork=True, depth="read_only")))
    seen: dict[str, Any] = {}
    with patch.object(
        executor, "_execute_workflow_stream", _stream({"pr_review": {"final_status": "completed"}}, seen)
    ):
        await executor._run_pr_review_workflow("job-1")
    assert "user_env_vars" not in seen["initial_state"]
    executor._load_merged_env_vars.assert_not_awaited()


async def test_a_failed_review_fails_the_job_with_its_own_code() -> None:
    jobs = _Jobs(_row())
    executor = _executor(jobs)
    final = {
        "pr_review": {
            "final_status": "failed",
            "error_code": ErrorCode.PR_REVIEW_PUBLISH_FAILED.value,
            "failure_summary": "GitHub refused the review (403)",
        }
    }
    with patch.object(executor, "_execute_workflow_stream", _stream(final, {})):
        await executor._run_pr_review_workflow("job-1")
    last = jobs.updates[-1]
    assert (last["status"], last["error_code"], last["error_message"]) == (
        "failed",
        ErrorCode.PR_REVIEW_PUBLISH_FAILED.value,
        "GitHub refused the review (403)",
    )
    executor._finish_credential_uses.assert_awaited_with("job-1", error_code=ErrorCode.PR_REVIEW_PUBLISH_FAILED.value)


async def test_a_crash_fails_the_job_and_still_destroys_the_sandbox() -> None:
    jobs = _Jobs(_row())
    executor = _executor(jobs)
    with patch.object(executor, "_execute_workflow_stream", AsyncMock(side_effect=RuntimeError("graph exploded"))):
        await executor._run_pr_review_workflow("job-1")
    assert jobs.updates[-1]["status"] == "failed" and "graph exploded" in jobs.updates[-1]["error_message"]
    executor._cleanup_sandbox.assert_awaited_once()


def test_the_graph_config_carries_the_reviews_repository() -> None:
    executor = IssueExecutor(
        issues_repo=MagicMock(),
        jobs_repo=MagicMock(),
        traces_repo=MagicMock(),
        workflow_registry=MagicMock(),
        database_url="postgresql://unused/unused",
        db=MagicMock(),
    )
    assert isinstance(executor._build_graph_config("job-1")["configurable"]["pr_reviews_repo"], PrReviewsRepository)


async def test_a_crash_after_the_sandbox_exists_destroys_that_sandbox() -> None:
    """The graph raised, so there is no final state: the streamed state must carry the sandbox id."""
    jobs = _Jobs(_row())
    executor = _executor(jobs)

    async def run(initial_state: dict[str, Any], **kwargs: Any) -> None:
        kwargs["state_sink"]["sandbox_container_id"] = "sandbox-1"
        raise RuntimeError("graph exploded after init")

    with patch.object(executor, "_execute_workflow_stream", AsyncMock(side_effect=run)):
        await executor._run_pr_review_workflow("job-1")

    assert jobs.updates[-1]["status"] == "failed"
    executor._cleanup_sandbox.assert_awaited_once()
    assert executor._cleanup_sandbox.await_args.args == ({"sandbox_container_id": "sandbox-1"}, "job-1"), (
        "a crashed review leaked its sandbox: cleanup did not get the streamed state"
    )


async def test_nothing_in_the_job_config_overrides_the_reviewers_model_or_tools() -> None:
    """pipeline_config holds the pull request's own text; none of it may reach the agent's settings."""
    hostile = {
        "agent_models_override": {"pr-reviewer": "claude-haiku-4-5"},
        "agent_models": {"pr-reviewer": "claude-haiku-4-5"},
        "agent_tools": {"pr-reviewer": ["Bash", "Write"]},
        "pipeline_config": {"agent_tools": {"pr-reviewer": ["Bash", "Write"]}},
    }
    executor = _executor(_Jobs(_row(**hostile)))
    seen: dict[str, Any] = {}
    with patch.object(
        executor, "_execute_workflow_stream", _stream({"pr_review": {"final_status": "completed"}}, seen)
    ):
        await executor._run_pr_review_workflow("job-1")

    state = seen["initial_state"]
    assert state["agent_models_override"] == {"pr-reviewer": "claude-opus-5"}, state["agent_models_override"]
    assert "pipeline_config" not in state, "the agent node reads agent_tools from state.pipeline_config"
    assert not {"agent_tools", "agent_models"} & set(state), sorted(state)


async def test_an_override_that_lost_its_price_entry_falls_back_to_the_heavy_tier() -> None:
    """The PUT check ran when the override was saved; the price table may have changed since."""
    executor = _executor(_Jobs(_row(model="grok-0-unlisted")))
    seen: dict[str, Any] = {}
    with (
        patch.object(executor, "_execute_workflow_stream", _stream({"pr_review": {"final_status": "completed"}}, seen)),
        patch("embry0.services._executor_run.logger") as log,
    ):
        await executor._run_pr_review_workflow("job-1")

    assert seen["initial_state"]["agent_models_override"] == {"pr-reviewer": "claude-opus-5"}, (
        "an unpriced override must fall back to the heavy tier, not fail the review at the agent"
    )
    events = [call.args[0] for call in log.warning.call_args_list]
    assert "pr_review_model_unusable" in events, events


async def test_an_override_that_was_disabled_falls_back_to_the_heavy_tier() -> None:
    executor = _executor(_Jobs(_row(model="claude-sonnet-5")))
    seen: dict[str, Any] = {}
    policy = AsyncMock(return_value={"disabled_models": ["claude-sonnet-5"]})
    with (
        patch.object(executor, "_execute_workflow_stream", _stream({"pr_review": {"final_status": "completed"}}, seen)),
        patch("embry0.services._executor_run._model_policy_for_precheck", policy),
    ):
        await executor._run_pr_review_workflow("job-1")

    assert seen["initial_state"]["agent_models_override"] == {"pr-reviewer": "claude-opus-5"}, (
        "a disabled override must fall back to the heavy tier, not to the resolver's medium-tier default"
    )


def _ending(kind: str) -> AsyncMock:
    if kind == "crashed":
        return AsyncMock(side_effect=RuntimeError("graph exploded"))
    if kind == "interrupted":
        return AsyncMock(return_value=(None, True, None))
    status = "completed" if kind == "completed" else "failed"
    return AsyncMock(return_value=({"pr_review": {"final_status": status}}, False, None))


@pytest.mark.parametrize("kind", ["completed", "failed", "crashed", "interrupted"])
async def test_every_ending_announces_the_finished_job_after_its_terminal_write(kind: str) -> None:
    jobs = _Jobs(_row())
    executor = _executor(jobs)
    order: list[str] = []
    original_update = jobs.update

    async def update(job_id: str, **fields: Any) -> None:
        order.append(f"update:{fields.get('status')}")
        await original_update(job_id, **fields)

    jobs.update = update  # type: ignore[method-assign]
    executor._announce_finished = AsyncMock(side_effect=lambda job_id: order.append(f"announce:{job_id}"))  # type: ignore[method-assign]
    with patch.object(executor, "_execute_workflow_stream", _ending(kind)):
        await executor._run_pr_review_workflow("job-1")
    terminal = "completed" if kind == "completed" else "failed"
    assert order[-2:] == [f"update:{terminal}", "announce:job-1"], (
        f"a {kind} review must announce job_finished after its terminal write, got {order}"
    )
