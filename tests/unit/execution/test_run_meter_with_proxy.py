"""The orchestrator's meter against a real metering proxy: ``ProxyManager``'s admin transport (its own script, run
here as a local process in place of ``docker exec``) talks to the xai-proxy app, and a run's calls are charged from
what the proxy counted (embry0.execution.run_meter, embry0.agents.run_charge)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from embry0.agents.invocation import ProviderRecord
from embry0.agents.run_charge import charge_run
from embry0.execution.proxy import manager as manager_module
from embry0.execution.proxy.manager import ProxyManager
from embry0.execution.proxy.metering import METER
from embry0.execution.proxy.xai_proxy import create_xai_proxy_app
from embry0.execution.run_meter import RunMeter
from tests.unit.execution.proxy.test_metered_proxies import _SSE, _Answer, _Upstream
from tests.unit.execution.proxy.upstream_stub import route_upstream
from tests.unit.execution.test_run_meter import _Repo

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
async def proxy(aiohttp_server, monkeypatch) -> Any:
    server = await aiohttp_server(create_xai_proxy_app(ADMIN))
    monkeypatch.setitem(manager_module.METERED_PROXIES, "xai-proxy", (server.port, "xai"))
    monkeypatch.setitem(manager_module.METERED_PROXIES, "local-llm-proxy", (1, "custom"))
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
    return server


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
    assert mgr.metered_proxies() == ["xai-proxy"], "the local-llm-proxy was not launched"
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


async def test_a_declaration_waits_for_a_replay_no_longer_than_the_bound_and_its_run_is_charged_at_least_its_sandbox_s(
    proxy: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(manager_module, "REPLAY_WAIT_SECONDS", 0.2, raising=False)
    mgr = proxy.mgr
    meter = RunMeter(mgr, _Repo())
    slow = await _slow_replay(mgr)
    try:
        started = asyncio.get_running_loop().time()
        window = await meter.begin("sbx-1", "run-1", "job-1")
        assert asyncio.get_running_loop().time() - started < 5, "the run waited for the replay without bound"
        assert window.declared, "the run was not declared once the wait ran out"
        await _sandbox_call(proxy)
        started = asyncio.get_running_loop().time()
        metering = await meter.end(window)
        assert asyncio.get_running_loop().time() - started < 5, "the end waited for the replay without bound"
    finally:
        slow.cancel()
    cost = (1000 * 2.0 + 100 * 6.0 + 400 * 2.0 * 0.25) / 1e6
    charge = charge_run(metering, _record(), sandbox_cost_usd=cost * 3, sandbox_tokens=(3000, 300, 1200, 0))
    assert charge.metering["gap"] is True, "a run declared out of order was taken as whole"
    assert charge.cost_usd >= cost * 3, "charged less than its sandbox reported"
    assert not mgr.declared_unordered("xai-proxy", "run-1"), "the mark outlived the run"


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

            async def _deliver() -> None:
                await asyncio.sleep(0.5)
                await real_once(**kw)

            box["late"] = asyncio.create_task(_deliver())
            raise TimeoutError("docker command timed out after 15s")
        return await real_once(**kw)

    return _once


async def test_a_run_declared_after_an_abandoned_replay_is_not_taken_as_whole(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """The review's late run: the replay gave up on run A's declaration, which still reached the proxy, after the
    sandbox's next run B was declared, and took the sandbox. B is charged at least what its sandbox reported."""
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
    await box["late"]
    await _call_to(new)
    charge = charge_run(await meter.end(window_b), _record(), sandbox_cost_usd=ONE_CALL, sandbox_tokens=(1, 1, 0, 0))
    assert charge.metering["gap"] is True, "a run declared after a given-up replay was taken as whole"
    assert charge.cost_usd >= ONE_CALL, f"B was charged {charge.cost_usd}, less than its sandbox reported"


