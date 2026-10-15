"""run_agent_node — assembles AgentInvocation, delegates to executor, returns state update."""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from embry0.execution.agent_runner import AgentOutput
from embry0.orchestration.nodes.agent import run_agent_node


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
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))

    fake_out = AgentOutput(
        agent_type="developer",
        is_error=False,
        output="ok",
        cost_usd=0.02,
        duration_ms=100,
        tools_called={"Read": 1},
    )
    with patch("embry0.orchestration.nodes.agent.select_executor") as mock_factory:
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
    with patch("embry0.orchestration.nodes.agent.select_executor") as mock_factory:
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
    with patch("embry0.orchestration.nodes.agent.select_executor") as mock_factory:
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


@pytest.mark.asyncio
async def test_default_auth_mode_reaches_global_defaults() -> None:
    """config.default_auth_mode is the global default when the caller passes none."""

    class _Cfg:
        default_auth_mode = "api_key"
        default_execution_mode = "sdk"

    runner = _make_fake_runner()
    result = await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0},
        agent_runner=runner,
        agent_type="developer",
        prompt="do it",
        credentials={"api_key": "", "oauth_token": "oat-present"},
        config={"configurable": {"embry0_config": _Cfg()}},
    )
    assert result.get("error_code") == "ERR_MISSING_API_KEY", (
        "DEFAULT_AUTH_MODE=api_key must reach global_defaults, "
        f"got error_code={result.get('error_code')!r} stage={result.get('current_stage')!r}"
    )
    assert runner.run.await_count == 0


@pytest.mark.asyncio
async def test_repo_preferences_auth_mode_reaches_repo_prefs() -> None:
    """The prefs row is not on state. The node must load it once and honour auth_mode."""
    calls = {"n": 0}

    class _Prefs:
        async def get(self, repo: str) -> dict[str, str | None]:
            calls["n"] += 1
            assert repo == "acme/widgets"
            return {"repo": repo, "auth_mode": "api_key", "execution_mode": None}

    runner = _make_fake_runner()
    result = await run_agent_node(
        state={"repo": "acme/widgets", "sandbox_container_id": "c1", "total_cost_usd": 0.0},
        agent_runner=runner,
        agent_type="developer",
        prompt="do it",
        credentials={"api_key": "", "oauth_token": "oat-present"},
        config={"configurable": {"repo_preferences_repo": _Prefs()}},
    )
    assert result.get("error_code") == "ERR_MISSING_API_KEY", (
        "repo_preferences.auth_mode must reach the resolver, "
        f"got error_code={result.get('error_code')!r} stage={result.get('current_stage')!r}"
    )
    assert result.get("repo_preferences", {}).get("auth_mode") == "api_key", (
        f"the loaded row must land on state, got {result.get('repo_preferences')!r}"
    )
    assert calls["n"] == 1, f"repo preferences must be loaded once, got {calls['n']}"


@pytest.mark.asyncio
async def test_resolved_kind_from_configurable_reaches_the_resolver() -> None:
    """Both credential strings are set. The mismatch is the kind on configurable."""
    runner = _make_fake_runner()
    result = await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0},
        agent_runner=runner,
        agent_type="developer",
        prompt="do it",
        credentials={"api_key": "sk-abc", "oauth_token": "oat-present"},
        global_defaults={"execution_mode": "sdk", "auth_mode": "api_key"},
        config={"configurable": {"llm_credential_kinds": {"anthropic": "llm_oauth"}}},
    )
    assert result.get("error_code") == "ERR_MISSING_API_KEY", (
        "configurable llm_credential_kinds must reach the resolver, "
        f"got error_code={result.get('error_code')!r} stage={result.get('current_stage')!r}"
    )


