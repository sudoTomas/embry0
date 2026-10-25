"""Canonical input struct for AgentExecutor implementations.

An AgentInvocation is constructed once per node-level agent call (in
run_agent_node) from the five-level config resolution chain. It is immutable
and carries everything an executor needs to run Claude — no hidden lookups,
no state mutations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from embry0.safety.policy import SafetyPolicy


class SkillMountSpec(TypedDict):
    """A skill source mounted into the sandbox: the SDK loads it as a local plugin."""

    source_id: str
    plugin_name: str
    container_path: str


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

    Built by embry0/orchestration/nodes/agent.py::run_agent_node after
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
    # EMB-36: non-Anthropic provider name serving this model ("xai"), or
    # None for Anthropic. The executor overlays the provider's base_url +
    # container-env API key on the CLI subprocess for this agent only.
    provider: str | None = None
    # Correlation (spec 3.4): the run id minted by run_agent_node before the
    # sandbox exec, and the graph node id this run executes under. Empty for
    # callers that predate them; the executor then falls back to agent_type.
    run_id: str = ""
    node: str = ""
    # Spec 3.3: every ready, enabled skill source SandboxManager mounted into
    # this job's sandbox. build_sdk_options turns each into a local plugin;
    # `skills` is the allowlist over them (and over the repository's own).
    skill_mounts: tuple[SkillMountSpec, ...] = ()
    # Provider fallback (spec 2026-10-25): set when ``model`` stands in for the
    # resolved one because its provider's account is paused, as
    # ``{"instead_of", "provider", "reason", "until"}``. Orchestrator-side
    # only: the sandbox is never sent it.
    fallback: dict[str, Any] | None = None
    # OpenRouter's live catalog: the (input, output) $/Mtok of a model priced
    # from it. The sandbox has no copy of the catalog, so the run's rates ride
    # here; None for every model the static price table covers.
    pricing_usd_per_mtok: tuple[float, float] | None = None
    # Local models: set when ``model`` is the job's local-first model, run in
    # place of the resolved one, as ``{"instead_of"}``. Orchestrator-side only.
    local_first: dict[str, Any] | None = None
