import asyncio
import base64
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.sandbox_manager import SandboxManager, _sanitize_docker_name_component


def _make_proxy_manager(token: str = "tok-abc123") -> MagicMock:
    """Return a mock ProxyManager whose enroll_sandbox returns a fake token."""
    proxy_mgr = MagicMock()
    proxy_mgr.enroll_sandbox = AsyncMock(return_value=token)
    proxy_mgr.unenroll_sandbox = AsyncMock()
    return proxy_mgr


def _permissive_enforcer() -> MagicMock:
    """Successful stand-in for EgressEnforcer.

    The default profile joins sandbox-internet, and create() installs the
    private-range drop even when egress_allowlist is None. The docker mock
    cannot run the iptables helper, so tests that are not about enforcement
    get this. Tests that assert on the rules replace the class themselves.
    """
    fake = MagicMock()
    fake.container_ip = AsyncMock(return_value="172.20.0.9")
    fake.ensure_helper_image = AsyncMock()
    fake.apply = AsyncMock()
    fake.clear = AsyncMock()
    return fake


@pytest.fixture(autouse=True)
def _stub_egress_unless_overridden(monkeypatch):
    from embry0.execution.runtime import egress as egress_mod

    monkeypatch.setattr(egress_mod, "EgressEnforcer", MagicMock(return_value=_permissive_enforcer()))


def _permissive_isolation() -> MagicMock:
    """Successful stand-in for SandboxIsolation (#554).

    Every sandbox on sandbox-restricted gets the isolation chain, and the
    docker mock cannot run the iptables helper. Tests about the chain
    replace the class themselves.
    """
    fake = MagicMock()
    fake.ensure = AsyncMock(return_value=["172.18.0.2"])
    return fake


@pytest.fixture(autouse=True)
def _stub_isolation_unless_overridden(monkeypatch):
    """Stub both rule sets that name proxy addresses: the isolation chain and,
    for refresh_proxy_policy (#568), the shared egress exceptions."""
    from embry0.execution.runtime import DockerRuntime
    from embry0.execution.runtime import isolation as isolation_mod

    monkeypatch.setattr(isolation_mod, "SandboxIsolation", MagicMock(return_value=_permissive_isolation()))
    monkeypatch.setattr(DockerRuntime, "refresh_egress", AsyncMock(return_value=[]))


@pytest.fixture
def manager() -> SandboxManager:
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-abc123")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])
    docker.build_stop_cmd = MagicMock(return_value=["docker", "stop", "..."])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "..."])
    docker.build_network_cmd = MagicMock(return_value=["docker", "network", "..."])
    return SandboxManager(docker=docker, proxy_manager=_make_proxy_manager())


@pytest.mark.asyncio
async def test_create_sandbox(manager: SandboxManager):
    container_id, sandbox_token = await manager.create(job_id="job-abc123")
    assert container_id == "container-abc123"
    assert sandbox_token == "tok-abc123"
    manager._docker.build_run_cmd.assert_called_once()
    call_kwargs = manager._docker.build_run_cmd.call_args
    assert call_kwargs.kwargs["name"] == "sandbox-job-abc123"


@pytest.mark.asyncio
async def test_destroy_sandbox(manager: SandboxManager):
    await manager.destroy("sandbox-job-abc123")
    manager._proxy_manager.unenroll_sandbox.assert_awaited_once_with("sandbox-job-abc123")
    manager._docker.build_stop_cmd.assert_called_once()
    manager._docker.build_rm_cmd.assert_called_once()


@pytest.mark.asyncio
async def test_connect_network(manager: SandboxManager):
    await manager.connect_network("sandbox-job-abc123", "sandbox-internet")
    manager._docker.build_network_cmd.assert_called_with("connect", "sandbox-internet", "sandbox-job-abc123")


@pytest.mark.asyncio
async def test_disconnect_network(manager: SandboxManager):
    await manager.disconnect_network("sandbox-job-abc123", "sandbox-internet")
    manager._docker.build_network_cmd.assert_called_with("disconnect", "sandbox-internet", "sandbox-job-abc123")


@pytest.mark.asyncio
async def test_create_uses_sandbox_profile_defaults(manager: SandboxManager):
    await manager.create(job_id="job-x")
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    assert kwargs["memory"] == "8g"
    assert kwargs["cpus"] == "4"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["network"] == "sandbox-restricted"