@pytest.mark.skip(reason="In-process executor fallback removed in 2026-04-28-sandbox-hardening")
@pytest.mark.asyncio
async def test_run_agent_node_select_executor_error_returns_error_updates(monkeypatch, tmp_path) -> None:
    """If select_executor raises AuthConfigError, return error state (Phase 2 guard)."""
    from embry0.execution.auth_provider import AuthConfigError
    from embry0.safety.error_codes import ErrorCode

    with patch(
        "embry0.orchestration.nodes.agent.select_executor",
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
    from embry0.execution.auth_provider import AuthConfigError
    from embry0.safety.error_codes import ErrorCode

    runner = AsyncMock()
    runner.run = AsyncMock(side_effect=AuthConfigError(ErrorCode.INVALID_CONFIG, "token rejected by upstream"))

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
    """After a successful run, the running trace row is finalized via traces_repo.finish."""
    fake_out = AgentOutput(
        agent_type="developer",
        is_error=False,
        output="all good",
        cost_usd=1.5,
        duration_ms=2000,
        tools_called={"Read": 3, "Edit": 1},
    )
    traces_repo = AsyncMock()
    traces_repo.create = AsyncMock(return_value="run-abcdef")

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
    assert traces_repo.create.call_args.kwargs["job_id"] == "job-test"
    assert traces_repo.create.call_args.kwargs["agent_type"] == "developer"
    assert traces_repo.finish.await_count == 1
    call_kwargs = traces_repo.finish.call_args.kwargs
    assert call_kwargs["cost_usd"] == pytest.approx(1.5)
    assert call_kwargs["duration_ms"] == 2000
    assert call_kwargs["result"] == "success"
    assert call_kwargs["tools_called"] == {"Read": 3, "Edit": 1}


@pytest.mark.asyncio
async def test_run_agent_node_persists_token_counts_on_trace_and_state() -> None:
    """EMB-35: token fields flow from AgentOutput into traces_repo.finish kwargs
    and onto the agent_outputs state entry."""
    fake_out = AgentOutput(
        agent_type="developer",
        is_error=False,
        output="ok",
        cost_usd=0.9,
        duration_ms=1000,
        input_tokens=1500,
        output_tokens=250,
        cache_read_tokens=88000,
        cache_creation_tokens=3000,
    )
    traces_repo = AsyncMock()
    traces_repo.create = AsyncMock(return_value="trc-token")

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
            "tools": ["Read"],
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={"configurable": {"job_id": "job-test", "traces_repo": traces_repo}},
    )

    call_kwargs = traces_repo.finish.call_args.kwargs
    assert call_kwargs["input_tokens"] == 1500
    assert call_kwargs["output_tokens"] == 250
    assert call_kwargs["cache_read_tokens"] == 88000
    assert call_kwargs["cache_creation_tokens"] == 3000

    entry = updates["agent_outputs"][0]
    assert entry["input_tokens"] == 1500
    assert entry["output_tokens"] == 250
    assert entry["cache_read_tokens"] == 88000
    assert entry["cache_creation_tokens"] == 3000


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


@pytest.mark.asyncio
async def test_run_agent_node_appends_turn_budget_advisory() -> None:
    """RAV-1260: every agent prompt carries its actual turn budget plus the
    reserve-final-turns instruction, so agents decide before the cap kills
    them instead of investigating to death."""
    captured_config: dict[str, Any] = {}

    class _Runner:
        async def run(self, container: str, config: dict, network=None, on_event=None, resume_session=None):
            captured_config.update(config)
            return AgentOutput(
                agent_type="triage",
                is_error=False,
                error_message=None,
                output="ok",
                cost_usd=0.0,
                duration_ms=5,
                tools_called={},
            )

    updates = await run_agent_node(
        state={"sandbox_container_id": "c1", "pipeline_config": {}},
        agent_runner=_Runner(),
        agent_type="triage",
        prompt="classify this issue",
        credentials={"api_key": "sk-abc", "oauth_token": ""},
        global_defaults={"execution_mode": "sdk", "auth_mode": "api_key"},
    )

    assert not updates.get("errors")
    prompt = captured_config["prompt"]
    assert prompt.startswith("classify this issue")
    # Triage's role default is 15 turns; the advisory must state the REAL budget.
    assert "Turn budget: you have 15 turns" in prompt
    assert "Reserve your final 2 turns" in prompt
    assert captured_config["max_turns"] == 15


@pytest.mark.asyncio
async def test_turn_budget_advisory_states_the_provider_scaled_budget() -> None:
    """The advisory names the budget the agent really gets. A grok agent's budget is scaled
    after its model is resolved, so an advisory written before resolution would tell grok
    triage it has 15 turns while it has 30."""
    captured_config: dict[str, Any] = {}

    class _Runner:
        async def run(self, container: str, config: dict, network=None, on_event=None, resume_session=None):
            captured_config.update(config)
            return AgentOutput(
                agent_type="triage",
                is_error=False,
                error_message=None,
                output="ok",
                cost_usd=0.0,
                duration_ms=5,
                tools_called={},
            )

    updates = await run_agent_node(
        state={"sandbox_container_id": "c1", "pipeline_config": {}, "agent_models_override": {"triage": "grok-4.7"}},
        agent_runner=_Runner(),
        agent_type="triage",
        prompt="classify this issue",
        credentials={"api_key": "sk-abc", "oauth_token": ""},
        global_defaults={"execution_mode": "sdk", "auth_mode": "api_key"},
        timeout_seconds=180,
    )

    assert not updates.get("errors")
    assert captured_config["model"] == "grok-4.7"
    assert (captured_config["max_turns"], captured_config["timeout_seconds"]) == (30, 360)
    assert "Turn budget: you have 30 turns" in captured_config["prompt"], "advisory must state the scaled budget"


