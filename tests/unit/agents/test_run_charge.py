"""What a run is charged from the metering proxies' tallies and its sandbox's figure (embry0.agents.run_charge)."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.agents.invocation import ProviderRecord
from embry0.agents.run_charge import BootTally, RunMetering, charge_run


def _record(name: str = "xai", rates: tuple[float, float] | None = (2.0, 6.0)) -> ProviderRecord:
    return ProviderRecord(
        name=name,
        runtime="sdk" if name != "custom" else "chat_completions",
        upstream_model="grok-4.7",
        rates=rates,
        cache_read_factor=0.25,
        cache_write_factor=1.5,
        resolved_at="2026-11-25T00:00:00+00:00",
    )


def _usage(i: int = 0, o: int = 0, cr: int = 0, cc: int = 0, requests: int = 1, estimated: int = 0) -> dict[str, int]:
    return {
        "input_tokens": i,
        "output_tokens": o,
        "cache_read_tokens": cr,
        "cache_creation_tokens": cc,
        "cache_creation_1h_tokens": 0,
        "web_search_requests": 0,
        "web_fetch_requests": 0,
        "requests": requests,
        "estimated": estimated,
    }


def _snapshot(
    models: dict[str, dict[str, int]] | None = None, *, open_calls: list[dict[str, Any]] | None = None, errors: int = 0
) -> dict[str, Any]:
    return {"run_id": "run-1", "sandbox_id": "sbx", "models": models or {}, "errors": errors, "open": open_calls or []}


def _price(i: int, o: int, cr: int = 0, cc: int = 0) -> float:
    return (i * 2.0 + o * 6.0 + cr * 2.0 * 0.25 + cc * 2.0 * 1.5) / 1e6


def _whole(snapshot: dict[str, Any], proxy: str = "xai-proxy", boot: str = "b1") -> RunMetering:
    return RunMetering(
        run_id="run-1",
        targets=(proxy,),
        declared={proxy: boot},
        tallies=(BootTally(proxy, boot, snapshot, final=True),),
    )


SANDBOX = {"sandbox_cost_usd": 0.0101, "sandbox_tokens": (3000, 400, 400, 500)}


def test_an_xai_run_through_its_proxy_is_charged_the_proxy_s_count() -> None:
    metering = _whole(_snapshot({"grok-4.7": _usage(3000, 400, 400, 500, requests=2)}))
    charge = charge_run(metering, _record(), **SANDBOX)
    assert charge.source == "proxy"
    assert charge.cost_usd == pytest.approx(_price(3000, 400, 400, 500))
    assert (charge.input_tokens, charge.output_tokens, charge.cache_read_tokens, charge.cache_creation_tokens) == (
        3000,
        400,
        400,
        500,
    )
    assert charge.sandbox_cost_usd == pytest.approx(0.0101)
    assert charge.disagreement is None
    report = charge.metering
    assert (report["expected"], report["gap"]) == (True, False)
    assert report["proxies"]["xai-proxy"]["requests"] == 2


def test_the_proxy_wins_over_a_lower_sandbox_figure_when_its_tally_is_whole() -> None:
    """The point of metering: what the sandbox says is advisory."""
    metering = _whole(_snapshot({"grok-4.7": _usage(30_000, 4_000)}))
    charge = charge_run(metering, _record(), sandbox_cost_usd=0.001, sandbox_tokens=(10, 1, 0, 0))
    assert charge.cost_usd == pytest.approx(_price(30_000, 4_000))
    assert charge.source == "proxy"
    assert charge.disagreement == "proxy_higher"


def test_a_call_in_flight_at_the_run_s_end_is_charged_its_ceiling() -> None:
    ceiling = {
        "model": "grok-4.7",
        "since": 1.0,
        **{k: v for k, v in _usage(5000, 32000).items() if k not in ("requests", "estimated")},
    }
    metering = _whole(_snapshot({"grok-4.7": _usage(100, 10)}, open_calls=[ceiling]))
    charge = charge_run(metering, _record(), **SANDBOX)
    assert charge.cost_usd == pytest.approx(_price(5100, 32010))
    assert charge.metering["proxies"]["xai-proxy"]["open_at_ceiling"] == 1


@pytest.mark.parametrize(
    "metering",
    [
        RunMetering(
            run_id="run-1",
            targets=("xai-proxy",),
            declared={"xai-proxy": "b1"},
            tallies=(
                BootTally("xai-proxy", "b1", _snapshot({"grok-4.7": _usage(100, 10)}), final=False),
                BootTally("xai-proxy", "b2", _snapshot({"grok-4.7": _usage(100, 10)}), final=True),
            ),
        ),
        RunMetering(run_id="run-1", targets=("xai-proxy",), declared={"xai-proxy": "b1"}, unreached=("xai-proxy",)),
        RunMetering(run_id="run-1", targets=("xai-proxy",), declared={}),
        RunMetering(
            run_id="run-1",
            targets=("xai-proxy",),
            declared={"xai-proxy": "b1"},
            tallies=(BootTally("xai-proxy", "b2", _snapshot({"grok-4.7": _usage(100, 10)}), final=True),),
        ),
        RunMetering(
            run_id="run-1",
            targets=("xai-proxy",),
            declared={"xai-proxy": "b1"},
            tallies=(
                BootTally("xai-proxy", "b0", _snapshot({"grok-4.7": _usage(100, 10)}), final=False),
                BootTally("xai-proxy", "b1", _snapshot({"grok-4.7": _usage(100, 10)}), final=True),
            ),
        ),
        RunMetering(
            run_id="run-1",
            targets=("xai-proxy",),
            declared={"xai-proxy": "b1"},
            tallies=(BootTally("xai-proxy", "b1", _snapshot({"grok-4.7": _usage(100, 10)}), final=True),),
            unreached=("xai-proxy",),
        ),
    ],
    ids=[
        "the proxy process changed",
        "its end was not read",
        "never declared",
        "declared under another process",
        "another process's tally beside the final one",
        "a final tally though its end was not read",
    ],
)
def test_a_tally_that_is_not_whole_is_charged_the_larger_figure(metering: RunMetering) -> None:
    charge = charge_run(metering, _record(), **SANDBOX)
    assert charge.metering["gap"] is True
    assert charge.cost_usd == pytest.approx(max(0.0101, charge.metering["proxy_cost_usd"]))
    assert charge.source in ("sandbox_gap", "proxy_gap")
    assert charge.cost_usd >= 0.0101, "a tally that is not whole charged less than the sandbox reported"


def test_a_lost_process_s_calls_count_with_the_next_one_s() -> None:
    """Two processes' tallies are two sets of calls: summed, and the larger figure still wins on a gap."""
    first = _snapshot({"grok-4.7": _usage(50_000, 5_000)}, open_calls=[{"model": "grok-4.7", **_usage(1000, 2000)}])
    metering = RunMetering(
        run_id="run-1",
        targets=("xai-proxy",),
        declared={"xai-proxy": "b1"},
        tallies=(
            BootTally("xai-proxy", "b1", first, final=False),
            BootTally("xai-proxy", "b2", _snapshot({"grok-4.7": _usage(1000, 100)}), final=True),
        ),
    )
    charge = charge_run(metering, _record(), sandbox_cost_usd=0.01, sandbox_tokens=(1, 1, 0, 0))
    assert charge.source == "proxy_gap"
    assert charge.cost_usd == pytest.approx(_price(52_000, 7_100))
    assert charge.metering["proxies"]["xai-proxy"]["boots"] == ["b1", "b2"]


