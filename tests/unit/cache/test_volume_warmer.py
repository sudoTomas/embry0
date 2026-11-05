"""Unit tests for embry0.cache.volume_warmer.warm_shared_volume.

Two tests as required by C2:
  1. Warms (runs npm ci + upserts state) when there is no prior sha recorded.
  2. Skips (no sandbox, no npm ci) when prior sha matches current sha.
"""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.cache.volume_warmer import warm_shared_volume


def _stub_deps():
    """Return a set of minimal stub dependencies."""
    docker = MagicMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.build_exec_cmd = lambda cid, cmd: ["docker", "exec", cid, *cmd]
    docker.run_cmd = AsyncMock(return_value="")

    sandbox_mgr = AsyncMock()
    sandbox_mgr.create = AsyncMock(return_value=("sb-warmer", "tok-" + "A" * 40))
    sandbox_mgr.destroy = AsyncMock()

    profiles_repo = MagicMock()
    profiles_repo.get = AsyncMock(return_value={"name": "slim", "extra_networks": []})

    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = ""

    state_repo = AsyncMock()
    state_repo.get = AsyncMock(return_value=None)

    return docker, sandbox_mgr, profiles_repo, proxy_mgr, state_repo


def _volume_mgr(*, verified: bool = True) -> MagicMock:
    """A volume manager whose mount check answers ``verified``."""
    mgr = MagicMock()
    mgr.verify = AsyncMock(return_value=verified)
    return mgr


@pytest.mark.asyncio
async def test_the_warmer_mounts_a_volume_only_after_its_labels_are_checked(monkeypatch):
    """A volume that fails the mount check (missing, so the daemon would create it unlabelled,
    or not this deployment's for the key) is never mounted: no warmer sandbox is created."""
    import inspect

    from embry0.cache import volume_manager
    from embry0.workflows.qa._subtask_prep import ClonedSandbox

    docker, sandbox_mgr, profiles_repo, proxy_mgr, state_repo = _stub_deps()
    error = getattr(volume_manager, "VolumeOwnershipError", None)
    assert error is not None, "embry0.cache.volume_manager has no VolumeOwnershipError for a refused volume"
    assert "volume_mgr" in inspect.signature(warm_shared_volume).parameters, (
        "warm_shared_volume() takes no volume manager to check the volume before it mounts it"
    )
    monkeypatch.setattr(
        "embry0.cache.volume_warmer.prep_qa_sandbox_clone", AsyncMock(return_value=ClonedSandbox(head_sha="abc"))
    )
    refusing = _volume_mgr(verified=False)
    try:
        await warm_shared_volume(
            repo="org/r1",
            branch="main",
            volume_name="embry0-qa-vol-per-job-x",
            current_lockfile_sha="aaa",
            scope="per-job",
            scope_key="job-1",
            docker=docker,
            sandbox_mgr=sandbox_mgr,
            profiles_repo=profiles_repo,
            proxy_mgr=proxy_mgr,
            state_repo=state_repo,
            volume_mgr=refusing,
        )
    except error:
        pass
    else:
        raise AssertionError("a volume that fails its mount check must be refused, not warmed")
    sandbox_mgr.create.assert_not_awaited()
    checked = refusing.verify.await_args.args[0]
    assert (checked.name, str(checked.scope), checked.scope_key) == ("embry0-qa-vol-per-job-x", "per-job", "job-1"), (
        f"the mount check must be asked about the volume, scope and key being mounted; asked about {checked}"
    )


