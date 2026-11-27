"""The orchestrator's meter: runs declared and ended on the metering proxies, their tallies read and kept
(embry0.execution.run_meter)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from prometheus_client import CollectorRegistry

from embry0.execution.run_meter import RunMeter
from embry0.metrics.loop import LoopMetrics, sample
from embry0.storage.repositories.run_meter import tally_key


class _Repo:
    """run_meter in memory, with the repository's rules: a final row is never replaced by a read."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.pruned: list[timedelta] = []
        self.fail_reads = False

    async def record(self, proxy: str, boot_id: str, tallies: Any, *, final: bool = False) -> int:
        for t in tallies:
            key = (proxy, boot_id, tally_key(t))
            held = self.rows.get(key)
            if held is not None and held["final"]:
                continue
            self.rows[key] = {
                "proxy": proxy,
                "boot_id": boot_id,
                "snapshot": dict(t),
                "final": final,
                "run_id": t.get("run_id"),
            }
        return len(list(tallies))

    async def for_run(self, run_id: str) -> list[dict[str, Any]]:
        if self.fail_reads:
            raise RuntimeError("database away")
        return [r for r in self.rows.values() if r["run_id"] == run_id]

    async def prune(self, older_than: timedelta) -> int:
        self.pruned.append(older_than)
        return 0


class _Proxies:
    """ProxyManager's metering surface, over in-memory proxies: name -> boot id and tallies by run."""

    def __init__(self, enrolled: dict[str, list[str]]) -> None:
        self.enrolled = enrolled  # sandbox -> metering proxies it is enrolled with
        self.boot = {"xai-proxy": "b1", "llm-proxy": "l1"}
        self.tallies: dict[str, dict[str, dict[str, Any]]] = {p: {} for p in self.boot}
        self.fail: dict[tuple[str, str], Exception] = {}
        self.calls: list[tuple[str, str]] = []

    def metered_proxies(self) -> list[str]:
        return list(self.boot)

    def metered_proxies_for(self, sandbox_id: str) -> list[str]:
        return list(self.enrolled.get(sandbox_id, []))

    def _maybe_fail(self, proxy: str, call: str) -> None:
        self.calls.append((proxy, call))
        if (proxy, call) in self.fail:
            raise self.fail[(proxy, call)]

    async def declare_run(
        self, proxy: str, sandbox_id: str, run_id: str, job_id: str | None = None, provider: str | None = None
    ) -> str:
        self._maybe_fail(proxy, "declare")
        self.declared_providers = {**getattr(self, "declared_providers", {}), (proxy, run_id): provider}
        self.tallies[proxy][run_id] = {
            "run_id": run_id,
            "sandbox_id": sandbox_id,
            "job_id": job_id,
            "models": {},
            "errors": 0,
            "open": [],
        }
        return self.boot[proxy]

    async def end_run(self, proxy: str, run_id: str, *, seq: int | None = None) -> dict[str, Any] | None:
        self.end_seqs = {**getattr(self, "end_seqs", {}), proxy: seq}
        self._maybe_fail(proxy, "end")
        tally = self.tallies[proxy].pop(run_id, None)
        return None if tally is None else {"boot_id": self.boot[proxy], "tally": tally}

    def declared_unordered(self, proxy: str, run_id: str) -> bool:
        return (proxy, run_id) in getattr(self, "unordered", set())

    def replay_abandoned(self, proxy: str) -> bool:
        return proxy in getattr(self, "abandoned", set())

    async def read_meter(self, proxy: str) -> dict[str, Any]:
        self._maybe_fail(proxy, "read")
        stale = getattr(self, "stale", {}).get(proxy, 0)
        return {"boot_id": self.boot[proxy], "tallies": list(self.tallies[proxy].values()), "stale_refused": stale}


def _meter(proxies: _Proxies, repo: _Repo | None = None) -> tuple[RunMeter, CollectorRegistry]:
    registry = CollectorRegistry()
    return RunMeter(proxies, repo, metrics=LoopMetrics(registry), poll_seconds=0.01), registry


def _calls(n: int = 1, output: int = 10) -> dict[str, Any]:
    return {"grok-4.7": {"input_tokens": 100 * n, "output_tokens": output, "requests": n, "estimated": 0}}


