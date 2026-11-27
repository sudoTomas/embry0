"""The orchestrator's meter against a real metering proxy: ``ProxyManager``'s admin transport (its own script, run
here as a local process in place of ``docker exec``) talks to the xai-proxy app, and a run's calls are charged from
what the proxy counted (embry0.execution.run_meter, embry0.agents.run_charge)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from embry0.agents.invocation import ProviderRecord
from embry0.agents.run_charge import charge_run
from embry0.execution.proxy import manager as manager_module
from embry0.execution.proxy.manager import ProxyManager
from embry0.execution.proxy.metering import METER
from embry0.execution.proxy.xai_proxy import TOKENS, create_xai_proxy_app
from embry0.execution.run_meter import RunMeter
from tests.unit.execution.proxy.test_metered_proxies import _SSE, _Answer, _Upstream
from tests.unit.execution.proxy.upstream_stub import route_upstream
from tests.unit.execution.test_run_meter import _Repo
from tests.unit.execution.test_xai_refresher_registry import scrubber_snapshot  # noqa: F401

ANSWERS = Path(__file__).parents[2] / "fixtures" / "model-answers"
ADMIN = "test-admin-secret-not-real"
TOKEN = "sandbox-bearer-token-at-least-32-chars-long"


class _LocalExec:
    """``docker exec <proxy> python3 -c <script>`` run as a local process: the manager's own script, unchanged."""

    def build_exec_cmd(self, container: str, command: list[str], **_kw: Any) -> list[str]:
        return [sys.executable if command[0] == "python3" else command[0], *command[1:]]

    async def run_cmd(self, cmd: list[str], timeout: int = 60, stdin_data: bytes | None = None, **_kw: Any) -> str:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await asyncio.wait_for(proc.communicate(stdin_data), timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"Docker command failed: {err.decode(errors='replace')}")
        return out.decode()


@pytest.fixture
async def proxy(aiohttp_server, monkeypatch) -> AsyncIterator[Any]:
    server = await aiohttp_server(create_xai_proxy_app(ADMIN))
    monkeypatch.setitem(manager_module.METERED_PROXIES, "xai-proxy", (server.port, "xai"))
    monkeypatch.setitem(manager_module.METERED_PROXIES, "llm-proxy", (1, "custom"))
    mgr = ProxyManager(_LocalExec(), proxy_admin_token=ADMIN)  # type: ignore[arg-type]
    mgr._http = MagicMock()  # start() was called
    mgr.xai_proxy_url = "http://xai-proxy:9106"
    await mgr._exec_admin_request(
        container="xai-proxy",
        method="POST",
        path="/admin/enroll",
        port=server.port,
        body={"sandbox_id": "sbx-1", "sandbox_token": TOKEN},
    )
    await mgr._exec_admin_request(
        container="xai-proxy", method="POST", path="/admin/token", port=server.port, body={"access_token": "at-1"}
    )
    upstream = _Upstream()
    route_upstream(server.app, upstream)
    server.mgr, server.upstream = mgr, upstream
    yield server
    # What a given-up replay left to send goes in the background: done before the proxy closes.
    await asyncio.gather(*getattr(mgr, "_remainder_tasks", ()), return_exceptions=True)


def _record() -> ProviderRecord:
    return ProviderRecord(
        name="xai",
        runtime="sdk",
        upstream_model="grok-4.7",
        rates=(2.0, 6.0),
        cache_read_factor=0.25,
        cache_write_factor=1.5,
        resolved_at="2026-11-25T00:00:00+00:00",
    )


async def _sandbox_call(proxy: Any) -> None:
    import aiohttp

    proxy.upstream.answers.append(_Answer((ANSWERS / "anthropic-stream.sse").read_bytes(), content_type=_SSE))
    body = {"model": "grok-4.7", "max_tokens": 32000, "stream": True, "messages": []}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            proxy.make_url("/v1/messages"), json=body, headers={"Authorization": f"Bearer {TOKEN}"}
        ) as r:
            assert r.status == 200
            await r.read()


async def test_a_run_s_calls_are_counted_by_the_proxy_read_kept_and_charged(proxy: Any) -> None:
    repo = _Repo()
    meter = RunMeter(proxy.mgr, repo)
    window = await meter.begin("sbx-1", "run-1", "job-1")
    assert window.declared == {"xai-proxy": proxy.app[METER].boot_id}, "the run was not declared on the proxy"
    await _sandbox_call(proxy)
    await meter.poll_once()
    kept = repo.rows[("xai-proxy", proxy.app[METER].boot_id, "run-1")]
    assert kept["final"] is False and kept["snapshot"]["models"]["grok-4.7"]["requests"] == 1
    metering = await meter.end(window)
    charge = charge_run(metering, _record(), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0))
    assert charge.source == "proxy"
    assert charge.cost_usd == pytest.approx((1000 * 2.0 + 100 * 6.0 + 400 * 2.0 * 0.25) / 1e6)
    assert repo.rows[("xai-proxy", proxy.app[METER].boot_id, "run-1")]["final"] is True
    assert "run:run-1" not in proxy.mgr._admin_state["xai-proxy"], "an ended run would be replayed"


async def test_a_meter_larger_than_64_kib_is_read_whole(proxy: Any) -> None:
    """The admin script once read 64 KiB of an answer, so a large meter would not parse and was lost."""
    meter = proxy.app[METER]
    for n in range(500):
        meter.declare(f"sbx-{n:04d}", f"run-{n:04d}-{'x' * 60}")
    read = await proxy.mgr.read_meter("xai-proxy")
    assert len(read["tallies"]) == 500


async def test_a_run_declared_on_a_recreated_proxy_is_given_back_after_its_enrollment(proxy: Any) -> None:
    """Replayed state goes enrollments first: a proxy takes a run only for an enrolled sandbox."""
    mgr = proxy.mgr
    await mgr.declare_run("xai-proxy", "sbx-1", "run-1")
    # The sandbox was enrolled again after the run was declared: its enrollment is now held after the run.
    mgr._remember("xai-proxy", "DELETE", "/admin/enroll/sbx-1", proxy.port, None)
    await mgr._exec_admin_request(
        container="xai-proxy",
        method="POST",
        path="/admin/enroll",
        port=proxy.port,
        body={"sandbox_id": "sbx-1", "sandbox_token": TOKEN},
    )
    held = list(mgr._admin_state["xai-proxy"])
    assert held.index("run:run-1") < held.index("enroll:sbx-1"), "not the case under test: the run held first"
    sent: list[str] = []

    async def _record_once(**kw: Any) -> dict[str, Any]:
        sent.append(f"{kw['method']} {kw['path']} {kw['body'].get('run_id') or kw['body'].get('sandbox_id', '')}")
        return {}

    mgr._exec_admin_once = _record_once  # type: ignore[method-assign]
    restored, failed = await mgr._replay("xai-proxy")
    assert failed == 0
    assert sent.index("POST /admin/runs run-1") > sent.index("POST /admin/enroll sbx-1"), sent


async def test_a_run_whose_end_was_never_heard_is_not_replayed_forever(proxy: Any) -> None:
    mgr = proxy.mgr
    mgr._enroll_ttl_seconds = 0  # held as long as an enrollment would be
    await mgr.declare_run("xai-proxy", "sbx-1", "run-1")
    assert "run:run-1" in mgr._admin_state["xai-proxy"]
    await asyncio.sleep(0.01)
    mgr._remember("xai-proxy", "GET", "/admin/meter", proxy.port, None)  # any admin call drops what expired
    assert "run:run-1" not in mgr._admin_state["xai-proxy"]


