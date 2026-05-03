"""Tests for DinD certs + extra_networks + dind /etc/hosts injection in SandboxManager.

When SandboxProfile.dind_enabled=True, SandboxManager.create() must:
  - Bind-mount /certs/client RO at /certs/client (DinD-side filesystem path,
    NOT a named volume — DinD interprets volume sources in its own namespace).
  - Set DOCKER_HOST / DOCKER_TLS_VERIFY / DOCKER_CERT_PATH env vars.
  - Connect the sandbox container to each profile.extra_networks entry.
  - Inject --add-host=dind:<orch-resolved-ip> so the sandbox can reach
    `tcp://dind:2376` from inside DinD's network namespace (Phase 1.5).

When dind_enabled=False, none of the above should happen.

We don't actually launch containers — we mock DockerClient and inspect
the kwargs passed to build_run_cmd plus the post-create network calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from athanor.execution.sandbox_manager import SandboxManager


def _slim_profile() -> dict[str, Any]:
    return {
        "base_image": "athanor-sandbox:latest",
        "memory": "8g",
        "cpus": "4",
        "pids_limit": 256,
        "cap_drop": ["ALL"],
        "cap_add": [],
        "security_opt": ["no-new-privileges"],
        "agent_timeout_seconds": 300,
        "container_timeout_seconds": 3600,
        "dind_enabled": False,
        "idle_timeout_seconds": 600,
        "extra_networks": [],
        "env_defaults": {},
    }


def _qa_jvm_profile() -> dict[str, Any]:
    p = _slim_profile()
    p.update(
        {
            "base_image": "athanor-sandbox-qa:latest",
            "dind_enabled": True,
            "extra_networks": ["backend"],
        }
    )
    return p


def _make_docker_mock() -> MagicMock:
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-id-xyz")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])
    docker.build_network_cmd = MagicMock(
        side_effect=lambda action, network, container: [
            "docker",
            "network",
            action,
            network,
            container,
        ]
    )
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "..."])
    return docker


def _make_proxy_manager() -> MagicMock:
    proxy_mgr = MagicMock()
    proxy_mgr.enroll_sandbox = AsyncMock(return_value="sandbox-token-xyz")
    proxy_mgr.unenroll_sandbox = AsyncMock()
    return proxy_mgr


@pytest.mark.asyncio
async def test_dind_enabled_mounts_certs_and_sets_env() -> None:
    """qa-jvm profile (dind_enabled=True): certs mounted, DOCKER_HOST set,
    --add-host=dind:<ip> injected so the sandbox can reach tcp://dind:2376."""
    docker = _make_docker_mock()
    proxy_mgr = _make_proxy_manager()
    mgr = SandboxManager(docker=docker, proxy_manager=proxy_mgr)

    with patch(
        "athanor.execution.sandbox_manager.socket.gethostbyname",
        return_value="172.24.0.3",
    ):
        container_id, token = await mgr.create(
            job_id="job-qa-1",
            profile=_qa_jvm_profile(),
            env={"USER_VAR": "x"},
        )

    assert container_id == "container-id-xyz"
    assert token == "sandbox-token-xyz"

    # Inspect kwargs passed to build_run_cmd.
    docker.build_run_cmd.assert_called_once()
    kwargs = docker.build_run_cmd.call_args.kwargs

    # Volume: bind-mount of /certs/client RO at /certs/client. Must NOT be a
    # named volume ("dind-certs-client") — DinD would silently create an empty
    # volume of that name in its own namespace and the sandbox would fail TLS.
    volumes = kwargs.get("volumes", []) or []
    assert "/certs/client:/certs/client:ro" in volumes, (
        f"DinD certs not bind-mounted RO; got volumes={volumes}"
    )
    assert not any("dind-certs-client" in v for v in volumes), (
        f"DinD certs must be a bind-mount, not a named volume; got volumes={volumes}"
    )

    # Env: DOCKER_HOST + DOCKER_TLS_VERIFY + DOCKER_CERT_PATH must be present.
    env_passed = kwargs.get("env", {}) or {}
    assert env_passed.get("DOCKER_HOST") == "tcp://dind:2376", env_passed
    assert env_passed.get("DOCKER_TLS_VERIFY") == "1", env_passed
    assert env_passed.get("DOCKER_CERT_PATH") == "/certs/client", env_passed
    # Caller-provided env still passes through.
    assert env_passed.get("USER_VAR") == "x", env_passed

    # extra_hosts must include dind → resolved IP. Without this the sandbox
    # cannot resolve `dind` from inside DinD's network namespace and
    # tcp://dind:2376 is unreachable. Phase 1.5.
    extra_hosts = kwargs.get("extra_hosts", {}) or {}
    assert extra_hosts.get("dind") == "172.24.0.3", extra_hosts

    # Backend network must be connected post-create.
    docker.build_network_cmd.assert_any_call("connect", "backend", "container-id-xyz")