async def test_a_run_is_declared_where_its_sandbox_is_enrolled_and_ended_there() -> None:
    proxies = _Proxies({"sbx": ["xai-proxy"]})
    repo = _Repo()
    meter, _ = _meter(proxies, repo)
    window = await meter.begin("sbx", "run-1", "job-1")
    assert (window.targets, window.declared) == (("xai-proxy",), {"xai-proxy": "b1"})
    proxies.tallies["xai-proxy"]["run-1"]["models"] = _calls(2)
    metering = await meter.end(window)
    assert [(t.proxy, t.boot_id, t.final) for t in metering.tallies] == [("xai-proxy", "b1", True)]
    assert metering.tallies[0].snapshot["models"] == _calls(2)
    assert repo.rows[("xai-proxy", "b1", "run-1")]["final"] is True, "the final tally was not kept"
    assert ("llm-proxy", "declare") not in proxies.calls, "declared where the sandbox is not enrolled"


async def test_a_run_is_declared_on_its_provider_at_every_proxy_it_is_enrolled_with() -> None:
    proxies = _Proxies({"sbx": ["xai-proxy", "llm-proxy"]})
    meter, _ = _meter(proxies, _Repo())
    try:
        await meter.begin("sbx", "run-1", "job-1", provider="openai")  # type: ignore[call-arg]
    except TypeError as exc:
        pytest.fail(f"a run cannot be declared on its provider: {exc}")
    assert getattr(proxies, "declared_providers", {}) == {
        ("xai-proxy", "run-1"): "openai",
        ("llm-proxy", "run-1"): "openai",
    }, "the run was not declared on its provider"


async def test_a_declare_that_fails_leaves_the_proxy_out_and_counts() -> None:
    proxies = _Proxies({"sbx": ["xai-proxy", "llm-proxy"]})
    proxies.fail[("xai-proxy", "declare")] = RuntimeError("proxy away")
    meter, registry = _meter(proxies, _Repo())
    window = await meter.begin("sbx", "run-1")
    assert window.targets == ("xai-proxy", "llm-proxy")
    assert window.declared == {"llm-proxy": "l1"}
    labels = {"proxy": "xai-proxy", "call": "declare"}
    assert sample(registry, "embry0_meter_admin_failures_total", labels) == 1


async def test_an_end_that_fails_or_finds_no_run_leaves_the_proxy_unreached() -> None:
    proxies = _Proxies({"sbx": ["xai-proxy", "llm-proxy"]})
    meter, registry = _meter(proxies, _Repo())
    window = await meter.begin("sbx", "run-1")
    proxies.fail[("xai-proxy", "end")] = RuntimeError("proxy away")
    proxies.tallies["llm-proxy"].clear()  # recreated, and not given the run back
    metering = await meter.end(window)
    assert sorted(metering.unreached) == ["llm-proxy", "xai-proxy"]
    assert sample(registry, "embry0_meter_admin_failures_total", {"proxy": "xai-proxy", "call": "end"}) == 1


async def test_a_run_s_earlier_process_read_before_it_went_counts_at_the_end() -> None:
    """The proxy process changed mid-run: the earlier one's last read is kept, and gathered with the final one."""
    proxies = _Proxies({"sbx": ["xai-proxy"]})
    repo = _Repo()
    meter, _ = _meter(proxies, repo)
    window = await meter.begin("sbx", "run-1")
    proxies.tallies["xai-proxy"]["run-1"]["models"] = _calls(5)
    await meter.poll_once()
    proxies.boot["xai-proxy"] = "b2"  # recreated; the manager gave it the run back
    proxies.tallies["xai-proxy"]["run-1"]["models"] = _calls(1)
    metering = await meter.end(window)
    by_boot = {t.boot_id: (t.final, t.snapshot["models"]) for t in metering.tallies}
    assert by_boot == {"b1": (False, _calls(5)), "b2": (True, _calls(1))}


async def test_what_this_process_ended_counts_though_the_kept_rows_cannot_be_read() -> None:
    proxies = _Proxies({"sbx": ["xai-proxy"]})
    repo = _Repo()
    meter, _ = _meter(proxies, repo)
    window = await meter.begin("sbx", "run-1")
    proxies.tallies["xai-proxy"]["run-1"]["models"] = _calls(3)
    repo.fail_reads = True
    metering = await meter.end(window)
    assert [(t.boot_id, t.final) for t in metering.tallies] == [("b1", True)]


