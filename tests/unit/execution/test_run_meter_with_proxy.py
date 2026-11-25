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
    await mgr.declare_run("xai-proxy", "sbx-1", "run-2")
    held = list(mgr._admin_state["xai-proxy"])
    assert held.index("run:run-1") < held.index("enroll:sbx-1"), "not the case under test: the run held first"
    sent: list[str] = []

    async def _record_once(**kw: Any) -> dict[str, Any]:
        sent.append(f"{kw['method']} {kw['path']} {kw['body'].get('run_id') or kw['body'].get('sandbox_id', '')}")
        return {}

    mgr._exec_admin_once = _record_once  # type: ignore[method-assign]
    restored, failed = await mgr._replay("xai-proxy")
    assert failed == 0
    enroll = sent.index("POST /admin/enroll sbx-1")
    assert all(sent.index(f"POST /admin/runs {run}") > enroll for run in ("run-1", "run-2")), sent


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