def test_only_the_metering_proxies_a_sandbox_is_enrolled_with_are_its_targets() -> None:
    mgr = ProxyManager(MagicMock(), proxy_admin_token=ADMIN)
    mgr.xai_proxy_url = "http://xai-proxy:9106"
    mgr._remember("xai-proxy", "POST", "/admin/enroll", 9106, {"sandbox_id": "sbx-1", "sandbox_token": TOKEN})
    mgr._remember("git-proxy", "POST", "/admin/enroll", 9101, {"sandbox_id": "sbx-1", "sandbox_token": TOKEN})
    assert mgr.metered_proxies() == ["xai-proxy"], "the llm-proxy was not launched"
    assert mgr.metered_proxies_for("sbx-1") == ["xai-proxy"]
    assert mgr.metered_proxies_for("sbx-2") == []
    mgr._remember("xai-proxy", "DELETE", "/admin/enroll/sbx-1", 9106, None)
    assert mgr.metered_proxies_for("sbx-1") == []


async def test_a_run_whose_end_lost_its_answer_is_not_given_back_to_a_recreated_proxy(proxy: Any) -> None:
    """The end reached the proxy, its answer did not reach the orchestrator: the run is over all the same."""
    mgr = proxy.mgr
    await mgr.declare_run("xai-proxy", "sbx-1", "run-A")
    real_once = mgr._exec_admin_once

    async def _answer_lost(**kw: Any) -> dict[str, Any]:
        response = await real_once(**kw)
        if kw["method"] == "DELETE":
            raise RuntimeError("docker exec xai-proxy for DELETE /admin/runs/run-A failed: timed out")
        return response

    mgr._exec_admin_once = _answer_lost  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await mgr.end_run("xai-proxy", "run-A")
    assert "run-A" not in proxy.app[METER]._runs, "the end did not reach the proxy: not the case under test"
    assert "run:run-A" not in mgr._admin_state["xai-proxy"], "an ended run would be replayed"


async def test_a_stale_run_is_never_replayed_over_the_sandbox_s_next_one(proxy: Any) -> None:
    """The review's case: run A's end was not heard, run B began, a recreated proxy was replayed meanwhile. B's call
    counts under B, and B is charged it."""
    mgr, meter_app = proxy.mgr, proxy.app[METER]
    meter = RunMeter(mgr, _Repo())
    await mgr.declare_run("xai-proxy", "sbx-1", "run-A")
    meter_app.end("run-A")  # A's end reached the proxy; the orchestrator never heard
    window = await meter.begin("sbx-1", "run-B", "job-1")
    assert "run:run-A" not in mgr._admin_state["xai-proxy"], "B's declaration left A held"
    held = mgr._admin_state["xai-proxy"]
    saved = held.pop("run:run-B")  # a replay whose list was taken before B's declaration was held
    sent: list[str] = []

    async def _to_a_recreated_proxy(**kw: Any) -> dict[str, Any]:
        sent.append(f"{kw['method']} {kw['path']} {kw['body'].get('run_id', '')}")
        return {}

    real_once, mgr._exec_admin_once = mgr._exec_admin_once, _to_a_recreated_proxy  # type: ignore[method-assign]
    await mgr._replay("xai-proxy")
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    held["run:run-B"] = saved
    assert "POST /admin/runs run-A" not in sent, f"the replay gave the sandbox its old run: {sent}"
    assert meter_app._current["sbx-1"] == "run-B"
    await _sandbox_call(proxy)
    metering = await meter.end(window)
    cost = (1000 * 2.0 + 100 * 6.0 + 400 * 2.0 * 0.25) / 1e6
    charge = charge_run(metering, _record(), sandbox_cost_usd=cost, sandbox_tokens=(1000, 100, 400, 0))
    assert (charge.source, charge.metering["gap"]) == ("proxy", False)
    assert charge.cost_usd == pytest.approx(cost), "B was charged less than its call"


async def test_an_entry_dropped_while_a_replay_runs_is_not_sent(proxy: Any) -> None:
    mgr = proxy.mgr
    await mgr.declare_run("xai-proxy", "sbx-1", "run-1")
    sent: list[str] = []

    async def _drop_the_run_on_the_first_call(**kw: Any) -> dict[str, Any]:
        sent.append(f"{kw['method']} {kw['path']}")
        mgr._admin_state["xai-proxy"].pop("run:run-1", None)  # the run ended while the replay ran
        return {}

    mgr._exec_admin_once = _drop_the_run_on_the_first_call  # type: ignore[method-assign]
    await mgr._replay("xai-proxy")
    assert "POST /admin/runs" not in sent, f"an ended run was replayed: {sent}"


async def _recreated(aiohttp_server: Any, old: Any, monkeypatch: Any) -> Any:
    """A fresh process of the same xai-proxy (a new boot, nothing enrolled), on which the manager's held state will
    be replayed."""
    server = await aiohttp_server(create_xai_proxy_app(ADMIN))
    monkeypatch.setitem(manager_module.METERED_PROXIES, "xai-proxy", (server.port, "xai"))
    held = old.mgr._admin_state["xai-proxy"]
    for key, (path, _port, body, expires) in list(held.items()):
        held[key] = (path, server.port, body, expires)
    upstream = _Upstream()
    route_upstream(server.app, upstream)
    server.mgr, server.upstream = old.mgr, upstream
    return server


async def test_a_run_declared_while_a_replay_sends_the_last_one_lands_after_it(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """The review's reordering: the replay has checked run A is held and is sending it; the sandbox's next run B is
    declared then. Two docker execs at once arrive in either order, so B waits for the replay: it lands after A, takes
    the sandbox back, and is charged its own call."""
    mgr = proxy.mgr
    meter = RunMeter(mgr, _Repo())
    await meter.begin("sbx-1", "run-A", "job-1")  # A is still the sandbox's run (its end not yet attempted)
    new = await _recreated(aiohttp_server, proxy, monkeypatch)
    real_once = mgr._exec_admin_once
    box: dict[str, Any] = {}

    async def _b_declared_while_a_is_sent(**kw: Any) -> dict[str, Any]:
        if kw["path"] == "/admin/runs" and (kw["body"] or {}).get("run_id") == "run-A" and "b" not in box:
            box["b"] = asyncio.create_task(meter.begin("sbx-1", "run-B", "job-1"))
            # Give B every chance to reach the proxy first; with nothing to wait for it would.
            await asyncio.wait({box["b"]}, timeout=2.0)
        return await real_once(**kw)

    mgr._exec_admin_once = _b_declared_while_a_is_sent  # type: ignore[method-assign]
    await mgr._replay("xai-proxy")
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    window_b = await box["b"]
    assert new.app[METER]._current["sbx-1"] == "run-B", "the replayed run took the sandbox from the run after it"
    new.upstream.answers.append(_Answer((ANSWERS / "anthropic-stream.sse").read_bytes(), content_type=_SSE))
    import aiohttp

    body = {"model": "grok-4.7", "max_tokens": 32000, "stream": True, "messages": []}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            new.make_url("/v1/messages"), json=body, headers={"Authorization": f"Bearer {TOKEN}"}
        ) as r:
            await r.read()
    cost = (1000 * 2.0 + 100 * 6.0 + 400 * 2.0 * 0.25) / 1e6
    charge = charge_run(await meter.end(window_b), _record(), sandbox_cost_usd=cost, sandbox_tokens=(1000, 100, 400, 0))
    assert charge.cost_usd == pytest.approx(cost), f"B was charged {charge.cost_usd} ({charge.source})"