@pytest.mark.asyncio
async def test_sandbox_manager_does_not_inject_github_token(manager: SandboxManager, monkeypatch):
    """SandboxManager must NOT read GITHUB_TOKEN from the orchestrator's env
    or inject it into the sandbox container. Git auth flows through the
    credential proxy instead (see Plan C Task 2)."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_would_leak_if_injected")

    await manager.create(job_id="job-xyz")

    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}

    assert "GITHUB_TOKEN" not in env_passed, (
        f"GITHUB_TOKEN must not be injected into the sandbox env. Found keys: {sorted(env_passed)}"
    )


@pytest.mark.asyncio
async def test_sandbox_manager_passes_through_caller_env(manager: SandboxManager):
    """Caller-provided env (like EMBRY0_GIT_PROXY_URL) must reach the container."""
    await manager.create(
        job_id="job-xyz",
        env={"EMBRY0_GIT_PROXY_URL": "http://git-proxy:9101"},
    )

    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}

    assert env_passed.get("EMBRY0_GIT_PROXY_URL") == "http://git-proxy:9101"


@pytest.mark.asyncio
async def test_create_passes_tmpfs_mounts_through_to_docker_run(manager: SandboxManager):
    """tmpfs_mounts must be threaded into build_run_cmd so callers (the QA
    fan-out) can overlay an empty per-container .qa/ on top of the shared
    cache volume — without it, sub-tasks share a single /workspace/.qa/
    directory and clobber each other's result.json (regression seen on
    job-f42d46553080: 11 of 14 sub-tasks reported byte-identical failure
    summaries copied from one neighbouring sub-task)."""
    await manager.create(
        job_id="job-fanout",
        volumes=[("embry0-qa-vol-job-1", "/workspace")],
        tmpfs_mounts=["/workspace/.qa"],
    )
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    assert kwargs.get("tmpfs_mounts") == ["/workspace/.qa"]


_JVM_PROFILE = {"name": "dev-jvm-python", "base_image": "embry0-sandbox-dev-jvm-python:latest"}


def _commands(manager: SandboxManager) -> list[list[str]]:
    return [call.args[0] for call in manager._docker.run_cmd.call_args_list]


@pytest.mark.asyncio
async def test_jvm_profile_mounts_a_shared_maven_cache(manager: SandboxManager):
    """A cold Maven download ate the agent's whole turn budget. JVM sandboxes
    share one repository volume, and the agent user has to be able to write it."""
    manager._docker._build_base_cmd = MagicMock(return_value=["docker", "--host", "tcp://dind:2376"])
    await manager.create(job_id="job-jvm", profile=_JVM_PROFILE)
    volumes = manager._docker.build_run_cmd.call_args.kwargs["volumes"] or []
    assert "embry0-maven-repository:/home/agent/.m2" in volumes, "jvm sandboxes must share a maven cache"

    sandbox_run, owner = _commands(manager)[:2]
    assert sandbox_run == ["docker", "run", "..."], "the sandbox starts first: its run is what pulls the image"
    assert owner == [
        "docker",
        "--host",
        "tcp://dind:2376",
        "run",
        "--rm",
        "--network=none",
        "--cap-drop=ALL",
        "--cap-add=CHOWN",
        "--security-opt=no-new-privileges",
        "--user",
        "root",
        "-v",
        "embry0-maven-repository:/home/agent/.m2",
        "--entrypoint",
        "chown",
        "embry0-sandbox-dev-jvm-python:latest",
        "agent:agent",
        "/home/agent/.m2",
    ]


@pytest.mark.asyncio
async def test_the_maven_cache_is_never_chowned_from_inside_the_sandbox(manager: SandboxManager):
    """#453: the sandbox drops every capability, so root inside it cannot
    chown and every JVM sandbox died at creation. The sandbox must keep its
    empty capability set, and nothing may exec a chown in it."""
    manager._docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    await manager.create(job_id="job-jvm", profile=_JVM_PROFILE)
    run_kwargs = manager._docker.build_run_cmd.call_args.kwargs
    assert (run_kwargs["cap_drop"], run_kwargs["cap_add"]) == (["ALL"], [])
    chown_execs = [c for c in manager._docker.build_exec_cmd.call_args_list if "chown" in str(c)]
    assert chown_execs == [], "a chown exec'd in the sandbox needs a capability the sandbox does not have"


@pytest.mark.asyncio
async def test_a_maven_cache_that_cannot_be_handed_over_fails_the_sandbox(manager: SandboxManager):
    from embry0.execution.sandbox_manager import SandboxInitError

    async def run(cmd, timeout=60):  # noqa: ASYNC109 — mirrors DockerClient.run_cmd
        if "--entrypoint" in cmd:
            raise RuntimeError("Docker command failed: chown: Operation not permitted")
        return "container-abc123"

    manager._docker.run_cmd = AsyncMock(side_effect=run)
    with pytest.raises(SandboxInitError, match="Maven cache is not writable"):
        await manager.create(job_id="job-jvm", profile=_JVM_PROFILE)
    manager._docker.build_rm_cmd.assert_called_once_with("sandbox-job-jvm")
    manager._proxy_manager.enroll_sandbox.assert_not_called()


@pytest.mark.asyncio
async def test_python_profile_does_not_mount_the_maven_cache(manager: SandboxManager):
    await manager.create(
        job_id="job-py",
        profile={"name": "dev-python", "base_image": "embry0-sandbox-dev-python:latest"},
    )
    volumes = manager._docker.build_run_cmd.call_args.kwargs["volumes"] or []
    assert "embry0-maven-repository:/home/agent/.m2" not in volumes


@pytest.mark.asyncio
async def test_create_omits_tmpfs_mounts_when_caller_does_not_set_them(manager: SandboxManager):
    await manager.create(job_id="job-no-tmpfs")
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    assert kwargs.get("tmpfs_mounts") is None


@pytest.mark.asyncio
async def test_create_with_custom_profile(manager: SandboxManager):
    profile = {
        "base_image": "embry0-sandbox-java:17",
        "memory": "12g",
        "cpus": "6",
        "pids_limit": 512,
    }
    await manager.create(job_id="job-y", profile=profile)
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    assert kwargs["image"] == "embry0-sandbox-java:17"
    assert kwargs["memory"] == "12g"
    assert kwargs["cpus"] == "6"
    assert kwargs["pids_limit"] == 512


@pytest.mark.asyncio
async def test_create_passes_profile_extra_hosts_to_docker_run(manager: SandboxManager):
    """EMB-28: profile extra_hosts become --add-host entries."""
    profile = {"extra_hosts": {"app.example.com": "192.168.1.10"}}
    await manager.create(job_id="job-eh", profile=profile)
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    assert kwargs["extra_hosts"] == {"app.example.com": "192.168.1.10"}


@pytest.mark.asyncio
async def test_create_drops_invalid_profile_extra_hosts(manager: SandboxManager):
    """Bad hostnames and non-IP values are dropped, valid entries kept."""
    profile = {
        "extra_hosts": {
            "good.example.com": "10.0.0.5",
            "bad host!": "10.0.0.6",
            "no-ip.example.com": "not-an-ip",
        }
    }
    await manager.create(job_id="job-eh2", profile=profile)
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    assert kwargs["extra_hosts"] == {"good.example.com": "10.0.0.5"}


@pytest.mark.asyncio
async def test_profile_extra_hosts_cannot_override_dind_alias(manager: SandboxManager, monkeypatch):
    """The 'dind' alias is orchestrator-owned; a profile entry is dropped and
    the resolved gateway IP wins when dind is enabled."""
    monkeypatch.setattr(manager, "_resolve_dind_gateway_ip", lambda: "172.30.0.2")
    # #522: dind only reaches the sandbox when the repo holds the admin grant.
    registry = MagicMock()
    registry.docker_access = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_docker_access_registry", registry)
    profile = {"dind_enabled": True, "extra_hosts": {"dind": "6.6.6.6", "app.local": "10.0.0.9"}}
    await manager.create(job_id="job-eh3", profile=profile, repo="acme/widgets")
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    assert kwargs["extra_hosts"]["dind"] == "172.30.0.2"
    assert kwargs["extra_hosts"]["app.local"] == "10.0.0.9"


@pytest.mark.asyncio
async def test_create_rolls_back_on_enrollment_failure():
    """If enroll_sandbox fails, the container is removed and SandboxInitError raised."""
    from embry0.execution.sandbox_manager import SandboxInitError

    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-fail")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run"])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "sandbox-job-fail"])

    proxy_mgr = MagicMock()
    proxy_mgr.enroll_sandbox = AsyncMock(side_effect=RuntimeError("proxy unreachable"))
    proxy_mgr.unenroll_sandbox = AsyncMock()

    mgr = SandboxManager(docker=docker, proxy_manager=proxy_mgr)

    with pytest.raises(SandboxInitError, match="enrollment failed"):
        await mgr.create(job_id="job-fail")

    docker.build_rm_cmd.assert_called()  # rollback was attempted


@pytest.mark.asyncio
async def test_create_rolls_back_partial_enrollment_on_enroll_failure():
    """On enroll_sandbox failure, unenroll_sandbox is called before container rm
    to clean up any partial proxy enrollments that succeeded before the failure.
    """
    from embry0.execution.sandbox_manager import SandboxInitError

    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-partial")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run"])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "sandbox-job-partial"])

    proxy_mgr = MagicMock()
    proxy_mgr.enroll_sandbox = AsyncMock(side_effect=RuntimeError("github-proxy unreachable"))
    proxy_mgr.unenroll_sandbox = AsyncMock()

    mgr = SandboxManager(docker=docker, proxy_manager=proxy_mgr)

    with pytest.raises(SandboxInitError, match="enrollment failed"):
        await mgr.create(job_id="job-partial")

    # unenroll_sandbox must have been called exactly once with the container_id
    assert proxy_mgr.unenroll_sandbox.call_count == 1
    assert proxy_mgr.unenroll_sandbox.call_args_list[0].args[0] == "container-partial"


# ---------------------------------------------------------------------------
# _sanitize_docker_name_component — defense-in-depth helper (bug_014)
# ---------------------------------------------------------------------------


def test_sanitize_docker_name_component_replaces_colons_and_slashes():
    """Colons and slashes in job_ids must be replaced with '-' so Docker
    container/network names satisfy [a-zA-Z0-9][a-zA-Z0-9_.-]+."""
    assert _sanitize_docker_name_component("a:b/c::d") == "a-b-c--d"


def test_sanitize_docker_name_component_noop_on_clean_input():
    """Already-valid names must be returned unchanged."""
    assert _sanitize_docker_name_component("job-1__hub") == "job-1__hub"


@pytest.mark.asyncio
async def test_create_container_name_is_docker_safe_for_colon_job_id(manager: SandboxManager):
    """SandboxManager.create() must produce a container name that matches the
    Docker naming regex even when the job_id contains colons."""
    docker_name_re = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.\\-]+")

    await manager.create(job_id="job-1__hub")
    call_kwargs = manager._docker.build_run_cmd.call_args
    name = call_kwargs.kwargs["name"]
    assert docker_name_re.fullmatch(name), f"Container name {name!r} does not satisfy Docker naming regex"


# ---------------------------------------------------------------------------
# _resolve_token_for_repo / per-owner token resolution + enrollment wiring
# (per-owner token resolution)
# ---------------------------------------------------------------------------


def _mgr(github_token="ghp_DEFAULT"):
    return SandboxManager(
        docker=AsyncMock(),
        proxy_manager=AsyncMock(),
        github_token=github_token,
    )


def test_resolve_token_for_repo_uses_owner_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "ghp_acme")
    assert _mgr()._resolve_token_for_repo("acme-corp/widgets") == "ghp_acme"


def test_resolve_token_for_repo_falls_back(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN__OCTO_ORG", raising=False)
    assert _mgr()._resolve_token_for_repo("octo-org/embry0") == "ghp_DEFAULT"


def test_resolve_token_for_repo_none_returns_default():
    assert _mgr()._resolve_token_for_repo(None) == "ghp_DEFAULT"


def test_resolve_token_for_repo_no_slash_returns_default():
    assert _mgr()._resolve_token_for_repo("noslash") == "ghp_DEFAULT"


@pytest.mark.asyncio
async def test_create_passes_resolved_token_to_enroll_sandbox(monkeypatch):
    """create() must resolve the per-owner token from `repo` and forward it
    to enroll_sandbox as the github_token kwarg."""
    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "ghp_acme_owner")

    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-repo")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])

    proxy_mgr = _make_proxy_manager()

    mgr = SandboxManager(docker=docker, proxy_manager=proxy_mgr, github_token="ghp_DEFAULT")

    await mgr.create(job_id="job-repo", repo="acme-corp/widgets")

    # git_host/git_username are None for GitHub so the proxy applies its own
    # default — that is what keeps every pre-#166 sandbox byte-identical (#167).
    proxy_mgr.enroll_sandbox.assert_awaited_once_with(
        "container-repo",
        github_token="ghp_acme_owner",
        git_host=None,
        git_username=None,
        forge="github",
        xai_credential_id=None,
        repo="acme-corp/widgets",
        # #521: the git-proxy forwards to where the sandbox clones from, read only by default.
        git_upstream="https://github.com",
        git_push=False,
        git_push_branches=(),
        job_id="job-repo",
    )


# ---------------------------------------------------------------------------
# Worker-cap env-var injection (EAGAIN fix — iss-19330094)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_injects_worker_cap_env_vars(manager: SandboxManager):
    """Sandbox must receive worker-cap env vars sized from the profile's cpus
    value.  Without these, Node.js os.cpus() returns the HOST CPU count (e.g.
    47 on the observed host) and build tools (Next.js, webpack) spawn per-host-CPU
    workers that exceed the sandbox's pids_limit → EAGAIN."""
    await manager.create(job_id="job-worker-caps")
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}

    # Default profile has cpus="4", so worker caps should be 4
    assert env_passed.get("UV_THREADPOOL_SIZE") == "4"
    assert env_passed.get("NEXT_PRIVATE_WORKER_THREADS") == "4"
    assert env_passed.get("GOMAXPROCS") == "4"
    assert env_passed.get("MAKEFLAGS") == "-j4"


