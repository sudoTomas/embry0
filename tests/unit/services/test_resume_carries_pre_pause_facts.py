"""A resumed run keeps the facts its job established before the pause (#482).

A resume replays from the checkpoint, so the state ``_process_stream`` hands
back holds only the updates of the nodes that ran AFTER the resume. For
``merge_as_is`` that is ``{"current_stage": "completed"}`` and nothing else:
the developer's ``branch_name`` was missing, so the orchestrator's PR fallback
never fired and job-92ba8fd8669a completed with its work stranded on a pushed
branch. The same gap hid ``sandbox_container_id`` from the cleanup, so the
sandbox of every resume that ran no agent outlived its job.

These tests drive a real LangGraph pause and resume through the real
``IssueExecutor.resume``; only the forge call and the audit sink are faked.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from embry0.orchestration.state import JobState
from embry0.services.issue_executor import IssueExecutor
from embry0.workflows.issue_to_pr.nodes import max_retries_node

BRANCH = "embry0/iss-1-fix-the-idle-timer"
BASE = "develop"
CONTAINER = "sandbox-job-1"
PR = "https://github.com/acme/app/pull/9"


async def _init(state: dict[str, Any]) -> dict[str, Any]:
    return {"base_branch": BASE, "sandbox_container_id": CONTAINER, "current_stage": "init_complete"}


def _developer(*, is_error: bool = False, summary: str | None = None):
    async def node(state: dict[str, Any]) -> dict[str, Any]:
        update: dict[str, Any] = {
            "branch_name": BRANCH,
            "current_stage": "developer_complete",
            "agent_outputs": [{"agent_type": "developer", "is_error": is_error, "output": "done"}],
        }
        if summary:
            update["developer_summary"] = summary
        return update

    return node


def _paused_review(*, stage: str, is_error: bool = False):
    """Review ran before the pause and left the job at ``stage``."""

    async def node(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "current_stage": stage,
            "agent_outputs": [{"agent_type": "review", "is_error": is_error, "output": "no verdict"}],
        }

    return node


def _review_asks_then(after_answer: Any):
    """Review pauses on a question; after the answer it runs ``after_answer``."""

    async def node(state: dict[str, Any]) -> dict[str, Any]:
        interrupt({"reason": "ask_user", "questions": [{"id": "q-1", "question": "Which clock?"}]})
        return await after_answer(state)

    return node


async def _approves(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_stage": "review_complete",
        "agent_outputs": [{"agent_type": "review", "is_error": False, "output": '{"decision": "approved"}'}],
    }


def _graph(last_name: str, last_node: Any, *, developer: Any = None) -> StateGraph:
    builder = StateGraph(JobState)
    builder.add_node("init", _init)
    builder.add_node("developer", developer or _developer())
    builder.add_node(last_name, last_node)
    builder.add_edge(START, "init")
    builder.add_edge("init", "developer")
    builder.add_edge("developer", last_name)
    builder.add_edge(last_name, END)
    return builder


def _max_retries_graph(
    *, stage: str = "review_unparseable", review_error: bool = False, summary: str | None = None
) -> StateGraph:
    builder = StateGraph(JobState)
    builder.add_node("init", _init)
    builder.add_node("developer", _developer(summary=summary))
    builder.add_node("review", _paused_review(stage=stage, is_error=review_error))
    builder.add_node("max_retries", max_retries_node)
    builder.add_edge(START, "init")
    builder.add_edge("init", "developer")
    builder.add_edge("developer", "review")
    builder.add_edge("review", "max_retries")
    builder.add_edge("max_retries", END)
    return builder


class _Workflow:
    def __init__(self, builder: StateGraph) -> None:
        self._builder = builder

    def compile(self, config: dict[str, Any]) -> Any:
        return self._builder.compile(checkpointer=config["checkpointer"])


def _executor(builder: StateGraph) -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._jobs.get = AsyncMock(
        return_value={"repo": "acme/app", "task": "Fix the idle timer", "issue_number": 7, "status": "completed"}
    )
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()
    ex._issues.update_parent_status = AsyncMock()
    # #518: a job that ends reads its issue before putting it back to open.
    ex._issues.get = AsyncMock(return_value={"id": "iss-1", "status": "in_progress"})
    ex._db = None
    ex._audit_log_path = None
    ex._database_url = "postgresql://unused"
    ex._webhook_notifier = None
    ex._github_token = "t"
    ex._repo_prefs = None
    ex._sandbox = MagicMock()
    ex._sandbox.destroy = AsyncMock()
    ex._sandbox.reenroll = AsyncMock()
    ex._forge_for = AsyncMock(return_value="github")  # type: ignore[method-assign]
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._post_tracker_outcome = AsyncMock()  # type: ignore[method-assign]
    ex._build_graph_config = lambda job_id, **_kw: {"configurable": {"thread_id": job_id}}  # type: ignore[method-assign]
    ex._select_workflow = lambda _pipeline: _Workflow(builder)  # type: ignore[method-assign]
    return ex


async def _pause_then_resume(builder: StateGraph, resume_value: Any) -> tuple[IssueExecutor, AsyncMock, AsyncMock]:
    """Run the graph to its pause, then resume it through ``IssueExecutor.resume``."""
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "job-1"}}
    paused = builder.compile(checkpointer=saver)
    await paused.ainvoke({"job_id": "job-1", "repo": "acme/app", "task": "Fix the idle timer"}, config)
    assert (await paused.aget_state(config)).next, "the graph must be paused before the resume"

    @asynccontextmanager
    async def shared_saver(_url: str):
        yield saver

    ex = _executor(builder)
    opened = AsyncMock(return_value=PR)
    resolver = AsyncMock(return_value="main")
    with (
        patch("embry0.services._executor_run.checkpointer_context", shared_saver),
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
        patch("embry0.services._executor_outcomes._open_change_request", new=opened),
        patch("embry0.services.base_branch.resolve_base_branch", new=resolver),
    ):
        await ex.resume("iss-1", "job-1", resume_value)
    return ex, opened, resolver


def _terminal_update(ex: IssueExecutor) -> dict[str, Any]:
    terminal = [c.kwargs for c in ex._jobs.update.await_args_list if c.kwargs.get("status") not in (None, "running")]
    assert terminal, f"the job never got a terminal status: {ex._jobs.update.await_args_list}"
    return terminal[-1]


@pytest.mark.asyncio
async def test_merge_as_is_opens_the_pr_from_the_branch_pushed_before_the_pause() -> None:
    ex, opened, resolver = await _pause_then_resume(_max_retries_graph(), {"choice": "merge_as_is", "guidance": ""})

    assert opened.await_count == 1, (
        "merge_as_is on a job with a pushed branch and no PR must open the PR (#482); "
        f"the fallback was awaited {opened.await_count} time(s)"
    )
    kwargs = opened.await_args.kwargs
    assert kwargs["branch"] == BRANCH, f"PR opened from the wrong branch: {kwargs['branch']!r}"
    assert kwargs["target_branch"] == BASE, f"PR must target the branch init cloned, got {kwargs['target_branch']!r}"
    resolver.assert_not_awaited()
    terminal = _terminal_update(ex)
    assert terminal["status"] == "completed", terminal
    assert terminal["pr_url"] == PR, f"the job row must record the PR it delivered: {terminal}"


@pytest.mark.asyncio
async def test_merge_as_is_opens_the_pr_with_the_summary_written_before_the_pause() -> None:
    """#488: the PR body is the developer's description of its change, set before the pause."""
    summary = "Stop oversized company names from wedging the Blujay spot drain."
    _, opened, _ = await _pause_then_resume(
        _max_retries_graph(summary=summary), {"choice": "merge_as_is", "guidance": ""}
    )

    assert opened.await_count == 1
    assert summary in opened.await_args.kwargs["body"], (
        f"the merge_as_is PR must carry the developer's summary:\n{opened.await_args.kwargs['body']}"
    )