def test_a_run_its_proxies_do_not_carry_is_charged_its_sandbox_s_figure() -> None:
    """An Anthropic run, though its sandbox is enrolled with the xai-proxy: the proxy carries none of its calls."""
    anthropic = _record("anthropic")
    metering = _whole(_snapshot())
    charge = charge_run(metering, anthropic, **SANDBOX)
    assert (charge.source, charge.cost_usd, charge.metering["expected"]) == ("sandbox", 0.0101, False)
    assert charge_run(None, _record(), **SANDBOX).source == "sandbox", "a run with no meter"
    assert charge_run(metering, None, **SANDBOX).source == "sandbox", "an orchestrator that predates records"


def test_a_local_model_s_run_is_charged_its_proxy_s_count() -> None:
    metering = _whole(_snapshot({"qwen": _usage(900, 30)}), proxy="llm-proxy")
    charge = charge_run(metering, _record("custom", (0.5, 1.0)), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0))
    assert charge.source == "proxy"
    assert charge.cost_usd == pytest.approx((900 * 0.5 + 30 * 1.0) / 1e6)


def test_tallies_kept_under_the_llm_proxy_s_old_name_are_still_its_providers() -> None:
    """A run interrupted before the rename is closed from the tallies kept then, which name local-llm-proxy."""
    metering = _whole(_snapshot({"qwen": _usage(900, 30)}), proxy="local-llm-proxy")
    charge = charge_run(metering, _record("custom", (0.5, 1.0)), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0))
    assert charge.source == "proxy", f"tallies kept under the old name were not taken as the proxy's: {charge.source}"
    assert charge.cost_usd == pytest.approx((900 * 0.5 + 30 * 1.0) / 1e6)


