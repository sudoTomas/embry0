"""EMB-29 egress-allowlist enforcement unit tests.

Covers allowlist derivation/resolution, the iptables script shapes shipped
to the DinD-netns helper, and the SandboxManager fail-closed/teardown
wiring. The DockerClient is mocked — rule semantics are asserted on the
generated commands (mirroring test_network_assertion.py's approach).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.runtime.egress import (
    BLOCKED_PRIVATE_RANGES,
    EGRESS_CHAIN,
    LOG_PREFIX,
    EgressEnforcer,
    derive_deployed_egress_allowlist,
    resolve_allowlist_to_ips,
)
from tests.unit.execution.fake_iptables import _chain_docker

# ---------------------------------------------------------------------------
# allowlist derivation + resolution
# ---------------------------------------------------------------------------


def test_derive_declares_lan_targets_only():
    """Only PRIVATE destinations need declaring — public egress stays open."""
    out = derive_deployed_egress_allowlist(
        frontend_url="https://app.qa.test/",
        extra_hosts={
            "app.qa.test": "192.168.1.10",
            "hub.qa.test": "192.168.1.10",
        },
    )
    assert out == ["192.168.1.10"]  # deduped, no public hosts


def test_derive_public_frontend_yields_no_declarations():
    """A CDN-fronted public frontend needs nothing declared (its rotating
    public IPs are unaffected by the private-range blocklist)."""
    out = derive_deployed_egress_allowlist(
        frontend_url="https://definitely-not-a-real-host.invalid/login",
        extra_hosts={},
    )
    assert out == []


def test_derive_private_ip_frontend_is_declared():
    out = derive_deployed_egress_allowlist(
        frontend_url="http://192.168.1.10/",
        extra_hosts={},
    )
    assert out == ["192.168.1.10"]


def test_resolve_passes_ips_and_cidrs_through():
    assert resolve_allowlist_to_ips(["192.168.1.10", "10.0.0.0/24", ""]) == [
        "192.168.1.10",
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
async def test_apply_builds_private_range_blocklist():
    docker = _docker()
    enforcer = EgressEnforcer(docker)
    await enforcer.apply("172.20.0.5", ["192.168.1.10", "10.1.2.3"], proxy_ips=["172.20.0.2"])

    script = _helper_script(docker)
    # The sandbox's own rules are keyed on its source IP: its declared LAN
    # exceptions, then the jump to the shared chain.
    assert "-s 172.20.0.5 -d 192.168.1.10/32 -j RETURN" in script
    assert "-s 172.20.0.5 -d 10.1.2.3/32 -j RETURN" in script
    assert f"-s 172.20.0.5 -j {EGRESS_CHAIN}" in script
    # The shared chain: replies, the proxies, then LOG + DROP per private range.
    for blocked in BLOCKED_PRIVATE_RANGES:
        assert f"-A {EGRESS_CHAIN} -d {blocked} -j DROP" in script
    assert f"-A {EGRESS_CHAIN} -d 172.20.0.2/32 -j RETURN" in script
    assert "-s 172.20.0.5 -j DROP" not in script  # public egress stays open
    assert LOG_PREFIX in script
    assert "--ctstate ESTABLISHED,RELATED" in script
    # The chain is written and checked before the jump to it exists.
    assert script.index("iptables-restore") < script.index(f"-j {EGRESS_CHAIN}\n")


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
    assert "iptables -S DOCKER-USER" in script
    assert "172.20.0.5/32" in script
    assert "iptables -D DOCKER-USER" in script


@pytest.mark.asyncio
async def test_clear_leaves_rules_of_an_address_it_prefixes(tmp_path):
    """Clearing x.y.z.8 must not touch the rules of x.y.z.80 (#566)."""
    docker, table = _chain_docker(tmp_path)
    rules = table.chain
    enforcer = EgressEnforcer(docker)
    await enforcer.apply("10.77.0.8", [])
    await enforcer.apply("10.77.0.80", [])
    # A third sandbox whose DESTINATION allow contains the cleared address.
    await enforcer.apply("10.77.0.9", ["10.77.0.8"])
    per_sandbox = 1  # the jump to the shared chain
    assert sum("-s 10.77.0.8/32 " in r for r in rules()) == per_sandbox, rules()

    await enforcer.clear("10.77.0.8")
    after = rules()

    assert not any("-s 10.77.0.8/32 " in r for r in after), f"clear left rules of 10.77.0.8: {after}"
    kept_80 = sum("-s 10.77.0.80/32 " in r for r in after)
    assert kept_80 == per_sandbox, f"clear(10.77.0.8) removed rules of 10.77.0.80: {kept_80} of {per_sandbox} left"
    kept_9 = sum("-s 10.77.0.9/32 " in r for r in after)
    assert kept_9 == per_sandbox + 1, (
        f"clear(10.77.0.8) removed rules of 10.77.0.9, whose allow targets 10.77.0.8: {kept_9} of {per_sandbox + 1}"
    )


@pytest.mark.asyncio
async def test_clear_on_an_empty_chain_is_a_no_op(tmp_path):
    docker, table = _chain_docker(tmp_path)
    await EgressEnforcer(docker).clear("10.77.0.8")
    assert table.chain() == []


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


@pytest.mark.asyncio
async def test_a_fresh_daemon_without_the_helper_image_logs_no_error():
    """#655: on a fresh host the helper image does not exist yet, so the probe's
    "No such image" is the normal first-run answer, not an error to heal."""
    from unittest.mock import patch

    from structlog.testing import capture_logs

    from embry0.execution.docker_client import DockerClient

    def _proc(rc: int, err: bytes) -> SimpleNamespace:
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        stdout.feed_eof()
        stderr.feed_data(err)
        stderr.feed_eof()

        async def wait() -> int:
            return rc

        return SimpleNamespace(stdout=stdout, stderr=stderr, stdin=None, returncode=rc, pid=4242, wait=wait)

    probe, build = _proc(1, b"Error response from daemon: No such image: x\n"), _proc(0, b"")
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=[probe, build]):
        with capture_logs() as logs:
            await EgressEnforcer(DockerClient()).ensure_helper_image()

    errors = [entry for entry in logs if entry["log_level"] == "error"]
    assert not errors, f"an absent helper image must not log at error: {errors}"