async def _run_with_trace(fake_out: AgentOutput) -> tuple[dict[str, Any], dict[str, Any]]:
    traces_repo = AsyncMock()
    traces_repo.create = AsyncMock(return_value="trc-x")
    runner = AsyncMock()
    runner.run = AsyncMock(return_value=fake_out)
    updates = await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
        agent_runner=runner,
        agent_type="developer",
        prompt="do it",
        agent_definition={
            "model": "claude-opus-4-7",
            "tools": ["Read"],
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={"configurable": {"job_id": "job-test", "traces_repo": traces_repo}},
    )
    return updates, traces_repo.finish.call_args.kwargs


@pytest.mark.asyncio
async def test_errored_run_records_its_code_and_stderr_tail_on_the_trace() -> None:
    """#281: the cause of an errored run survives per RUN, on its trace row —
    and stays out of graph state, which is checkpointed and streamed."""
    updates, trace = await _run_with_trace(
        AgentOutput(
            agent_type="developer",
            is_error=True,
            error_message="Command failed with exit code 1",
            error_code="ERR_AGENT_PROCESS_FAILED",
            stderr_tail="ENOSPC: no space left on device",
        )
    )
    assert trace["result"] == "error"
    assert trace["error_code"] == "ERR_AGENT_PROCESS_FAILED"
    assert trace["stderr_tail"] == "ENOSPC: no space left on device"
    assert updates["agent_outputs"][0]["error_code"] == "ERR_AGENT_PROCESS_FAILED"
    assert "stderr_tail" not in updates["agent_outputs"][0]


@pytest.mark.asyncio
async def test_agent_output_records_the_ledger_provider() -> None:
    """Anthropic's invocation.provider is None; the use ledger key is anthropic.
    A grok model records xai. The outcome hook filters on this field."""
    updates, _trace = await _run_with_trace(AgentOutput(agent_type="developer", output="ok"))
    assert updates["agent_outputs"][0].get("provider") == "anthropic", (
        "an Anthropic run must record provider anthropic, not omit it"
    )

    traces_repo = AsyncMock()
    traces_repo.create = AsyncMock(return_value="trc-x")
    runner = AsyncMock()
    runner.run = AsyncMock(return_value=AgentOutput(agent_type="developer", output="ok"))
    grok = await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
        agent_runner=runner,
        agent_type="developer",
        prompt="do it",
        agent_definition={
            "model": "grok-4.5",
            "tools": ["Read"],
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
        credentials={"api_key": "", "oauth_token": ""},
        config={"configurable": {"job_id": "job-test", "traces_repo": traces_repo}},
    )
    assert grok["agent_outputs"][0].get("provider") == "xai", "a grok run must record provider xai"


@pytest.mark.asyncio
async def test_successful_run_records_no_failure_fields() -> None:
    _, trace = await _run_with_trace(AgentOutput(agent_type="developer", output="ok", stderr_tail="ignored"))
    assert trace["error_code"] is None
    assert trace["stderr_tail"] is None


@pytest.mark.asyncio
async def test_run_agent_node_writes_a_running_trace_row_before_the_exec_and_finishes_it() -> None:
    order: list[str] = []
    traces_repo = AsyncMock()

    async def _create(**kwargs: Any) -> str:
        order.append("create")
        assert kwargs["result"] == "running", "the row written before the exec must say running"
        assert kwargs["trace_id"], "the run id is the trace id"
        return kwargs["trace_id"]

    async def _finish(trace_id: str, **kwargs: Any) -> None:
        order.append("finish")
        assert kwargs["result"] == "success"

    traces_repo.create = AsyncMock(side_effect=_create)
    traces_repo.finish = AsyncMock(side_effect=_finish)

    runner = AsyncMock()

    async def _run(**kwargs: Any) -> AgentOutput:
        order.append("exec")
        assert kwargs["config"]["run_id"], "the sandbox must receive the run id"
        assert kwargs["config"]["node"] == "generic_agent"
        return AgentOutput(agent_type="research", is_error=False, output="ok", cost_usd=0.1, duration_ms=5)

    runner.run = AsyncMock(side_effect=_run)
    await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
        agent_runner=runner,
        agent_type="research",
        node="generic_agent",
        prompt="go",
        agent_definition={
            "model": "claude-sonnet-5",
            "tools": [],
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
        credentials={"api_key": "", "oauth_token": "t"},
        config={"configurable": {"job_id": "job-test", "traces_repo": traces_repo}},
    )
    assert order == ["create", "exec", "finish"]
    assert traces_repo.finish.call_args.args[0] == traces_repo.create.call_args.kwargs["trace_id"], (
        "finish must finalize the row create wrote"
    )
    assert re.fullmatch(r"run-[0-9a-f]{32}", traces_repo.create.call_args.kwargs["trace_id"]), (
        "spec 3.4: the run id is a full UUID4, not a 12-hex prefix"
    )


