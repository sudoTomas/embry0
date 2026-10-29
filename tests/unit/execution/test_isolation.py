"""The sandbox-restricted isolation chain (#554): what it allows, and when it refuses.

What the chain does to real traffic is proven on a Docker daemon by
tests/integration/test_sandbox_isolation_dind.py; these pin the rules, the
helper script's guards and ``ensure``'s refusals without one.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.runtime.isolation import (
    CHAIN,
    LOG_PREFIX,
    IsolationError,
    SandboxIsolation,
    bridge_interface,
    build_ensure_script,
    chain_rules,
    proxy_addresses,
)
from embry0.safety.error_codes import ErrorCode, classify_failure

_NETWORK_ID = "0123456789ab" + "c" * 52


def _network(containers: dict[str, str] | None = None, **extra: Any) -> dict[str, Any]:
    """``docker network inspect`` output: container name -> address."""
    eps = {f"id-{name}": {"Name": name, "IPv4Address": f"{ip}/16"} for name, ip in (containers or {}).items()}
    return {"Name": "sandbox-restricted", "Id": _NETWORK_ID, "Options": {}, "Containers": eps, **extra}


def _isolation(network: dict[str, Any] | Exception) -> tuple[SandboxIsolation, MagicMock, MagicMock]:
    docker = MagicMock()
    if isinstance(network, Exception):
        docker.inspect_network = AsyncMock(side_effect=network)
    else:
        docker.inspect_network = AsyncMock(return_value=network)
    helper = MagicMock()
    helper.ensure_helper_image = AsyncMock()
    helper._run_in_dind_netns = AsyncMock(return_value="")
    return SandboxIsolation(docker, helper=helper), docker, helper


# --- the bridge --------------------------------------------------------------


def test_the_bridge_is_named_after_the_network_id() -> None:
    assert bridge_interface(_network()) == "br-0123456789ab"


def test_a_network_that_names_its_bridge_keeps_that_name() -> None:
    net = _network(Options={"com.docker.network.bridge.name": "sbx-restricted"})
    assert bridge_interface(net) == "sbx-restricted"


@pytest.mark.parametrize(
    "net",
    [
        {"Name": "sandbox-restricted", "Id": "", "Options": {}},
        {"Name": "sandbox-restricted", "Id": "short", "Options": {}},
        {"Name": "sandbox-restricted", "Id": _NETWORK_ID, "Options": {"com.docker.network.bridge.name": "br0; reboot"}},
        {"Name": "sandbox-restricted", "Id": _NETWORK_ID, "Options": {"com.docker.network.bridge.name": "x" * 16}},
        {"Name": "sandbox-restricted", "Id": _NETWORK_ID, "Options": {"com.docker.network.bridge.name": "br0\n"}},
    ],
)
def test_an_unusable_bridge_name_is_refused(net: dict[str, Any]) -> None:
    with pytest.raises(IsolationError, match="bridge interface"):
        bridge_interface(net)


# --- the proxies -------------------------------------------------------------


def test_only_the_named_proxies_are_allowed_never_another_container() -> None:
    net = _network(
        {
            "git-proxy": "10.88.0.2",
            "sandbox-job-1": "10.88.0.9",
            "minio-proxy": "10.88.0.10",
        }
    )
    assert proxy_addresses(net, ["git-proxy", "minio-proxy", "context-proxy"]) == ["10.88.0.2", "10.88.0.10"], (
        "the allowed set must be the named proxies on the network, numerically sorted, and no sandbox"
    )


def test_a_proxy_without_an_address_is_an_error_not_a_skip() -> None:
    net = {"Containers": {"x": {"Name": "git-proxy", "IPv4Address": ""}}}
    with pytest.raises(IsolationError, match="git-proxy"):
        proxy_addresses(net, ["git-proxy"])


# --- the rules and the script ------------------------------------------------


def test_the_chain_lets_replies_and_proxies_through_and_drops_the_rest() -> None:
    rules = chain_rules(["10.88.0.2", "10.88.0.3"])
    assert rules == [
        f"-A {CHAIN} -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN",
        f"-A {CHAIN} -d 10.88.0.2/32 -j RETURN",
        f"-A {CHAIN} -d 10.88.0.3/32 -j RETURN",
        f'-A {CHAIN} -m limit --limit 6/min -j LOG --log-prefix "{LOG_PREFIX}"',
        f"-A {CHAIN} -j DROP",
    ]


def test_a_proxy_address_is_validated_before_it_reaches_a_rule() -> None:
    with pytest.raises(ValueError):
        chain_rules(["10.88.0.2 -j ACCEPT"])


def test_the_script_checks_the_kernel_and_the_bridge_before_it_writes() -> None:
    script = build_ensure_script("br-0123456789ab", ["10.88.0.2"])
    write = script.index("iptables-restore --noflush")
    for guard in (
        "/proc/sys/net/bridge/bridge-nf-call-iptables",
        "ip link show dev br-0123456789ab",
        "iptables -C FORWARD -j DOCKER-USER",
    ):
        assert guard in script, f"guard missing: {guard}"
        assert script.index(guard) < write, f"guard runs after the write: {guard}"


def test_the_script_writes_the_chain_and_puts_the_jump_first_in_one_transaction() -> None:
    script = build_ensure_script("br-0123456789ab", ["10.88.0.2"])
    body = script[script.index("*filter") : script.index("COMMIT")]
    assert f":{CHAIN} - [0:0]" in body, "the chain is not declared, so it is not flushed"
    assert f"-A {CHAIN} -d 10.88.0.2/32 -j RETURN" in body
    assert f"-I DOCKER-USER 1 -i br-0123456789ab -o br-0123456789ab -j {CHAIN}" in body
    # The rules travel in a quoted heredoc: the shell expands nothing in them.
    assert "<<'RULES'" in script


def test_the_script_moves_an_existing_jump_instead_of_adding_a_second() -> None:
    script = build_ensure_script("br-0123456789ab", ["10.88.0.2"])
    jump = f"DOCKER-USER -i br-0123456789ab -o br-0123456789ab -j {CHAIN}"
    assert f"if iptables -C {jump} 2>/dev/null; then MOVE='-D {jump}'; fi" in script


def test_the_script_verifies_the_jump_is_evaluated_and_the_chain_ends_in_drop() -> None:
    script = build_ensure_script("br-0123456789ab", ["10.88.0.2"])
    after = script[script.index("iptables-restore --noflush") :]
    assert '$0 == "-A DOCKER-USER -j RETURN"' in after, "the jump is not checked against Docker's closing RETURN"
    assert f'!= "-A {CHAIN} -j DROP"' in after


@pytest.mark.parametrize(
    ("bridge", "chain"),
    [
        ("br-x; reboot", CHAIN),
        ("br-0123456789ab", "chain; reboot"),
        ("br-0123456789ab\n", CHAIN),
        ("br-0123456789ab", CHAIN + "\n"),
    ],
)
def test_the_script_refuses_names_that_are_not_names(bridge: str, chain: str) -> None:
    with pytest.raises(IsolationError):
        build_ensure_script(bridge, ["10.88.0.2"], chain)


# --- ensure ------------------------------------------------------------------


async def test_ensure_writes_the_running_proxies() -> None:
    iso, docker, helper = _isolation(_network({"git-proxy": "10.88.0.2", "sandbox-job-1": "10.88.0.9"}))
    assert await iso.ensure(["git-proxy"]) == ["10.88.0.2"]
    docker.inspect_network.assert_awaited_once_with("sandbox-restricted")
    helper.ensure_helper_image.assert_awaited_once()
    script = helper._run_in_dind_netns.await_args.args[0]
    assert f"-A {CHAIN} -d 10.88.0.2/32 -j RETURN" in script
    assert "10.88.0.9" not in script, "a sandbox's address reached the allow list"


async def test_ensure_refuses_to_write_a_chain_without_a_proxy() -> None:
    iso, _, helper = _isolation(_network({"sandbox-job-1": "10.88.0.9"}))
    with pytest.raises(IsolationError, match="no proxy is running"):
        await iso.ensure(["git-proxy"])
    helper._run_in_dind_netns.assert_not_awaited()


async def test_ensure_refuses_an_empty_proxy_list() -> None:
    iso, _, helper = _isolation(_network({"git-proxy": "10.88.0.2"}))
    with pytest.raises(IsolationError, match="no proxy is running"):
        await iso.ensure([])
    helper._run_in_dind_netns.assert_not_awaited()


async def test_ensure_refuses_a_network_with_ipv6() -> None:
    iso, _, helper = _isolation(_network({"git-proxy": "10.88.0.2"}, EnableIPv6=True))
    with pytest.raises(IsolationError, match="IPv6"):
        await iso.ensure(["git-proxy"])
    helper._run_in_dind_netns.assert_not_awaited()


async def test_ensure_fails_when_the_network_cannot_be_read() -> None:
    iso, _, helper = _isolation(RuntimeError("no such network"))
    with pytest.raises(IsolationError, match="no such network"):
        await iso.ensure(["git-proxy"])
    helper._run_in_dind_netns.assert_not_awaited()


@pytest.mark.parametrize("failing", ["ensure_helper_image", "_run_in_dind_netns"])
async def test_ensure_fails_when_the_helper_fails(failing: str) -> None:
    iso, _, helper = _isolation(_network({"git-proxy": "10.88.0.2"}))
    setattr(helper, failing, AsyncMock(side_effect=RuntimeError("Docker command failed: bridge-nf-call-iptables")))
    with pytest.raises(IsolationError, match="bridge-nf-call-iptables"):
        await iso.ensure(["git-proxy"])


def test_an_isolation_failure_is_a_sandbox_init_failure() -> None:
    assert classify_failure(exc=IsolationError("x")) is ErrorCode.SANDBOX_INIT