# ---------------------------------------------------------------------------
# the shared proxy chain (#568), on the fake filter table
# ---------------------------------------------------------------------------

SANDBOX = "172.20.0.9"
LAN_TARGET = "192.168.1.10"
SUBNET = "172.20.0.0/16"


@pytest.mark.asyncio
async def test_a_sandbox_reaches_its_targets_and_the_proxies_and_no_other_private_address(tmp_path):
    docker, table = _chain_docker(tmp_path)
    await EgressEnforcer(docker).apply(SANDBOX, [LAN_TARGET], proxy_ips=["172.20.0.2"])

    assert table.verdict(SANDBOX, "172.20.0.2") == "pass", "the sandbox cannot reach the proxy"
    assert table.verdict(SANDBOX, LAN_TARGET) == "pass", "the sandbox cannot reach its declared target"
    assert table.verdict(SANDBOX, "93.184.216.34") == "pass", "public egress is closed"
    assert table.verdict(SANDBOX, "172.20.0.3") == "drop", "the sandbox reaches another sandbox"
    assert table.verdict(SANDBOX, "192.168.1.11") == "drop", "the sandbox reaches the LAN"
    assert table.verdict(SANDBOX, "172.20.0.3", established=True) == "pass", "replies are dropped"
    assert table.verdict("172.20.0.3", "172.20.0.4") == "pass", "a container without rules was restricted"


@pytest.mark.asyncio
async def test_applying_twice_replaces_the_block(tmp_path):
    docker, table = _chain_docker(tmp_path)
    enforcer = EgressEnforcer(docker)
    await enforcer.apply(SANDBOX, [LAN_TARGET], proxy_ips=["172.20.0.2"])
    await enforcer.apply(SANDBOX, [], proxy_ips=["172.20.0.2"])

    rules = [r for r in table.chain() if f"-s {SANDBOX}/32 " in r]
    assert rules == [f"-A DOCKER-USER -s {SANDBOX}/32 -j {EGRESS_CHAIN}"], rules
    assert table.verdict(SANDBOX, LAN_TARGET) == "drop", "the earlier block's exception survived"


