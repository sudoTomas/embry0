"""A "merge as is" whose change request does not open goes back to its pause (#459).

Before, the graph ended at ``max_retries`` and the executor opened the change
request afterwards: when the forge refused it, ``_open_change_request`` logged
it and returned None, the job ended ``completed`` with no pull request and no
error, and the sandbox, the only copy of the work, was destroyed.

These tests drive a real LangGraph pause and resume through the real
``IssueExecutor.resume``, with the issue-to-pr graph's own ``max_retries``,
``deliver_as_is`` and routing between them; only the forge call is faked.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from embry0.orchestration.state import JobState
from embry0.workflows.issue_to_pr.nodes import max_retries_node
from tests.unit.services.test_resume_carries_pre_pause_facts import (
    BRANCH,
    CONTAINER,
    PR,
    _developer,
    _executor,
    _init,
    _paused_review,
)

REFUSED = "GitHub refused it: 422 Validation Failed (No commits between develop and embry0/iss-1-fix-the-idle-timer)"


def _graph() -> StateGraph:
    """The job paused at ``review_unparseable``, with the issue-to-pr graph's own pause and what follows it.

    The routing after ``max_retries`` and every node it leads to (bar ``retry``, which would run the developer
    again) are taken from the real issue-to-pr graph, so this graph is wired as production's is.
    """
    from embry0.workflows.issue_to_pr.graph import IssueToprWorkflow

    real = IssueToprWorkflow().compile().builder
    (route,) = real.branches["max_retries"].values()
    builder = StateGraph(JobState)
    builder.add_node("init", _init)
    builder.add_node("developer", _developer())
    builder.add_node("review", _paused_review(stage="review_unparseable"))
    builder.add_node("max_retries", max_retries_node)
    after: dict[str, str] = {}
    for answer, target in (route.ends or {}).items():
        if target in (END, "retry"):
            after[answer] = END
        else:
            builder.add_node(target, real.nodes[target].runnable, destinations=real.nodes[target].ends)
            after[answer] = target
    builder.add_edge(START, "init")
    builder.add_edge("init", "developer")
    builder.add_edge("developer", "review")
    builder.add_edge("review", "max_retries")
    builder.add_conditional_edges("max_retries", route.path, after)
    return builder


class _Forge:
    """The forge's answers to the change requests embry0 asks for, in turn: a URL, or a refusal and its reason."""

    def __init__(self, *answers: str | None) -> None:
        self.answers = list(answers)
        self.asked: list[dict[str, Any]] = []

    async def __call__(self, _self: Any, **kwargs: Any) -> str | None:
        self.asked.append(kwargs)
        answer = self.answers.pop(0)
        if answer is None and kwargs.get("diagnostics") is not None:
            kwargs["diagnostics"]["change_request_error"] = REFUSED
        return answer


class _Job:
    """One job paused at its pause, resumed through ``IssueExecutor.resume`` as many times as a test answers."""

    def __init__(self) -> None:
        self.saver = InMemorySaver()
        self.config = {"configurable": {"thread_id": "job-1"}}
        self.builder = _graph()
        self.ex = _executor(self.builder)
        ex = self.ex
        # The executor's own opener, handed to the graph as production's _build_graph_config does.
        deliver = getattr(ex, "_deliver_as_is", None)
        configurable = {"deliver_as_is": deliver} if deliver is not None else {}
        ex._build_graph_config = lambda job_id, **_kw: {  # type: ignore[method-assign]
            "configurable": {"thread_id": job_id, **configurable}
        }

    async def pause(self) -> None:
        graph = self.builder.compile(checkpointer=self.saver)
        await graph.ainvoke(
            {"job_id": "job-1", "issue_id": "iss-1", "repo": "acme/app", "task": "Fix the idle timer"}, self.config
        )
        assert (await graph.aget_state(self.config)).next, "the graph must be paused before the resume"

    async def answer(self, choice: str, forge: _Forge) -> None:
        saver = self.saver

        @asynccontextmanager
        async def shared_saver(_url: str):
            yield saver

        with (
            patch("embry0.services._executor_run.checkpointer_context", shared_saver),
            patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
            patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
            patch("embry0.services._executor_outcomes._open_change_request", new=forge.__call__),
            patch("embry0.services.base_branch.resolve_base_branch", new=AsyncMock(return_value="main")),
        ):
            await self.ex.resume("iss-1", "job-1", {"choice": choice, "guidance": ""})

    def statuses(self) -> list[str]:
        return [c.kwargs["status"] for c in self.ex._jobs.update.await_args_list if c.kwargs.get("status")]

    def last_pause(self) -> dict[str, Any]:
        interrupts = [
            c.args[1]
            for c in self.ex._broadcast_event.await_args_list
            if len(c.args) > 1 and isinstance(c.args[1], dict) and c.args[1].get("type") == "interrupt"
        ]
        assert interrupts, "no pause was announced"
        return dict(interrupts[-1].get("summary") or {})

    async def state(self) -> dict[str, Any]:
        graph = self.builder.compile(checkpointer=self.saver)
        return dict((await graph.aget_state(self.config)).values)


