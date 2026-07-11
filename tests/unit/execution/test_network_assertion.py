"""Unit tests for DockerClient.assert_sandbox_networks_or_die."""

import json
from unittest.mock import AsyncMock

import pytest

from embry0.execution.docker_client import DockerClient


@pytest.fixture
def docker_with_inspect():
    """DockerClient whose run_cmd returns JSON we control per call."""
    docker = DockerClient()
    docker.run_cmd = AsyncMock()
    return docker


def _net_json(name: str, *, masq: str | None) -> str:
    opts = {} if masq is None else {"com.docker.network.bridge.enable_ip_masquerade": masq}
    return json.dumps([{"Name": name, "Options": opts}])


@pytest.mark.asyncio
async def test_passes_when_both_networks_correct(docker_with_inspect):
    docker_with_inspect.run_cmd.side_effect = [
        _net_json("sandbox-restricted", masq="false"),
        _net_json("sandbox-internet", masq=None),
    ]
    await docker_with_inspect.assert_sandbox_networks_or_die()  # no raise


@pytest.mark.asyncio
async def test_fails_when_restricted_missing(docker_with_inspect):
    docker_with_inspect.run_cmd.side_effect = [
        RuntimeError("Error: No such network: sandbox-restricted"),
    ]
    with pytest.raises(RuntimeError, match="sandbox-restricted network missing"):
        await docker_with_inspect.assert_sandbox_networks_or_die()


@pytest.mark.asyncio
async def test_fails_when_masquerade_enabled(docker_with_inspect):
    docker_with_inspect.run_cmd.side_effect = [
        _net_json("sandbox-restricted", masq="true"),
    ]
    with pytest.raises(RuntimeError, match="enable_ip_masquerade is 'true'"):
        await docker_with_inspect.assert_sandbox_networks_or_die()


@pytest.mark.asyncio
async def test_fails_when_masquerade_unset(docker_with_inspect):
    docker_with_inspect.run_cmd.side_effect = [
        _net_json("sandbox-restricted", masq=None),
    ]
    with pytest.raises(RuntimeError, match="enable_ip_masquerade is None"):
        await docker_with_inspect.assert_sandbox_networks_or_die()


@pytest.mark.asyncio
async def test_fails_when_internet_missing(docker_with_inspect):
    docker_with_inspect.run_cmd.side_effect = [
        _net_json("sandbox-restricted", masq="false"),
        RuntimeError("Error: No such network: sandbox-internet"),
    ]
    with pytest.raises(RuntimeError, match="sandbox-internet network missing"):
        await docker_with_inspect.assert_sandbox_networks_or_die()


@pytest.mark.asyncio
async def test_fails_when_options_null(docker_with_inspect):
    docker_with_inspect.run_cmd.side_effect = [
        json.dumps([{"Name": "sandbox-restricted", "Options": None}]),
    ]
    with pytest.raises(RuntimeError, match="enable_ip_masquerade is None"):
        await docker_with_inspect.assert_sandbox_networks_or_die()