@pytest.mark.asyncio
async def test_create_worker_caps_follow_custom_profile_cpus(manager: SandboxManager):
    """Worker caps must track the profile's cpus, not hardcoded values."""
    profile = {"cpus": "6", "pids_limit": 512}
    await manager.create(job_id="job-6cpu", profile=profile)
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}

    assert env_passed.get("UV_THREADPOOL_SIZE") == "6"
    assert env_passed.get("NEXT_PRIVATE_WORKER_THREADS") == "6"
    assert env_passed.get("GOMAXPROCS") == "6"
    assert env_passed.get("MAKEFLAGS") == "-j6"


@pytest.mark.asyncio
async def test_create_worker_caps_do_not_override_user_env(manager: SandboxManager):
    """User-provided env vars must take precedence over auto-injected
    worker caps.  A user who knows their build needs UV_THREADPOOL_SIZE=16
    should not have it silently capped to 4."""
    await manager.create(
        job_id="job-user-override",
        env={"UV_THREADPOOL_SIZE": "16", "GOMAXPROCS": "8"},
    )
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}

    # User values win
    assert env_passed["UV_THREADPOOL_SIZE"] == "16"
    assert env_passed["GOMAXPROCS"] == "8"
    # Non-overridden caps still injected
    assert env_passed["NEXT_PRIVATE_WORKER_THREADS"] == "4"
    assert env_passed["MAKEFLAGS"] == "-j4"