@pytest.mark.asyncio
async def test_merge_as_is_destroys_the_sandbox_created_before_the_pause() -> None:
    ex, _, _ = await _pause_then_resume(_max_retries_graph(), {"choice": "merge_as_is", "guidance": ""})

    destroyed = [c.args[0] for c in ex._sandbox.destroy.await_args_list]
    assert destroyed == [CONTAINER], f"a finished resume must destroy the job's sandbox; destroyed {destroyed}"


@pytest.mark.asyncio
async def test_abandon_destroys_the_sandbox_and_opens_no_pr() -> None:
    ex, opened, _ = await _pause_then_resume(_max_retries_graph(), {"choice": "abandon", "guidance": ""})

    opened.assert_not_awaited()
    assert _terminal_update(ex)["status"] == "failed"
    destroyed = [c.args[0] for c in ex._sandbox.destroy.await_args_list]
    assert destroyed == [CONTAINER], f"an abandoned job must not leave its sandbox running; destroyed {destroyed}"


@pytest.mark.asyncio
async def test_a_review_that_approves_after_an_answer_opens_the_pr() -> None:
    """The developer does not re-run here either, so its branch must come from the checkpoint."""
    builder = _graph("review", _review_asks_then(_approves))
    ex, opened, _ = await _pause_then_resume(builder, [{"input_id": "q-1", "answer": "the game clock"}])

    assert opened.await_count == 1, (
        "a job whose review approved after an answered question must get its PR; "
        f"the fallback was awaited {opened.await_count} time(s)"
    )
    assert opened.await_args.kwargs["branch"] == BRANCH
    assert _terminal_update(ex)["pr_url"] == PR