async def test_every_proxy_is_read_and_kept_and_a_failed_read_does_not_stop_the_others() -> None:
    proxies = _Proxies({"sbx": ["xai-proxy", "llm-proxy"]})
    repo = _Repo()
    meter, registry = _meter(proxies, repo)
    meter.start()
    await meter.stop()  # seeds, then a last read
    proxies.tallies["llm-proxy"]["unattributed"] = {
        "run_id": None,
        "sandbox_id": "sbx",
        "started": 7.0,
        "models": _calls(4),
        "errors": 0,
        "open": [],
    }
    proxies.fail[("xai-proxy", "read")] = RuntimeError("proxy away")
    await meter.poll_once()
    assert ("llm-proxy", "l1", "unattributed:sbx:7.0") in repo.rows
    assert sample(registry, "embry0_meter_unattributed_requests", {"proxy": "llm-proxy"}) == 4
    assert (sample(registry, "embry0_meter_last_read_timestamp_seconds", {"proxy": "llm-proxy"}) or 0) > 0
    assert sample(registry, "embry0_meter_admin_failures_total", {"proxy": "xai-proxy", "call": "read"}) == 1


async def test_the_meter_s_series_are_seeded_when_it_starts() -> None:
    meter, registry = _meter(_Proxies({}))
    meter.start()
    try:
        for proxy in ("xai-proxy", "llm-proxy"):
            assert sample(registry, "embry0_meter_last_read_timestamp_seconds", {"proxy": proxy}) is not None
            assert sample(registry, "embry0_meter_unattributed_requests", {"proxy": proxy}) == 0
            assert sample(registry, "embry0_meter_replay_abandoned", {"proxy": proxy}) == 0
            for call in ("declare", "end", "read"):
                assert sample(registry, "embry0_meter_admin_failures_total", {"proxy": proxy, "call": call}) == 0
    finally:
        await meter.stop()


async def test_the_reads_go_on_until_stop_which_reads_once_more() -> None:
    proxies = _Proxies({})
    meter, _ = _meter(proxies, _Repo())
    meter.start()
    for _ in range(50):
        if sum(1 for _, call in proxies.calls if call == "read") >= 4:
            break
        await asyncio.sleep(0.01)
    await meter.stop()
    reads = sum(1 for _, call in proxies.calls if call == "read")
    assert reads >= 4, "the meter did not keep reading"
    await asyncio.sleep(0.05)
    assert sum(1 for _, call in proxies.calls if call == "read") == reads, "it read on after stop"


async def test_old_tallies_are_pruned_at_most_once_an_hour() -> None:
    clock = [0.0]
    repo = _Repo()
    meter = RunMeter(_Proxies({}), repo, clock=lambda: clock[0])
    await meter.poll_once()
    await meter.poll_once()
    clock[0] = 3601.0
    await meter.poll_once()
    assert repo.pruned == [timedelta(days=30), timedelta(days=30)]


def test_a_charge_is_counted_by_its_source_and_a_disagreement_dated() -> None:
    from embry0.agents.run_charge import Charge

    registry = CollectorRegistry()
    meter = RunMeter(_Proxies({}), None, metrics=LoopMetrics(registry))
    assert (
        sample(registry, "embry0_run_meter_last_disagreement_timestamp_seconds", {"direction": "sandbox_higher"}) == 0
    )
    charge = Charge(
        0.5, 1, 2, 3, 4, sandbox_cost_usd=0.5, metering={"source": "sandbox_gap"}, disagreement="sandbox_higher"
    )
    meter.charged(charge, run_id="run-1")
    assert sample(registry, "embry0_run_meter_charges_total", {"source": "sandbox_gap"}) == 1
    assert sample(registry, "embry0_run_meter_disagreements_total", {"direction": "sandbox_higher"}) == 1
    assert (
        sample(registry, "embry0_run_meter_last_disagreement_timestamp_seconds", {"direction": "sandbox_higher"}) or 0
    ) > 0


