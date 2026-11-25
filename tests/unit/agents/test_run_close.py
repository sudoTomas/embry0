"""A run that never ended is charged what is known of it, and its trace closed (embry0.agents.run_close, #694)."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.agents.invocation import ProviderRecord
from embry0.agents.run_close import close_run, metering_from_kept, sandbox_figure
from embry0.execution import events

XAI = ProviderRecord(
    name="xai",
    runtime="sdk",
    upstream_model="grok-4.7",
    rates=(2.0, 6.0),
    cache_read_factor=0.25,
    cache_write_factor=1.0,
)
UNPRICED = ProviderRecord(
    name="xai", runtime="sdk", upstream_model="grok-x", rates=None, cache_read_factor=0.25, cache_write_factor=1.0
)


def _usage(input_tokens: int, output_tokens: int, cost: float | None, *, parent: str | None = None, cache: int = 0):
    return events.turn_usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache,
        cache_creation_tokens=0,
        cost_usd=cost,
        model="grok-4.7",
        run_id="run-1",
        node="developer",
        agent="developer",
        parent_tool_use_id=parent,
    )


def test_a_call_s_usage_sent_again_with_each_content_block_counts_once() -> None:
    """The CLI sends one message's usage with each of its blocks: the input side repeats, the output grows."""
    figure = sandbox_figure(
        [_usage(1000, 5, 0.010), _usage(1000, 20, 0.012), _usage(1000, 20, 0.012), _usage(1300, 40, 0.020)], XAI
    )
    assert figure.calls == 2, f"one call was counted per content block: {figure.calls}"
    assert figure.tokens == (2300, 60, 0, 0)
    assert figure.cost_usd == pytest.approx(0.032)
    assert (figure.source, figure.unpriced_calls, figure.reported_usd) == ("turn_usage", 0, None)


def test_what_each_message_adds_is_summed_never_folded() -> None:
    """Since #676 a sandbox sends what each message adds to its response: the later blocks add no input, so equal
    zero-input events are different additions, all counted (two of them were folded into one)."""
    figure = sandbox_figure(
        [_usage(1000, 5, 0.0103), _usage(0, 10, 0.00006), _usage(0, 10, 0.00006), _usage(1300, 40, 0.0028)], XAI
    )
    assert figure.tokens == (2300, 65, 0, 0), f"what a message added was folded away: {figure.tokens}"
    assert figure.cost_usd == pytest.approx(0.0103 + 0.00012 + 0.0028)


def _result(parent: str | None = None) -> dict[str, Any]:
    return events.tool_result(
        tool_id="t",
        content="ok",
        is_error=False,
        run_id="run-1",
        node="developer",
        agent="developer",
        parent_tool_use_id=parent,
    )


ONE = (1000 * 2.0 + 1 * 6.0 + 400 * 2.0 * 0.25) / 1e6


def test_two_real_calls_with_the_same_figures_are_two_calls_on_either_image() -> None:
    """The review's case: a tool_result lies between two calls, never between one response's blocks."""
    newer = sandbox_figure([_usage(1000, 1, None, cache=400), _result(), _usage(1000, 1, None, cache=400)], XAI)
    assert (newer.calls, newer.cost_usd) == (2, pytest.approx(2 * ONE)), "two real calls were counted as one"
    older = sandbox_figure(
        [
            _usage(1000, 1, None, cache=400),
            _usage(1000, 1, None, cache=400),
            _result(),
            _usage(1000, 1, None, cache=400),
        ],
        XAI,
    )
    assert (older.calls, older.cost_usd) == (2, pytest.approx(2 * ONE)), "an older image's repeat was counted again"


def test_a_newer_image_s_added_output_is_part_of_its_call() -> None:
    """Since #676 a later block sends only what it adds: its output belongs to the call, which stays one call."""
    figure = sandbox_figure([_usage(1000, 1, None, cache=400), _usage(0, 39, None), _result()], XAI)
    assert (figure.calls, figure.tokens) == (1, (1000, 40, 400, 0)), figure
    assert figure.cost_usd == pytest.approx((1000 * 2.0 + 40 * 6.0 + 400 * 2.0 * 0.25) / 1e6)


