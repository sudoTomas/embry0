"""Unit tests for athanor.cache.volume_warmer.warm_shared_volume.

Two tests as required by C2:
  1. Warms (runs npm ci + upserts state) when there is no prior sha recorded.
  2. Skips (no sandbox, no npm ci) when prior sha matches current sha.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.cache.volume_warmer import warm_shared_volume


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
        from athanor.workflows.qa._subtask_prep import ClonedSandbox
        return ClonedSandbox(head_sha="abc123")

    monkeypatch.setattr("athanor.cache.volume_warmer.prep_qa_sandbox_clone", fake_clone)

    result = await warm_shared_volume(
        repo="org/r1",
        branch="main",
        volume_name="athanor-qa-vol-job-1",
        current_lockfile_sha="aaa",
        scope="per-job",
        scope_key="job-1",
        docker=docker,
        sandbox_mgr=sandbox_mgr,
        profiles_repo=profiles_repo,
        proxy_mgr=proxy_mgr,
        state_repo=state_repo,
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
    assert call_kwargs["volume_name"] == "athanor-qa-vol-job-1"

    # Sandbox should have been destroyed in the finally block
    sandbox_mgr.destroy.assert_awaited_once_with("sb-warmer")

    # Sandbox should have been created with the volume mounted at /workspace/node_modules
    sandbox_mgr.create.assert_awaited_once()
    create_kwargs = sandbox_mgr.create.call_args.kwargs
    assert create_kwargs.get("volumes") == [("athanor-qa-vol-job-1", "/workspace/node_modules")]


@pytest.mark.asyncio
async def test_warm_skips_when_volume_already_warmed_for_same_lockfile(monkeypatch):
    """When state_repo.get returns a VolumeState whose last_warmed_sha matches
    current_lockfile_sha, the warmer should:
    - return "skipped" immediately
    - NOT create a sandbox
    - NOT run npm ci
    """
    from datetime import datetime, timezone

    docker, sandbox_mgr, profiles_repo, proxy_mgr, state_repo = _stub_deps()

    from athanor.storage.repositories.qa_volume_state import VolumeState
    state_repo.get = AsyncMock(return_value=VolumeState(
        scope="per-job",
        scope_key="job-1",
        volume_name="athanor-qa-vol-job-1",
        last_warmed_sha="aaa",
        last_warmed_at=datetime.now(timezone.utc),
    ))

    async def crash(**kw):
        raise AssertionError("prep_qa_sandbox_clone should not be called on warm-skip")

    monkeypatch.setattr("athanor.cache.volume_warmer.prep_qa_sandbox_clone", crash)

    result = await warm_shared_volume(
        repo="org/r1",
        branch="main",
        volume_name="athanor-qa-vol-job-1",
        current_lockfile_sha="aaa",
        scope="per-job",
        scope_key="job-1",
        docker=docker,
        sandbox_mgr=sandbox_mgr,
        profiles_repo=profiles_repo,
        proxy_mgr=proxy_mgr,
        state_repo=state_repo,
    )

    assert result == "skipped"
    sandbox_mgr.create.assert_not_awaited()
    docker.run_cmd.assert_not_awaited()
    state_repo.upsert.assert_not_awaited()
