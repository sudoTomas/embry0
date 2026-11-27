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
        self,
        proxy: str,
        sandbox_id: str,
        run_id: str,
        job_id: str | None = None,
        provider: str | None = None,
        budget: dict[str, Any] | None = None,
    ) -> str:
        self._maybe_fail(proxy, "declare")
        self.declared_providers = {**getattr(self, "declared_providers", {}), (proxy, run_id): provider}
        self.declared_budgets = {**getattr(self, "declared_budgets", {}), (proxy, run_id): budget}
        self.tallies[proxy][run_id] = {
            "run_id": run_id,
            "sandbox_id": sandbox_id,
            "job_id": job_id,
            "models": {},
            "errors": 0,
            "open": [],
        }
        return self.boot[proxy]

    async def update_purses(self, proxy: str, purses: list[dict[str, Any]]) -> None:
        self._maybe_fail(proxy, "purses")
        self.purse_updates = [*getattr(self, "purse_updates", []), (proxy, purses)]

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


def test_a_sandbox_is_metered_where_it_is_enrolled_with_a_metering_proxy() -> None:
    """Only such a run's budget is read: the day's and month's spend is a rollup of every job's."""
    meter, _ = _meter(_Proxies({"sbx": ["xai-proxy"], "none": []}), _Repo())
    meters = getattr(meter, "meters", None)
    assert meters is not None, "the meter cannot say which sandboxes it meters"
    assert (meters("sbx"), meters("none")) == (True, False)


def _request(job: float = 2.0, daily: float | None = None, job_key: str = "job:job-1") -> dict[str, Any]:
    """A run's budget request (embry0.orchestration.run_budget): its rates, and what each scope has left settled."""
    scopes = [{"key": job_key, "limit": "job", "remaining_usd": job}]
    if daily is not None:
        scopes.append({"key": "daily", "limit": "daily", "remaining_usd": daily})
    return {"rates": {"in": 2.0, "out": 6.0}, "cache": {"read": 0.25, "write": 1.0}, "scopes": scopes}


async def _begin(meter: RunMeter, run_id: str, budget: Any, sandbox: str = "sbx", provider: str = "xai") -> Any:
    try:
        return await meter.begin(sandbox, run_id, "job-1", provider=provider, budget=budget)  # type: ignore[call-arg]
    except TypeError as exc:
        pytest.fail(f"a run cannot be declared with its budget: {exc}")


def _purses(proxies: _Proxies, proxy: str, run_id: str) -> dict[str, Any]:
    budget = getattr(proxies, "declared_budgets", {}).get((proxy, run_id))
    assert budget is not None, f"{run_id} was declared at {proxy} with no budget"
    assert "purses" in budget, f"{run_id} was declared at {proxy} with no purses: {budget}"
    return {p["key"]: p for p in budget["purses"]}


async def test_a_run_is_declared_with_its_purses_at_the_proxy_serving_its_provider() -> None:
    proxies = _Proxies({"sbx": ["xai-proxy", "llm-proxy"]})
    meter, _ = _meter(proxies, _Repo())
    await _begin(meter, "run-1", _request(job=0.5))
    purses = _purses(proxies, "xai-proxy", "run-1")
    assert purses == {"job:job-1": {"key": "job:job-1", "limit": "job", "remaining_usd": 0.5, "members": []}}
    assert proxies.declared_budgets[("llm-proxy", "run-1")] is None, "a proxy not serving the run was given a budget"


async def test_runs_of_one_job_at_one_proxy_share_its_purse_and_at_two_split_it() -> None:
    """At one proxy the purse counts each member as it goes; across proxies the ledger splits what is left."""
    proxies = _Proxies({"sbx-a": ["xai-proxy"], "sbx-b": ["xai-proxy"], "sbx-c": ["llm-proxy"]})
    meter, _ = _meter(proxies, _Repo())
    await _begin(meter, "run-a", _request(job=2.0), sandbox="sbx-a")
    await _begin(meter, "run-b", _request(job=2.0), sandbox="sbx-b")
    assert _purses(proxies, "xai-proxy", "run-b")["job:job-1"] == {
        "key": "job:job-1",
        "limit": "job",
        "remaining_usd": 2.0,
        "members": ["run-a"],
    }, "the second run was not told it shares the purse with the first"
    await _begin(meter, "run-c", _request(job=2.0), sandbox="sbx-c", provider="openai")
    assert _purses(proxies, "llm-proxy", "run-c")["job:job-1"]["remaining_usd"] == 0.0, (
        "the job's purse at a second proxy was given what the first already holds"
    )