def test_an_unpriced_record_is_charged_the_sandbox_s_figure() -> None:
    charge = charge_run(_whole(_snapshot({"grok-4.7": _usage(10, 1)})), _record(rates=None), **SANDBOX)
    assert (charge.source, charge.cost_usd) == ("sandbox_unpriced", 0.0101)


@pytest.mark.parametrize(
    ("proxy_tokens", "sandbox_cost", "expected"),
    [
        ((3000, 400), 0.0101 * 1.09, None),
        ((3000, 400), 0.0101 + 0.06, "sandbox_higher"),
        ((3000, 400), 0.0101 + 0.04, None),
        ((300_000, 40_000), 0.5, "proxy_higher"),
        ((500_000, 0), 1.08, None),
    ],
    ids=["within 10%", "over 10% and $0.05", "over 10% but under $0.05", "proxy higher", "over $0.05 but within 10%"],
)
def test_the_figures_disagree_past_10_percent_and_5_cents(
    proxy_tokens: tuple[int, int], sandbox_cost: float, expected: str | None
) -> None:
    metering = _whole(_snapshot({"grok-4.7": _usage(*proxy_tokens)}))
    charge = charge_run(metering, _record(), sandbox_cost_usd=sandbox_cost, sandbox_tokens=(0, 0, 0, 0))
    assert charge.disagreement == expected
    assert charge.metering["disagreement"] == expected


def test_a_gap_where_the_sandbox_reported_more_is_charged_the_sandbox_and_says_they_disagree() -> None:
    metering = RunMetering(
        run_id="run-1", targets=("xai-proxy",), declared={"xai-proxy": "b1"}, unreached=("xai-proxy",)
    )
    charge = charge_run(metering, _record(), sandbox_cost_usd=0.5, sandbox_tokens=(1, 2, 3, 4))
    assert (charge.source, charge.cost_usd, charge.disagreement) == ("sandbox_gap", 0.5, "sandbox_higher")
    assert (charge.input_tokens, charge.output_tokens) == (1, 2), "the sandbox's tokens go with its figure"


def test_malformed_counts_in_a_tally_count_as_nothing_not_as_an_error() -> None:
    snapshot = _snapshot({"grok-4.7": {**_usage(100, 10), "input_tokens": "lots", "output_tokens": -5}})
    snapshot["open"] = ["not a call", {"model": None, "output_tokens": True}]
    charge = charge_run(_whole(snapshot), _record(), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0))
    assert (charge.input_tokens, charge.output_tokens) == (0, 0)


def test_a_run_declared_out_of_order_is_a_gap_of_its_own_kind() -> None:
    """Its tally is complete but may miss calls an older run took: charged the larger figure, under its own source."""
    snapshot = _snapshot({"grok-4.7": _usage(3000, 400, 400, 500)})
    unordered = RunMetering(
        run_id="run-1",
        targets=("xai-proxy",),
        declared={"xai-proxy": "b1"},
        tallies=(BootTally("xai-proxy", "b1", snapshot, final=True),),
        unordered=("xai-proxy",),
    )
    charge = charge_run(unordered, _record(), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0))
    assert (charge.source, charge.metering["gap"], charge.metering["unordered"]) == (
        "proxy_unordered",
        True,
        ["xai-proxy"],
    )
    charge = charge_run(unordered, _record(), sandbox_cost_usd=1.0, sandbox_tokens=(1, 1, 0, 0))
    assert (charge.source, charge.cost_usd) == ("sandbox_unordered", 1.0)
    lost = RunMetering(
        run_id="run-1",
        targets=("xai-proxy",),
        declared={"xai-proxy": "b1"},
        unreached=("xai-proxy",),
        unordered=("xai-proxy",),
    )
    assert charge_run(lost, _record(), sandbox_cost_usd=1.0, sandbox_tokens=(1, 1, 0, 0)).source == "sandbox_gap"
