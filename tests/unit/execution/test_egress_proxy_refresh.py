"""#568: a sandbox that outlives a restart follows the proxies' new addresses.

A paused sandbox keeps its egress rules across an orchestrator restart, and
the restart recreates the proxies, possibly at new ``sandbox-internet``
addresses. Resuming it must leave it able to reach the proxies where they
are now and unable to reach their old addresses, which Docker may already
have given to another sandbox. The rules run on the fake filter table of
test_egress.py, through the real DockerRuntime and SandboxManager.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.runtime import DockerRuntime
from embry0.execution.runtime.rootless import RootlessDockerRuntime
from embry0.execution.sandbox_manager import SandboxManager
from embry0.services.credentials.resolver import ResolvedCredential
from tests.unit.execution.fake_iptables import _chain_docker

SANDBOX = "172.20.0.9"
LAN_TARGET = "192.168.1.10"
OLD_PROXY, NEW_PROXY = "172.20.0.2", "172.20.0.7"


def _daemon(tmp_path, addresses: dict[str, str]):
    """A docker client whose helper runs hit the fake table and whose inspects read ``addresses``."""
    docker, table = _chain_docker(tmp_path)
    run_helper = docker.run_cmd.side_effect

    async def run_cmd(cmd, timeout=30, **_kw):
        if cmd and cmd[0] == "inspect-running":
            return "true"
        if "--entrypoint" in cmd:
            return await run_helper(cmd, timeout)
        if cmd[-3:-1] == ["image", "inspect"] or "exec" in cmd:
            return ""
        if "inspect" in cmd and "--format" in cmd:
            return addresses.get(cmd[-1], "")
        raise AssertionError(f"unexpected docker command {cmd}")

    async def inspect_network(name):
        assert name == "sandbox-internet"
        return {
            "Name": name,
            "IPAM": {"Config": [{"Subnet": "172.20.0.0/16"}]},
            "Containers": {
                f"id-{c}": {"Name": c, "IPv4Address": f"{ip}/16"} for c, ip in addresses.items() if c.endswith("-proxy")
            },
        }

    docker.run_cmd = AsyncMock(side_effect=run_cmd)
    docker.inspect_network = AsyncMock(side_effect=inspect_network)
    docker.build_exec_cmd = lambda container, cmd, **_kw: ["exec", container, *cmd]
    docker.build_inspect_running_cmd = lambda container: ["inspect-running", container]
    return docker, table


def _proxy_manager() -> MagicMock:
    pm = MagicMock()
    pm.enroll_sandbox = AsyncMock(return_value="tok-new")
    pm.launched_proxies = MagicMock(return_value=["git-proxy"])
    pm.internet_proxies = MagicMock(return_value=["git-proxy"])
    pm.git_proxy_url = ""
    pm.xai_proxy_url = ""
    return pm


@pytest.fixture(autouse=True)
def _no_isolation_chain(monkeypatch):
    """The sandbox-restricted chain is tested elsewhere; here only the egress rules run."""
    from embry0.execution.runtime import isolation as isolation_mod

    fake = MagicMock()
    fake.ensure = AsyncMock(return_value=["10.88.0.2"])
    monkeypatch.setattr(isolation_mod, "SandboxIsolation", MagicMock(return_value=fake))


@pytest.mark.parametrize("runtime_cls", [DockerRuntime, RootlessDockerRuntime], ids=["dind", "rootless"])
async def test_a_resumed_sandbox_reaches_the_recreated_proxy_and_not_its_old_address(tmp_path, runtime_cls):
    addresses = {"cid-1": SANDBOX, "git-proxy": OLD_PROXY}
    docker, table = _daemon(tmp_path, addresses)
    runtime = runtime_cls(docker)
    # Created before the restart, with the proxy at its old address.
    await runtime.attach_policy("cid-1", allowed_destinations=[LAN_TARGET], reachable_containers=["git-proxy"])
    assert table.verdict(SANDBOX, OLD_PROXY) == "pass", "precondition: the sandbox reaches the proxy"

    # The restart recreated the proxy elsewhere; the job resumes.
    addresses["git-proxy"] = NEW_PROXY
    resolver = MagicMock()
    resolver.resolve_forge = AsyncMock(
        return_value=ResolvedCredential(
            id="cred-gh",
            kind="forge_token",
            provider="github",
            host="github.com",
            label="bot",
            secret="tok-reg",
            git_author_name="Bot",
            git_author_email="bot@example.com",
            source="registry",
            trace=(),
        )
    )
    mgr = SandboxManager(docker=docker, runtime=runtime, proxy_manager=_proxy_manager(), credential_resolver=resolver)
    await mgr.reenroll(
        "cid-1", job_id="job-1", repo="acme/widgets", forge="github", xai_credential_id=None, context_scope=None
    )

    assert table.verdict(SANDBOX, NEW_PROXY) == "pass", "the resumed sandbox cannot reach the proxy at its new address"
    assert table.verdict(SANDBOX, OLD_PROXY) == "drop", (
        "the resumed sandbox still reaches the proxy's old address, which Docker may give to another sandbox"
    )
    assert table.verdict(SANDBOX, LAN_TARGET) == "pass", "the resumed sandbox lost its declared target"