async def test_a_deployment_purse_takes_no_more_than_the_job_purses_at_its_proxy() -> None:
    """So jobs at one proxy never take the whole day from jobs at another."""
    proxies = _Proxies({"sbx-a": ["xai-proxy"], "sbx-b": ["llm-proxy"]})
    meter, _ = _meter(proxies, _Repo())
    await _begin(meter, "run-a", _request(job=10.0, daily=100.0, job_key="job:a"), sandbox="sbx-a")
    assert _purses(proxies, "xai-proxy", "run-a")["daily"]["remaining_usd"] == 10.0
    await _begin(meter, "run-b", _request(job=10.0, daily=100.0, job_key="job:b"), sandbox="sbx-b", provider="openai")
    assert _purses(proxies, "llm-proxy", "run-b")["daily"]["remaining_usd"] == 10.0, "the day went to the first proxy"
    await _begin(meter, "run-c", _request(job=10.0, daily=15.0, job_key="job:c"), sandbox="sbx-a")
    assert _purses(proxies, "xai-proxy", "run-c")["daily"]["remaining_usd"] == 5.0, (
        "near the day's cap, what the other proxy holds was not taken off"
    )


async def test_a_settled_run_leaves_its_purses_which_are_worked_out_again() -> None:
    proxies = _Proxies({"sbx-a": ["xai-proxy"], "sbx-b": ["xai-proxy"]})
    meter, _ = _meter(proxies, _Repo())
    window = await _begin(meter, "run-a", _request(job=2.0), sandbox="sbx-a")
    await _begin(meter, "run-b", _request(job=2.0), sandbox="sbx-b")
    settle = getattr(meter, "settle", None)
    assert settle is not None, "the meter cannot settle a run"
    await settle(window, _request(job=1.6))  # run-a was charged $0.40, now in its trace
    (proxy, figures), *_ = proxies.purse_updates
    assert (proxy, figures) == (
        "xai-proxy",
        [{"key": "job:job-1", "limit": "job", "remaining_usd": 1.6, "members": ["run-b"]}],
    ), proxies.purse_updates


async def test_a_run_that_cannot_be_worked_out_again_stays_counted() -> None:
    proxies = _Proxies({"sbx": ["xai-proxy"]})
    meter, _ = _meter(proxies, _Repo())
    window = await _begin(meter, "run-1", _request(job=2.0))
    settle = getattr(meter, "settle", None)
    assert settle is not None, "the meter cannot settle a run"
    await settle(window, None)
    assert getattr(proxies, "purse_updates", []) == [], "the run left its purse with its charge unknown"
    await _begin(meter, "run-2", _request(job=2.0), sandbox="sbx")
    assert _purses(proxies, "xai-proxy", "run-2")["job:job-1"]["members"] == ["run-1"]


async def test_a_run_whose_declaration_failed_draws_on_no_purse() -> None:
    proxies = _Proxies({"sbx-a": ["xai-proxy"], "sbx-b": ["xai-proxy"]})
    proxies.fail[("xai-proxy", "declare")] = RuntimeError("proxy away")
    meter, _ = _meter(proxies, _Repo())
    await _begin(meter, "run-a", _request(job=2.0), sandbox="sbx-a")
    del proxies.fail[("xai-proxy", "declare")]
    await _begin(meter, "run-b", _request(job=2.0), sandbox="sbx-b")
    assert _purses(proxies, "xai-proxy", "run-b")["job:job-1"]["members"] == []


async def test_at_start_up_a_budget_waits_for_the_last_process_s_runs_to_close_then_fails_closed() -> None:
    meter, _ = _meter(_Proxies({}), _Repo())
    hold = getattr(meter, "hold_budgets_until", None)
    assert hold is not None, "a run's budget does not wait for the last process's runs to close"
    closing: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    hold(closing)
    assert await meter.budgets_ready(timeout=0.01) is False, "a budget was read with their charges unsettled"
    closing.set_result(None)
    assert await meter.budgets_ready(timeout=0.01) is True


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
    # Three calls answered in the run, and the one in flight at its ceiling.
    expected = {"grok-4.7": {"input_tokens": 300 + 50, "output_tokens": 30 + 500, "requests": 4, "estimated": 1}}
    assert counted == [expected], f"the run was not given what the proxy counted under no run during it: {counted}"
    (grown,) = [t.snapshot for t in metering.tallies if t.proxy == "llm-proxy"]
    assert (grown["errors"], grown["open"]) == (2, []), "the run's errors were lost, or its call in flight kept apart"
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


