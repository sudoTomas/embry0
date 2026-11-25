"""An Agent SDK run's live figures count each model response once (#676).

The CLI sends each content block of a response as its own assistant message, under the response's id and with the
usage the response started with. The executor counts one turn per response and sends each response's usage once, so
the job page's running totals (``frontend/src/lib/eventTotals.ts``: the sum of a run's ``turn_usage`` events, and its
highest top-level ``turn_start``) agree with the run's ``cost_update`` before it arrives: its turns, its input and its
cache tokens. The stream gives no response's final output count, so output stays a lower bound until then.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit.agents.test_provider_record import _grok, _grok_price, _recorded, _result_of, _sdk_run


def _live(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The page's running totals of a run, as ``eventTotals.applyTotals`` adds them up before its ``cost_update``."""
    usage = [e for e in events if e.get("type") == "turn_usage"]
    turns = [e["turn_number"] for e in events if e.get("type") == "turn_start" and not e.get("parent_tool_use_id")]
    return {
        "turns": max(turns, default=0),
        "input": sum(e["input_tokens"] for e in usage),
        "output": sum(e["output_tokens"] for e in usage),
        "cache_read": sum(e["cache_read_tokens"] for e in usage),
        "cache_creation": sum(e["cache_creation_tokens"] for e in usage),
        "cost": sum(e["cost_usd"] or 0 for e in usage),
    }


async def test_a_recorded_multi_block_run_s_live_figures_match_its_cost_update(tmp_path, monkeypatch) -> None:
    """Two calls, the first sent as two messages (text, then tool_use): two turns, each call's input once."""
    out, events, _ = await _sdk_run(_grok(), _recorded("grok-two-calls.jsonl"), tmp_path, monkeypatch)
    assert not out.is_error, out.error_message
    (cost,) = [e for e in events if e.get("type") == "cost_update"]
    live = _live(events[: events.index(cost)])
    assert live["turns"] == cost["num_turns"] == _result_of("grok-two-calls.jsonl")["num_turns"] == 2, live
    assert (live["input"], live["cache_read"], live["cache_creation"]) == (
        cost["tokens_in"],
        cost["cache_read_tokens"],
        cost["cache_creation_tokens"],
    ), f"each call's input once: {live}, the result says {cost}"
    assert live["output"] == 2 <= cost["tokens_out"], "one starting output token a call: a lower bound until the result"
    assert live["cost"] == pytest.approx(_grok_price(1000, 1, 400, 0) + _grok_price(2000, 1, 0, 500))
    assert [e["turn_number"] for e in events if e.get("type") == "turn_start"] == [1, 2]
    assert len([e for e in events if e.get("type") == "turn_usage"]) == 2, "the second block of a call adds nothing"


def _block(message_id: str, usage: dict[str, int], text: str = "x", parent: str | None = None) -> Any:
    """An assistant message as the CLI sends one content block of a response."""
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        model="grok-4.7",
        message_id=message_id,
        usage=usage,
        parent_tool_use_id=parent,
        session_id="s",
    )


async def test_a_block_that_reports_more_of_its_response_s_usage_sends_what_it_adds(tmp_path, monkeypatch) -> None:
    """Should a later block report more of the response (its output so far), the events still sum to the response's
    usage: each sends what it adds, and one that adds nothing sends none."""
    blocks = [
        _block("m1", {"input_tokens": 1000, "output_tokens": 1}),
        _block("m1", {"input_tokens": 1000, "output_tokens": 40}),
        _block("m1", {"input_tokens": 1000, "output_tokens": 40}),
        _block("m2", {"input_tokens": 1500, "output_tokens": 1, "cache_read_input_tokens": 1000}),
    ]
    out, events, _ = await _sdk_run(_grok(), blocks, tmp_path, monkeypatch)
    sent = [
        (e["input_tokens"], e["output_tokens"], e["cache_read_tokens"]) for e in events if e["type"] == "turn_usage"
    ]
    assert sent == [(1000, 1, 0), (0, 39, 0), (1500, 1, 1000)], sent
    assert _live(events)["turns"] == 2 and _live(events)["output"] == 41


async def test_messages_without_a_response_id_are_each_a_response(tmp_path, monkeypatch) -> None:
    blocks = [
        _block("", {"input_tokens": 10, "output_tokens": 1}),
        _block("", {"input_tokens": 10, "output_tokens": 1}),
    ]
    _out, events, _ = await _sdk_run(_grok(), blocks, tmp_path, monkeypatch)
    assert (_live(events)["turns"], _live(events)["input"]) == (2, 20)


async def test_a_subagent_s_response_is_no_turn_and_its_usage_is_sent_once(tmp_path, monkeypatch) -> None:
    blocks = [
        _block("m1", {"input_tokens": 100, "output_tokens": 1}),
        _block("s1", {"input_tokens": 50, "output_tokens": 1}, parent="toolu_task"),
        _block("s1", {"input_tokens": 50, "output_tokens": 1}, parent="toolu_task"),
    ]
    _out, events, _ = await _sdk_run(_grok(), blocks, tmp_path, monkeypatch)
    assert (_live(events)["turns"], _live(events)["input"]) == (1, 150)
