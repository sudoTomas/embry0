"""#522: a sandbox profile can ask for the shared Docker daemon; only the repo's admin grant gives it.

The daemon (DinD) also runs every credential proxy and every other job's
sandbox, so its client certs are root on the Docker host. Before #522 the
builtin ``qa-jvm`` profile alone handed them to whatever repo QA'd on it.

We do not launch containers: DockerClient is a mock and the assertions read
what ``build_run_cmd`` was asked to start.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.sandbox_manager import SandboxInitError, SandboxManager
from embry0.safety.error_codes import ErrorCode
from embry0.storage.seeds.sandbox_profiles_builtin import BUILTIN_SANDBOX_PROFILES

# Autouse: every sandbox on sandbox-restricted gets the isolation chain (#554),
# which the docker mock cannot write.
from tests.unit.execution.test_sandbox_manager import _stub_isolation_unless_overridden  # noqa: F401

GRANTED = "acme/widgets"
NOT_GRANTED = "acme/gadgets"


class _Registry:
    """Stand-in for ReposRepository.docker_access."""

    def __init__(self, *granted: str) -> None:
        self._granted = set(granted)
        self.asked: list[str] = []

    async def docker_access(self, repo: str) -> bool:
        self.asked.append(repo)
        return repo in self._granted


@pytest.fixture(autouse=True)
def _no_real_egress_or_dns(monkeypatch):
    """Profiles on sandbox-internet install egress rules; DinD profiles resolve `dind`.

    Neither can run here. Both are stubbed so that a sandbox that DOES get
    Docker reaches build_run_cmd, which is what these tests read.
    """
    from embry0.execution.runtime import egress as egress_mod

    fake = MagicMock()
    fake.container_ip = AsyncMock(return_value="172.20.0.9")
    fake.ensure_helper_image = AsyncMock()
    fake.apply = AsyncMock()
    monkeypatch.setattr(egress_mod, "EgressEnforcer", MagicMock(return_value=fake))
    monkeypatch.setattr(SandboxManager, "_resolve_dind_gateway_ip", staticmethod(lambda: "172.24.0.3"))


def _docker() -> MagicMock:
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-xyz")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "..."])
    docker.build_network_cmd = MagicMock(return_value=["docker", "network", "..."])
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    return docker


def _proxy() -> MagicMock:
    proxy = MagicMock()
    proxy.enroll_sandbox = AsyncMock(return_value="sandbox-token")
    proxy.unenroll_sandbox = AsyncMock()
    proxy.egress_exception_containers = MagicMock(return_value=[])
    return proxy


def _builtin(name: str) -> dict[str, Any]:
    """A builtin profile as profiles_repo.get returns it: the seed plus its name."""
    return {"name": name, "is_builtin": True, **BUILTIN_SANDBOX_PROFILES[name]}


def _docker_handed_over(docker: MagicMock) -> list[str]:
    """Every way the started sandbox could reach the daemon; empty when none."""
    found: list[str] = []
    for call in docker.build_run_cmd.call_args_list:
        env = call.kwargs.get("env") or {}
        volumes = call.kwargs.get("volumes") or []
        hosts = call.kwargs.get("extra_hosts") or {}
        found += [f"env {k}" for k in ("DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH") if k in env]
        found += [f"volume {v}" for v in volumes if "/certs/client" in v]
        found += ["extra_hosts dind"] if "dind" in hosts else []
    return found


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(BUILTIN_SANDBOX_PROFILES))
async def test_no_builtin_profile_alone_yields_the_docker_daemon(name: str) -> None:
    """A builtin profile, with no grant anywhere, never starts a sandbox holding the daemon.

    Built without a registry on purpose: that is exactly the pre-#522
    constructor, so this test runs unchanged against the old tree.
    """
    docker = _docker()
    mgr = SandboxManager(docker=docker, proxy_manager=_proxy())
    try:
        await mgr.create(job_id=f"job-{name}", profile=_builtin(name), repo=NOT_GRANTED)
    except SandboxInitError:
        pass  # refusing is fine; starting it with the daemon is not
    handed = _docker_handed_over(docker)
    assert handed == [], (
        f"builtin profile {name!r} gave an ungranted repo the shared Docker daemon on its own: {handed}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(n for n, p in BUILTIN_SANDBOX_PROFILES.items() if p["dind_enabled"]))
async def test_a_docker_profile_on_an_ungranted_repo_is_refused_before_anything_exists(name: str) -> None:
    docker = _docker()
    proxy = _proxy()
    registry = _Registry(GRANTED)
    mgr = SandboxManager(docker=docker, proxy_manager=proxy, docker_access_registry=registry)

    with pytest.raises(SandboxInitError) as info:
        await mgr.create(job_id="job-refused", profile=_builtin(name), repo=NOT_GRANTED)

    assert info.value.error_code == ErrorCode.INCOHERENT_CONFIG, info.value.error_code
    message = str(info.value)
    assert NOT_GRANTED in message and name in message, message
    # The refusal says what the grant would hand over, and who can give it.
    assert "credential proxies" in message and "admin" in message, message
    assert f"PUT /api/v1/repos/{NOT_GRANTED}/-/docker-access" in message, message
    assert registry.asked == [NOT_GRANTED]
    docker.run_cmd.assert_not_called()
    proxy.enroll_sandbox.assert_not_called()


@pytest.mark.asyncio
async def test_the_explicit_grant_hands_over_the_daemon() -> None:
    docker = _docker()
    mgr = SandboxManager(docker=docker, proxy_manager=_proxy(), docker_access_registry=_Registry(GRANTED))

    await mgr.create(job_id="job-granted", profile=_builtin("qa-jvm"), repo=GRANTED)

    kwargs = docker.build_run_cmd.call_args.kwargs
    assert kwargs["env"].get("DOCKER_HOST") == "tcp://dind:2376", kwargs["env"]
    assert kwargs["env"].get("DOCKER_CERT_PATH") == "/certs/client", kwargs["env"]
    assert "/certs/client:/certs/client:ro" in kwargs["volumes"], kwargs["volumes"]
    assert kwargs["extra_hosts"].get("dind") == "172.24.0.3", kwargs["extra_hosts"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(n for n, p in BUILTIN_SANDBOX_PROFILES.items() if not p["dind_enabled"]))
async def test_the_grant_does_not_add_docker_to_a_profile_that_did_not_ask(name: str) -> None:
    docker = _docker()
    mgr = SandboxManager(docker=docker, proxy_manager=_proxy(), docker_access_registry=_Registry(GRANTED))

    await mgr.create(job_id=f"job-{name}", profile=_builtin(name), repo=GRANTED)

    assert _docker_handed_over(docker) == [], f"{name!r} does not ask for Docker, yet the sandbox got it"


@pytest.mark.asyncio
async def test_no_registry_wired_grants_nobody() -> None:
    """A manager built without the registry fails closed — even for a repo an admin granted."""
    docker = _docker()
    mgr = SandboxManager(docker=docker, proxy_manager=_proxy())

    with pytest.raises(SandboxInitError, match="has not been granted Docker access"):
        await mgr.create(job_id="job-unwired", profile=_builtin("qa-jvm"), repo=GRANTED)
    docker.run_cmd.assert_not_called()


@pytest.mark.asyncio
async def test_a_sandbox_without_a_repo_never_gets_the_daemon() -> None:
    docker = _docker()
    registry = _Registry(GRANTED)
    mgr = SandboxManager(docker=docker, proxy_manager=_proxy(), docker_access_registry=registry)

    with pytest.raises(SandboxInitError, match="no repo to hold a Docker access grant"):
        await mgr.create(job_id="job-repoless", profile=_builtin("qa-jvm"))
    assert registry.asked == []
    docker.run_cmd.assert_not_called()


@pytest.mark.asyncio
async def test_only_a_real_true_is_a_grant() -> None:
    """A truthy stand-in (a mock, a string) must not open the daemon."""
    registry = MagicMock()
    registry.docker_access = AsyncMock(return_value=MagicMock())
    docker = _docker()
    mgr = SandboxManager(docker=docker, proxy_manager=_proxy(), docker_access_registry=registry)

    with pytest.raises(SandboxInitError, match="has not been granted Docker access"):
        await mgr.create(job_id="job-truthy", profile=_builtin("qa-jvm"), repo=GRANTED)
    docker.run_cmd.assert_not_called()


@pytest.mark.asyncio
async def test_an_unreadable_grant_fails_closed_with_its_cause() -> None:
    registry = MagicMock()
    registry.docker_access = AsyncMock(side_effect=OSError("connection refused"))
    docker = _docker()
    mgr = SandboxManager(docker=docker, proxy_manager=_proxy(), docker_access_registry=registry)

    with pytest.raises(SandboxInitError, match="could not read Docker access") as info:
        await mgr.create(job_id="job-db-down", profile=_builtin("qa-jvm"), repo=GRANTED)
    assert info.value.error_code == ErrorCode.SANDBOX_INIT
    assert "connection refused" in str(info.value)
    docker.run_cmd.assert_not_called()


@pytest.mark.asyncio
async def test_a_docker_free_profile_never_consults_the_registry() -> None:
    """The lookup is for profiles that ask; every other sandbox is unaffected by it."""
    registry = MagicMock()
    registry.docker_access = AsyncMock(side_effect=AssertionError("asked for a profile without DinD"))
    mgr = SandboxManager(docker=_docker(), proxy_manager=_proxy(), docker_access_registry=registry)

    await mgr.create(job_id="job-slim", profile=_builtin("slim"), repo=NOT_GRANTED)
    registry.docker_access.assert_not_called()