async def test_two_undeclared_runs_in_a_row_count_a_call_in_flight_between_them_once() -> None:
    """A call still in flight when one run ends is charged there at its ceiling: the next run is not given it again,
    whether it is still in flight at that run's end or ended during it."""
    from embry0.agents.run_charge import charge_run

    proxies = _Proxies({"sbx": ["llm-proxy"]})
    proxies.fail[("llm-proxy", "declare")] = RuntimeError("the proxy refused the declaration")
    key = "unattributed:sbx:1.0"
    proxies.tallies["llm-proxy"][key] = _unattributed("sbx", {})
    meter, _ = _meter(proxies, _Repo())
    in_flight = {"id": 7, "model": "grok-4.7", "since": 2.0, "input_tokens": 50, "output_tokens": 500}

    first = await meter.begin("sbx", "run-1", "job-1")
    proxies.tallies["llm-proxy"][key].update(models=_calls(1, output=10), open=[in_flight])
    ended_first = await meter.end(first)

    second = await meter.begin("sbx", "run-2", "job-1")  # the call is still in flight here
    proxies.tallies["llm-proxy"][key]["models"] = {
        "grok-4.7": {"input_tokens": 100 + 50 + 200, "output_tokens": 10 + 500 + 20, "requests": 3, "estimated": 1}
    }  # the call ended at its ceiling during the second run, which made one call of its own
    proxies.tallies["llm-proxy"][key]["open"] = []
    ended_second = await meter.end(second)

    def output(metering: Any) -> int:
        return charge_run(metering, _record(), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0)).output_tokens

    assert output(ended_first) == 10 + 500, "the first run was not charged its call in flight at its ceiling"
    assert output(ended_second) == 20, f"the second run was given the first run's call again: {output(ended_second)}"


async def test_a_call_in_flight_across_two_undeclared_runs_is_not_given_to_the_second() -> None:
    proxies = _Proxies({"sbx": ["llm-proxy"]})
    proxies.fail[("llm-proxy", "declare")] = RuntimeError("the proxy refused the declaration")
    key = "unattributed:sbx:1.0"
    in_flight = {"id": 7, "model": "grok-4.7", "since": 2.0, "input_tokens": 50, "output_tokens": 500}
    proxies.tallies["llm-proxy"][key] = _unattributed("sbx", {})
    meter, _ = _meter(proxies, _Repo())
    first = await meter.begin("sbx", "run-1", "job-1")
    proxies.tallies["llm-proxy"][key]["open"] = [in_flight]
    await meter.end(first)
    second = await meter.begin("sbx", "run-2", "job-1")
    metering = await meter.end(second)  # still in flight
    from embry0.agents.run_charge import charge_run

    charge = charge_run(metering, _record(), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0))
    assert (charge.input_tokens, charge.output_tokens) == (0, 0), (
        f"the second run was given a call that was in flight before it began: {charge.metering['proxies']}"
    )


def _output(metering: Any) -> tuple[int, int]:
    from embry0.agents.run_charge import charge_run

    charge = charge_run(metering, _record(), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0))
    return charge.input_tokens, charge.output_tokens


async def _undeclared(proxies: _Proxies, meter: RunMeter, run_id: str, between: Any) -> Any:
    """Run ``run_id`` undeclared on the llm-proxy, ``between(tally)`` changing the sandbox's calls under no run during
    it; its metering."""
    proxies.fail[("llm-proxy", "declare")] = RuntimeError("the proxy refused the declaration")
    window = await meter.begin("sbx", run_id, "job-1")
    between(proxies.tallies["llm-proxy"]["unattributed:sbx:1.0"])
    return await meter.end(window)


@pytest.mark.parametrize("renamed", [True, False], ids=["its-model-makes-another-call", "its-model-makes-none"])
async def test_a_call_in_flight_that_ends_under_another_model_s_name_is_counted_once(renamed: bool) -> None:
    """The proxy keeps a call in flight under the model it was asked for, and its end under the one its answer named:
    the run after is given the totals' rise, so the call is not counted again under the new name, whether or not its
    first model makes another call in that run."""
    proxies = _Proxies({"sbx": ["llm-proxy"]})
    in_flight = {"id": 3, "model": "qwen", "since": 2.0, "input_tokens": 50, "output_tokens": 500}
    proxies.tallies["llm-proxy"]["unattributed:sbx:1.0"] = _unattributed("sbx", {})
    meter, _ = _meter(proxies, _Repo())
    await _undeclared(proxies, meter, "run-1", lambda tally: tally.update(open=[in_flight]))
    own = {"input_tokens": 200, "output_tokens": 20, "requests": 1, "estimated": 0}

    def ended(tally: dict[str, Any]) -> None:
        tally["models"] = {
            "deployed-model-id": {"input_tokens": 50, "output_tokens": 500, "requests": 1, "estimated": 1},
            ("qwen" if renamed else "another-model"): dict(own),
        }  # the call ended at its ceiling, named after the answer's model; the run made one call of its own
        tally["open"] = []

    metering = await _undeclared(proxies, meter, "run-2", ended)
    assert _output(metering) == (200, 20), f"the call was counted again under its answer's model: {metering.tallies}"


