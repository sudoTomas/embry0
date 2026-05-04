"""developer_node verifies the branch was pushed before routing to review."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_state(**over):
    base = {
        "job_id": "J",
        "issue_id": "iss-abc12345",
        "repo": "o/r",
        "task": "test task",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_developer_routes_to_review_when_branch_pushed():
    """ls-remote returns non-empty (branch present on origin) → goto=review."""
    from athanor.workflows.issue_to_pr.nodes import developer_node

    docker = MagicMock()
    # Compose a realistic ls-remote output: "<sha>\trefs/heads/<branch>"
    docker.run_cmd = AsyncMock(return_value="abc123def\trefs/heads/athanor/iss-abc12345-test-task")
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])

    fake_result = {
        "agent_outputs": [{
            "agent_type": "developer",
            "is_error": False,
            "output": '{"pr_url": "https://github.com/o/r/pull/9", "branch": "athanor/iss-abc12345-test-task"}',
            "messages": [{"role": "assistant", "content": "ok"}],
            "mode": "anthropic_api",
        }],
        "events": [],
        "total_cost_usd": 0.05,
    }

    with (
        patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=fake_result)),
        patch("athanor.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    assert cmd.goto == "review"
    # The verification call ran
    docker.run_cmd.assert_awaited()
    last_call = docker.run_cmd.await_args[0][0]
    assert "ls-remote" in last_call
    assert "--heads" in last_call


@pytest.mark.asyncio
async def test_developer_routes_to_max_retries_when_branch_not_pushed():
    """ls-remote returns empty → branch missing from remote → goto=max_retries
    with DEV_BRANCH_NOT_PUSHED error_code."""
    from athanor.safety.error_codes import ErrorCode
    from athanor.workflows.issue_to_pr.nodes import developer_node

    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="")  # empty stdout = no such branch
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])

    fake_result = {
        "agent_outputs": [{
            "agent_type": "developer",
            "is_error": False,
            "output": "I made the changes locally",  # no PR URL — agent forgot to push
            "messages": [{"role": "assistant", "content": "done"}],
            "mode": "anthropic_api",
        }],
        "events": [],
        "total_cost_usd": 0.05,
    }

    with (
        patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=fake_result)),
        patch("athanor.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    assert cmd.goto == "max_retries"
    assert cmd.update["error_code"] == ErrorCode.DEV_BRANCH_NOT_PUSHED.value
    assert any("did not push" in e for e in cmd.update["errors"])
    assert cmd.update["current_stage"] == "dev_branch_missing"


@pytest.mark.asyncio
async def test_developer_skips_verification_when_no_docker():
    """No docker in config → skip verification, default to review (don't block on infra absence)."""
    from athanor.workflows.issue_to_pr.nodes import developer_node

    fake_result = {
        "agent_outputs": [{
            "agent_type": "developer",
            "is_error": False,
            "output": "{}",
            "messages": [],
            "mode": "anthropic_api",
        }],
        "events": [],
        "total_cost_usd": 0.05,
    }

    with (
        patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=fake_result)),
        patch("athanor.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}}},  # no docker
        )

    assert cmd.goto == "review"


@pytest.mark.asyncio
async def test_developer_skips_verification_when_docker_call_raises():
    """ls-remote raises (network glitch) → benefit of doubt, default to review."""
    from athanor.workflows.issue_to_pr.nodes import developer_node

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("network glitch"))
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])

    fake_result = {
        "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "{}", "messages": [], "mode": "anthropic_api"}],
        "events": [], "total_cost_usd": 0.05,
    }

    with (
        patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=fake_result)),
        patch("athanor.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    assert cmd.goto == "review"
