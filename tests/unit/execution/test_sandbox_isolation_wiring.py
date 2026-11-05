"""SandboxManager applies the sandbox-restricted isolation chain (#554).

A sandbox on ``sandbox-restricted`` is handed to a job only once the chain
is written for the proxies running now, after its own egress rules; a
paused sandbox gets it again before it is re-enrolled. The chain itself is
tested in test_isolation.py and, on a real daemon, in
tests/integration/test_sandbox_isolation_dind.py.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.runtime.isolation import IsolationError
from embry0.execution.sandbox_manager import SandboxInitError, SandboxManager, SandboxReenrollError
from embry0.safety.error_codes import ErrorCode

# Autouse: the egress helper cannot run against the docker mock either, and
# neither can the egress-exception refresh a re-enrollment runs (#568).
from tests.unit.execution.test_sandbox_manager import (  # noqa: F401
    _stub_egress_unless_overridden,
    _stub_isolation_unless_overridden,
)

PROXIES = ["git-proxy", "minio-proxy", "presign-proxy"]


def _docker() -> MagicMock:
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-abc123")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])
    docker.build_rm_cmd = MagicMock(side_effect=lambda name: ["docker", "rm", "-f", name])
    docker.build_network_cmd = MagicMock(return_value=["docker", "network", "..."])
    docker.build_exec_cmd = MagicMock(side_effect=lambda container, cmd, **_kw: ["exec", container, *cmd])
    docker.build_inspect_running_cmd = MagicMock(return_value=["inspect-running"])
    return docker


def _proxy_manager() -> MagicMock:
    pm = MagicMock()
    pm.enroll_sandbox = AsyncMock(return_value="tok-abc123")
    pm.unenroll_sandbox = AsyncMock()
    pm.launched_proxies = MagicMock(return_value=list(PROXIES))
    pm.internet_proxies = MagicMock(return_value=[])
    pm.git_proxy_url = ""
    pm.xai_proxy_url = ""
    return pm


def _install_isolation(monkeypatch, ensure: AsyncMock) -> MagicMock:
    from embry0.execution.runtime import isolation as isolation_mod

    fake = MagicMock()
    fake.ensure = ensure
    cls = MagicMock(return_value=fake)
    monkeypatch.setattr(isolation_mod, "SandboxIsolation", cls)
    return cls


async def test_create_isolates_the_sandbox_after_its_egress_rules(monkeypatch):
    from embry0.execution.runtime import egress as egress_mod

    order: list[str] = []
    enforcer = MagicMock()
    enforcer.container_ip = AsyncMock(return_value="10.89.0.9")
    enforcer.ensure_helper_image = AsyncMock()
    enforcer.apply = AsyncMock(side_effect=lambda *a, **k: order.append("egress"))
    monkeypatch.setattr(egress_mod, "EgressEnforcer", MagicMock(return_value=enforcer))
    ensure = AsyncMock(side_effect=lambda proxies: order.append("isolation") or ["10.88.0.2"])
    _install_isolation(monkeypatch, ensure)
    pm = _proxy_manager()

    await SandboxManager(docker=_docker(), proxy_manager=pm).create(job_id="job-1")

    ensure.assert_awaited_once_with(PROXIES)
    assert order == ["egress", "isolation"], (
        f"the chain must be written after the sandbox's egress rules, so its jump is above them: {order}"
    )


async def test_create_refuses_a_sandbox_it_cannot_isolate(monkeypatch):
    from embry0.execution.runtime import egress as egress_mod

    enforcer = MagicMock()
    enforcer.container_ip = AsyncMock(return_value="10.89.0.9")
    enforcer.ensure_helper_image = AsyncMock()
    enforcer.apply = AsyncMock()
    enforcer.clear = AsyncMock()
    monkeypatch.setattr(egress_mod, "EgressEnforcer", MagicMock(return_value=enforcer))
    _install_isolation(monkeypatch, AsyncMock(side_effect=IsolationError("bridge-nf-call-iptables is not 1")))
    docker = _docker()
    pm = _proxy_manager()
    resolver = MagicMock()
    resolver.resolve_forge = AsyncMock(return_value=MagicMock(secret="tok", host="github.com"))
    mgr = SandboxManager(docker=docker, proxy_manager=pm, credential_resolver=resolver)

    with pytest.raises(SandboxInitError, match="Sandbox isolation failed: bridge-nf-call-iptables") as raised:
        await mgr.create(job_id="job-1", repo="acme/widgets")

    assert raised.value.error_code is ErrorCode.SANDBOX_INIT
    pm.unenroll_sandbox.assert_awaited_once_with("container-abc123")
    enforcer.apply.assert_awaited_once()
    # Its egress rules were installed before the chain failed; they go with it.
    enforcer.clear.assert_awaited_once_with("10.89.0.9")
    assert ["docker", "rm", "-f", "container-abc123"] in [c.args[0] for c in docker.run_cmd.await_args_list], (
        "the sandbox that could not be isolated was left running"
    )
    assert mgr.last_forge_credential("container-abc123") is None


async def test_a_sandbox_off_the_restricted_network_does_not_touch_the_chain(monkeypatch):
    ensure = AsyncMock(return_value=["10.88.0.2"])
    _install_isolation(monkeypatch, ensure)
    profile = {"default_network": "sandbox-internet", "extra_networks": []}

    await SandboxManager(docker=_docker(), proxy_manager=_proxy_manager()).create(job_id="job-1", profile=profile)

    ensure.assert_not_awaited()


async def test_a_profile_cannot_join_the_restricted_network_as_an_extra_network(monkeypatch):
    """The profile gate (#553) allows sandbox-internet only, so it refuses this before any chain is written."""
    ensure = AsyncMock(return_value=["10.88.0.2"])
    _install_isolation(monkeypatch, ensure)
    profile = {"default_network": "some-job-network", "extra_networks": ["sandbox-restricted"]}

    with pytest.raises(SandboxInitError) as caught:
        await SandboxManager(docker=_docker(), proxy_manager=_proxy_manager()).create(job_id="job-1", profile=profile)

    assert caught.value.error_code == ErrorCode.INCOHERENT_CONFIG, caught.value.error_code
    ensure.assert_not_awaited()


async def test_a_proxy_manager_without_the_list_asks_for_no_proxy(monkeypatch):
    """ensure() refuses an empty list, so a stand-in without it fails closed, not open."""
    ensure = AsyncMock(return_value=["10.88.0.2"])
    _install_isolation(monkeypatch, ensure)

    class _NoList:
        enroll_sandbox = AsyncMock(return_value="tok")
        unenroll_sandbox = AsyncMock()

    await SandboxManager(docker=_docker(), proxy_manager=_NoList()).create(job_id="job-1")

    ensure.assert_awaited_once_with([])


async def test_concurrent_rewrites_of_the_shared_chain_run_one_at_a_time(monkeypatch):
    in_flight = 0
    peak = 0

    async def _ensure(proxies):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return ["10.88.0.2"]

    _install_isolation(monkeypatch, AsyncMock(side_effect=_ensure))
    mgr = SandboxManager(docker=_docker(), proxy_manager=_proxy_manager())

    await asyncio.gather(*(mgr.ensure_isolation() for _ in range(4)))

    assert peak == 1, f"{peak} rewrites of the one chain ran at once"


async def test_reenroll_rewrites_the_chain_before_it_enrolls(monkeypatch):
    order: list[str] = []
    ensure = AsyncMock(side_effect=lambda proxies: order.append("isolation") or ["10.88.0.2"])
    _install_isolation(monkeypatch, ensure)
    pm = _proxy_manager()
    pm.enroll_sandbox = AsyncMock(side_effect=lambda *a, **k: order.append("enroll") or "tok-new")
    docker = _docker()
    docker.run_cmd = AsyncMock(return_value="true")

    await SandboxManager(docker=docker, proxy_manager=pm).reenroll(
        "cid-1", job_id="job-1", repo="acme/widgets", forge="github", xai_credential_id=None, context_scope=None
    )

    ensure.assert_awaited_once_with(PROXIES)
    assert order == ["isolation", "enroll"]


async def test_reenroll_rewrites_the_egress_exceptions_before_the_chain(monkeypatch):
    """#568: a sandbox that outlived a restart gets the proxies' new sandbox-internet addresses."""
    from embry0.execution.runtime import DockerRuntime

    order: list[str] = []
    refresh = AsyncMock(side_effect=lambda proxies: order.append("egress") or ["10.89.0.2"])
    monkeypatch.setattr(DockerRuntime, "refresh_egress", refresh)
    _install_isolation(monkeypatch, AsyncMock(side_effect=lambda proxies: order.append("isolation") or ["10.88.0.2"]))
    pm = _proxy_manager()
    pm.internet_proxies = MagicMock(return_value=["git-proxy", "minio-proxy"])
    pm.enroll_sandbox = AsyncMock(side_effect=lambda *a, **k: order.append("enroll") or "tok-new")
    docker = _docker()
    docker.run_cmd = AsyncMock(return_value="true")

    await SandboxManager(docker=docker, proxy_manager=pm).reenroll(
        "cid-1", job_id="job-1", repo="acme/widgets", forge="github", xai_credential_id=None, context_scope=None
    )

    refresh.assert_awaited_once_with(["git-proxy", "minio-proxy"])
    assert order == ["egress", "isolation", "enroll"], (
        f"egress exceptions, then the isolation chain (its jump above them), then the enrollment: {order}"
    )


async def test_reenroll_keeps_the_job_paused_when_the_chain_cannot_be_written(monkeypatch):
    _install_isolation(monkeypatch, AsyncMock(side_effect=IsolationError("no proxy is running")))
    pm = _proxy_manager()
    docker = _docker()
    docker.run_cmd = AsyncMock(return_value="true")

    with pytest.raises(SandboxReenrollError, match="proxy rules not in place: no proxy is running"):
        await SandboxManager(docker=docker, proxy_manager=pm).reenroll(
            "cid-1", job_id="job-1", repo="acme/widgets", forge="github", xai_credential_id=None, context_scope=None
        )

    pm.enroll_sandbox.assert_not_awaited()