@pytest.mark.asyncio
async def test_a_refused_merge_as_is_goes_back_to_its_pause_with_the_forges_reason() -> None:
    """The #459 scenario: the forge refuses the change request; the job pauses again, never ``completed``."""
    job = _Job()
    await job.pause()
    await job.answer("merge_as_is", _Forge(None))

    assert "completed" not in job.statuses(), (
        f"a merge as is whose change request did not open ended the job completed: {job.statuses()}"
    )
    assert job.statuses()[-1] == "paused", f"the job did not go back to its pause: {job.statuses()}"
    job.ex._sandbox.destroy.assert_not_awaited()
    summary = job.last_pause()
    assert REFUSED in summary.get("message", ""), f"the pause does not say what the forge said: {summary}"
    assert summary.get("merge_as_is_failed") == REFUSED, summary
    assert summary.get("options") == ["continue_retrying", "merge_as_is", "abandon"], (
        f"the pause it came back to does not offer what it offered before: {summary.get('options')}"
    )
    state = await job.state()
    assert state.get("current_stage") == "review_unparseable" and not state.get("merged_as_is"), (
        f"the pause is not the one the job came from: {state.get('current_stage')}, {state.get('merged_as_is')}"
    )
    assert state.get("sandbox_container_id") == CONTAINER, state.get("sandbox_container_id")


@pytest.mark.asyncio
async def test_merging_as_is_again_once_the_forge_takes_it_completes_with_the_change_request() -> None:
    job = _Job()
    await job.pause()
    await job.answer("merge_as_is", _Forge(None))
    assert job.statuses()[-1] == "paused", f"the first merge as is did not pause again: {job.statuses()}"

    forge = _Forge(PR)
    await job.answer("merge_as_is", forge)
    terminal = [c.kwargs for c in job.ex._jobs.update.await_args_list if c.kwargs.get("status") == "completed"]
    assert terminal and terminal[-1].get("pr_url") == PR, (
        f"the second merge as is did not complete the job with its change request: {job.statuses()}, {terminal}"
    )
    assert len(forge.asked) == 1 and forge.asked[0]["branch"] == BRANCH, (
        f"the change request was asked for {len(forge.asked)} time(s), not once: {forge.asked}"
    )
    state = await job.state()
    assert not state.get("merge_as_is_failed"), f"the forge's earlier answer outlived the delivery: {state}"


@pytest.mark.asyncio
async def test_stopping_at_the_second_pause_abandons_the_job() -> None:
    job = _Job()
    await job.pause()
    await job.answer("merge_as_is", _Forge(None))
    assert job.statuses()[-1] == "paused", f"the first merge as is did not pause again: {job.statuses()}"

    await job.answer("abandon", _Forge())
    terminal = [c.kwargs for c in job.ex._jobs.update.await_args_list if c.kwargs.get("status") == "failed"]
    assert terminal and terminal[-1].get("error_code") == "ERR_ABANDONED", (
        f"stopping at the second pause did not abandon the job: {job.statuses()}, {terminal}"
    )
    state = await job.state()
    assert not state.get("merge_as_is_failed"), f"the forge's answer outlived the stop: {state}"


