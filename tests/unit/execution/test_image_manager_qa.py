"""Test that ContainerReaper sweeps QA orphans by both label families + qa-net-* networks."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.execution.image_manager import ContainerReaper


@pytest.mark.asyncio
async def test_sweep_qa_orphans_removes_old_containers_via_both_labels():
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    # Order:
    #   1. ps for compose label -> "old-cid-1\n"
    #   2. inspect cid 1 -> old timestamp
    #   3. rm cid 1
    #   4. ps for athanor.qa_job_id label -> "old-cid-2\n"
    #   5. inspect cid 2 -> old timestamp
    #   6. rm cid 2
    #   7. network ls -> "qa-net-A\nqa-net-B\n"
    #   8. network rm qa-net-A
    #   9. network rm qa-net-B
    docker.run_cmd = AsyncMock(
        side_effect=[
            "old-cid-1\n",
            "2020-01-01T00:00:00Z",
            "",
            "old-cid-2\n",
            "2020-01-01T00:00:00Z",
            "",
            "qa-net-A\nqa-net-B\n",
            "",
            "",
        ]
    )

    reaper = ContainerReaper(docker=docker, max_age_hours=24)
    await reaper._sweep_qa_orphans()

    cmds = [c.args[0] for c in docker.run_cmd.call_args_list]
    flat = " ".join(" ".join(map(str, c)) for c in cmds)
    assert "com.docker.compose.project=qa_" in flat
    assert "athanor.qa_job_id=" in flat
    assert "rm -f old-cid-1" in flat
    assert "rm -f old-cid-2" in flat
    assert "network rm qa-net-A" in flat
    assert "network rm qa-net-B" in flat


@pytest.mark.asyncio
async def test_sweep_qa_orphans_skips_young_containers():
    """Containers younger than max_age_hours are not removed."""
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    young = (datetime.now(UTC) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    docker.run_cmd = AsyncMock(
        side_effect=[
            "young-cid\n",  # ps for compose label
            young,  # inspect cid -> recent timestamp
            "",  # ps for second label
            "",  # network ls
        ]
    )

    reaper = ContainerReaper(docker=docker, max_age_hours=24)
    await reaper._sweep_qa_orphans()
    cmds = [c.args[0] for c in docker.run_cmd.call_args_list]
    flat = " ".join(" ".join(map(str, c)) for c in cmds)
    assert "rm -f young-cid" not in flat


@pytest.mark.asyncio
async def test_sweep_qa_orphans_swallows_in_use_network():
    """Network removal failure (in-use) is logged but doesn't abort."""
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.run_cmd = AsyncMock(
        side_effect=[
            "",  # ps for compose label -> empty
            "",  # ps for athanor.qa_job_id label -> empty
            "qa-net-busy\n",  # network ls
            RuntimeError("network has active endpoints"),  # network rm
        ]
    )

    reaper = ContainerReaper(docker=docker, max_age_hours=24)
    # Should not raise.
    await reaper._sweep_qa_orphans()
