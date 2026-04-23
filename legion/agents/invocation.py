"""Canonical input struct for AgentExecutor implementations.

An AgentInvocation is constructed once per node-level agent call (in
run_agent_node) from the five-level config resolution chain. It is immutable
and carries everything an executor needs to run Claude — no hidden lookups,
no state mutations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from legion.safety.policy import SafetyPolicy


@dataclass(frozen=True)
class ChannelConfig:
    """Per-invocation channel plugin wiring. Present only in cli+oauth cell.

    Phase 1 does not act on this field; it is reserved so Phase 3 additions
    do not require changing AgentInvocation's shape.
    """

    enabled: bool
    platform: Literal["telegram", "discord", "imessage"]
    chat_id: str


@dataclass(frozen=True)
class AgentInvocation:
    """Everything an executor needs to run a single agent turn.

    Built by legion/orchestration/nodes/agent.py::run_agent_node after
    resolving config through the five-level precedence chain. Immutable;
    passed to SdkAgentExecutor and (from Phase 2) CliAgentExecutor without
    reinterpretation.
    """

    agent_type: str
    prompt: str
    system_prompt: str
    system_context: str
    model: str
    tools: list[str]
    skills: list[str]
    mcp_servers: dict[str, Any]
    max_turns: int
    timeout_seconds: int
    execution_mode: Literal["sdk", "cli"]
    auth_mode: Literal["api_key", "oauth"]
    safety_policy: SafetyPolicy
    channel_config: ChannelConfig | None
