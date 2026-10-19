"""agent_started names the skill sources the SDK loaded as plugins (spec 3.3)."""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import patch

from claude_agent_sdk import ResultMessage

from embry0.agents.executor import SdkAgentExecutor
from embry0.agents.invocation import AgentInvocation
from embry0.execution import events
from embry0.safety.policy import default_policy_for_agent

_INV = AgentInvocation(
    agent_type="developer",
    prompt="do it",
    system_prompt="",
    system_context="",
    model="claude-sonnet-4-6",
    tools=["Bash"],
    skills=[],
    mcp_servers={},
    max_turns=5,
    timeout_seconds=60,
    execution_mode="sdk",
    auth_mode="oauth",
    safety_policy=default_policy_for_agent("developer"),
    channel_config=None,
)


async def _stream() -> Any:
    yield ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s1",
        total_cost_usd=0.0,
        usage={"input_tokens": 1, "output_tokens": 1},
        result="ok",
    )


async def _agent_started(invocation: AgentInvocation, tmp_path) -> dict[str, Any]:
    seen: list[dict[str, Any]] = []
    with (
        patch.dict("os.environ", {"EMBRY0_WORKSPACE_ROOT": str(tmp_path)}),
        patch("claude_agent_sdk.query", return_value=_stream()),
    ):
        await SdkAgentExecutor().run(invocation, {"configurable": {}, "_test_writer": seen.append})
    [started] = [e for e in seen if e["type"] == "agent_started"]
    return started


async def test_agent_started_lists_the_mounted_skill_sources(tmp_path) -> None:
    mounts = (
        {"source_id": "superpowers", "plugin_name": "superpowers", "container_path": "/opt/embry0/skills/superpowers"},
        {"source_id": "local", "plugin_name": "local", "container_path": "/opt/embry0/skills/local"},
    )
    started = await _agent_started(dataclasses.replace(_INV, skill_mounts=mounts), tmp_path)
    assert started["skill_sources"] == ["superpowers", "local"]


async def test_agent_started_without_mounts_carries_no_skill_sources(tmp_path) -> None:
    started = await _agent_started(_INV, tmp_path)
    assert "skill_sources" not in started, "absent, not an empty list, when nothing is mounted"


def test_the_constructor_omits_skill_sources_unless_given() -> None:
    base = {"model": "m", "max_turns": 1, "run_id": "r", "node": "n", "agent": "a"}
    assert "skill_sources" not in events.agent_started(**base)
    assert events.agent_started(**base, skill_sources=["x"])["skill_sources"] == ["x"]