def test_a_subagent_s_calls_are_its_own_and_do_not_end_its_parent_s() -> None:
    """The parent asks for the Task tool; the subagent's calls and results ride under it; the parent's next call
    comes after the Task's own result."""
    figure = sandbox_figure(
        [
            _usage(1000, 5, None),
            _usage(1000, 5, None),  # an older image's repeat: the Task call's block
            _usage(500, 5, None, parent="task-1"),
            _result("task-1"),
            _usage(500, 5, None, parent="task-1"),  # the subagent's second call, same figures
            _usage(1000, 5, None),  # the parent's repeat again, before the Task's result: still its first call
            _result(),
            _usage(1000, 5, None),
        ],
        XAI,
    )
    assert figure.calls == 4, f"calls: {figure.calls}"
    assert figure.tokens == (3000, 20, 0, 0)


def test_calls_under_different_parents_or_inputs_are_not_merged() -> None:
    figure = sandbox_figure(
        [_usage(1000, 5, 0.01), _usage(1000, 5, 0.01, parent="tool-1"), _usage(1000, 5, 0.01, cache=10)], XAI
    )
    assert figure.calls == 3


def test_a_call_with_no_cost_is_priced_at_the_record_s_rates_or_counted_unpriced() -> None:
    priced = sandbox_figure([_usage(1_000_000, 0, None)], XAI)
    assert (priced.cost_usd, priced.unpriced_calls) == (pytest.approx(2.0), 0)
    unpriced = sandbox_figure([_usage(1_000_000, 10, None)], UNPRICED)
    assert (unpriced.cost_usd, unpriced.unpriced_calls, unpriced.tokens[0]) == (0.0, 1, 1_000_000)
    assert sandbox_figure([_usage(10, 10, None)], None).unpriced_calls == 1


def test_the_run_s_cost_update_settles_its_figure() -> None:
    update = events.cost_update(
        cost_usd=0.5,
        duration_ms=1,
        duration_api_ms=1,
        num_turns=4,
        tokens_in=100,
        tokens_out=20,
        cache_read_tokens=3,
        cache_creation_tokens=4,
        run_id="run-1",
        node="developer",
        agent="developer",
    )
    figure = sandbox_figure([_usage(1000, 5, 0.01), update], XAI)
    assert (figure.cost_usd, figure.tokens, figure.calls, figure.reported_usd, figure.source) == (
        0.5,
        (100, 20, 3, 4),
        4,
        0.5,
        "cost_update",
    )


def test_no_usage_event_is_no_figure() -> None:
    figure = sandbox_figure([{"type": "text", "text": "hi"}], XAI)
    assert (figure.cost_usd, figure.calls, figure.source) == (0.0, 0, "none")


def _kept(proxy: str, boot: str, *, final: bool = False, open_calls: list[dict[str, Any]] | None = None):
    models = {"grok-4.7": {"input_tokens": 50_000, "output_tokens": 4_000, "requests": 3, "estimated": 0}}
    return {"proxy": proxy, "boot_id": boot, "final": final, "snapshot": {"models": models, "open": open_calls or []}}


def test_the_kept_tallies_of_a_run_are_its_metering_never_whole_without_its_end() -> None:
    assert metering_from_kept("run-1", []) is None
    metering = metering_from_kept("run-1", [_kept("xai-proxy", "b1"), _kept("xai-proxy", "b2")])
    assert metering is not None
    assert (metering.targets, metering.unreached, len(metering.tallies)) == (("xai-proxy",), ("xai-proxy",), 2)
    ended = metering_from_kept("run-1", [_kept("xai-proxy", "b1", final=True)], declared={"xai-proxy": "b1"})
    assert ended is not None and ended.unreached == ()


def test_a_metered_run_is_charged_its_kept_tallies_summed_with_calls_in_flight_at_their_ceiling() -> None:
    in_flight = [{"model": "grok-4.7", "input_tokens": 10_000, "output_tokens": 128_000}]
    metering = metering_from_kept("run-1", [_kept("xai-proxy", "b1"), _kept("xai-proxy", "b2", open_calls=in_flight)])
    closing = close_run(
        cause="restart", record=XAI, metering=metering, figure=sandbox_figure([], XAI), duration_ms=1000
    )
    expected = (2 * (50_000 * 2.0 + 4_000 * 6.0) + 10_000 * 2.0 + 128_000 * 6.0) / 1e6
    assert closing.charged_from == "proxy"
    assert closing.fields["cost_usd"] == pytest.approx(expected)
    assert closing.fields["metering"]["gap"] is True, "a run whose end was never read was taken as whole"
    assert closing.fields["metering"]["closed"]["cause"] == "restart"
    assert (closing.fields["result"], closing.fields["error_code"]) == ("error", "ERR_RUN_INTERRUPTED")
    assert closing.delta_usd == pytest.approx(expected)


