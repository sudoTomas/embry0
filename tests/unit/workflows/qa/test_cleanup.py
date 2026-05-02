from unittest.mock import AsyncMock

import pytest

from athanor.workflows.qa.cleanup import cleanup_qa_resources


@pytest.mark.asyncio
async def test_cleanup_runs_all_three_filters_and_removes_network():
    docker = AsyncMock()
    docker._build_base_cmd = lambda: ["docker", "--host", "tcp://dind:2376"]
    docker.run_cmd = AsyncMock(return_value="")

    await cleanup_qa_resources(docker, job_id="JOB1")

    cmds = [c.args[0] for c in docker.run_cmd.call_args_list]
    flat = [" ".join(map(str, c)) for c in cmds]
    assert any("com.docker.compose.project=qa_JOB1" in s for s in flat)
    assert any("athanor.qa_job_id=JOB1" in s for s in flat)
    assert any("network rm qa-net-JOB1" in s for s in flat)


@pytest.mark.asyncio
async def test_cleanup_swallows_already_removed_errors():
    docker = AsyncMock()
    docker._build_base_cmd = lambda: ["docker"]
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("network not found"))
    # Should not raise.
    await cleanup_qa_resources(docker, job_id="JOB2")