async def test_a_call_that_began_between_runs_is_charged_to_the_run_it_ends_in() -> None:
    """Charged by no run when it began (it began outside one), so the run it ends in is charged it, whatever its
    ceiling: a call held open between runs and ended short is not left to no one."""
    proxies = _Proxies({"sbx": ["llm-proxy"]})
    meter, _ = _meter(proxies, _Repo())
    first = await meter.begin("sbx", "run-1", "job-1")  # declared: its calls are its own tally's
    await meter.end(first)
    key = "unattributed:sbx:1.0"
    held = {"id": 9, "model": "grok-4.7", "since": 3.0, "input_tokens": 40, "output_tokens": 128_000}
    proxies.tallies["llm-proxy"][key] = {**_unattributed("sbx", {}), "open": [held]}  # opened outside any run

    def during(tally: dict[str, Any]) -> None:
        tally["models"] = {"grok-4.7": {"input_tokens": 2000 + 30, "output_tokens": 3000 + 10, "requests": 2}}
        tally["open"] = []  # the run's own calls, and the held one, ended short

    metering = await _undeclared(proxies, meter, "run-2", during)
    assert _output(metering) == (2030, 3010), (
        f"the call held open between runs was charged to no one: {_output(metering)}"
    )


@pytest.mark.parametrize("lost", ["the meter restarted", "the proxy names no ids"])
async def test_a_call_in_flight_not_known_to_be_charged_is_charged_again(lost: str) -> None:
    """Where the meter cannot tell that the run before was charged a call at its ceiling, the run it ends in is
    charged it too: more, never less."""
    proxies = _Proxies({"sbx": ["llm-proxy"]})
    in_flight = {"id": 5, "model": "grok-4.7", "since": 2.0, "input_tokens": 50, "output_tokens": 500}
    if lost == "the proxy names no ids":
        del in_flight["id"]
    proxies.tallies["llm-proxy"]["unattributed:sbx:1.0"] = _unattributed("sbx", {})
    meter, _ = _meter(proxies, _Repo())
    first = await _undeclared(proxies, meter, "run-1", lambda tally: tally.update(open=[in_flight]))
    assert _output(first) == (50, 500)
    if lost == "the meter restarted":
        meter, _ = _meter(proxies, _Repo())

    def ended(tally: dict[str, Any]) -> None:
        tally["models"] = {"grok-4.7": {"input_tokens": 50, "output_tokens": 500, "requests": 1, "estimated": 1}}
        tally["open"] = []

    assert _output(await _undeclared(proxies, meter, "run-2", ended)) == (50, 500), f"{lost}: not charged again"


async def test_a_proxy_restarted_between_runs_reusing_a_key_and_an_id_has_its_call_charged() -> None:
    """A recreated proxy numbers its calls from the start again, and a tally may come back under the same key: a call
    of the new process that carries an id a run was charged in the old one is not that call, and is charged."""
    proxies = _Proxies({"sbx": ["llm-proxy"]})
    key = "unattributed:sbx:1.0"
    in_flight = {"id": 1, "model": "grok-4.7", "since": 2.0, "input_tokens": 50, "output_tokens": 500}
    proxies.tallies["llm-proxy"][key] = _unattributed("sbx", {})
    meter, _ = _meter(proxies, _Repo())
    await _undeclared(proxies, meter, "run-1", lambda tally: tally.update(open=[dict(in_flight)]))
    proxies.boot["llm-proxy"] = "l2"  # recreated: its tallies start again, the same key and the same first id
    proxies.tallies["llm-proxy"][key] = {**_unattributed("sbx", {}), "open": [dict(in_flight)]}  # began between runs

    def ended(tally: dict[str, Any]) -> None:
        tally["models"] = {"grok-4.7": {"input_tokens": 50, "output_tokens": 500, "requests": 1, "estimated": 1}}
        tally["open"] = []

    metering = await _undeclared(proxies, meter, "run-2", ended)
    assert _output(metering) == (50, 500), f"the new process's call was taken for the one charged: {_output(metering)}"