def test_a_metered_run_whose_sandbox_reported_more_is_charged_that() -> None:
    metering = metering_from_kept("run-1", [_kept("xai-proxy", "b1")])
    figure = sandbox_figure([_usage(1000, 5, 5.0)], XAI)
    closing = close_run(cause="cancelled", record=XAI, metering=metering, figure=figure, duration_ms=1)
    assert (closing.charged_from, closing.fields["cost_usd"]) == ("sandbox", 5.0)


def test_an_unmetered_run_is_charged_its_sandbox_s_last_figure() -> None:
    figure = sandbox_figure([_usage(1000, 5, 0.25)], XAI)
    closing = close_run(cause="cancelled", record=XAI, metering=None, figure=figure, duration_ms=1)
    assert (closing.charged_from, closing.fields["cost_usd"], closing.fields["input_tokens"]) == ("sandbox", 0.25, 1000)
    assert "turn_usage" in closing.fields["metering"]["closed"]["reason"]


def test_a_run_with_nothing_known_is_closed_at_zero_and_says_so() -> None:
    closing = close_run(cause="restart", record=None, metering=None, figure=sandbox_figure([], None), duration_ms=-5)
    closed = closing.fields["metering"]["closed"]
    assert (closing.charged_from, closing.fields["cost_usd"], closing.delta_usd) == ("nothing", 0.0, 0.0)
    assert closed["charged_from"] == "nothing" and "not known" in closed["reason"]
    assert closing.fields["duration_ms"] == 0
    assert "not known" in closing.fields["result_summary"]


def test_the_close_adds_only_what_the_run_s_cost_update_did_not() -> None:
    update = events.cost_update(
        cost_usd=0.5,
        duration_ms=1,
        duration_api_ms=1,
        num_turns=1,
        tokens_in=1,
        tokens_out=1,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        run_id="run-1",
        node="developer",
        agent="developer",
    )
    metering = metering_from_kept("run-1", [_kept("xai-proxy", "b1")])
    proxy = (50_000 * 2.0 + 4_000 * 6.0) / 1e6
    closing = close_run(
        cause="restart", record=XAI, metering=metering, figure=sandbox_figure([update], XAI), duration_ms=1
    )
    assert closing.fields["cost_usd"] == pytest.approx(0.5)  # the sandbox's own total is the larger
    assert closing.delta_usd == 0.0, "the run's cost_update had added it already"
    cheap = sandbox_figure([{**update, "cost_usd": 0.01}], XAI)
    closing = close_run(cause="restart", record=XAI, metering=metering, figure=cheap, duration_ms=1)
    assert closing.delta_usd == pytest.approx(proxy - 0.01)


def test_the_close_s_cost_update_carries_what_it_adds() -> None:
    closing = close_run(
        cause="cancelled", record=XAI, metering=None, figure=sandbox_figure([_usage(1000, 5, 0.25)], XAI), duration_ms=7
    )
    update = closing.cost_update(run_id="run-1", node="developer", agent="developer")
    required = set(events.contract()["cost_update"]["required"])
    assert required <= set(update), f"the close's cost_update lacks {required - set(update)}"
    assert (update["type"], update["cost_usd"], update["run_id"], update["num_turns"]) == (
        "cost_update",
        0.25,
        "run-1",
        1,
    )


def test_a_record_prices_a_run_again_from_its_trace_s_pricing() -> None:
    again = ProviderRecord.from_trace_pricing(XAI.trace_pricing())
    assert again is not None
    assert again.price(input_tokens=1000, output_tokens=100, cache_read_tokens=10) == XAI.price(
        input_tokens=1000, output_tokens=100, cache_read_tokens=10
    )
    unpriced = ProviderRecord.from_trace_pricing(UNPRICED.trace_pricing())
    assert unpriced is not None and unpriced.rates is None
    for kept in (None, {}, {"provider": ""}, {**XAI.trace_pricing(), "rates": {"in": -1, "out": 1}}, "x"):
        assert ProviderRecord.from_trace_pricing(kept) is None, kept
    assert ProviderRecord.from_trace_pricing({**XAI.trace_pricing(), "cache": None}) is None