async def test_stop_reads_every_proxy_once_more_before_the_proxies_go() -> None:
    """The shutdown's last read: what each proxy counted since the last 5-second read is kept."""
    proxies = _Proxies({"sbx": ["xai-proxy"]})
    repo = _Repo()
    meter, _ = _meter(proxies, repo)
    meter._poll_seconds = 3600  # the loop reads once at most
    window = await meter.begin("sbx", "run-1")
    meter.start()
    await meter.stop()
    reads_before = [c for c in proxies.calls if c[1] == "read"]
    proxies.tallies["xai-proxy"][window.run_id]["models"] = _calls(9)
    meter.start()
    await meter.stop()
    assert len([c for c in proxies.calls if c[1] == "read"]) > len(reads_before)
    assert repo.rows[("xai-proxy", "b1", "run-1")]["snapshot"]["models"] == _calls(9), "stop did not read again"


async def test_the_replay_mode_is_a_gauge_seeded_0() -> None:
    proxies = _Proxies({})
    meter, registry = _meter(proxies, _Repo())
    meter.start()
    await meter.stop()
    assert sample(registry, "embry0_meter_replay_abandoned", {"proxy": "xai-proxy"}) == 0
    proxies.abandoned = {"xai-proxy"}  # type: ignore[attr-defined]
    await meter.poll_once()
    assert sample(registry, "embry0_meter_replay_abandoned", {"proxy": "xai-proxy"}) == 1
    assert sample(registry, "embry0_meter_replay_abandoned", {"proxy": "llm-proxy"}) == 0
    proxies.abandoned = set()  # type: ignore[attr-defined]
    await meter.poll_once()
    assert sample(registry, "embry0_meter_replay_abandoned", {"proxy": "xai-proxy"}) == 0


async def test_the_stale_requests_a_proxy_refused_are_a_gauge_seeded_0() -> None:
    proxies = _Proxies({})
    meter, registry = _meter(proxies, _Repo())
    meter._metrics.meter_proxies(proxies.metered_proxies())
    assert sample(registry, "embry0_proxy_stale_admin_requests", {"proxy": "xai-proxy"}) == 0
    proxies.stale = {"xai-proxy": 3}  # type: ignore[attr-defined]
    await meter.poll_once()
    assert sample(registry, "embry0_proxy_stale_admin_requests", {"proxy": "xai-proxy"}) == 3


async def test_a_run_s_ends_are_numbered_when_it_ends_on_every_proxy() -> None:
    """A run declared on two proxies is ended on one, then the other. The sandbox's next run, declared meanwhile, is
    numbered after both ends: the second proxy's end is not numbered only when its turn comes."""
    from embry0.execution.admin_seq import next_admin_seq

    proxies = _Proxies({"sbx-1": ["xai-proxy", "llm-proxy"]})
    meter, _registry = _meter(proxies, _Repo())
    window = await meter.begin("sbx-1", "run-A", "job-1")
    real_end = proxies.end_run
    meanwhile: list[int] = []

    async def _end_run(proxy: str, run_id: str, *, seq: int | None = None) -> dict[str, Any] | None:
        if not meanwhile:
            meanwhile.append(next_admin_seq())  # the next run's declaration, numbered while this end goes out
        return await real_end(proxy, run_id, seq=seq)

    proxies.end_run = _end_run  # type: ignore[method-assign]
    await meter.end(window)
    ends = getattr(proxies, "end_seqs", {})
    assert set(ends) == {"xai-proxy", "llm-proxy"}, f"ends sent unnumbered: {ends}"
    assert all(isinstance(n, int) and n < meanwhile[0] for n in ends.values()), (
        f"an end was numbered after the next run's declaration: {ends} vs {meanwhile}"
    )


def _unattributed(sandbox_id: str, models: dict[str, Any]) -> dict[str, Any]:
    """A proxy's tally of a sandbox's calls under no run."""
    return {"run_id": None, "sandbox_id": sandbox_id, "started": 1.0, "models": models, "errors": 0, "open": []}


def _record(name: str = "grok-4.7") -> Any:
    from embry0.agents.invocation import ProviderRecord

    return ProviderRecord(
        name="openai",
        runtime="chat_completions",
        upstream_model=name,
        rates=(2.0, 6.0),
        cache_read_factor=0.25,
        cache_write_factor=1.5,
    )