@pytest.mark.asyncio
async def test_continuing_from_the_second_pause_clears_the_forges_answer() -> None:
    """A continue runs the developer again: a later pause must not repeat the old merge failure."""
    job = _Job()
    await job.pause()
    await job.answer("merge_as_is", _Forge(None))
    await job.answer("continue_retrying", _Forge())
    state = await job.state()
    assert state.get("current_stage") == "developer_retry", (
        f"continuing from the second pause did not run the developer again: {job.statuses()}, "
        f"{state.get('current_stage')}"
    )
    assert not state.get("merge_as_is_failed"), f"the forge's answer outlived the continue: {state}"


def test_the_card_line_says_what_the_forge_said() -> None:
    """The brief card's "Now" line is the only reason line on the page (#440): it carries the forge's answer."""
    from embry0.services.job_status_words import status_words

    summary = {"reason": "max_retries", "options": ["continue_retrying", "merge_as_is", "abandon"]}
    words = status_words(
        {"status": "paused", "current_stage": "review_unparseable"},
        {"reason": "max_retries", "summary": {**summary, "merge_as_is_failed": REFUSED}},
    )
    assert words["headline"].startswith("Paused: the review gave no verdict"), words["headline"]
    assert "Merging it as it is opened no change request" in words["headline"] and "422" in words["headline"], (
        f"the card's line does not say why merging as is opened nothing: {words['headline']}"
    )


@pytest.mark.asyncio
async def test_a_change_request_opened_by_the_merge_is_not_opened_or_noted_again() -> None:
    """The delivery opened it inside the graph: the executor must not open it again, nor note on it twice."""
    from tests.unit.services.test_failed_job_pr import _executor as _result_executor
    from tests.unit.services.test_failed_job_pr import _patches

    ex = _result_executor()
    opened = AsyncMock(return_value=PR)
    noted = AsyncMock()
    record = {"branch": BRANCH, "agent": "developer", "reason": "turn cap", "committed": True}
    a, b, c, d = _patches(opened)
    with a, b, c, d, patch("embry0.services._executor_outcomes._note_on_existing_change_request", new=noted):
        await ex._handle_workflow_result(
            "iss-1",
            "job-1",
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": "completed",
                "merged_as_is": True,
                "merge_as_is_delivered": True,
                "pr_url": PR,
                "branch_name": BRANCH,
                "unfinished_work": record,
            },
        )
    opened.assert_not_awaited()
    assert noted.await_count == 0, f"the change request the merge just opened was noted again: {noted.await_args}"
    terminal = [c.kwargs for c in ex._jobs.update.await_args_list if c.kwargs.get("status")]
    assert terminal[-1]["status"] == "completed" and terminal[-1]["pr_url"] == PR, terminal


def test_the_executor_hands_the_graph_its_change_request_opener() -> None:
    """Without it the delivery node completes the job and the executor opens the change request afterwards,
    where a refusal ends the job ``completed`` with nothing delivered: production must always pass it."""
    from tests.unit.services.test_issue_executor_graph_config import _make_executor

    executor = _make_executor()
    deliver = executor._build_graph_config(job_id="job-1")["configurable"].get("deliver_as_is")
    assert deliver is not None and getattr(deliver, "__self__", None) is executor, (
        f"the graph is not given the executor's merge-as-is opener: {deliver!r}"
    )
    assert getattr(deliver, "__func__", None) is type(executor)._deliver_as_is, deliver