# ---------------------------------------------------------------------------
# Profile env_defaults merging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_merges_profile_env_defaults(manager: SandboxManager):
    """Profile env_defaults (stored in DB) should be merged into the
    sandbox env.  Previously they were stored but never wired through."""
    profile = {"env_defaults": {"LANG": "C.UTF-8", "FOO": "bar"}}
    await manager.create(job_id="job-env-defaults", profile=profile)
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}

    assert env_passed.get("LANG") == "C.UTF-8"
    assert env_passed.get("FOO") == "bar"


@pytest.mark.asyncio
async def test_create_caller_env_wins_over_profile_env_defaults(manager: SandboxManager):
    """Caller-provided env must take precedence over profile env_defaults."""
    profile = {"env_defaults": {"LANG": "C.UTF-8"}}
    await manager.create(
        job_id="job-env-defaults-override",
        profile=profile,
        env={"LANG": "en_US.UTF-8"},
    )
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}

    assert env_passed["LANG"] == "en_US.UTF-8"


@pytest.mark.asyncio
async def test_create_drops_reserved_keys_from_profile_env_defaults(manager: SandboxManager):
    """Profile env_defaults must not become an unfiltered env-injection path:
    reserved keys/prefixes (GITHUB_TOKEN, DOCKER_*, ...) are dropped with the
    same defense-in-depth the user env-var path gets."""
    profile = {
        "env_defaults": {
            "GITHUB_TOKEN": "ghp_smuggled",
            "DOCKER_HOST": "tcp://evil:2375",
            "QA_ARTIFACT_URL": "http://evil",
            "LANG": "C.UTF-8",
        }
    }
    await manager.create(job_id="job-reserved-defaults", profile=profile)
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}

    assert "GITHUB_TOKEN" not in env_passed
    assert "DOCKER_HOST" not in env_passed
    assert "QA_ARTIFACT_URL" not in env_passed
    assert env_passed.get("LANG") == "C.UTF-8"


@pytest.mark.asyncio
async def test_create_drops_reserved_keys_from_profile_env_defaults_case_insensitively(
    manager: SandboxManager,
):
    """Case-variant spellings (`github_token`, `Docker_Host`) must be dropped
    too — profile env_defaults come straight from the DB, bypassing the
    EnvVarInput pattern/case validation an exact-match check would rely on."""
    profile = {
        "env_defaults": {
            "github_token": "ghp_smuggled",
            "Docker_Host": "tcp://evil:2375",
            "LANG": "C.UTF-8",
        }
    }
    await manager.create(job_id="job-reserved-defaults-case", profile=profile)
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}

    assert "ghp_smuggled" not in str(env_passed)
    assert "tcp://evil" not in str(env_passed)
    assert env_passed.get("LANG") == "C.UTF-8"


@pytest.mark.asyncio
async def test_create_with_egress_allowlist_applies_rules(manager: SandboxManager, monkeypatch):
    """EMB-29: an allowlisted sandbox on sandbox-internet gets DOCKER-USER
    rules keyed on its network IP."""
    from embry0.execution.runtime import egress as egress_mod

    fake_enforcer = MagicMock()
    fake_enforcer.container_ip = AsyncMock(return_value="172.20.0.9")
    fake_enforcer.ensure_helper_image = AsyncMock()
    fake_enforcer.apply = AsyncMock()
    monkeypatch.setattr(egress_mod, "EgressEnforcer", MagicMock(return_value=fake_enforcer))

    container_id, _tok = await manager.create(
        job_id="job-egress1",
        profile={"extra_networks": ["sandbox-internet"]},
        egress_allowlist=["192.168.1.10", "api.anthropic.com"],
    )
    assert container_id == "container-abc123"
    fake_enforcer.apply.assert_awaited_once_with("172.20.0.9", ["192.168.1.10", "api.anthropic.com"], proxy_ips=[])


def _enforcer_with_addresses(addresses: dict[str, object]) -> MagicMock:
    """An enforcer whose ``container_ip`` answers per container. A value that
    is an exception is raised; a missing container has no address."""

    async def container_ip(container: str, network: str = "sandbox-internet") -> str | None:
        value = addresses.get(container)
        if isinstance(value, Exception):
            raise value
        return value  # type: ignore[return-value]

    enforcer = MagicMock()
    enforcer.container_ip = AsyncMock(side_effect=container_ip)
    enforcer.ensure_helper_image = AsyncMock()
    enforcer.apply = AsyncMock()
    enforcer.clear = AsyncMock()
    return enforcer