@pytest.mark.asyncio
async def test_dind_disabled_does_not_mount_certs() -> None:
    """slim profile (dind_enabled=False): no certs mount, no DOCKER_HOST, no extra nets."""
    docker = _make_docker_mock()
    proxy_mgr = _make_proxy_manager()
    mgr = SandboxManager(docker=docker, proxy_manager=proxy_mgr)

    await mgr.create(job_id="job-slim-1", profile=_slim_profile(), env={})

    docker.build_run_cmd.assert_called_once()
    kwargs = docker.build_run_cmd.call_args.kwargs

    volumes = kwargs.get("volumes", []) or []
    assert not any("/certs/client" in v for v in volumes), (
        f"Certs volume unexpectedly mounted; got volumes={volumes}"
    )

    env_passed = kwargs.get("env", {}) or {}
    assert "DOCKER_HOST" not in env_passed, env_passed
    assert "DOCKER_TLS_VERIFY" not in env_passed, env_passed
    assert "DOCKER_CERT_PATH" not in env_passed, env_passed

    # No extra_networks => build_network_cmd should not have been called for "connect".
    connect_calls = [
        call
        for call in docker.build_network_cmd.call_args_list
        if call.args and call.args[0] == "connect"
    ]
    assert connect_calls == [], (
        f"No extra networks should be connected for slim profile; got: {connect_calls}"
    )


@pytest.mark.asyncio
async def test_dind_enabled_with_no_profile_falls_back_to_default() -> None:
    """When profile is None, defaults should NOT enable DinD (slim is the default)."""
    docker = _make_docker_mock()
    proxy_mgr = _make_proxy_manager()
    mgr = SandboxManager(docker=docker, proxy_manager=proxy_mgr)

    await mgr.create(job_id="job-default")

    kwargs = docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}
    assert "DOCKER_HOST" not in env_passed, env_passed
    volumes = kwargs.get("volumes", []) or []
    assert not any("/certs/client" in v for v in volumes)


@pytest.mark.asyncio
async def test_dind_enabled_multiple_extra_networks() -> None:
    """All extra_networks entries must be connected, in order."""
    docker = _make_docker_mock()
    proxy_mgr = _make_proxy_manager()
    mgr = SandboxManager(docker=docker, proxy_manager=proxy_mgr)

    profile = _qa_jvm_profile()
    profile["extra_networks"] = ["backend", "metrics"]

    with patch(
        "athanor.execution.sandbox_manager.socket.gethostbyname",
        return_value="172.24.0.3",
    ):
        await mgr.create(job_id="job-multi-net", profile=profile)

    connect_args = [
        call.args
        for call in docker.build_network_cmd.call_args_list
        if call.args and call.args[0] == "connect"
    ]
    assert ("connect", "backend", "container-id-xyz") in connect_args
    assert ("connect", "metrics", "container-id-xyz") in connect_args


@pytest.mark.asyncio
async def test_dind_disabled_does_not_inject_dind_host() -> None:
    """slim profile (dind_enabled=False): no extra_hosts entry for dind."""
    docker = _make_docker_mock()
    proxy_mgr = _make_proxy_manager()
    mgr = SandboxManager(docker=docker, proxy_manager=proxy_mgr)

    await mgr.create(job_id="job-slim-no-dind", profile=_slim_profile(), env={})

    kwargs = docker.build_run_cmd.call_args.kwargs
    extra_hosts = kwargs.get("extra_hosts") or {}
    assert "dind" not in extra_hosts, extra_hosts


@pytest.mark.asyncio
async def test_dind_enabled_resolution_failure_raises() -> None:
    """If `dind` cannot be resolved by the orchestrator (misconfig), refuse to
    create the sandbox loudly rather than silently producing a broken one."""
    docker = _make_docker_mock()
    proxy_mgr = _make_proxy_manager()
    mgr = SandboxManager(docker=docker, proxy_manager=proxy_mgr)

    with patch(
        "athanor.execution.sandbox_manager.socket.gethostbyname",
        side_effect=OSError("name does not resolve"),
    ):
        with pytest.raises(RuntimeError, match="Cannot resolve `dind`"):
            await mgr.create(job_id="job-resolution-fail", profile=_qa_jvm_profile())
