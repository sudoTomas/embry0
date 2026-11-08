"""The triage, developer and review nodes keep only the agent events they read.

Each node streams every event of its agent's run to the graph's stream
writer as it arrives, and reads a few kinds once the agent has finished:
the questions the agent asked (``agent_ask_user``, all three nodes) and
triage's QA decision (a ``set_qa_decision`` tool call). Only those are held
for the length of the node, so a run that emits a great many other events
does not hold them all; the questions and the decision still arrive.
"""

from __future__ import annotations

import weakref
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langgraph.types import Command

from embry0.workflows.issue_to_pr.nodes import developer_node, review_node, triage_node
from tests.unit.workflows.test_command_routing import _make_review_config, _state, _triage_config

NOISE_EVENTS = 100_000

ASK = {"type": "agent_ask_user", "question": "Which database should this use?", "category": "general"}
QA_DECISION = {
    "type": "tool_call",
    "tool_name": "set_qa_decision",
    "tool_id": "tool-qa",
    "tool_input": {"needs_qa": True, "reason": "A page changes", "acceptance_criteria": ["the page renders"]},
    "input": "...",
    "node": "triage",
}


class _Event(dict[str, Any]):
    """An agent event a test can hold a weak reference to, to see whether anything still keeps it."""


def _noise(i: int) -> _Event:
    kinds: list[dict[str, Any]] = [
        {"type": "progress", "message": f"step {i}"},
        {"type": "text", "text": f"thinking about file {i}"},
        {"type": "tool_call", "tool_name": "Read", "tool_input": {"path": f"src/module_{i}.py"}},
        {"type": "tool_result", "tool_name": "Read", "output": "x" * 64},
    ]
    return _Event(kinds[i % len(kinds)], node="agent")


class _ChattyRun:
    """A run_agent_node that streams NOISE_EVENTS events nobody reads, then ``tail``.

    At the end of the run, before it returns, it counts the noise events that
    are still alive: the run itself drops each one once it has been streamed,
    so any still alive is held by the node.
    """

    def __init__(self, tail: list[dict[str, Any]], output: str) -> None:
        self.tail = tail
        self.output = output
        self.noise_alive_at_end: int | None = None

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        on_event = kwargs["on_event"]
        refs = []
        for i in range(NOISE_EVENTS):
            event = _noise(i)
            refs.append(weakref.ref(event))
            on_event(event)
            del event
        for event in self.tail:
            on_event(dict(event))
        self.noise_alive_at_end = sum(ref() is not None for ref in refs)
        return {
            "agent_outputs": [
                {
                    "agent_type": kwargs.get("agent_type", "unknown"),
                    "is_error": False,
                    "output": self.output,
                    "cost_usd": 0.0,
                    "duration_ms": 10,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 0.0,
            "current_stage": f"{kwargs.get('agent_type', 'agent')}_complete",
        }


class _CountingWriter:
    """The graph's stream writer: counts the agent's events it is sent and keeps none of them."""

    def __init__(self) -> None:
        self.agent_events = 0

    def __call__(self, event: dict[str, Any]) -> None:
        if isinstance(event, _Event):
            self.agent_events += 1


async def _drive(node: Any, run: _ChattyRun, state: dict[str, Any], config: Any) -> tuple[Any, _CountingWriter]:
    writer = _CountingWriter()
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=writer),
    ):
        result = await node(state, config)
    return result, writer


def _held(run: _ChattyRun, node: str) -> None:
    assert run.noise_alive_at_end == 0, (
        f"the {node} node still held {run.noise_alive_at_end} of {NOISE_EVENTS} agent events it never reads"
    )


@pytest.mark.asyncio
async def test_developer_holds_no_unread_event_and_still_asks() -> None:
    run = _ChattyRun([ASK], output="")

    result, writer = await _drive(developer_node, run, _state(), None)

    _held(run, "developer")
    assert isinstance(result, Command) and result.goto == "ask_user_interrupt", result
    asked = [q["question"] for q in result.update["pending_agent_questions"]]
    assert asked == [ASK["question"]], f"the developer's question was lost: {asked}"
    assert writer.agent_events == NOISE_EVENTS, "every event must still stream to the writer"


@pytest.mark.asyncio
async def test_review_holds_no_unread_event_and_still_asks() -> None:
    run = _ChattyRun([ASK], output='{"decision": "approved", "reasoning": "ok"}')

    result, writer = await _drive(
        review_node, run, _state(agent_outputs=[], pr_url="http://x"), _make_review_config(object())
    )

    _held(run, "review")
    assert isinstance(result, Command) and result.goto == "ask_user_interrupt", result
    asked = [q["question"] for q in result.update["pending_agent_questions"]]
    assert asked == [ASK["question"]], f"the review's question was lost: {asked}"
    assert writer.agent_events == NOISE_EVENTS, "every event must still stream to the writer"


@pytest.mark.asyncio
async def test_triage_holds_no_unread_event_and_still_asks() -> None:
    run = _ChattyRun([ASK], output='{"action": "proceed"}')

    with patch("embry0.orchestration.nodes.triage.parse_triage_response", return_value={"action": "proceed"}):
        result, writer = await _drive(triage_node, run, _state(), _triage_config(MagicMock()))

    _held(run, "triage")
    assert isinstance(result, Command) and result.goto == "ask_user_interrupt", result
    asked = [q["question"] for q in result.update["pending_agent_questions"]]
    assert asked == [ASK["question"]], f"triage's question was lost: {asked}"
    assert writer.agent_events == NOISE_EVENTS, "every event must still stream to the writer"


@pytest.mark.asyncio
async def test_triage_holds_no_unread_event_and_still_reads_its_qa_decision() -> None:
    run = _ChattyRun([QA_DECISION], output='{"action": "proceed"}')

    with patch("embry0.orchestration.nodes.triage.parse_triage_response", return_value={"action": "proceed"}):
        result, _ = await _drive(triage_node, run, _state(), _triage_config(MagicMock()))

    _held(run, "triage")
    update = result.update if isinstance(result, Command) else result
    qa = update.get("qa") or {}
    assert (qa.get("needs_qa"), qa.get("qa_required_reason"), qa.get("acceptance_criteria")) == (
        True,
        "A page changes",
        ["the page renders"],
    ), f"triage's QA decision was lost among the other events: {qa}"


def test_the_forwarder_streams_everything_and_keeps_only_what_is_read() -> None:
    from embry0.workflows.issue_to_pr.nodes._common import _kept_event_forwarder

    streamed: list[Any] = []
    kept, forward = _kept_event_forwarder(streamed.append, lambda event: event.get("type") == "agent_ask_user")
    first, second = {**ASK, "question": "first?"}, {**ASK, "question": "second?"}

    for event in [{"type": "text", "text": "a"}, first, {"type": "progress"}, second, {"type": "text", "text": "b"}]:
        forward(event)

    assert kept == [first, second], "the kept events, in the order they came"
    assert len(streamed) == 5 and streamed[1] is first and streamed[3] is second