async def test_a_run_not_declared_is_charged_what_the_proxy_counted_under_no_run_during_it() -> None:
    """Its calls went through the proxy under the sandbox's calls under no run: what those grew by during the run is
    the proxy's count of it, and the run is charged the larger of that and its sandbox's figure."""
    from embry0.agents.run_charge import charge_run

    proxies = _Proxies({"sbx": ["llm-proxy"]})
    proxies.fail[("llm-proxy", "declare")] = RuntimeError("the proxy refused the declaration")
    proxies.tallies["llm-proxy"]["unattributed:sbx:1.0"] = {**_unattributed("sbx", _calls(1)), "errors": 1}
    proxies.tallies["llm-proxy"]["unattributed:other:1.0"] = _unattributed("other", _calls(9))  # another sandbox
    meter, _ = _meter(proxies, _Repo())
    window = await meter.begin("sbx", "run-1", "job-1", provider="openai")
    assert window.declared == {}, window.declared
    in_flight = {"model": "grok-4.7", "since": 2.0, "input_tokens": 50, "output_tokens": 500}
    proxies.tallies["llm-proxy"]["unattributed:sbx:1.0"].update(
        models=_calls(4, output=40), errors=3, open=[in_flight]
    )  # three calls answered in the run, two errors, one in flight

    metering = await meter.end(window)

    counted = [t.snapshot["models"] for t in metering.tallies if t.proxy == "llm-proxy"]
    assert counted == [{"grok-4.7": {"input_tokens": 300, "output_tokens": 30, "requests": 3, "estimated": 0}}], (
        f"the run was not given what the proxy counted under no run during it: {counted}"
    )
    (grown,) = [t.snapshot for t in metering.tallies if t.proxy == "llm-proxy"]
    assert (grown["errors"], grown["open"]) == (2, [in_flight]), "the run's errors or its call in flight were lost"
    charge = charge_run(metering, _record(), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0))
    assert charge.source == "proxy_gap", f"an undeclared run ignored the proxy's count: {charge.source}"
    assert charge.cost_usd == ((300 + 50) * 2.0 + (30 + 500) * 6.0) / 1e6, "not the calls answered and in flight"
    higher = charge_run(metering, _record(), sandbox_cost_usd=1.0, sandbox_tokens=(1, 1, 0, 0))
    assert higher.source == "sandbox_gap", "the larger figure was not charged"


async def test_a_run_not_declared_whose_start_was_not_read_is_charged_every_call_under_no_run() -> None:
    """Where the sandbox's calls under no run stood at the run's start is not known: all of them count (more, never
    less)."""
    proxies = _Proxies({"sbx": ["llm-proxy"]})
    proxies.fail[("llm-proxy", "declare")] = RuntimeError("unreachable")
    proxies.fail[("llm-proxy", "read")] = RuntimeError("unreachable")
    meter, _ = _meter(proxies, _Repo())
    window = await meter.begin("sbx", "run-1", "job-1")
    del proxies.fail[("llm-proxy", "read")]
    proxies.tallies["llm-proxy"]["unattributed:sbx:1.0"] = _unattributed("sbx", _calls(2))
    metering = await meter.end(window)
    counted = [t.snapshot["models"]["grok-4.7"]["requests"] for t in metering.tallies if t.proxy == "llm-proxy"]
    assert counted == [2], f"the sandbox's calls under no run were not all counted: {counted}"


async def test_a_declared_run_is_not_given_the_sandbox_s_calls_under_no_run() -> None:
    """Its calls count under it: the sandbox's calls under no run (before it, or outside any run) are not its."""
    proxies = _Proxies({"sbx": ["llm-proxy"]})
    proxies.tallies["llm-proxy"]["unattributed:sbx:1.0"] = _unattributed("sbx", _calls(5))
    meter, _ = _meter(proxies, _Repo())
    window = await meter.begin("sbx", "run-1", "job-1", provider="openai")
    assert set(window.declared) == {"llm-proxy"}
    proxies.tallies["llm-proxy"]["run-1"]["models"] = _calls(2)
    metering = await meter.end(window)
    counted = [t.snapshot["models"]["grok-4.7"]["requests"] for t in metering.tallies]
    assert counted == [2], f"a declared run was given more than its own calls: {counted}"
