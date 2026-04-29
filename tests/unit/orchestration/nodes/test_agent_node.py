"""run_agent_node — assembles AgentInvocation, delegates to executor, returns state update."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from athanor.execution.agent_runner import AgentOutput
from athanor.orchestration.nodes.agent import run_agent_node


def _make_fake_runner(agent_type: str = "developer", **kwargs: Any) -> AsyncMock:
    """Return an AsyncMock runner whose .run returns a minimal successful AgentOutput."""
    defaults = {
        "is_error": False,
        "error_message": None,
        "output": "ok",
        "cost_usd": 0.0,
        "duration_ms": 10,
        "tools_called": {},
    }
    defaults.update(kwargs)
    out = AgentOutput(agent_type=agent_type, **defaults)
    runner = AsyncMock()
    runner.run = AsyncMock(return_value=out)
    return runner


@pytest.mark.skip(reason="In-process executor fallback removed in 2026-04-28-sandbox-hardening")
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


@pytest.mark.skip(reason="In-process executor fallback removed in 2026-04-28-sandbox-hardening")
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


@pytest.mark.skip(reason="In-process executor fallback removed in 2026-04-28-sandbox-hardening")
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
    """When agent_definition is provided, resolved model/tools reach the sandbox runner."""
    captured: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> AgentOutput:
        captured["model"] = kwargs["config"]["model"]
        captured["tools"] = kwargs["config"]["tools"]
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

    class _Runner:
        run = staticmethod(_capture)

    await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0},
        agent_runner=_Runner(),
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
    """AuthConfigError from resolver surfaces as an error entry with error_code set.

    We must supply a real runner (not None) so agent_runner is None guard does not
    fire first. The AuthConfigError is raised by resolve_agent_invocation because
    auth_mode=api_key with an empty api_key — the runner.run path is never reached.
    """
    state = {"sandbox_container_id": "c1", "total_cost_usd": 0.0}
    # auth_mode=api_key but no api_key in credentials -> MISSING_API_KEY
    result = await run_agent_node(
        state=state,
        agent_runner=_make_fake_runner(),
        agent_type="developer",
        prompt="do it",
        credentials={"api_key": "", "oauth_token": ""},
        global_defaults={"execution_mode": "sdk", "auth_mode": "api_key"},
    )
    assert result["current_stage"] == "developer_failed"
    assert result["agent_outputs"][0]["is_error"] is True
    assert result["error_code"] == "ERR_MISSING_API_KEY"
    assert result["errors"][0].startswith("developer:")


@pytest.mark.skip(reason="In-process executor fallback removed in 2026-04-28-sandbox-hardening")
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
async def test_run_agent_node_runner_auth_error_returns_error_updates() -> None:
    """AuthConfigError raised by runner.run is caught and returned as a soft error dict."""
    from athanor.execution.auth_provider import AuthConfigError
    from athanor.safety.error_codes import ErrorCode

    runner = AsyncMock()
    runner.run = AsyncMock(
        side_effect=AuthConfigError(ErrorCode.INVALID_CONFIG, "token rejected by upstream")
    )

    updates = await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
        agent_runner=runner,
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
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
    )

    assert updates["current_stage"] == "developer_failed"
    assert updates["agent_outputs"][0]["is_error"] is True
    assert updates["error_code"] == ErrorCode.INVALID_CONFIG.value
    assert "token rejected by upstream" in updates["errors"][0]
    runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_agent_node_delegates_to_agent_runner_with_full_invocation(monkeypatch, tmp_path) -> None:
    """When agent_runner is provided, run_agent_node serializes the full AgentInvocation."""
    captured_config: dict[str, Any] = {}
    captured_kwargs: dict[str, Any] = {}

    async def fake_run(**kwargs: Any) -> AgentOutput:
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

    async def _capture(**kwargs: Any) -> AgentOutput:
        captured["model"] = kwargs["config"]["model"]
        return AgentOutput(agent_type="developer", output="done", cost_usd=0.0)

    class _Runner:
        run = staticmethod(_capture)

    await run_agent_node(
        state={
            "sandbox_container_id": "c1",
            "total_cost_usd": 0.0,
            "agent_models_override": {"developer": "claude-opus-4-6"},
        },
        agent_runner=_Runner(),
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


@pytest.mark.asyncio
async def test_run_agent_node_persists_trace_on_success() -> None:
    """After a successful run, a trace row is written via traces_repo.create."""
    fake_out = AgentOutput(
        agent_type="developer",
        is_error=False,
        output="all good",
        cost_usd=1.5,
        duration_ms=2000,
        tools_called={"Read": 3, "Edit": 1},
    )
    traces_repo = AsyncMock()
    traces_repo.create = AsyncMock(return_value="trc-abcdef")

    runner = AsyncMock()
    runner.run = AsyncMock(return_value=fake_out)

    state: dict[str, Any] = {
        "sandbox_container_id": "c1",
        "total_cost_usd": 0.0,
        "pipeline_config": {},
    }
    updates = await run_agent_node(
        state=state,
        agent_runner=runner,
        agent_type="developer",
        prompt="do it",
        agent_definition={
            "model": "claude-opus-4-7",
            "tools": ["Read", "Edit"],
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={"configurable": {"job_id": "job-test", "traces_repo": traces_repo}},
    )

    assert updates["current_stage"] == "developer_complete"
    assert traces_repo.create.await_count == 1
    call_kwargs = traces_repo.create.call_args.kwargs
    assert call_kwargs["job_id"] == "job-test"
    assert call_kwargs["agent_type"] == "developer"
    assert call_kwargs["cost_usd"] == pytest.approx(1.5)
    assert call_kwargs["duration_ms"] == 2000
    assert call_kwargs["result"] == "success"
    assert call_kwargs["tools_called"] == {"Read": 3, "Edit": 1}


@pytest.mark.asyncio
async def test_run_agent_node_trace_persist_failure_does_not_fail_run() -> None:
    """If traces_repo.create raises, the agent run still returns a valid updates dict."""
    fake_out = AgentOutput(
        agent_type="developer",
        is_error=False,
        output="done",
        cost_usd=0.5,
        duration_ms=1000,
        tools_called={},
    )
    traces_repo = AsyncMock()
    traces_repo.create = AsyncMock(side_effect=RuntimeError("DB unreachable"))

    runner = AsyncMock()
    runner.run = AsyncMock(return_value=fake_out)

    state: dict[str, Any] = {
        "sandbox_container_id": "c1",
        "total_cost_usd": 0.0,
        "pipeline_config": {},
    }
    updates = await run_agent_node(
        state=state,
        agent_runner=runner,
        agent_type="developer",
        prompt="do it",
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={"configurable": {"job_id": "job-test", "traces_repo": traces_repo}},
    )

    # Agent run must succeed regardless of trace write failure.
    assert updates["current_stage"] == "developer_complete"
    assert updates["total_cost_usd"] == pytest.approx(0.5)
    assert traces_repo.create.await_count == 1
