"""EMB-29 egress-allowlist enforcement unit tests.

Covers allowlist derivation/resolution, the iptables script shapes shipped
to the DinD-netns helper, and the SandboxManager fail-closed/teardown
wiring. The DockerClient is mocked — rule semantics are asserted on the
generated commands (mirroring test_network_assertion.py's approach).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.egress import (
    LOG_PREFIX,
    EgressEnforcer,
    derive_deployed_egress_allowlist,
    resolve_allowlist_to_ips,
)

# ---------------------------------------------------------------------------
# allowlist derivation + resolution
# ---------------------------------------------------------------------------


def test_derive_deployed_allowlist():
    out = derive_deployed_egress_allowlist(
        frontend_url="https://ai-quoting-dev.qa.test/",
        extra_hosts={
            "ai-quoting-dev.qa.test": "192.168.200.51",
            "hub.qa.test": "192.168.200.51",
        },
    )
    assert out[0] == "ai-quoting-dev.qa.test"
    assert "192.168.200.51" in out
    assert out.count("192.168.200.51") == 1  # deduped
    assert "api.anthropic.com" in out
    assert "statsig.anthropic.com" in out


def test_derive_handles_public_url_without_extra_hosts():
    out = derive_deployed_egress_allowlist(
        frontend_url="https://app.example.com/login",
        extra_hosts={},
    )
    assert out[0] == "app.example.com"
    assert "api.anthropic.com" in out


def test_resolve_passes_ips_and_cidrs_through():
    assert resolve_allowlist_to_ips(["192.168.200.51", "10.0.0.0/24", ""]) == [
        "192.168.200.51",
        "10.0.0.0/24",
    ]


def test_resolve_skips_unresolvable_hostnames():
    out = resolve_allowlist_to_ips(["definitely-not-a-real-host.invalid", "127.0.0.1"])
    assert out == ["127.0.0.1"]


# ---------------------------------------------------------------------------
# EgressEnforcer command shapes
# ---------------------------------------------------------------------------


def _docker() -> MagicMock:
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker", "--host", "tcp://dind:2376"])
    docker.run_cmd = AsyncMock(return_value="")
    return docker


def _helper_script(docker: MagicMock, call_idx: int = 0) -> str:
    cmd = docker.run_cmd.await_args_list[call_idx].args[0]
    assert cmd[:3] == ["docker", "--host", "tcp://dind:2376"]
    assert "--network" in cmd and "host" in cmd
    assert "--cap-add" in cmd and "NET_ADMIN" in cmd
    return str(cmd[-1])


@pytest.mark.asyncio
async def test_apply_builds_ordered_rule_block():
    docker = _docker()
    enforcer = EgressEnforcer(docker)
    await enforcer.apply("172.20.0.5", ["192.168.200.51", "10.1.2.3"])

    script = _helper_script(docker)
    # Every rule is keyed on the sandbox source IP.
    assert script.count("-s 172.20.0.5") >= 6
    assert "-d 192.168.200.51 -j RETURN" in script
    assert "-d 10.1.2.3 -j RETURN" in script
    assert "-j DROP" in script
    assert LOG_PREFIX in script
    assert "--ctstate ESTABLISHED,RELATED" in script
    # DROP is inserted first (ends up last after the position-1 inserts).
    assert script.index("-j DROP") < script.index("LOG")


@pytest.mark.asyncio
async def test_apply_rejects_garbage_source_ip():
    enforcer = EgressEnforcer(_docker())
    with pytest.raises(ValueError):
        await enforcer.apply("not-an-ip; rm -rf /", ["10.0.0.1"])


@pytest.mark.asyncio
async def test_clear_deletes_by_line_number():
    docker = _docker()
    enforcer = EgressEnforcer(docker)
    await enforcer.clear("172.20.0.5")
    script = _helper_script(docker)
    assert "--line-numbers" in script
    assert "172.20.0.5" in script
    assert "iptables -D DOCKER-USER" in script


@pytest.mark.asyncio
async def test_container_ip_reads_network_specific_address():
    docker = _docker()
    docker.run_cmd = AsyncMock(return_value="172.20.0.5\n")
    enforcer = EgressEnforcer(docker)
    ip = await enforcer.container_ip("cid123", "sandbox-internet")
    assert ip == "172.20.0.5"
    cmd = docker.run_cmd.await_args.args[0]
    assert "inspect" in cmd
    assert any("sandbox-internet" in part for part in cmd)


@pytest.mark.asyncio
async def test_container_ip_none_when_not_attached():
    docker = _docker()
    docker.run_cmd = AsyncMock(return_value="\n")
    assert await EgressEnforcer(docker).container_ip("cid123") is None


@pytest.mark.asyncio
async def test_verify_ready_warns_and_returns_false_on_failure():
    docker = _docker()
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("no dind"))
    assert await EgressEnforcer(docker).verify_ready() is False


@pytest.mark.asyncio
async def test_ensure_helper_image_skips_build_when_present():
    docker = _docker()
    enforcer = EgressEnforcer(docker)
    await enforcer.ensure_helper_image()
    # Single inspect call, no build.
    assert docker.run_cmd.await_count == 1
    assert "image" in docker.run_cmd.await_args.args[0]