async def test_a_run_ended_while_a_replay_sends_it_is_ended_after_it(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """A run's end that starts while the replay sends that run lands after it: the run is ended on the recreated
    proxy and its final tally read, rather than the end finding nothing and the replay leaving the run declared."""
    mgr = proxy.mgr
    meter = RunMeter(mgr, _Repo())
    window_a = await meter.begin("sbx-1", "run-A", "job-1")
    new = await _recreated(aiohttp_server, proxy, monkeypatch)
    real_once = mgr._exec_admin_once
    box: dict[str, Any] = {}

    async def _a_ended_while_sent(**kw: Any) -> dict[str, Any]:
        if kw["path"] == "/admin/runs" and (kw["body"] or {}).get("run_id") == "run-A" and "end" not in box:
            box["end"] = asyncio.create_task(meter.end(window_a))
            await asyncio.wait({box["end"]}, timeout=2.0)
        return await real_once(**kw)

    mgr._exec_admin_once = _a_ended_while_sent  # type: ignore[method-assign]
    await mgr._replay("xai-proxy")
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    metering = await box["end"]
    assert metering.unreached == (), "the end landed before the replayed declaration and found nothing"
    assert "sbx-1" not in new.app[METER]._current, "the replay left the ended run declared"


async def test_a_replay_gives_up_at_its_first_timeout_and_the_next_starts_over(proxy: Any) -> None:
    """A recreated proxy that does not answer: the rest of its state would each wait as long, and every declaration
    and end on it waits for the replay."""
    mgr = proxy.mgr
    await mgr.declare_run("xai-proxy", "sbx-1", "run-1")
    sent: list[str] = []

    async def _hung(**kw: Any) -> dict[str, Any]:
        sent.append(kw["path"])
        raise TimeoutError

    real_once, mgr._exec_admin_once = mgr._exec_admin_once, _hung  # type: ignore[method-assign]
    restored, failed = await mgr._replay("xai-proxy")
    assert (sent, restored, failed) == (["/admin/enroll"], 0, 3), "the replay went on after a timeout"
    assert set(mgr._admin_state["xai-proxy"]) == {"enroll:sbx-1", "token:", "run:run-1"}, "the held state was lost"
    resent: list[str] = []

    async def _answers(**kw: Any) -> dict[str, Any]:
        resent.append(kw["path"])
        return {}

    mgr._exec_admin_once = _answers  # type: ignore[method-assign]
    assert await mgr._replay("xai-proxy") == (3, 0)
    assert resent == ["/admin/enroll", "/admin/token", "/admin/runs"]
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]


async def _slow_replay(mgr: Any) -> asyncio.Task[None]:
    """A replay of the xai-proxy that holds its lock far longer than any wait for it."""
    lock = mgr._replay_locks.setdefault("xai-proxy", asyncio.Lock())
    await lock.acquire()

    async def _release_later() -> None:
        await asyncio.sleep(30)
        lock.release()

    return asyncio.create_task(_release_later())


async def test_a_declaration_waits_for_a_replay_no_longer_than_the_bound(proxy: Any, monkeypatch: Any) -> None:
    """Metering never holds a run up for long; ordering does not need the wait (the proxy orders by number), so the run
    is charged its own call."""
    monkeypatch.setattr(manager_module, "REPLAY_WAIT_SECONDS", 0.2)
    mgr = proxy.mgr
    meter = RunMeter(mgr, _Repo())
    slow = await _slow_replay(mgr)
    try:
        started = asyncio.get_running_loop().time()
        window = await meter.begin("sbx-1", "run-1", "job-1")
        assert asyncio.get_running_loop().time() - started < 5, "the run waited for the replay without bound"
        await _sandbox_call(proxy)
        started = asyncio.get_running_loop().time()
        metering = await meter.end(window)
        assert asyncio.get_running_loop().time() - started < 5, "the end waited for the replay without bound"
    finally:
        slow.cancel()
    charge = charge_run(metering, _record(), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0))
    assert (charge.source, charge.cost_usd) == ("proxy", pytest.approx(ONE_CALL))


async def test_a_run_across_an_abandoned_replay_is_charged_at_least_its_sandbox_s(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """Recreated, then given up on: the new process never heard of the run, whose end finds nothing there."""
    mgr = proxy.mgr
    meter = RunMeter(mgr, _Repo())
    window = await meter.begin("sbx-1", "run-1", "job-1")
    await _recreated(aiohttp_server, proxy, monkeypatch)

    async def _hung(**kw: Any) -> dict[str, Any]:
        raise TimeoutError

    real_once, mgr._exec_admin_once = mgr._exec_admin_once, _hung  # type: ignore[method-assign]
    await mgr._replay("xai-proxy")
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    metering = await meter.end(window)
    charge = charge_run(metering, _record(), sandbox_cost_usd=0.02, sandbox_tokens=(1, 1, 0, 0))
    assert (charge.metering["gap"], charge.cost_usd) == (True, pytest.approx(0.02))


async def _call_to(server: Any) -> None:
    import aiohttp

    server.upstream.answers.append(_Answer((ANSWERS / "anthropic-stream.sse").read_bytes(), content_type=_SSE))
    body = {"model": "grok-4.7", "max_tokens": 32000, "stream": True, "messages": []}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            server.make_url("/v1/messages"), json=body, headers={"Authorization": f"Bearer {TOKEN}"}
        ) as r:
            await r.read()


ONE_CALL = (1000 * 2.0 + 100 * 6.0 + 400 * 2.0 * 0.25) / 1e6


def _lands_late(real_once: Any, box: dict[str, Any], match: Any) -> Any:
    """A transport whose first request ``match`` picks times out here but still reaches the proxy 0.5 s later, as a
    killed ``docker exec`` does (killing the client does not stop the exec on the daemon)."""

    async def _once(**kw: Any) -> dict[str, Any]:
        if match(kw) and "late" not in box:

            async def _deliver() -> Any:
                await asyncio.sleep(0.5)
                try:
                    return await real_once(**kw)
                except Exception as exc:  # noqa: BLE001 — what the proxy answered the late request is the result
                    return exc

            box["late"] = asyncio.create_task(_deliver())
            raise TimeoutError("docker command timed out after 15s")
        return await real_once(**kw)

    return _once