@pytest.mark.asyncio
async def test_egress_rules_except_the_proxies_on_the_egress_network(manager: SandboxManager, monkeypatch):
    """#472: a proxy on both networks resolves, for a sandbox on both, to its
    sandbox-internet address — inside the range the rules drop. Without an
    exception every request to the xai-proxy timed out."""
    from embry0.execution.runtime import egress as egress_mod

    manager._proxy_manager.internet_proxies = MagicMock(return_value=["github-proxy", "xai-proxy", "context-proxy"])
    enforcer = _enforcer_with_addresses(
        {
            "container-abc123": "172.20.0.9",
            "github-proxy": "172.20.0.2",
            "xai-proxy": "172.20.0.5",
            "context-proxy": "172.20.0.6",
        }
    )
    monkeypatch.setattr(egress_mod, "EgressEnforcer", MagicMock(return_value=enforcer))

    await manager.create(
        job_id="job-proxies",
        profile={"extra_networks": ["sandbox-internet"]},
        egress_allowlist=["192.168.1.10"],
    )
    enforcer.apply.assert_awaited_once_with(
        "172.20.0.9", ["192.168.1.10"], proxy_ips=["172.20.0.2", "172.20.0.5", "172.20.0.6"]
    )

    # With no allowlist the proxies are still excepted, and nothing else is.
    enforcer.apply.reset_mock()
    await manager.create(job_id="job-proxies-2", profile={"extra_networks": ["sandbox-internet"]})
    enforcer.apply.assert_awaited_once_with("172.20.0.9", [], proxy_ips=["172.20.0.2", "172.20.0.5", "172.20.0.6"])


@pytest.mark.asyncio
async def test_a_proxy_whose_address_cannot_be_read_is_left_out(manager: SandboxManager, monkeypatch):
    """Leaving a proxy out keeps the sandbox's egress closed, so it must not
    fail the sandbox: a proxy that is gone cannot be reached either way."""
    from embry0.execution.runtime import egress as egress_mod

    manager._proxy_manager.internet_proxies = MagicMock(return_value=["github-proxy", "xai-proxy", "context-proxy"])
    enforcer = _enforcer_with_addresses(
        {
            "container-abc123": "172.20.0.9",
            "github-proxy": RuntimeError("No such container: github-proxy"),
            "xai-proxy": "172.20.0.5",
            # context-proxy: running, but not on the network.
        }
    )
    monkeypatch.setattr(egress_mod, "EgressEnforcer", MagicMock(return_value=enforcer))

    container_id, _tok = await manager.create(job_id="job-proxy-gone", profile={"extra_networks": ["sandbox-internet"]})
    assert container_id == "container-abc123"
    enforcer.apply.assert_awaited_once_with("172.20.0.9", [], proxy_ips=["172.20.0.5"])


@pytest.mark.asyncio
async def test_a_proxy_manager_without_the_list_adds_no_exception(monkeypatch):
    """The sandbox manager also runs where the proxy manager is a stand-in."""
    from embry0.execution.runtime import egress as egress_mod

    class _NoList:
        enroll_sandbox = AsyncMock(return_value="tok")
        unenroll_sandbox = AsyncMock()

    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-abc123")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "..."])
    docker.build_network_cmd = MagicMock(return_value=["docker", "network", "..."])
    bare = SandboxManager(docker=docker, proxy_manager=_NoList())
    enforcer = _enforcer_with_addresses({"container-abc123": "172.20.0.9"})
    monkeypatch.setattr(egress_mod, "EgressEnforcer", MagicMock(return_value=enforcer))

    await bare.create(job_id="job-bare", profile={"extra_networks": ["sandbox-internet"]})
    enforcer.apply.assert_awaited_once_with("172.20.0.9", [], proxy_ips=[])


@pytest.mark.asyncio
async def test_create_on_sandbox_internet_applies_private_drop_without_allowlist(manager: SandboxManager, monkeypatch):
    """Joining sandbox-internet is enough. egress_allowlist left as None still
    installs the private-range drop with an empty exception list. A profile
    that does not join that network gets no rules."""
    from unittest.mock import call

    from embry0.execution.runtime import egress as egress_mod

    fake_enforcer = MagicMock()
    fake_enforcer.container_ip = AsyncMock(return_value="172.20.0.9")
    fake_enforcer.ensure_helper_image = AsyncMock()
    fake_enforcer.apply = AsyncMock()
    enforcer_cls = MagicMock(return_value=fake_enforcer)
    monkeypatch.setattr(egress_mod, "EgressEnforcer", enforcer_cls)

    container_id, _tok = await manager.create(
        job_id="job-egress-none",
        profile={"extra_networks": ["sandbox-internet"]},
    )
    assert container_id == "container-abc123"
    applied = fake_enforcer.apply.await_args_list
    assert applied == [call("172.20.0.9", [], proxy_ips=[])], (
        "joining sandbox-internet with egress_allowlist=None must apply the "
        "private-range drop for that container's sandbox-internet IP and an "
        f"empty destination list; got {applied!r}"
    )

    fake_enforcer.apply.reset_mock()
    enforcer_cls.reset_mock()
    await manager.create(
        job_id="job-egress-offline",
        profile={"extra_networks": []},
    )
    assert fake_enforcer.apply.await_count == 0, "a profile with extra_networks [] must not call apply"


@pytest.mark.asyncio
async def test_create_egress_failure_fails_closed(manager: SandboxManager, monkeypatch):
    """Rule-install failure destroys the sandbox instead of running open."""
    from embry0.execution.runtime import egress as egress_mod
    from embry0.execution.sandbox_manager import SandboxInitError

    fake_enforcer = MagicMock()
    fake_enforcer.container_ip = AsyncMock(return_value="172.20.0.9")
    fake_enforcer.ensure_helper_image = AsyncMock()
    fake_enforcer.apply = AsyncMock(side_effect=RuntimeError("iptables exploded"))
    monkeypatch.setattr(egress_mod, "EgressEnforcer", MagicMock(return_value=fake_enforcer))

    with pytest.raises(SandboxInitError, match="egress enforcement failed"):
        await manager.create(
            job_id="job-egress2",
            profile={"extra_networks": ["sandbox-internet"]},
            egress_allowlist=["192.168.1.10"],
        )
    manager._proxy_manager.unenroll_sandbox.assert_awaited()
    manager._docker.build_rm_cmd.assert_called()


