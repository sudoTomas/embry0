import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.sandbox_manager import SandboxManager, _sanitize_docker_name_component


def _make_proxy_manager(token: str = "tok-abc123") -> MagicMock:
    """Return a mock ProxyManager whose enroll_sandbox returns a fake token."""
    proxy_mgr = MagicMock()
    proxy_mgr.enroll_sandbox = AsyncMock(return_value=token)
    proxy_mgr.unenroll_sandbox = AsyncMock()
    return proxy_mgr


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

    proxy_mgr.enroll_sandbox.assert_awaited_once_with("container-repo", github_token="ghp_acme_owner")


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
