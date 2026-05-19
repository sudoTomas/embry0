from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.cache.volume_manager import (
    SharedVolumeManager,
    VolumeScope,
)


def _stub_docker():
    docker = MagicMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.run_cmd = AsyncMock(return_value="")
    return docker


@pytest.mark.asyncio
async def test_create_per_job_volume_uses_job_id_in_name():
    docker = _stub_docker()
    state_repo = AsyncMock()
    mgr = SharedVolumeManager(docker=docker, state_repo=state_repo)
    name = await mgr.ensure(scope=VolumeScope.PER_JOB, scope_key="job-1")
    assert "job-1" in name
    state_repo.upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_per_repo_uses_repo_in_name():
    docker = _stub_docker()
    state_repo = AsyncMock()
    mgr = SharedVolumeManager(docker=docker, state_repo=state_repo)
    name = await mgr.ensure(scope=VolumeScope.PER_REPO, scope_key="org/r1")
    assert "org" in name and "r1" in name


@pytest.mark.asyncio
async def test_per_org_scope_raises_not_implemented():
    docker = _stub_docker()
    state_repo = AsyncMock()
    mgr = SharedVolumeManager(docker=docker, state_repo=state_repo)
    with pytest.raises(NotImplementedError):
        await mgr.ensure(scope=VolumeScope.PER_ORG, scope_key="org")


@pytest.mark.asyncio
async def test_destroy_removes_volume():
    docker = _stub_docker()
    state_repo = AsyncMock()
    mgr = SharedVolumeManager(docker=docker, state_repo=state_repo)
    await mgr.destroy("athanor-qa-vol-job-1")
    cmds = [" ".join(c.args[0]) for c in docker.run_cmd.call_args_list]
    assert any("volume rm" in s for s in cmds)


@pytest.mark.asyncio
async def test_create_skips_when_volume_already_exists():
    """Ensure is idempotent: re-calling for same scope_key returns same name."""
    docker = _stub_docker()
    docker.run_cmd = AsyncMock(
        side_effect=[
            "athanor-qa-vol-job-1\n",  # `volume ls` returns existing
        ]
    )
    state_repo = AsyncMock()
    state_repo.get = AsyncMock(return_value=None)
    mgr = SharedVolumeManager(docker=docker, state_repo=state_repo)
    name = await mgr.ensure(scope=VolumeScope.PER_JOB, scope_key="job-1")
    assert name == "athanor-qa-vol-job-1"
    # `volume create` was NOT called
    cmds = [" ".join(c.args[0]) for c in docker.run_cmd.call_args_list]
    assert not any("volume create" in s for s in cmds)