@pytest.mark.asyncio
async def test_create_without_internet_network_skips_enforcement(manager: SandboxManager, monkeypatch):
    """Allowlist on a restricted-only sandbox is a no-op (nothing to fence)."""
    from embry0.execution.runtime import egress as egress_mod

    enforcer_cls = MagicMock()
    monkeypatch.setattr(egress_mod, "EgressEnforcer", enforcer_cls)
    # NOTE: the DEFAULT profile includes sandbox-internet, so the no-egress
    # case must opt out explicitly.
    await manager.create(
        job_id="job-egress3",
        profile={"extra_networks": []},
        egress_allowlist=["192.168.1.10"],
    )
    enforcer_cls.assert_not_called()


@pytest.mark.asyncio
async def test_destroy_clears_rules_for_enforced_sandbox(manager: SandboxManager, monkeypatch):
    from embry0.execution.runtime import egress as egress_mod

    fake_enforcer = MagicMock()
    fake_enforcer.container_ip = AsyncMock(return_value="172.20.0.9")
    fake_enforcer.ensure_helper_image = AsyncMock()
    fake_enforcer.apply = AsyncMock()
    fake_enforcer.clear = AsyncMock()
    monkeypatch.setattr(egress_mod, "EgressEnforcer", MagicMock(return_value=fake_enforcer))

    container_id, _tok = await manager.create(
        job_id="job-egress4",
        profile={"extra_networks": ["sandbox-internet"]},
        egress_allowlist=["192.168.1.10"],
    )
    await manager.destroy(container_id)
    fake_enforcer.clear.assert_awaited_once_with("172.20.0.9")


@pytest.mark.asyncio
async def test_create_injects_provider_keys_from_orchestrator_env(manager: SandboxManager, monkeypatch):
    """An undeclared provider set does not copy orchestrator env keys.

    The historical name expected ``XAI_API_KEY`` from the process environment.
    That copy is gone: a declared set receives the key only through resolve_llm.
    """
    monkeypatch.setenv("XAI_API_KEY", "xai-sekrit")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oat-sekrit")
    await manager.create(job_id="job-prov1")
    env_arg = manager._docker.build_run_cmd.call_args.kwargs["env"]
    assert "XAI_API_KEY" not in env_arg, (
        "an undeclared provider set must not copy XAI_API_KEY from the orchestrator env"
    )
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env_arg, (
        "an undeclared provider set must not copy CLAUDE_CODE_OAUTH_TOKEN from the orchestrator env"
    )