@pytest.mark.asyncio
async def test_merge_as_is_after_an_errored_review_still_delivers() -> None:
    """Only facts are carried, never the outcome: the pre-pause errored output must not fail the job."""
    builder = _max_retries_graph(stage="review_errored", review_error=True)
    ex, opened, _ = await _pause_then_resume(builder, {"choice": "merge_as_is", "guidance": ""})

    terminal = _terminal_update(ex)
    assert terminal["status"] == "completed", (
        "a person chose merge_as_is; an agent output from before the pause must not turn that into a failure: "
        f"{terminal}"
    )
    assert opened.await_count == 1, f"the fallback was awaited {opened.await_count} time(s)"


@pytest.mark.asyncio
async def test_a_resume_that_raises_after_the_push_opens_its_pr_and_destroys_the_sandbox() -> None:
    async def raises(_state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("budget overrun")

    builder = _graph("review", _review_asks_then(raises))
    ex, opened, _ = await _pause_then_resume(builder, [{"input_id": "q-1", "answer": "the game clock"}])

    terminal = _terminal_update(ex)
    assert terminal["status"] == "failed"
    # #349: the developer pushed before the raise, so the work gets its PR.
    assert opened.await_count == 1, "a resume that raised after the push must not strand the branch"
    assert opened.await_args.kwargs["branch"] == BRANCH
    assert opened.await_args.kwargs["labels"] == ["embry0", "human-merge-only"]
    assert terminal.get("pr_url") == PR, terminal
    destroyed = [c.args[0] for c in ex._sandbox.destroy.await_args_list]
    assert destroyed == [CONTAINER], f"a resume that raised must still destroy the job's sandbox; destroyed {destroyed}"


# --- _execute_workflow_stream: what is carried, and when -----------------------------------


class _Saver:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _stream_executor(updates: dict[str, Any] | None, checkpoint: dict[str, Any]) -> tuple[IssueExecutor, MagicMock]:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._database_url = ""
    ex._sandbox = None
    ex._build_graph_config = lambda job_id, **_kw: {"configurable": {"thread_id": job_id}}  # type: ignore[method-assign]
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=MagicMock(values=dict(checkpoint), next=()))
    workflow = MagicMock()
    workflow.compile = MagicMock(return_value=graph)
    ex._select_workflow = lambda _pipeline: workflow  # type: ignore[method-assign]

    async def process_stream(*_args, **_kwargs):
        return (dict(updates) if updates is not None else None), False, None

    ex._process_stream = process_stream  # type: ignore[method-assign]
    return ex, graph


CHECKPOINT = {"branch_name": BRANCH, "base_branch": BASE, "sandbox_container_id": CONTAINER, "repo": "acme/app"}


@pytest.fixture
def _no_checkpointer(monkeypatch):
    monkeypatch.setattr("embry0.services._executor_run.checkpointer_context", lambda _url: _Saver())


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_checkpointer")
async def test_this_runs_updates_win_over_carried_facts() -> None:
    ex, _ = _stream_executor(
        {"current_stage": "completed", "branch_name": "embry0/renamed", "base_branch": ""}, CHECKPOINT
    )

    final, interrupted, _ = await ex._execute_workflow_stream(
        Command(resume={"choice": "merge_as_is"}), "iss-1", "job-1"
    )

    assert not interrupted
    assert final is not None
    assert final["branch_name"] == "embry0/renamed", f"a non-empty update must win: {final}"
    assert final["base_branch"] == BASE, f"an empty update must not erase the carried base branch: {final}"
    assert final["sandbox_container_id"] == CONTAINER, final
    assert "repo" not in final, f"only the named delivery facts are carried: {final}"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_checkpointer")
async def test_a_resume_with_no_update_still_returns_none() -> None:
    ex, _ = _stream_executor(None, CHECKPOINT)

    final, _, _ = await ex._execute_workflow_stream(Command(resume={"choice": "merge_as_is"}), "iss-1", "job-1")

    assert final is None, f"carrying facts must not invent a result for a run that produced none: {final}"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_checkpointer")
async def test_a_fresh_run_carries_nothing() -> None:
    ex, graph = _stream_executor({"current_stage": "completed"}, CHECKPOINT)
    sink: dict[str, Any] = {}

    final, _, _ = await ex._execute_workflow_stream({"job_id": "job-1"}, "iss-1", "job-1", state_sink=sink)

    assert final == {"current_stage": "completed"}, f"a fresh run has no earlier pause to carry from: {final}"
    assert sink == {}, sink
    graph.aget_state.assert_not_awaited()