async def test_a_given_up_enrollment_that_lands_after_the_sandbox_s_next_run_leaves_that_run_not_whole(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """The review's abandoned case: the replay gave up on the enrollment; the sandbox was enrolled again and its next
    run declared; the old enrollment then landed and ended that run on the proxy."""
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
    await box["late"]
    await _call_to(new)
    charge = charge_run(await meter.end(window_b), _record(), sandbox_cost_usd=ONE_CALL, sandbox_tokens=(1, 1, 0, 0))
    assert charge.metering["gap"] is True, "a run declared after a given-up replay was taken as whole"
    assert charge.cost_usd >= ONE_CALL


async def test_a_completed_replay_makes_the_proxy_s_runs_whole_again(proxy: Any) -> None:
    mgr = proxy.mgr

    async def _hung(**kw: Any) -> dict[str, Any]:
        raise TimeoutError

    real_once, mgr._exec_admin_once = mgr._exec_admin_once, _hung  # type: ignore[method-assign]
    await mgr._replay("xai-proxy")
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    assert await mgr._after_replay("xai-proxy") is False, "a proxy whose replay was given up counts as waited out"
    await mgr._replay("xai-proxy")
    assert await mgr._after_replay("xai-proxy") is True, "a completed replay left the proxy's runs not whole"


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


async def test_runs_declared_out_of_order_are_told_from_runs_whose_tally_was_lost(proxy: Any) -> None:
    """While a given-up replay may still land, a healthy run is charged ``*_unordered``; a run whose end is lost is a
    gap like any other, and its trace says which."""
    mgr = proxy.mgr
    meter = RunMeter(mgr, _Repo())
    await _give_up_replay(mgr, lambda kw: kw["path"] == "/admin/token")
    assert mgr.replay_abandoned("xai-proxy")
    healthy = await meter.begin("sbx-1", "run-1", "job-1")
    await _sandbox_call(proxy)
    metering = await meter.end(healthy)
    assert (metering.unordered, metering.unreached) == (("xai-proxy",), ()), "an ordered failure was recorded"
    charge = charge_run(metering, _record(), sandbox_cost_usd=0.0, sandbox_tokens=(0, 0, 0, 0))
    assert charge.source == "proxy_unordered" and charge.metering["unordered"] == ["xai-proxy"]
    lost = await meter.begin("sbx-1", "run-2", "job-1")
    real_once = mgr._exec_admin_once

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


async def test_the_mode_ends_with_the_first_answer_to_a_request_sent_late_enough(proxy: Any, monkeypatch: Any) -> None:
    """A request of the given-up replay can arrive no later than LATE_REQUEST_SECONDS after it; an answer to one sent
    after that means nothing of the replay is on its way. A proxy that does not answer stays in the mode."""
    monkeypatch.setattr(manager_module, "LATE_REQUEST_SECONDS", 0.3, raising=False)
    mgr = proxy.mgr
    await _give_up_replay(mgr, lambda kw: kw["path"] == "/admin/token")
    await mgr.read_meter("xai-proxy")
    assert mgr.replay_abandoned("xai-proxy"), "the mode ended before a late request could no longer arrive"
    await asyncio.sleep(0.35)
    real_once = mgr._exec_admin_once

    async def _stuck(**kw: Any) -> dict[str, Any]:
        raise TimeoutError

    mgr._exec_admin_once = _stuck  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        await mgr.read_meter("xai-proxy")
    assert mgr.replay_abandoned("xai-proxy"), "a proxy that answered nothing left the mode"
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    await mgr.read_meter("xai-proxy")
    assert not mgr.replay_abandoned("xai-proxy"), "the mode outlasted every late request"
    assert await mgr._after_replay("xai-proxy") is True


async def test_what_a_given_up_replay_did_not_send_is_sent_when_the_mode_ends(
    proxy: Any, aiohttp_server: Any, monkeypatch: Any
) -> None:
    """Else the sandboxes it did not reach are refused until they enroll again or the proxy is recreated. What was
    done since (a token pushed again) is not sent a second time."""
    monkeypatch.setattr(manager_module, "LATE_REQUEST_SECONDS", 0.2, raising=False)
    mgr = proxy.mgr
    await mgr.declare_run("xai-proxy", "sbx-1", "run-1")
    new = await _recreated(aiohttp_server, proxy, monkeypatch)
    assert await _give_up_replay(mgr, lambda kw: kw["path"] == "/admin/enroll") == ["/admin/enroll"]
    port = manager_module.METERED_PROXIES["xai-proxy"][0]
    await mgr._exec_admin_request(
        container="xai-proxy", method="POST", path="/admin/token", port=port, body={"access_token": "at-2"}
    )
    await asyncio.sleep(0.25)
    sent: list[str] = []
    real_once = mgr._exec_admin_once

    async def _recording(**kw: Any) -> dict[str, Any]:
        sent.append(f"{kw['method']} {kw['path']}")
        return await real_once(**kw)

    mgr._exec_admin_once = _recording  # type: ignore[method-assign]
    await mgr.read_meter("xai-proxy")
    mgr._exec_admin_once = real_once  # type: ignore[method-assign]
    assert sent == ["GET /admin/meter", "POST /admin/enroll", "POST /admin/runs"], sent
    assert not mgr.replay_abandoned("xai-proxy")
    assert new.app[METER]._current["sbx-1"] == "run-1", "the sandbox's run was not given back"
    await _call_to(new)
    assert new.app[METER].snapshot()["tallies"][0]["models"], "the sandbox's call was refused"