async def _refused_with(answer: str) -> str:
    """What ``_deliver_as_is`` makes of the forge answering ``answer``."""
    from tests.unit.services.test_failed_job_pr import _executor as _result_executor

    async def refuses(_self: Any, **kwargs: Any) -> None:
        kwargs["diagnostics"]["change_request_error"] = answer

    ex = _result_executor()
    state = {"job_id": "job-1", "issue_id": "iss-1", "repo": "acme/app", "branch_name": BRANCH, "pr_url": ""}
    with (
        patch("embry0.services._executor_outcomes._open_change_request", new=refuses),
        patch("embry0.services._executor_outcomes._base_branch_for_change_request", new=AsyncMock(return_value="main")),
    ):
        url, reason = await ex._deliver_as_is(state)
    assert url is None
    return reason


@pytest.mark.asyncio
async def test_the_forges_answer_is_redacted_and_bounded_everywhere_it_is_shown() -> None:
    """A proxy's error page can echo the request's headers back: none of it reaches the pause, the card or state."""
    from embry0.services.job_status_words import status_words

    token = "tok-000000000001"
    reason = await _refused_with(
        "GitHub refused it: 502 <html>Bad gateway. Request had Authorization: token " + token + "</html>"
    )
    assert token not in reason and "Bad gateway" in reason, f"the forge's answer was not redacted: {reason!r}"
    headline = status_words(
        {"status": "paused", "current_stage": "review_unparseable"},
        {"reason": "max_retries", "summary": {"merge_as_is_failed": reason}},
    )["headline"]
    assert token not in headline, headline
    long = await _refused_with("GitHub refused it: 502 " + "the page went on, " * 400)
    assert long.startswith("GitHub refused it: 502") and len(long) <= 300, (
        f"the forge's answer was not bounded: {len(long)} characters"
    )


@pytest.mark.asyncio
async def test_nothing_to_merge_is_not_offered_and_the_pause_says_why() -> None:
    """With neither a branch nor a change request, a merge as is could only come back with "no branch", forever."""
    import embry0.workflows.issue_to_pr.nodes as nodes_module

    summary: dict[str, Any] = {}

    def _interrupt(value: dict[str, Any]) -> dict[str, Any]:
        summary.update(value)
        return {"choice": "merge_as_is"}

    state = {"job_id": "job-1", "current_stage": "review_errored", "retry_count": 0, "pr_url": ""}
    with (
        patch.object(nodes_module, "interrupt", _interrupt),
        patch.object(nodes_module, "get_stream_writer", return_value=lambda _event: None),
    ):
        after = await max_retries_node(state, config=None)
    assert "merge_as_is" not in summary["options"], f"merge as is was offered with nothing to merge: {summary}"
    assert "no branch and no change request" in summary["message"], (
        f"the pause does not say why merge as is is not offered: {summary['message']}"
    )
    assert after.get("current_stage") == "abandoned" and not after.get("merged_as_is"), (
        f"a merge as is the pause did not offer was not read as a stop: {after}"
    )


@pytest.mark.asyncio
async def test_the_forges_answer_reads_as_written() -> None:
    """A reason that starts "GitHub" keeps its capital, and one that ends in a period does not get a second."""
    import embry0.workflows.issue_to_pr.nodes as nodes_module
    from embry0.services.job_status_words import status_words

    reason = "GitHub refused it: 422 Validation Failed."
    summary: dict[str, Any] = {}

    def _interrupt(value: dict[str, Any]) -> str:
        summary.update(value)
        return "abandon"

    state = {
        "job_id": "job-1",
        "current_stage": "review_errored",
        "retry_count": 0,
        "pr_url": "",
        "branch_name": BRANCH,
        "merge_as_is_failed": reason,
    }
    with (
        patch.object(nodes_module, "interrupt", _interrupt),
        patch.object(nodes_module, "get_stream_writer", return_value=lambda _event: None),
    ):
        await max_retries_node(state, config=None)
    assert ".." not in summary["message"] and "GitHub refused it" in summary["message"], summary["message"]
    headline = status_words(
        {"status": "paused", "current_stage": "review_errored"}, {"reason": "max_retries", "summary": summary}
    )["headline"]
    assert "GitHub refused it" in headline and "gitHub" not in headline and ".." not in headline, (
        f"the card's line does not read as the forge wrote it: {headline}"
    )
