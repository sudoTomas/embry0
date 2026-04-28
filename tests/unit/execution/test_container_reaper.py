"""Tests for ContainerReaper: timestamp parsing + active-job intersection."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.execution.image_manager import ContainerReaper


def _ago(hours: float, tz_abbrev: str = "UTC") -> str:
    """Format a UTC timestamp as Docker would: '<ts> <offset> <tz>'."""
    t = datetime.now(UTC) - timedelta(hours=hours)
    return t.strftime(f"%Y-%m-%d %H:%M:%S %z {tz_abbrev}")


@pytest.mark.asyncio
async def test_reaps_old_container_with_no_active_job():
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.build_stop_cmd = MagicMock(return_value=["docker", "stop"])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm"])
    docker.run_cmd = AsyncMock(side_effect=[
        f"abc123\tsandbox-job-1\t{_ago(48)}",  # ps -a output
        "",  # stop
        "",  # rm
    ])
    jobs_repo = MagicMock()
    jobs_repo.fetch_active_sandbox_containers = AsyncMock(return_value=set())
    reaper = ContainerReaper(docker, max_age_hours=24, jobs_repo=jobs_repo)
    await reaper._reap()
    # stop + rm should both have been issued
    assert docker.run_cmd.await_count == 3


@pytest.mark.asyncio
async def test_skips_container_backed_by_active_job():
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.run_cmd = AsyncMock(return_value=f"abc123\tsandbox-job-1\t{_ago(48)}")
    jobs_repo = MagicMock()
    jobs_repo.fetch_active_sandbox_containers = AsyncMock(return_value={"sandbox-job-1"})
    reaper = ContainerReaper(docker, max_age_hours=24, jobs_repo=jobs_repo)
    await reaper._reap()
    # Only the ps -a call; no stop, no rm
    assert docker.run_cmd.await_count == 1


@pytest.mark.asyncio
async def test_skips_container_younger_than_threshold():
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.run_cmd = AsyncMock(return_value=f"abc123\tsandbox-job-1\t{_ago(2)}")
    jobs_repo = MagicMock()
    jobs_repo.fetch_active_sandbox_containers = AsyncMock(return_value=set())
    reaper = ContainerReaper(docker, max_age_hours=24, jobs_repo=jobs_repo)
    await reaper._reap()
    assert docker.run_cmd.await_count == 1


@pytest.mark.asyncio
async def test_handles_unparseable_timestamp_gracefully():
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.run_cmd = AsyncMock(return_value="abc123\tsandbox-job-1\tAbout an hour ago")
    jobs_repo = MagicMock()
    jobs_repo.fetch_active_sandbox_containers = AsyncMock(return_value=set())
    reaper = ContainerReaper(docker, max_age_hours=24, jobs_repo=jobs_repo)
    await reaper._reap()  # no raise; logs warning and skips
    assert docker.run_cmd.await_count == 1


@pytest.mark.asyncio
async def test_fails_closed_when_active_fetch_errors():
    """If we can't determine which jobs are active, do NOT reap (might destroy live work)."""
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.run_cmd = AsyncMock(return_value=f"abc123\tsandbox-job-1\t{_ago(48)}")
    jobs_repo = MagicMock()
    jobs_repo.fetch_active_sandbox_containers = AsyncMock(side_effect=Exception("db down"))
    reaper = ContainerReaper(docker, max_age_hours=24, jobs_repo=jobs_repo)
    await reaper._reap()
    assert docker.run_cmd.await_count == 1  # only ps -a


@pytest.mark.asyncio
async def test_reaps_old_container_with_non_utc_tz_abbrev():
    """Production runs with TZ=America/New_York — Docker emits EDT/EST suffix."""
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.build_stop_cmd = MagicMock(return_value=["docker", "stop"])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm"])
    # Same timestamp as _ago(48), but with EDT suffix and -0400 offset
    t = datetime.now(UTC) - timedelta(hours=48)
    edt = t.strftime("%Y-%m-%d %H:%M:%S -0400 EDT")
    docker.run_cmd = AsyncMock(side_effect=[
        f"abc123\tsandbox-job-1\t{edt}",
        "",
        "",
    ])
    jobs_repo = MagicMock()
    jobs_repo.fetch_active_sandbox_containers = AsyncMock(return_value=set())
    reaper = ContainerReaper(docker, max_age_hours=24, jobs_repo=jobs_repo)
    await reaper._reap()
    assert docker.run_cmd.await_count == 3  # ps + stop + rm