async def test_a_given_up_run_declaration_that_lands_late_is_refused(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """The review's late run, with no window to rely on: run A's replayed declaration reaches the proxy after the
    sandbox's next run B was declared, and after the replay's remainder went too. Older than B's, it is refused: B
    keeps the sandbox and is charged its own call."""
    mgr = proxy.mgr
    meter = RunMeter(mgr, _Repo())
    await meter.begin("sbx-1", "run-A", "job-1")
    new = await _recreated(aiohttp_server, proxy, monkeypatch)
    box: dict[str, Any] = {}
    real_once = mgr._exec_admin_once
    mgr._exec_admin_once = _lands_late(  # type: ignore[method-assign]
        real_once, box, lambda kw: kw["path"] == "/admin/runs" and kw["body"].get("run_id") == "run-A"
    )
    await mgr._replay("xai-proxy")
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    window_b = await meter.begin("sbx-1", "run-B", "job-1")
    await asyncio.gather(*getattr(mgr, "_remainder_tasks", ()))
    assert not isinstance(await box["late"], dict), "the late, older declaration was taken"
    assert new.app[METER]._current["sbx-1"] == "run-B"
    await _call_to(new)
    charge = charge_run(await meter.end(window_b), _record(), sandbox_cost_usd=ONE_CALL, sandbox_tokens=(1, 1, 0, 0))
    assert (charge.source, charge.cost_usd) == ("proxy", pytest.approx(ONE_CALL)), "B was not charged its own call"


async def test_a_given_up_enrollment_that_lands_late_is_refused(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """The review's abandoned case: the sandbox enrolled again and declared its next run; the replay's older
    enrollment then reached the proxy. Refused: the run keeps the sandbox and is charged its own call."""
    mgr = proxy.mgr
    meter = RunMeter(mgr, _Repo())
    new = await _recreated(aiohttp_server, proxy, monkeypatch)
    box: dict[str, Any] = {}
    real_once = mgr._exec_admin_once
    mgr._exec_admin_once = _lands_late(real_once, box, lambda kw: kw["path"] == "/admin/enroll")  # type: ignore[method-assign]
    await mgr._replay("xai-proxy")
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    port = manager_module.METERED_PROXIES["xai-proxy"][0]
    for path, body in (
        ("/admin/enroll", {"sandbox_id": "sbx-1", "sandbox_token": TOKEN}),
        ("/admin/token", {"access_token": "at-1"}),
    ):
        await mgr._exec_admin_request(container="xai-proxy", method="POST", path=path, port=port, body=body)
    window_b = await meter.begin("sbx-1", "run-B", "job-1")
    assert not isinstance(await box["late"], dict), "the late, older enrollment was taken"
    await _call_to(new)
    charge = charge_run(await meter.end(window_b), _record(), sandbox_cost_usd=ONE_CALL, sandbox_tokens=(1, 1, 0, 0))
    assert (charge.source, charge.cost_usd) == ("proxy", pytest.approx(ONE_CALL))


async def test_a_replayed_older_enrollment_never_replaces_a_newer_one(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """The review's NIT 2: the remainder sends the sandbox's older enrollment while the sandbox enrolls again with a
    new bearer, and the newer one lands first. The older is refused, so the sandbox's current bearer is not refused."""
    mgr = proxy.mgr
    new = await _recreated(aiohttp_server, proxy, monkeypatch)
    await _give_up_replay(mgr, lambda kw: kw["path"] == "/admin/enroll")
    port = manager_module.METERED_PROXIES["xai-proxy"][0]
    newer = "sandbox-bearer-token-NEWER-at-least-32-chars"
    real_once = mgr._exec_admin_once
    box: dict[str, Any] = {}

    async def _newer_lands_first(**kw: Any) -> dict[str, Any]:
        if kw["path"] == "/admin/enroll" and kw["body"].get("sandbox_token") == TOKEN and "newer" not in box:
            box["newer"] = True
            await mgr._exec_admin_request(
                container="xai-proxy",
                method="POST",
                path="/admin/enroll",
                port=port,
                body={"sandbox_id": "sbx-1", "sandbox_token": newer},
            )
        return await real_once(**kw)

    mgr._exec_admin_once = _newer_lands_first  # type: ignore[method-assign]
    restored, failed = await mgr._send_held("xai-proxy", mgr._replay_remainder["xai-proxy"])
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    assert failed == 0, "a held request a newer one superseded was counted as failed"
    assert not mgr.replay_abandoned("xai-proxy")
    import aiohttp

    new.upstream.answers.append(_Answer((ANSWERS / "anthropic-message.json").read_bytes()))
    async with aiohttp.ClientSession() as session:
        body = {"model": "grok-4.7", "max_tokens": 10, "messages": []}
        async with session.post(
            new.make_url("/v1/messages"), json=body, headers={"Authorization": f"Bearer {newer}"}
        ) as r:
            assert r.status == 200, f"the sandbox's newer bearer was refused ({r.status}): the older enrollment won"


async def test_a_given_up_replay_s_remainder_goes_in_the_background_once_the_proxy_answers(
    proxy: Any, monkeypatch: Any
) -> None:
    """A proxy that answers nothing keeps it waiting; the first answer sends it, without holding that request up, and
    a declaration meanwhile waits no longer than the bound. Then the mode ends."""
    monkeypatch.setattr(manager_module, "REPLAY_WAIT_SECONDS", 0.2)
    mgr = proxy.mgr
    meter = RunMeter(mgr, _Repo())
    await _give_up_replay(mgr, lambda kw: kw["path"] == "/admin/enroll")
    assert mgr.replay_abandoned("xai-proxy")
    real_once = mgr._exec_admin_once

    async def _stuck(**kw: Any) -> dict[str, Any]:
        raise TimeoutError

    mgr._exec_admin_once = _stuck  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        await mgr.read_meter("xai-proxy")
    assert mgr.replay_abandoned("xai-proxy") and not getattr(mgr, "_remainder_tasks", ()), (
        "a proxy that answered nothing was sent it"
    )

    async def _slow_enroll(**kw: Any) -> dict[str, Any]:
        if kw["path"] == "/admin/enroll":
            await asyncio.sleep(1.0)
        return await real_once(**kw)

    mgr._exec_admin_once = _slow_enroll  # type: ignore[method-assign]
    started = asyncio.get_running_loop().time()
    await mgr.read_meter("xai-proxy")
    assert asyncio.get_running_loop().time() - started < 0.8, "the answering request waited for the remainder"
    started = asyncio.get_running_loop().time()
    window = await meter.begin("sbx-1", "run-1", "job-1")
    assert asyncio.get_running_loop().time() - started < 0.8, "a declaration waited out the remainder"
    await asyncio.gather(*getattr(mgr, "_remainder_tasks", ()))
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    assert not mgr.replay_abandoned("xai-proxy"), "the mode outlasted the remainder"
    await meter.end(window)


async def test_a_declaration_that_fails_leaves_no_mark(proxy: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(manager_module, "REPLAY_WAIT_SECONDS", 0.05, raising=False)
    mgr = proxy.mgr
    slow = await _slow_replay(mgr)

    async def _refused(**kw: Any) -> dict[str, Any]:
        raise RuntimeError("enroll xai-proxy /admin/runs returned HTTP 404")

    mgr._exec_admin_request = _refused  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError):
            await mgr.declare_run("xai-proxy", "sbx-1", "run-1")
    finally:
        slow.cancel()
    assert mgr._unordered_runs == set(), "a run that was never declared kept its mark"


async def _give_up_replay(mgr: Any, match: Any = lambda kw: True) -> list[str]:
    """Replay the xai-proxy's state over a transport whose first request ``match`` picks times out; what was sent."""
    sent: list[str] = []
    real_once = mgr._exec_admin_once
    box: dict[str, bool] = {}

    async def _once(**kw: Any) -> dict[str, Any]:
        sent.append(kw["path"])
        if match(kw) and "done" not in box:
            box["done"] = True
            raise TimeoutError
        return await real_once(**kw)

    mgr._exec_admin_once = _once  # type: ignore[method-assign]
    try:
        await mgr._replay("xai-proxy")
    finally:
        mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    return sent


async def test_a_run_on_a_proxy_that_does_not_order_requests_is_told_from_a_lost_tally(proxy: Any) -> None:
    """A proxy image that predates ordered requests does not echo the declaration's number: its runs there are charged
    ``*_unordered``, apart from a run whose end is lost (``*_gap``)."""
    mgr = proxy.mgr
    meter = RunMeter(mgr, _Repo())
    real_once = mgr._exec_admin_once

    async def _older_image(**kw: Any) -> dict[str, Any]:
        answer = await real_once(**kw)
        return {k: v for k, v in answer.items() if k != "seq"}

    mgr._exec_admin_once = _older_image  # type: ignore[method-assign]
    window = await meter.begin("sbx-1", "run-1", "job-1")
    await _sandbox_call(proxy)
    metering = await meter.end(window)
    assert (metering.unordered, metering.unreached) == (("xai-proxy",), ())
    charge = charge_run(metering, _record(), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0))
    assert charge.source == "proxy_unordered" and charge.metering["unordered"] == ["xai-proxy"]
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    ordered = await meter.begin("sbx-1", "run-2", "job-1")
    assert (await meter.end(ordered)).unordered == (), "a run on a proxy that echoes its number was unordered"
    lost = await meter.begin("sbx-1", "run-3", "job-1")

    async def _end_lost(**kw: Any) -> dict[str, Any]:
        if kw["method"] == "DELETE":
            raise RuntimeError("docker exec xai-proxy failed")
        return await real_once(**kw)

    mgr._exec_admin_once = _end_lost  # type: ignore[method-assign]
    metering = await meter.end(lost)
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    assert metering.unreached == ("xai-proxy",)
    charge = charge_run(metering, _record(), sandbox_cost_usd=0.01, sandbox_tokens=(1, 1, 0, 0))
    assert charge.source == "sandbox_gap" and charge.metering["unreached"] == ["xai-proxy"]


async def test_what_a_given_up_replay_did_not_send_is_sent_when_the_proxy_answers(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """Else the sandboxes it did not reach are refused until they enroll again or the proxy is recreated. What was
    done since (a token pushed again) is not sent a second time."""
    mgr = proxy.mgr
    await mgr.declare_run("xai-proxy", "sbx-1", "run-1")
    new = await _recreated(aiohttp_server, proxy, monkeypatch)
    assert await _give_up_replay(mgr, lambda kw: kw["path"] == "/admin/enroll") == ["/admin/enroll"]
    sent: list[str] = []
    real_once = mgr._exec_admin_once

    async def _recording(**kw: Any) -> dict[str, Any]:
        sent.append(f"{kw['method']} {kw['path']}")
        return await real_once(**kw)

    mgr._exec_admin_once = _recording  # type: ignore[method-assign]
    port = manager_module.METERED_PROXIES["xai-proxy"][0]
    await mgr._exec_admin_request(
        container="xai-proxy", method="POST", path="/admin/token", port=port, body={"access_token": "at-2"}
    )
    await asyncio.gather(*getattr(mgr, "_remainder_tasks", ()))
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    assert sent == ["POST /admin/token", "POST /admin/enroll", "POST /admin/runs"], sent
    assert not mgr.replay_abandoned("xai-proxy")
    assert new.app[METER]._current["sbx-1"] == "run-1", "the sandbox's run was not given back"
    await _call_to(new)
    assert new.app[METER].snapshot()["tallies"][0]["models"], "the sandbox's call was refused"


async def test_each_admin_request_is_numbered_newer_and_a_replay_sends_its_own_number(proxy: Any) -> None:
    import time

    mgr = proxy.mgr
    before = time.time_ns()
    await mgr.declare_run("xai-proxy", "sbx-1", "run-1")
    held = mgr._admin_state["xai-proxy"]
    numbers = [held["enroll:sbx-1"][2]["seq"], held["token:"][2]["seq"], held["run:run-1"][2]["seq"]]
    assert numbers == sorted(numbers) and len(set(numbers)) == 3, f"not newer each time: {numbers}"
    assert numbers[-1] >= before, "a later process's numbers would not be newer than an earlier one's"
    sent: dict[str, int] = {}

    async def _record(**kw: Any) -> dict[str, Any]:
        sent[kw["path"]] = kw["body"]["seq"]
        return {}

    real_once, mgr._exec_admin_once = mgr._exec_admin_once, _record  # type: ignore[method-assign]
    await mgr._replay("xai-proxy")
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    assert sent == {"/admin/enroll": numbers[0], "/admin/token": numbers[1], "/admin/runs": numbers[2]}, sent
    assert "seq" not in ((await mgr.read_meter("xai-proxy")) or {}).get("tallies", [{}])[0], "a read was numbered"


async def test_only_a_stale_refusal_is_a_stale_request(proxy: Any) -> None:
    from embry0.execution.proxy.manager import StaleAdminRequest

    mgr = proxy.mgr
    port = proxy.port
    with pytest.raises(StaleAdminRequest):
        await mgr._exec_admin_once(
            container="xai-proxy",
            method="POST",
            path="/admin/enroll",
            port=port,
            body={"sandbox_id": "sbx-1", "sandbox_token": TOKEN, "seq": 1},
        )
    await mgr.declare_run("xai-proxy", "sbx-1", "run-1")
    await mgr._exec_admin_request(
        container="xai-proxy",
        method="POST",
        path="/admin/enroll",
        port=port,
        body={"sandbox_id": "sbx-2", "sandbox_token": "another-bearer-token-at-least-32-chars"},
    )
    with pytest.raises(RuntimeError) as conflict:
        await mgr.declare_run("xai-proxy", "sbx-2", "run-1")  # a run that belongs to another sandbox: 409, not stale
    assert not isinstance(conflict.value, StaleAdminRequest)


async def test_an_unenroll_or_end_a_newer_request_superseded_stops_there(proxy: Any) -> None:
    from embry0.execution.proxy.manager import StaleAdminRequest

    mgr = proxy.mgr
    mgr.git_proxy_url = ""
    calls: list[str] = []

    async def _superseded(**kw: Any) -> dict[str, Any]:
        calls.append(kw["path"])
        raise StaleAdminRequest("refused as older than the last it took")

    real, mgr._exec_admin_request = mgr._exec_admin_request, _superseded  # type: ignore[method-assign]
    await mgr.unenroll_sandbox("sbx-1")
    assert calls == ["/admin/enroll/sbx-1"], f"a superseded unenroll was retried: {calls}"
    assert await mgr.end_run("xai-proxy", "run-1") is None, "a superseded end was not taken as no final tally"
    mgr._exec_admin_request = real  # type: ignore[method-assign]


async def test_a_declaration_is_never_held_by_a_slow_remainder(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """The review's probe: a long remainder going out while a run is declared. The declaration waits no longer than
    the bound, and its own answer does not make it wait for the remainder either."""
    monkeypatch.setattr(manager_module, "LATE_REQUEST_SECONDS", 0.0, raising=False)  # the window an earlier design had
    monkeypatch.setattr(manager_module, "REPLAY_WAIT_SECONDS", 0.3)
    mgr = proxy.mgr
    meter = RunMeter(mgr, _Repo())
    for n in range(4):
        mgr._remember("xai-proxy", "POST", "/admin/enroll", 1, {"sandbox_id": f"sbx-{n + 2}", "sandbox_token": TOKEN})
    await _recreated(aiohttp_server, proxy, monkeypatch)
    await _give_up_replay(mgr, lambda kw: kw["path"] == "/admin/enroll" and kw["body"]["sandbox_id"] == "sbx-2")
    real_once = mgr._exec_admin_once

    async def _slow_enroll(**kw: Any) -> dict[str, Any]:
        if kw["path"] == "/admin/enroll":
            await asyncio.sleep(1.0)
        return await real_once(**kw)

    mgr._exec_admin_once = _slow_enroll  # type: ignore[method-assign]
    clearing = asyncio.create_task(mgr.read_meter("xai-proxy"))
    await asyncio.sleep(0.1)
    started = asyncio.get_running_loop().time()
    await meter.begin("sbx-1", "run-B", "job-1")
    took = asyncio.get_running_loop().time() - started
    await clearing
    await asyncio.gather(*getattr(mgr, "_remainder_tasks", ()))
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    assert took < 1.5, f"the declaration took {took:.1f}s while the remainder went out"


async def test_one_proxy_s_remainder_never_holds_another_s(proxy: Any) -> None:
    mgr = proxy.mgr
    started: list[str] = []
    gate = asyncio.Event()

    async def _send_remainder(name: str) -> None:
        started.append(name)
        await gate.wait()

    mgr._send_remainder = _send_remainder  # type: ignore[method-assign]
    mgr._replay_abandoned.update({"xai-proxy", "llm-proxy"})
    mgr._resume_replay_soon("xai-proxy")
    mgr._resume_replay_soon("xai-proxy")
    mgr._resume_replay_soon("llm-proxy")
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(*getattr(mgr, "_remainder_tasks", ()))
    mgr._replay_abandoned.clear()
    assert sorted(started) == ["llm-proxy", "xai-proxy"], f"remainders started: {started}"


async def test_every_request_that_changes_a_proxy_s_state_is_numbered(proxy: Any) -> None:
    """A request without a number is taken unordered: every one the orchestrator sends that changes what a proxy holds
    carries one, a read does not."""
    mgr = proxy.mgr
    mgr.git_proxy_url = ""
    sent: list[tuple[str, str, Any]] = []
    real_once = mgr._exec_admin_once

    async def _recording(**kw: Any) -> dict[str, Any]:
        sent.append((kw["method"], kw["path"], (kw["body"] or {}).get("seq")))
        return await real_once(**kw)

    mgr._exec_admin_once = _recording  # type: ignore[method-assign]
    await mgr._exec_admin_request(  # as push_xai_token sends it (to the proxy's fixed port)
        container="xai-proxy",
        method="POST",
        path="/admin/token",
        port=proxy.port,
        body={"credential_id": "cred-1", "access_token": "at-2"},
    )
    await mgr.declare_run("xai-proxy", "sbx-1", "run-1")
    await mgr.read_meter("xai-proxy")
    await mgr.end_run("xai-proxy", "run-1")
    await mgr.unenroll_sandbox("sbx-1")
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    unnumbered = [(m, p) for m, p, seq in sent if m != "GET" and not isinstance(seq, int)]
    assert not unnumbered, f"sent without a number: {unnumbered}"
    assert [m for m, _, seq in sent if m == "GET" and seq is not None] == [], "a read was numbered"
    assert {m for m, _, _ in sent} == {"POST", "GET", "DELETE"}, sent
    assert manager_module._ordered("PUT", "/admin/git-push-branches/sbx-1"), "a change of push branches is unordered"


async def test_a_remainder_is_not_queued_behind_a_replay_under_way(proxy: Any) -> None:
    """A request answered while a replay holds the lock starts nothing: were that replay to give up, a queued remainder
    would go at once to a proxy that has just stopped answering."""
    mgr = proxy.mgr
    lock = mgr._replay_locks.setdefault("xai-proxy", asyncio.Lock())
    await lock.acquire()
    mgr._replay_abandoned.add("xai-proxy")
    try:
        await mgr.read_meter("xai-proxy")
        assert not mgr._remainder_tasks, "a remainder was queued behind the replay under way"
    finally:
        lock.release()
        mgr._replay_abandoned.discard("xai-proxy")


async def test_a_cancelled_run_s_end_never_has_the_next_run_s_declaration_refused(proxy: Any) -> None:
    """The review's probe: a replay holds the lock; run A's background end and run B's declaration both wait for it,
    and A's end reaches the proxy first. A's end was numbered when A ended, before B's declaration, so B is taken and
    charged its own call (it was refused, and B charged its sandbox's figure)."""
    mgr = proxy.mgr
    meter = RunMeter(mgr, _Repo())
    window_a = await meter.begin("sbx-1", "run-A", "job-1")
    lock = mgr._replay_locks.setdefault("xai-proxy", asyncio.Lock())
    await lock.acquire()  # a replay under way
    end_a = asyncio.create_task(meter.end(window_a))
    await asyncio.sleep(0.05)
    real_once = mgr._exec_admin_once

    async def _b_lands_last(**kw: Any) -> dict[str, Any]:
        if kw["path"] == "/admin/runs" and kw["method"] == "POST":
            await asyncio.sleep(0.5)
        return await real_once(**kw)

    mgr._exec_admin_once = _b_lands_last  # type: ignore[method-assign]
    begin_b = asyncio.create_task(meter.begin("sbx-1", "run-B", "job-1"))
    await asyncio.sleep(0.05)
    lock.release()
    metering_a, window_b = await asyncio.gather(end_a, begin_b)
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    assert "xai-proxy" in window_b.declared, "the next run's declaration was refused as older than the end"
    assert metering_a.unreached == (), "the cancelled run's own end was lost"
    await _call_to(proxy)
    charge = charge_run(await meter.end(window_b), _record(), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0))
    assert (charge.source, charge.cost_usd) == ("proxy", pytest.approx(ONE_CALL))


async def test_an_end_is_numbered_before_it_waits_for_a_replay(proxy: Any) -> None:
    mgr = proxy.mgr
    await mgr.declare_run("xai-proxy", "sbx-1", "run-1")
    lock = mgr._replay_locks.setdefault("xai-proxy", asyncio.Lock())
    await lock.acquire()
    sent: list[int] = []
    real_once = mgr._exec_admin_once

    async def _record(**kw: Any) -> dict[str, Any]:
        if kw["method"] == "DELETE":
            sent.append(kw["body"]["seq"])
        return await real_once(**kw)

    mgr._exec_admin_once = _record  # type: ignore[method-assign]
    ending = asyncio.create_task(mgr.end_run("xai-proxy", "run-1"))
    await asyncio.sleep(0.05)
    decided_after = mgr._next_seq()  # anything decided while the end waits
    lock.release()
    await ending
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    assert sent and sent[0] < decided_after, f"the end was numbered after its wait: {sent} vs {decided_after}"


async def test_a_token_fetched_earlier_but_pushed_later_never_replaces_a_newer_one(
    proxy: Any, monkeypatch: Any
) -> None:
    """Two pushes for one credential, each numbered when its value was fetched: the newer value goes first. The older
    one is refused, and its push is done, not an error: the proxy and what is held for a recreated proxy keep the newer
    value."""
    mgr = proxy.mgr
    real = mgr._exec_admin_request

    async def _to_the_test_proxy(**kw: Any) -> dict[str, Any]:
        return await real(**{**kw, "port": proxy.port})  # push_xai_token sends to the proxy's fixed port

    monkeypatch.setattr(mgr, "_exec_admin_request", _to_the_test_proxy)
    older, newer = mgr._next_seq(), mgr._next_seq()
    await mgr.push_xai_token("cred-1", "at-newer", "2030-01-01T00:00:00Z", seq=newer)
    await mgr.push_xai_token("cred-1", "at-older", "2030-01-01T00:00:00Z", seq=older)
    assert proxy.app[TOKENS]["cred-1"]["access_token"] == "at-newer", "an older token replaced a newer one"
    assert mgr._admin_state["xai-proxy"]["token:cred-1"][2]["access_token"] == "at-newer"


async def test_what_is_held_never_goes_back_to_an_older_request(proxy: Any) -> None:
    """Answers come back in any order: the older request's answer, handled last, does not replace or drop the newer
    one's entry, so a recreated proxy is given what this one took last."""
    mgr = proxy.mgr
    port = proxy.port
    enroll = {"sandbox_id": "sbx-9", "sandbox_token": TOKEN}
    mgr._remember("xai-proxy", "POST", "/admin/enroll", port, {**enroll, "seq": 20})
    mgr._remember("xai-proxy", "POST", "/admin/enroll", port, {**enroll, "sandbox_token": "older", "seq": 10})
    assert mgr._admin_state["xai-proxy"]["enroll:sbx-9"][2]["seq"] == 20, "an older enrollment replaced a newer one"
    mgr._remember("xai-proxy", "DELETE", "/admin/enroll/sbx-9", port, {"seq": 15})
    assert "enroll:sbx-9" in mgr._admin_state["xai-proxy"], "an older unenroll dropped a newer enrollment"
    token = {"credential_id": "cred-9", "access_token": "at-new", "seq": 20}
    mgr._remember("xai-proxy", "POST", "/admin/token", port, token)
    mgr._remember("xai-proxy", "POST", "/admin/token", port, {**token, "access_token": "at-old", "seq": 10})
    assert mgr._admin_state["xai-proxy"]["token:cred-9"][2]["access_token"] == "at-new"
    run = {"sandbox_id": "sbx-9", "run_id": "run-9", "seq": 20}
    mgr._remember("xai-proxy", "POST", "/admin/runs", port, run)
    mgr._remember("xai-proxy", "POST", "/admin/runs", port, {**run, "job_id": "older", "seq": 10})
    assert "job_id" not in mgr._admin_state["xai-proxy"]["run:run-9"][2]
    mgr._remember("xai-proxy", "DELETE", "/admin/enroll/sbx-9", port, {"seq": 30})
    assert "enroll:sbx-9" not in mgr._admin_state["xai-proxy"], "a newer unenroll was not taken"


@pytest.mark.usefixtures("scrubber_snapshot")  # the test tokens are taken out of the log scrubber after
async def test_the_token_fetched_last_stays_at_the_proxy_whichever_push_is_sent_last(
    proxy: Any, monkeypatch: Any
) -> None:
    """The review's second item, end to end. The initial push's batch fetched cred-a's token, then waits on cred-b. A
    stale-token hit meanwhile force-refreshes cred-a and pushes the newer token. The batch's push of the older one is
    sent last; the proxy keeps the newer."""
    from embry0.api.app import _apply_xai_proxy_health, _push_initial_xai_token
    from embry0.execution.xai_credential import XaiRefresherRegistry
    from tests.unit.execution.test_xai_refresher_registry import _cipher, _row, _Secrets
    from tests.unit.execution.test_xai_refresher_registry import _Health as _Quarantine
    from tests.unit.execution.test_xai_refresher_registry import _Repo as _Credentials

    mgr = proxy.mgr
    real = mgr._exec_admin_request

    async def _to_the_test_proxy(**kw: Any) -> dict[str, Any]:
        return await real(**{**kw, "port": proxy.port})  # push_xai_token sends to the proxy's fixed port

    monkeypatch.setattr(mgr, "_exec_admin_request", _to_the_test_proxy)
    repo = _Credentials([_row("cred-a"), _row("cred-b")], {"cred-a": _cipher("rt-a"), "cred-b": _cipher("rt-b")})
    registry = XaiRefresherRegistry(repo, _Secrets(), health=_Quarantine())
    gate = asyncio.Event()

    class _Refresher:
        def __init__(self, tokens: list[str], wait: bool = False) -> None:
            self.tokens, self.wait = tokens, wait
            self._access_expiry, self._clock = 3600.0, lambda: 0.0

        async def ensure_fresh(self, *, force: bool = False) -> str:
            if self.wait:
                await gate.wait()
            return self.tokens.pop(0)

    registry._refreshers["cred-a"] = _Refresher(["at-a-older", "at-a-newer"])
    registry._refreshers["cred-b"] = _Refresher(["at-b"], wait=True)
    initial = asyncio.create_task(_push_initial_xai_token(registry, mgr, attempts=1, delay_seconds=0))
    await asyncio.sleep(0.01)  # the batch holds cred-a's token and waits on cred-b
    await _apply_xai_proxy_health({"credentials": {"cred-a": {"stale": 1}}}, registry, mgr, _Quarantine())
    assert proxy.app[TOKENS]["cred-a"]["access_token"] == "at-a-newer"
    gate.set()
    await initial
    assert proxy.app[TOKENS]["cred-a"]["access_token"] == "at-a-newer", "the token fetched first replaced the later one"
    assert mgr._admin_state["xai-proxy"]["token:cred-a"][2]["access_token"] == "at-a-newer"


async def test_a_run_declared_on_its_provider_is_held_to_it_at_the_proxy(proxy: Any) -> None:
    """ADR 0033: the declaration names the run's provider, the proxy echoes it, and a recreated proxy is given the run
    back on it."""
    meter = RunMeter(proxy.mgr, _Repo())
    try:
        window = await meter.begin("sbx-1", "run-1", "job-1", provider="xai")  # type: ignore[call-arg]
    except TypeError as exc:
        pytest.fail(f"a run cannot be declared on its provider: {exc}")
    assert window.declared, "the run was not declared"
    held_to = getattr(proxy.app[METER], "declared_provider", lambda _sandbox: None)("sbx-1")
    assert held_to == "xai", "the proxy does not hold the run to its provider"
    assert proxy.mgr.declared_unordered("xai-proxy", "run-1") is False, "an echoed provider left the run not whole"
    held = proxy.mgr._admin_state["xai-proxy"]["run:run-1"][2]
    assert held.get("provider") == "xai", f"a recreated proxy would be given the run back on no provider: {held}"


async def test_a_proxy_that_does_not_echo_the_provider_leaves_the_run_not_whole(proxy: Any, monkeypatch) -> None:
    """An image from before runs held to their provider counts another provider's calls under the run too."""
    original = proxy.mgr._exec_admin_request

    async def older_image(**kwargs: Any) -> dict[str, Any]:
        answer = dict(await original(**kwargs))
        answer.pop("provider", None)
        return answer

    monkeypatch.setattr(proxy.mgr, "_exec_admin_request", older_image)
    try:
        await proxy.mgr.declare_run("xai-proxy", "sbx-1", "run-1", provider="xai")  # type: ignore[call-arg]
    except TypeError as exc:
        pytest.fail(f"a run cannot be declared on its provider: {exc}")
    assert proxy.mgr.declared_unordered("xai-proxy", "run-1"), "a run its proxy did not hold to its provider is whole"


_PURSE = {"key": "job:job-1", "limit": "job", "remaining_usd": 10.0, "members": []}
_BUDGET = {"rates": {"in": 2.0, "out": 6.0}, "cache": {"read": 0.25, "write": 1.5}, "purses": [_PURSE]}


async def _declared_with_budget(mgr: Any, run_id: str = "run-1", sandbox: str = "sbx-1", **purse: Any) -> None:
    budget = {**_BUDGET, "purses": [{**_PURSE, **purse}]}
    try:
        await mgr.declare_run("xai-proxy", sandbox, run_id, "job-1", provider="xai", budget=budget)
    except (TypeError, RuntimeError) as exc:
        pytest.fail(f"a run cannot be declared with its purse: {exc}")


def _left(server: Any) -> float:
    purse = server.app[METER].snapshot()["purses"].get("job:job-1")
    assert purse is not None, "the proxy holds no purse for the job"
    return float(purse["left_usd"])


async def _a_call_in_flight(server: Any, run_sandbox: str = "sbx-1") -> None:
    """1,000,000 output tokens at its ceiling: $6 at $6 a million, held at the proxy."""
    from embry0.execution.proxy.metering import read_call

    body = b'{"model": "grok-4.7", "max_tokens": 1000000, "stream": true, "messages": []}'
    server.app[METER].open(run_sandbox, read_call("anthropic", body))


async def test_a_run_is_declared_at_the_proxy_with_its_purse(proxy: Any) -> None:
    await _declared_with_budget(proxy.mgr)
    assert _left(proxy) == pytest.approx(10.0)


async def test_a_recreated_proxy_holds_a_purse_to_what_its_runs_could_not_have_spent_before(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """The new process counts the run from nothing: given back its whole purse, the run could spend it twice."""
    mgr = proxy.mgr
    await _declared_with_budget(mgr)
    await _a_call_in_flight(proxy)
    await mgr.read_meter("xai-proxy")  # the orchestrator's read every 5 seconds
    spent = proxy.app[METER].snapshot()["tallies"][0]["budget"]["spent_usd"]
    assert spent == pytest.approx(6.0, abs=0.01), spent
    new = await _recreated(aiohttp_server, proxy, monkeypatch)
    mgr._carry("xai-proxy")  # the recreation, as _revive does it
    restored, failed = await mgr._replay("xai-proxy")
    assert failed == 0
    assert _left(new) == pytest.approx(10.0 - spent), (
        f"the recreated proxy holds the purse to ${_left(new):.2f}, though the run could have spent ${spent:.2f}"
    )
    # A later figure from the ledger (worked out from settled runs alone) is carried off the same way.
    await mgr.update_purses("xai-proxy", [{**_PURSE, "remaining_usd": 8.0, "members": ["run-1"]}])
    assert _left(new) == pytest.approx(8.0 - spent), f"a later figure was given whole: ${_left(new):.2f}"


async def test_a_recreated_proxy_with_no_read_of_the_run_gives_it_nothing_more(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """What the run spent at the process that went away is not known: fail closed."""
    mgr = proxy.mgr
    await _declared_with_budget(mgr)
    await _a_call_in_flight(proxy)
    new = await _recreated(aiohttp_server, proxy, monkeypatch)
    mgr._carry("xai-proxy")
    await mgr._replay("xai-proxy")
    assert _left(new) == 0.0, f"a run never read before the recreation was given ${_left(new):.2f} more"


async def test_a_run_ended_but_not_settled_is_carried_off_its_purse_after_a_recreation(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """Its last tally, at its end, says it could have spent $6 there; it counts in the purse until it settles."""
    from embry0.execution.proxy.metering import Usage, _ModelTally

    mgr = proxy.mgr
    await _declared_with_budget(mgr, run_id="run-1")
    proxy.app[METER]._runs["run-1"].models["grok-4.7"] = _ModelTally(usage=Usage(output_tokens=1_000_000), requests=1)
    assert await mgr.end_run("xai-proxy", "run-1") is not None
    await _declared_with_budget(mgr, run_id="run-2", members=["run-1"])
    await mgr.read_meter("xai-proxy")  # run-2 read, having spent nothing
    new = await _recreated(aiohttp_server, proxy, monkeypatch)
    mgr._carry("xai-proxy")
    await mgr._replay("xai-proxy")
    assert _left(new) == pytest.approx(4.0), f"the ended run's $6 was let go: ${_left(new):.2f} left"


async def test_a_run_declared_after_a_recreation_draws_on_its_purse_carried_off(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    mgr = proxy.mgr
    await _declared_with_budget(mgr)
    await _a_call_in_flight(proxy)
    await mgr.read_meter("xai-proxy")
    new = await _recreated(aiohttp_server, proxy, monkeypatch)
    mgr._carry("xai-proxy")
    await mgr._replay("xai-proxy")
    enrolled = await mgr._exec_admin_request(
        container="xai-proxy",
        method="POST",
        path="/admin/enroll",
        port=new.port,
        body={"sandbox_id": "sbx-2", "sandbox_token": "another-sandbox-bearer-of-32-chars"},
    )
    assert enrolled is not None
    await _declared_with_budget(mgr, run_id="run-2", sandbox="sbx-2", members=["run-1"])
    assert _left(new) == pytest.approx(4.0, abs=0.01), f"the new run was given the purse whole: ${_left(new):.2f}"


async def test_a_proxy_is_carried_off_when_it_is_recreated_before_it_is_given_its_state_back() -> None:
    order: list[str] = []
    mgr = ProxyManager(_LocalExec(), proxy_admin_token=ADMIN)  # type: ignore[arg-type]
    mgr._launch_specs["xai-proxy"] = ({}, False)

    async def _not_running(name: str) -> bool:
        return False

    async def _removed(names: Any, **_kw: Any) -> None:
        order.append("remove")

    async def _launched(**_kw: Any) -> None:
        order.append("launch")

    async def _replayed(name: str) -> tuple[int, int]:
        order.append("replay")
        return 0, 0

    mgr._is_running = _not_running  # type: ignore[method-assign]
    mgr._remove_proxies = _removed  # type: ignore[method-assign]
    mgr._launch = _launched  # type: ignore[method-assign]
    mgr._replay = _replayed  # type: ignore[method-assign]
    carry = getattr(mgr, "_carry", None)
    assert carry is not None, "the manager carries nothing off a recreated proxy"
    mgr._carry = lambda name: (order.append("carry"), carry(name))[1]  # type: ignore[method-assign]
    assert await mgr._revive("xai-proxy") is True
    assert "carry" in order and order.index("carry") < order.index("replay"), order


async def test_a_recreated_proxy_is_given_the_latest_figure_not_the_declared_one(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """A run settled since its sibling's declaration left $3 in the purse; the replay must not restore the $10."""
    mgr = proxy.mgr
    await _declared_with_budget(mgr)
    await mgr.update_purses("xai-proxy", [{**_PURSE, "remaining_usd": 3.0, "members": ["run-1"]}])
    await mgr.read_meter("xai-proxy")  # run-1 read, having spent nothing
    new = await _recreated(aiohttp_server, proxy, monkeypatch)
    mgr._carry("xai-proxy")
    await mgr._replay("xai-proxy")
    assert _left(new) == pytest.approx(3.0), f"the replay restored the declared figure: ${_left(new):.2f}"