@pytest.mark.asyncio
async def test_refresh_moves_every_sandbox_to_the_proxies_running_now(tmp_path):
    """The proxies were recreated at new addresses; a sandbox from before must follow them."""
    docker, table = _chain_docker(tmp_path)
    enforcer = EgressEnforcer(docker)
    await enforcer.apply(SANDBOX, [LAN_TARGET], proxy_ips=["172.20.0.2"])

    converted = await enforcer.refresh(["172.20.0.7"], [SUBNET])

    assert converted == []
    assert table.verdict(SANDBOX, "172.20.0.7") == "pass", "the sandbox cannot reach the proxy at its new address"
    assert table.verdict(SANDBOX, "172.20.0.2") == "drop", (
        "the sandbox still reaches the proxy's old address, which Docker may give to a new sandbox"
    )
    assert table.verdict(SANDBOX, LAN_TARGET) == "pass", "the refresh dropped the sandbox's declared target"


def _pre_chain_block(source: str, returns: list[str]) -> list[str]:
    """DOCKER-USER rules as the per-sandbox egress of an earlier release wrote them."""
    src = f"-A DOCKER-USER -s {source}/32"
    rules = [f"{src} -d {d}/32 -j RETURN" for d in returns]
    rules.append(f"{src} -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN")
    rules += [f'{src} -d {r} -m limit --limit 6/min -j LOG --log-prefix "{LOG_PREFIX}"' for r in BLOCKED_PRIVATE_RANGES]
    rules += [f"{src} -d {r} -j DROP" for r in BLOCKED_PRIVATE_RANGES]
    return rules


@pytest.mark.asyncio
async def test_refresh_converts_a_sandbox_from_before_the_shared_chain(tmp_path):
    docker, table = _chain_docker(tmp_path)
    table.seed(
        {
            "DOCKER-USER": [
                *_pre_chain_block(SANDBOX, ["172.20.0.2", LAN_TARGET]),
                "-A DOCKER-USER -s 10.9.9.9/32 -d 10.0.0.1/32 -j RETURN",  # not embry0's
                "-A DOCKER-USER -j RETURN",
            ]
        }
    )

    converted = await EgressEnforcer(docker).refresh(["172.20.0.7"], [SUBNET])

    assert converted == [SANDBOX]
    rules = [r for r in table.chain() if f"-s {SANDBOX}/32 " in r]
    assert rules == [
        f"-A DOCKER-USER -s {SANDBOX}/32 -d {LAN_TARGET}/32 -j RETURN",
        f"-A DOCKER-USER -s {SANDBOX}/32 -j {EGRESS_CHAIN}",
    ], rules
    assert table.verdict(SANDBOX, "172.20.0.7") == "pass", "the converted sandbox cannot reach the proxy"
    assert table.verdict(SANDBOX, "172.20.0.2") == "drop", "the converted sandbox kept the old proxy exception"
    assert table.verdict(SANDBOX, LAN_TARGET) == "pass", "the conversion lost the declared target"
    assert "-A DOCKER-USER -s 10.9.9.9/32 -d 10.0.0.1/32 -j RETURN" in table.chain(), "a foreign rule was removed"


@pytest.mark.asyncio
async def test_no_jump_is_written_to_a_chain_that_is_not_in_place(tmp_path):
    """An empty chain allows everything, so the jump to it waits for the check."""
    docker, table = _chain_docker(tmp_path, restore_broken=True)
    table.seed({"DOCKER-USER": [], EGRESS_CHAIN: []})

    with pytest.raises(RuntimeError, match="not in place"):
        await EgressEnforcer(docker).apply(SANDBOX, [], proxy_ips=["172.20.0.2"])

    assert table.chain() == [], f"a jump was written to an empty chain: {table.chain()}"