@pytest.mark.asyncio
async def test_warm_runs_npm_ci_when_state_has_no_prior_sha(monkeypatch):
    """When state_repo.get returns None, the warmer should:
    - create a sandbox with the volume mounted
    - run npm ci
    - call state_repo.upsert with the current lockfile sha
    - return "warmed"
    """
    docker, sandbox_mgr, profiles_repo, proxy_mgr, state_repo = _stub_deps()

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.cache.volume_warmer.prep_qa_sandbox_clone", fake_clone)

    result = await warm_shared_volume(
        repo="org/r1",
        branch="main",
        volume_name="embry0-qa-vol-job-1",
        current_lockfile_sha="aaa",
        scope="per-job",
        scope_key="job-1",
        docker=docker,
        sandbox_mgr=sandbox_mgr,
        profiles_repo=profiles_repo,
        proxy_mgr=proxy_mgr,
        state_repo=state_repo,
        volume_mgr=_volume_mgr(),
    )

    assert result == "warmed"

    # npm ci should have been called via docker.run_cmd
    cmds = [" ".join(c.args[0]) for c in docker.run_cmd.call_args_list]
    assert any("npm ci" in s for s in cmds), f"npm ci not found in commands: {cmds}"

    # State should have been upserted with the current lockfile sha
    state_repo.upsert.assert_awaited_once()
    call_kwargs = state_repo.upsert.call_args.kwargs
    assert call_kwargs["last_warmed_sha"] == "aaa"
    assert call_kwargs["scope"] == "per-job"
    assert call_kwargs["scope_key"] == "job-1"
    assert call_kwargs["volume_name"] == "embry0-qa-vol-job-1"

    # Sandbox should have been destroyed in the finally block
    sandbox_mgr.destroy.assert_awaited_once_with("sb-warmer")

    # Sandbox should have been created with the volume mounted at /workspace
    # (the WHOLE workspace tree, not just node_modules — sub-tasks mount the
    # same volume at /workspace and need the cloned repo + node_modules to
    # both be present).
    sandbox_mgr.create.assert_awaited_once()
    create_kwargs = sandbox_mgr.create.call_args.kwargs
    assert create_kwargs.get("volumes") == [("embry0-qa-vol-job-1", "/workspace")]


@pytest.mark.asyncio
async def test_warm_skips_when_volume_already_warmed_for_same_lockfile(monkeypatch):
    """When state_repo.get returns a VolumeState whose last_warmed_sha matches
    current_lockfile_sha, the warmer should:
    - return "skipped" immediately
    - NOT create a sandbox
    - NOT run npm ci
    """
    from datetime import datetime

    docker, sandbox_mgr, profiles_repo, proxy_mgr, state_repo = _stub_deps()

    from embry0.storage.repositories.qa_volume_state import VolumeState

    state_repo.get = AsyncMock(
        return_value=VolumeState(
            scope="per-job",
            scope_key="job-1",
            volume_name="embry0-qa-vol-job-1",
            last_warmed_sha="aaa",
            last_warmed_at=datetime.now(UTC),
        )
    )

    async def crash(**kw):
        raise AssertionError("prep_qa_sandbox_clone should not be called on warm-skip")

    monkeypatch.setattr("embry0.cache.volume_warmer.prep_qa_sandbox_clone", crash)

    result = await warm_shared_volume(
        repo="org/r1",
        branch="main",
        volume_name="embry0-qa-vol-job-1",
        current_lockfile_sha="aaa",
        scope="per-job",
        scope_key="job-1",
        docker=docker,
        sandbox_mgr=sandbox_mgr,
        profiles_repo=profiles_repo,
        proxy_mgr=proxy_mgr,
        state_repo=state_repo,
        volume_mgr=_volume_mgr(),
    )

    assert result == "skipped"
    sandbox_mgr.create.assert_not_awaited()
    docker.run_cmd.assert_not_awaited()
    state_repo.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_warm_uses_custom_install_command_and_prebaked_image(monkeypatch):
    """RAV-1400: cache.install_command replaces the npm ci default verbatim,
    and a prebaked image tag overrides the warmer profile's base image."""
    docker, sandbox_mgr, profiles_repo, proxy_mgr, state_repo = _stub_deps()

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.cache.volume_warmer.prep_qa_sandbox_clone", fake_clone)

    result = await warm_shared_volume(
        repo="acme/console",
        branch="master",
        volume_name="vol-1",
        current_lockfile_sha="bbb",
        scope="per-job",
        scope_key="job-2",
        docker=docker,
        sandbox_mgr=sandbox_mgr,
        profiles_repo=profiles_repo,
        proxy_mgr=proxy_mgr,
        state_repo=state_repo,
        volume_mgr=_volume_mgr(),
        install_command="npm ci --prefer-offline --legacy-peer-deps",
        prebaked_image_tag="embry0-qa-acme_console:2026-08-12",
    )

    assert result == "warmed"
    cmds = [" ".join(c.args[0]) for c in docker.run_cmd.call_args_list]
    assert any("npm ci --prefer-offline --legacy-peer-deps" in s for s in cmds), cmds
    profile_used = sandbox_mgr.create.call_args.kwargs.get("profile") or sandbox_mgr.create.call_args.args[1]
    assert profile_used["base_image"] == "embry0-qa-acme_console:2026-08-12"