@pytest.mark.asyncio
async def test_create_omits_provider_keys_when_unset(manager: SandboxManager, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    await manager.create(job_id="job-prov2")
    env_arg = manager._docker.build_run_cmd.call_args.kwargs["env"]
    assert "XAI_API_KEY" not in env_arg


# --- private CA (spec 1A §3.2) ----------------------------------------------

CA_PEM = "-----BEGIN CERTIFICATE-----\nMIIDFTCCAf2g\nAoUvla6\n-----END CERTIFICATE-----\n"


def _ca_manager() -> SandboxManager:
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-abc123")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "..."])
    docker.build_network_cmd = MagicMock(return_value=["docker", "network", "..."])
    docker.build_exec_cmd = MagicMock(
        side_effect=lambda container, command, **kw: ["docker", "exec", container, *command]
    )
    return SandboxManager(
        docker=docker,
        proxy_manager=_make_proxy_manager(),
        gitlab_url="https://gitlab.dev.example",
        gitlab_ca_pem=CA_PEM,
    )


def _exec_script(docker: MagicMock) -> str:
    call = docker.build_exec_cmd.call_args
    assert call is not None, "embry0-trust-ca was never exec'd"
    return call.args[1][-1]


@pytest.mark.asyncio
async def test_gitlab_sandbox_gets_the_ca_env_before_docker_run():
    mgr = _ca_manager()
    await mgr.create(job_id="job-ca", forge="gitlab")
    env = mgr._docker.build_run_cmd.call_args.kwargs["env"]
    for key in ("GIT_SSL_CAINFO", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"):
        assert env[key] == "/home/agent/.embry0/ca-bundle.pem"


@pytest.mark.asyncio
async def test_gitlab_sandbox_execs_the_helper_with_the_pem():
    mgr = _ca_manager()
    await mgr.create(job_id="job-ca", forge="gitlab")
    script = _exec_script(mgr._docker)
    assert "embry0-trust-ca" in script
    assert "base64 -d" in script
    encoded = base64.b64encode(CA_PEM.encode()).decode()
    assert encoded in script
    # The PEM itself never reaches argv in raw form.
    assert "BEGIN CERTIFICATE" not in script


@pytest.mark.asyncio
async def test_the_ca_exec_is_the_first_thing_run_inside_the_container():
    """Ordering is the whole point: after `docker run`, before anything clones."""
    mgr = _ca_manager()
    await mgr.create(job_id="job-ca", forge="gitlab")
    commands = [call.args[0] for call in mgr._docker.run_cmd.call_args_list]
    assert commands[0][:2] == ["docker", "run"]
    assert commands[1][:3] == ["docker", "exec", "container-abc123"]
    assert commands[1][3:5] == ["bash", "-c"]
    # The CA exec is the only in-container command create issues, so the
    # bundle is in place before the first command a job could ever run.
    assert [c for c in commands if c[:2] == ["docker", "exec"]] == [commands[1]]


@pytest.mark.asyncio
async def test_github_sandbox_is_untouched():
    """A GitHub job on a GitLab-capable deployment must be byte-identical."""
    mgr = _ca_manager()
    await mgr.create(job_id="job-gh", forge="github")
    env = mgr._docker.build_run_cmd.call_args.kwargs["env"]
    assert "GIT_SSL_CAINFO" not in env
    mgr._docker.build_exec_cmd.assert_not_called()


@pytest.mark.asyncio
async def test_no_ca_configured_means_no_env_and_no_exec():
    mgr = _ca_manager()
    mgr._gitlab_ca_pem = ""
    await mgr.create(job_id="job-ca", forge="gitlab")
    assert "GIT_SSL_CAINFO" not in mgr._docker.build_run_cmd.call_args.kwargs["env"]
    mgr._docker.build_exec_cmd.assert_not_called()


@pytest.mark.asyncio
async def test_a_failed_ca_install_destroys_the_sandbox():
    """Fail closed: a sandbox that cannot verify the CA must not run a clone."""
    from embry0.execution.sandbox_manager import SandboxInitError

    mgr = _ca_manager()
    calls: list[list[str]] = []

    async def _run(cmd, timeout=60):
        calls.append(cmd)
        if "embry0-trust-ca" in " ".join(cmd):
            raise RuntimeError("no such file: embry0-trust-ca")
        return "container-abc123"

    mgr._docker.run_cmd = AsyncMock(side_effect=_run)
    with pytest.raises(SandboxInitError, match="CA bundle"):
        await mgr.create(job_id="job-ca", forge="gitlab")
    assert any(cmd[:2] == ["docker", "rm"] for cmd in calls), "container was not rolled back"


def _skill_store_with_one_tree(tmp_path):
    from embry0.skills.store import SkillStore

    store = SkillStore(tmp_path / "skills")
    staged = tmp_path / "staged"
    (staged / "skills" / "x").mkdir(parents=True)
    (staged / "skills" / "x" / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    store.publish("superpowers", "a" * 40, staged)
    sources = MagicMock()
    sources.list_all = AsyncMock(
        return_value=[
            {"id": "superpowers", "plugin_name": "superpowers", "status": "ready", "enabled": True},
            {"id": "gone", "plugin_name": "gone", "status": "ready", "enabled": True},
        ]
    )
    return store, sources


def _skills_docker() -> MagicMock:
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-abc123")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])
    docker.build_stop_cmd = MagicMock(return_value=["docker", "stop", "..."])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "..."])
    return docker


@pytest.mark.asyncio
async def test_create_mounts_every_ready_skill_source_read_only(tmp_path):
    store, sources = _skill_store_with_one_tree(tmp_path)
    docker = _skills_docker()
    mgr = SandboxManager(
        docker=docker, proxy_manager=_make_proxy_manager(), skill_store=store, skill_sources_repo=sources
    )
    container_id, _ = await mgr.create(job_id="job-skills")
    volumes = docker.build_run_cmd.call_args.kwargs["volumes"]
    expected = f"{tmp_path / 'skills' / 'superpowers' / ('a' * 40)}:/opt/embry0/skills/superpowers:ro"
    assert expected in volumes
    assert not any(v.startswith(str(tmp_path / "skills" / "gone")) for v in volumes), (
        "a source with no tree is skipped, not mounted"
    )
    # Keyed by the container id create returns: the node holds that id.
    mounts = await mgr.skill_mounts_for(container_id)
    assert [m.source_id for m in mounts] == ["superpowers"]
    sources.list_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_skill_mounts_for_reports_what_create_mounted_not_the_newer_manifest(tmp_path):
    store, sources = _skill_store_with_one_tree(tmp_path)
    mgr = SandboxManager(
        docker=_skills_docker(), proxy_manager=_make_proxy_manager(), skill_store=store, skill_sources_repo=sources
    )
    container_id, _ = await mgr.create(job_id="job-skills")
    sources.list_all.return_value = []
    mounts = await mgr.skill_mounts_for(container_id)
    assert [m.source_id for m in mounts] == ["superpowers"], "the sandbox has what create mounted"


def _skills_runtime(**methods) -> MagicMock:
    runtime = MagicMock()
    for name, mock in methods.items():
        setattr(runtime, name, mock)
    return runtime


@pytest.mark.asyncio
async def test_skill_mounts_for_a_resumed_container_reads_its_mounts_not_the_manifest(tmp_path):
    """After an orchestrator restart the record is gone; the container's own mounts say what it has."""
    store, sources = _skill_store_with_one_tree(tmp_path)
    sources.list_all.return_value.append({"id": "local", "plugin_name": "house", "enabled": True})
    older = str(tmp_path / "skills" / "local" / ("b" * 40))
    runtime = _skills_runtime(
        container_mounts=AsyncMock(
            return_value=[
                (older, "/opt/embry0/skills/local"),
                ("/var/lib/docker/volumes/m2/_data", "/home/agent/.m2"),
                ("/certs/client", "/opt/embry0/skills"),
            ]
        )
    )
    mgr = SandboxManager(
        docker=_skills_docker(),
        proxy_manager=_make_proxy_manager(),
        runtime=runtime,
        skill_store=store,
        skill_sources_repo=sources,
    )
    mounts = await mgr.skill_mounts_for("container-from-before-the-restart")
    assert [(m.source_id, m.plugin_name, m.host_path, m.container_path) for m in mounts] == [
        ("local", "house", older, "/opt/embry0/skills/local")
    ], "a resumed container reports the current manifest, not what it mounts"
    runtime.container_mounts.assert_awaited_once_with("container-from-before-the-restart")
    await mgr.skill_mounts_for("container-from-before-the-restart")
    runtime.container_mounts.assert_awaited_once()  # the answer is kept for the container


@pytest.mark.asyncio
async def test_skill_mounts_for_falls_back_to_the_manifest_when_the_inspect_fails(tmp_path):
    from structlog.testing import capture_logs

    store, sources = _skill_store_with_one_tree(tmp_path)
    runtime = _skills_runtime(container_mounts=AsyncMock(side_effect=RuntimeError("no such container")))
    mgr = SandboxManager(
        docker=_skills_docker(),
        proxy_manager=_make_proxy_manager(),
        runtime=runtime,
        skill_store=store,
        skill_sources_repo=sources,
    )
    with capture_logs() as logs:
        mounts = await mgr.skill_mounts_for("container-from-before-the-restart")
    assert [m.source_id for m in mounts] == ["superpowers"]
    assert any(e["event"] == "skill_mounts_inspect_failed" for e in logs)


def _store_with_trees(tmp_path, shas):
    from embry0.skills.store import SkillStore

    store = SkillStore(tmp_path / "skills")
    for sha in shas:
        staged = tmp_path / f"staged-{sha[:1]}"
        (staged / "skills" / "x").mkdir(parents=True)
        (staged / "skills" / "x" / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
        store.publish("superpowers", sha, staged)
    return store


@pytest.mark.asyncio
async def test_gc_skill_trees_removes_only_unmounted_stale_trees(tmp_path):
    a, b, c = "a" * 40, "b" * 40, "c" * 40
    store = _store_with_trees(tmp_path, [a, b, c])
    runtime = _skills_runtime(mounted_sources=AsyncMock(return_value={str(store.tree_path("superpowers", a))}))
    mgr = SandboxManager(
        docker=_skills_docker(),
        proxy_manager=_make_proxy_manager(),
        runtime=runtime,
        skill_store=store,
        skill_sources_repo=MagicMock(),
    )
    removed = await mgr.gc_skill_trees()
    assert removed == [store.tree_path("superpowers", b)]
    assert store.tree_path("superpowers", a).is_dir(), "a stale tree a container mounts was removed"
    assert not store.tree_path("superpowers", b).exists(), "an unmounted stale tree survived"
    assert store.tree_path("superpowers", c).is_dir(), "the current tree was removed"


@pytest.mark.asyncio
async def test_gc_skill_trees_compares_the_daemons_paths(tmp_path):
    """Rootless: the daemon reports its own path for a mount, not the orchestrator's."""
    from embry0.skills.store import SkillStore

    a, b = "a" * 40, "b" * 40
    _store_with_trees(tmp_path, [a, b])
    store = SkillStore(tmp_path / "skills", daemon_root=Path("/var/lib/embry0/skills"))
    runtime = _skills_runtime(mounted_sources=AsyncMock(return_value={f"/var/lib/embry0/skills/superpowers/{a}"}))
    mgr = SandboxManager(
        docker=_skills_docker(), proxy_manager=_make_proxy_manager(), runtime=runtime, skill_store=store
    )
    assert await mgr.gc_skill_trees() == []
    assert store.tree_path("superpowers", a).is_dir()


@pytest.mark.asyncio
async def test_gc_skill_trees_removes_nothing_when_the_daemon_cannot_list_mounts(tmp_path):
    a, b = "a" * 40, "b" * 40
    store = _store_with_trees(tmp_path, [a, b])
    runtime = _skills_runtime(mounted_sources=AsyncMock(side_effect=RuntimeError("daemon down")))
    mgr = SandboxManager(
        docker=_skills_docker(), proxy_manager=_make_proxy_manager(), runtime=runtime, skill_store=store
    )
    assert await mgr.gc_skill_trees() == []
    assert store.tree_path("superpowers", a).is_dir()


@pytest.mark.asyncio
async def test_gc_skill_trees_keeps_the_trees_of_a_create_still_in_docker_run(tmp_path):
    """The daemon lists a container only once `docker run` returns; its trees are mounted already."""
    a, b = "a" * 40, "b" * 40
    store = _store_with_trees(tmp_path, [a])
    sources = MagicMock()
    sources.list_all = AsyncMock(return_value=[{"id": "superpowers", "plugin_name": "superpowers", "enabled": True}])
    docker = _skills_docker()
    running = asyncio.Event()
    release = asyncio.Event()

    async def run_cmd(cmd, timeout=60):
        if cmd[:2] == ["docker", "run"]:
            running.set()
            await release.wait()
        return "container-abc123"

    docker.run_cmd = AsyncMock(side_effect=run_cmd)
    from embry0.execution.runtime import DockerRuntime

    runtime = DockerRuntime(docker)
    runtime.mounted_sources = AsyncMock(return_value=set())  # type: ignore[method-assign]
    mgr = SandboxManager(
        docker=docker,
        proxy_manager=_make_proxy_manager(),
        runtime=runtime,
        skill_store=store,
        skill_sources_repo=sources,
    )
    create = asyncio.create_task(mgr.create(job_id="job-skills"))
    await running.wait()
    staged = tmp_path / "staged-new"
    (staged / "skills" / "x").mkdir(parents=True)
    store.publish("superpowers", b, staged)
    assert await mgr.gc_skill_trees() == []
    assert store.tree_path("superpowers", a).is_dir(), "the tree a create is mounting was removed"
    release.set()
    await create
    assert mgr._skill_mounts_creating == []


@pytest.mark.asyncio
async def test_destroy_forgets_the_containers_skill_mounts(tmp_path):
    store, sources = _skill_store_with_one_tree(tmp_path)
    mgr = SandboxManager(
        docker=_skills_docker(), proxy_manager=_make_proxy_manager(), skill_store=store, skill_sources_repo=sources
    )
    container_id, _ = await mgr.create(job_id="job-skills")
    await mgr.destroy(container_id)
    assert container_id not in mgr._skill_mounts_by_container


@pytest.mark.asyncio
async def test_an_unreadable_skill_registry_never_stops_a_sandbox(tmp_path):
    store, sources = _skill_store_with_one_tree(tmp_path)
    sources.list_all = AsyncMock(side_effect=RuntimeError("db down"))
    docker = _skills_docker()
    mgr = SandboxManager(
        docker=docker, proxy_manager=_make_proxy_manager(), skill_store=store, skill_sources_repo=sources
    )
    container_id, _ = await mgr.create(job_id="job-skills")
    assert container_id == "container-abc123"
    volumes = docker.build_run_cmd.call_args.kwargs["volumes"] or []
    assert not any("/opt/embry0/skills/" in v for v in volumes)


@pytest.mark.asyncio
async def test_create_without_a_skill_store_mounts_nothing_extra(manager: SandboxManager):
    await manager.create(job_id="job-plain")
    volumes = manager._docker.build_run_cmd.call_args.kwargs["volumes"]
    assert not volumes or not any("/opt/embry0/skills/" in v for v in volumes)


@pytest.mark.asyncio
async def test_create_drops_a_profile_extra_host_ending_in_a_newline(manager: SandboxManager):
    """Python's ``$`` also matches just before a final newline, so ``match`` took ``app.example.com\\n``."""
    profile = {"extra_hosts": {"app.example.com\n": "10.0.0.5", "good.example.com": "10.0.0.6"}}
    await manager.create(job_id="job-eh-nl", profile=profile)
    hosts = manager._docker.build_run_cmd.call_args.kwargs["extra_hosts"]
    assert hosts == {"good.example.com": "10.0.0.6"}, f"a hostname ending in a newline must be dropped: {hosts!r}"