@pytest.mark.asyncio
async def test_run_agent_node_defaults_node_to_the_agent_type() -> None:
    runner = _make_fake_runner()
    await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
        agent_runner=runner,
        agent_type="developer",
        prompt="go",
        agent_definition={
            "model": "claude-sonnet-5",
            "tools": [],
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
        credentials={"api_key": "", "oauth_token": "t"},
    )
    assert runner.run.call_args.kwargs["config"]["node"] == "developer"


@pytest.mark.asyncio
async def test_run_agent_node_trace_finish_failure_does_not_fail_run() -> None:
    """The running row persisted; finalizing it fails. The run's outcome still returns."""
    traces_repo = AsyncMock()
    traces_repo.create = AsyncMock(return_value="run-x")
    traces_repo.finish = AsyncMock(side_effect=RuntimeError("DB unreachable"))
    updates = await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
        agent_runner=_make_fake_runner(cost_usd=0.5),
        agent_type="developer",
        prompt="do it",
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={"configurable": {"job_id": "job-test", "traces_repo": traces_repo}},
    )
    assert updates["current_stage"] == "developer_complete"
    assert updates["total_cost_usd"] == pytest.approx(0.5)
    assert traces_repo.finish.await_count == 1


def test_agent_attempt_counts_prior_runs_of_the_same_agent() -> None:
    from embry0.orchestration.nodes.agent import agent_attempt

    state = {"agent_outputs": [{"agent_type": "developer"}, {"agent_type": "review"}, {"agent_type": "developer"}]}
    assert agent_attempt(state, "developer") == 3
    assert agent_attempt(state, "review") == 2
    assert agent_attempt({}, "triage") == 1


@pytest.mark.asyncio
async def test_a_run_whose_exec_raises_finishes_its_trace_row_as_error() -> None:
    """The row was written running before the exec; a crash must not leave it running."""
    traces_repo = AsyncMock()
    traces_repo.create = AsyncMock(return_value="run-x")
    runner = AsyncMock()
    runner.run = AsyncMock(side_effect=RuntimeError("docker exec lost"))
    with pytest.raises(RuntimeError, match="docker exec lost"):
        await run_agent_node(
            state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
            agent_runner=runner,
            agent_type="developer",
            prompt="do it",
            credentials={"api_key": "", "oauth_token": "oauth-xyz"},
            config={"configurable": {"job_id": "job-test", "traces_repo": traces_repo}},
        )
    assert traces_repo.finish.await_count == 1, "a crashed exec must still finalize the running row"
    finished = traces_repo.finish.call_args
    assert finished.args[0] == traces_repo.create.call_args.kwargs["trace_id"]
    assert finished.kwargs["result"] == "error"
    assert finished.kwargs["result_summary"] == "docker exec lost"
    assert finished.kwargs["cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_an_auth_error_from_the_exec_finishes_its_trace_row_with_the_code() -> None:
    from embry0.execution.auth_provider import AuthConfigError
    from embry0.safety.error_codes import ErrorCode

    traces_repo = AsyncMock()
    traces_repo.create = AsyncMock(return_value="run-x")
    runner = AsyncMock()
    runner.run = AsyncMock(side_effect=AuthConfigError(ErrorCode.MISSING_API_KEY, "no key"))
    updates = await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
        agent_runner=runner,
        agent_type="developer",
        prompt="do it",
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={"configurable": {"job_id": "job-test", "traces_repo": traces_repo}},
    )
    assert updates["current_stage"] == "developer_failed"
    assert traces_repo.finish.await_count == 1
    assert traces_repo.finish.call_args.kwargs["result"] == "error"
    assert traces_repo.finish.call_args.kwargs["error_code"] == ErrorCode.MISSING_API_KEY.value
