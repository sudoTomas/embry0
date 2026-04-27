"""run_agent_node — assembles AgentInvocation, delegates to executor, returns state update."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from athanor.execution.agent_runner import AgentOutput
from athanor.orchestration.nodes.agent import run_agent_node


@pytest.mark.asyncio
async def test_run_agent_node_builds_invocation_and_delegates(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ATHANOR_WORKSPACE_ROOT", str(tmp_path))

    fake_out = AgentOutput(
        agent_type="developer",
        is_error=False,
        output="ok",
        cost_usd=0.02,
        duration_ms=100,
        tools_called={"Read": 1},
    )
    with patch("athanor.orchestration.nodes.agent.select_executor") as mock_factory:
        mock_executor = AsyncMock()
        mock_executor.run = AsyncMock(return_value=fake_out)
        mock_factory.return_value = mock_executor

        state: dict[str, Any] = {
            "sandbox_container_id": "c1",
            "total_cost_usd": 0.0,
            "pipeline_config": {},
            "agent_models_override": {},
        }
        updates = await run_agent_node(
            state=state,
            agent_runner=None,
            agent_type="developer",
            prompt="do it",
            model="claude-sonnet-4-6",
            tools=["Read"],
            max_turns=10,
            timeout_seconds=60,
            agent_definition={
                "model": "claude-sonnet-4-6",
                "tools": ["Read"],
                "skills": [],
                "system_prompt": "",
                "mcp_servers": {},
                "execution_mode": None,
                "auth_mode": "api_key",
            },
            credentials={"api_key": "sk-xyz", "oauth_token": ""},
            global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        )

    assert updates["total_cost_usd"] == pytest.approx(0.02)
    assert updates["current_stage"] == "developer_complete"
    assert updates["agent_outputs"][0]["cost_usd"] == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_run_agent_node_success() -> None:
    """Legacy calling convention (no agent_definition/credentials/defaults) still works."""
    fake_out = AgentOutput(
        agent_type="developer",
        is_error=False,
        output="Fixed the bug",
        cost_usd=0.42,
        duration_ms=15000,
        tools_called={"Read": 5, "Edit": 2},
    )
    with patch("athanor.orchestration.nodes.agent.select_executor") as mock_factory:
        mock_executor = AsyncMock()
        mock_executor.run = AsyncMock(return_value=fake_out)
        mock_factory.return_value = mock_executor

        state = {"sandbox_container_id": "sandbox-job-123", "total_cost_usd": 1.0}
        result = await run_agent_node(
            state=state,
            agent_runner=None,
            agent_type="developer",
            prompt="Fix the auth bug",
            model="claude-sonnet-4-6",
            tools=["Read", "Write", "Edit", "Bash"],
            credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        )

    assert len(result["agent_outputs"]) == 1
    assert result["agent_outputs"][0]["agent_type"] == "developer"
    assert result["agent_outputs"][0]["cost_usd"] == 0.42
    assert result["total_cost_usd"] == pytest.approx(1.42)
    assert result["current_stage"] == "developer_complete"


@pytest.mark.asyncio
async def test_run_agent_node_error() -> None:
    """Executor-level error (is_error=True) surfaces on the returned updates dict."""
    fake_out = AgentOutput(
        agent_type="developer",
        is_error=True,
        error_message="Timeout after 300s",
    )
    with patch("athanor.orchestration.nodes.agent.select_executor") as mock_factory:
        mock_executor = AsyncMock()
        mock_executor.run = AsyncMock(return_value=fake_out)
        mock_factory.return_value = mock_executor

        state = {"sandbox_container_id": "sandbox-job-123", "total_cost_usd": 0.0}
        result = await run_agent_node(
            state=state,
            agent_runner=None,
            agent_type="developer",
            prompt="Fix the bug",
            credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        )

    assert result["agent_outputs"][0]["is_error"] is True
    assert len(result["errors"]) == 1
    assert "Timeout" in result["errors"][0]
    assert result["current_stage"] == "developer_failed"


@pytest.mark.asyncio
async def test_run_agent_node_with_resolver() -> None:
    """When agent_definition is provided, resolved model/tools reach the executor."""
    captured: dict[str, Any] = {}

    async def _capture(invocation, _config):  # type: ignore[no-untyped-def]
        captured["model"] = invocation.model
        captured["tools"] = list(invocation.tools)
        return AgentOutput(agent_type="developer", output="done", cost_usd=0.5)

    agent_def = {
        "model": "claude-opus-4-6",
        "tools": ["Read", "Write", "Edit"],
        "skills": [],
        "system_prompt": "",
        "mcp_servers": {},
        "execution_mode": None,
        "auth_mode": None,
    }

    with patch("athanor.orchestration.nodes.agent.select_executor") as mock_factory:
        mock_executor = AsyncMock()
        mock_executor.run = _capture
        mock_factory.return_value = mock_executor

        await run_agent_node(
            state={"sandbox_container_id": "c1", "total_cost_usd": 0.0},
            agent_runner=None,
            agent_type="developer",
            prompt="implement feature",
            model="claude-sonnet-4-6",  # default, should be overridden by agent_def
            agent_definition=agent_def,
            credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        )

    assert captured["model"] == "claude-opus-4-6"
    assert captured["tools"] == ["Read", "Write", "Edit"]


@pytest.mark.asyncio
async def test_run_agent_node_config_error_sets_error_code() -> None:
    """AuthConfigError from resolver surfaces as an error entry with error_code set."""
    state = {"sandbox_container_id": "c1", "total_cost_usd": 0.0}
    # auth_mode=api_key but no api_key in credentials -> MISSING_API_KEY
    result = await run_agent_node(
        state=state,
        agent_runner=None,
        agent_type="developer",
        prompt="do it",
        credentials={"api_key": "", "oauth_token": ""},
        global_defaults={"execution_mode": "sdk", "auth_mode": "api_key"},
    )
    assert result["current_stage"] == "developer_failed"
    assert result["agent_outputs"][0]["is_error"] is True
    assert result["error_code"] == "ERR_MISSING_API_KEY"
    assert result["errors"][0].startswith("developer:")


@pytest.mark.asyncio
async def test_run_agent_node_select_executor_error_returns_error_updates(monkeypatch, tmp_path) -> None:
    """If select_executor raises AuthConfigError, return error state (Phase 2 guard)."""
    from athanor.execution.auth_provider import AuthConfigError
    from athanor.safety.error_codes import ErrorCode

    with patch(
        "athanor.orchestration.nodes.agent.select_executor",
        side_effect=AuthConfigError(ErrorCode.INVALID_CONFIG, "forced"),
    ):
        updates = await run_agent_node(
            state={"total_cost_usd": 0.0, "pipeline_config": {}},
            agent_runner=None,
            agent_type="developer",
            prompt="x",
            model="claude-sonnet-4-6",
            tools=["Read"],
            credentials={"api_key": "sk-abc", "oauth_token": ""},
            global_defaults={"execution_mode": "sdk", "auth_mode": "api_key"},
        )

    assert updates["agent_outputs"][0]["is_error"] is True
    assert updates["error_code"] == "ERR_INVALID_CONFIG"
    assert updates["current_stage"] == "developer_failed"


@pytest.mark.asyncio
async def test_run_agent_node_delegates_to_agent_runner_with_full_invocation(monkeypatch, tmp_path) -> None:
    """When agent_runner is provided, run_agent_node serializes the full AgentInvocation."""
    captured_config: dict[str, Any] = {}
    captured_kwargs: dict[str, Any] = {}

    async def fake_run(**kwargs):
        captured_kwargs.update(kwargs)
        captured_config.update(kwargs["config"])
        return AgentOutput(
            agent_type="developer",
            is_error=False,
            output="ok",
            cost_usd=0.01,
            duration_ms=50,
            tools_called={},
        )

    class _Runner:
        run = staticmethod(fake_run)

    updates = await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
        agent_runner=_Runner(),
        agent_type="developer",
        prompt="do it",
        model="claude-sonnet-4-6",
        tools=["Read"],
        agent_definition={
            "model": "claude-sonnet-4-6",
            "tools": ["Read"],
            "skills": ["code-review"],
            "system_prompt": "You are a reviewer.",
            "mcp_servers": {"mcp1": {"command": "npx"}},
            "execution_mode": None,
            "auth_mode": "api_key",
        },
        credentials={"api_key": "sk-abc", "oauth_token": ""},
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
    )

    # Confirm the full invocation made it into the serialized config.
    assert captured_config["skills"] == ["code-review"]
    assert captured_config["mcp_servers"] == {"mcp1": {"command": "npx"}}
    assert captured_config["system_prompt"] == "You are a reviewer."
    assert captured_config["execution_mode"] == "sdk"
    assert captured_config["auth_mode"] == "api_key"
    # And runner receives the container id from state.
    assert captured_kwargs["container"] == "c1"
    assert updates["total_cost_usd"] == pytest.approx(0.01)
    assert updates["current_stage"] == "developer_complete"


@pytest.mark.asyncio
async def test_run_agent_node_per_job_model_override_wins() -> None:
    """agent_models_override on state overrides the agent_definition model."""
    captured: dict[str, Any] = {}

    async def _capture(invocation, _config):  # type: ignore[no-untyped-def]
        captured["model"] = invocation.model
        return AgentOutput(agent_type="developer", output="done", cost_usd=0.0)

    with patch("athanor.orchestration.nodes.agent.select_executor") as mock_factory:
        mock_executor = AsyncMock()
        mock_executor.run = _capture
        mock_factory.return_value = mock_executor

        await run_agent_node(
            state={
                "sandbox_container_id": "c1",
                "total_cost_usd": 0.0,
                "agent_models_override": {"developer": "claude-opus-4-6"},
            },
            agent_runner=None,
            agent_type="developer",
            prompt="do it",
            agent_definition={
                "model": "claude-sonnet-4-6",
                "tools": ["Read"],
                "skills": [],
                "system_prompt": "",
                "mcp_servers": {},
                "execution_mode": None,
                "auth_mode": None,
            },
            credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        )

    assert captured["model"] == "claude-opus-4-6"