@pytest.mark.asyncio
async def test_warm_default_install_without_overrides(monkeypatch):
    """No install_command / no prebaked tag → historical npm ci default, slim profile untouched."""
    docker, sandbox_mgr, profiles_repo, proxy_mgr, state_repo = _stub_deps()

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.cache.volume_warmer.prep_qa_sandbox_clone", fake_clone)

    await warm_shared_volume(
        repo="org/r1",
        branch="main",
        volume_name="vol-2",
        current_lockfile_sha="ccc",
        scope="per-job",
        scope_key="job-3",
        docker=docker,
        sandbox_mgr=sandbox_mgr,
        profiles_repo=profiles_repo,
        proxy_mgr=proxy_mgr,
        state_repo=state_repo,
        volume_mgr=_volume_mgr(),
    )

    cmds = [" ".join(c.args[0]) for c in docker.run_cmd.call_args_list]
    assert any("npm ci --prefer-offline 2>&1" in s for s in cmds), cmds
    profile_used = sandbox_mgr.create.call_args.kwargs.get("profile") or sandbox_mgr.create.call_args.args[1]
    assert "base_image" not in profile_used


@pytest.mark.asyncio
async def test_warm_binds_repo_into_sandbox_enrollment(monkeypatch):
    """RAV-1400: create(repo=...) so the git-proxy serves the owner's token —
    without it, per-owner-token repos 403 on the warmer's clone."""
    docker, sandbox_mgr, profiles_repo, proxy_mgr, state_repo = _stub_deps()

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.cache.volume_warmer.prep_qa_sandbox_clone", fake_clone)

    await warm_shared_volume(
        repo="acme/console",
        branch="master",
        volume_name="vol-3",
        current_lockfile_sha="ddd",
        scope="per-job",
        scope_key="job-4",
        docker=docker,
        sandbox_mgr=sandbox_mgr,
        profiles_repo=profiles_repo,
        proxy_mgr=proxy_mgr,
        state_repo=state_repo,
        volume_mgr=_volume_mgr(),
    )

    assert sandbox_mgr.create.call_args.kwargs.get("repo") == "acme/console"


@pytest.mark.asyncio
async def test_warmer_clones_the_repo_forge(monkeypatch):
    """A gitlab repo's warm clone must not go to github.com (spec 3.6)."""
    docker, sandbox_mgr, profiles_repo, proxy_mgr, state_repo = _stub_deps()
    seen = {}

    async def fake_clone(**kw):
        from embry0.workflows.qa._subtask_prep import ClonedSandbox

        seen.update(kw)
        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("embry0.cache.volume_warmer.prep_qa_sandbox_clone", fake_clone)

    await warm_shared_volume(
        repo="acme/group/backend",
        branch="dev",
        volume_name="vol-1",
        current_lockfile_sha="aaa",
        scope="per-repo",
        scope_key="acme/group/backend",
        docker=docker,
        sandbox_mgr=sandbox_mgr,
        profiles_repo=profiles_repo,
        proxy_mgr=proxy_mgr,
        state_repo=state_repo,
        volume_mgr=_volume_mgr(),
        forge="gitlab",
        gitlab_url="https://gitlab.example.com",
    )

    assert seen["forge"] == "gitlab"
    assert seen["gitlab_url"] == "https://gitlab.example.com"
    assert sandbox_mgr.create.await_args.kwargs["forge"] == "gitlab"
