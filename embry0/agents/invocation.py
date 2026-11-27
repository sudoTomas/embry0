"""Canonical input struct for AgentExecutor implementations.

An AgentInvocation is constructed once per node-level agent call (in
run_agent_node) from the five-level config resolution chain. It is immutable
and carries everything an executor needs to run Claude — no hidden lookups,
no state mutations.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
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
class ProviderRecord:
    """What a sandbox needs of the provider a run's model routes to, resolved by the orchestrator at the run's start
    from the deployment's provider catalogue (#173, ADR 0031), so the sandbox reads no table of its own.

    ``rates`` are the (input, output) $/Mtok in force when the run was resolved (``resolved_at``), cache reads and
    writes billed at ``cache_read_factor`` and ``cache_write_factor`` of the input rate. ``base_url`` is an Agent SDK
    provider's endpoint (the CLI's ``ANTHROPIC_BASE_URL``), ``api_root`` a chat-completions provider's; ``key_env`` is
    the sandbox variable holding the provider's key, ``base_url_env`` the one holding an operator-given API root (the
    custom provider). Anthropic's record carries no address and no key variable: the CLI's own serve it.

    ``via_proxy``: the provider is reached through the llm-proxy (ADR 0033): ``api_root`` is the provider's path
    there, and the run authenticates with the sandbox's bearer file, never a key of its own. The orchestrator sets it
    on the record it hands a run (``embry0.execution.agent_runner``), not here.
    """

    name: str
    runtime: Literal["sdk", "chat_completions"]
    upstream_model: str
    rates: tuple[float, float] | None
    cache_read_factor: float
    cache_write_factor: float
    base_url: str = ""
    api_root: str = ""
    key_env: str = ""
    base_url_env: str = ""
    request_extras: Mapping[str, Any] = field(default_factory=dict)
    #: The model's context window in tokens, where the catalogue says one (a custom model); None asks its server.
    context_window: int | None = None
    resolved_at: str = ""
    via_proxy: bool = False
    #: The most output tokens a chat-completions call asks the model for, where the catalogue says
    #: (``max_output_tokens``); None: the executor's default.
    max_output_tokens: int | None = None

    @property
    def charged_from(self) -> Literal["cli", "catalogue"]:
        """What a run's charge comes from: Anthropic's CLI reports what it cost, every other provider's run is
        charged from ``rates`` and its tokens (the design's "Pricing a run")."""
        return "cli" if self.name == ANTHROPIC_NAME else "catalogue"

    def trace_pricing(self) -> dict[str, Any]:
        """What the run's trace records of its pricing (``traces.pricing``): no address, no key variable."""
        return {
            "provider": self.name,
            "model": self.upstream_model,
            "rates": {"in": self.rates[0], "out": self.rates[1]} if self.rates is not None else None,
            "cache": {"read": self.cache_read_factor, "write": self.cache_write_factor},
            "resolved_at": self.resolved_at,
            "charged_from": self.charged_from,
        }

    def to_config(self) -> dict[str, Any]:
        """The record as the sandbox runner's ``--config`` JSON carries it (``provider_record``)."""
        return {
            "name": self.name,
            "runtime": self.runtime,
            "upstream_model": self.upstream_model,
            "rates": list(self.rates) if self.rates is not None else None,
            "cache_read_factor": self.cache_read_factor,
            "cache_write_factor": self.cache_write_factor,
            "base_url": self.base_url,
            "api_root": self.api_root,
            "key_env": self.key_env,
            "base_url_env": self.base_url_env,
            "request_extras": dict(self.request_extras),
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "resolved_at": self.resolved_at,
            "via_proxy": self.via_proxy,
        }

    @classmethod
    def from_config(cls, value: Any) -> ProviderRecord | None:
        """The record ``value`` (``to_config``'s shape) holds; None when it is absent or not one, so the runner
        reads its own table as one that predates records did."""
        if not isinstance(value, Mapping):
            return None
        text = ("name", "upstream_model", "base_url", "api_root", "key_env", "base_url_env", "resolved_at")
        if any(not isinstance(value.get(key, ""), str) for key in text) or not value.get("name"):
            return None
        if value.get("runtime") not in ("sdk", "chat_completions"):
            return None
        factors = [value.get("cache_read_factor"), value.get("cache_write_factor")]
        if any(not _non_negative(f) for f in factors):
            return None
        extras = value.get("request_extras") or {}
        if not isinstance(extras, Mapping):
            return None
        rates = _rate_pair(value.get("rates"))
        if value.get("rates") is not None and rates is None:
            return None
        window = value.get("context_window")
        output = value.get("max_output_tokens")
        if output is not None and (isinstance(output, bool) or not isinstance(output, int) or output <= 0):
            return None
        if window is not None and (isinstance(window, bool) or not isinstance(window, int) or window <= 0):
            return None
        via_proxy = value.get("via_proxy", False)
        if not isinstance(via_proxy, bool):
            return None
        return cls(
            name=str(value["name"]),
            runtime=value["runtime"],
            upstream_model=str(value.get("upstream_model") or ""),
            rates=rates,
            cache_read_factor=float(value["cache_read_factor"]),
            cache_write_factor=float(value["cache_write_factor"]),
            base_url=str(value.get("base_url") or ""),
            api_root=str(value.get("api_root") or ""),
            key_env=str(value.get("key_env") or ""),
            base_url_env=str(value.get("base_url_env") or ""),
            request_extras=dict(extras),
            context_window=window,
            max_output_tokens=output,
            resolved_at=str(value.get("resolved_at") or ""),
            via_proxy=via_proxy,
        )

    @classmethod
    def from_trace_pricing(cls, value: Any) -> ProviderRecord | None:
        """A record that prices a run again from what its trace kept (:meth:`trace_pricing`); None when the trace kept
        none, or not that shape. It is good for pricing only: a trace keeps no runtime, address or key variable, so
        ``runtime`` is a placeholder and the rest are empty."""
        if not isinstance(value, Mapping) or not isinstance(value.get("provider"), str) or not value["provider"]:
            return None
        rates_kept = value.get("rates")
        rates = _rate_pair([rates_kept.get("in"), rates_kept.get("out")]) if isinstance(rates_kept, Mapping) else None
        if rates_kept is not None and rates is None:
            return None
        cache = value.get("cache")
        if not isinstance(cache, Mapping) or not (
            _non_negative(cache.get("read")) and _non_negative(cache.get("write"))
        ):
            return None
        return cls(
            name=value["provider"],
            runtime="sdk",
            upstream_model=str(value.get("model") or ""),
            rates=rates,
            cache_read_factor=float(cache["read"]),
            cache_write_factor=float(cache["write"]),
            resolved_at=str(value.get("resolved_at") or ""),
        )

    def price(
        self, *, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0, cache_creation_tokens: int = 0
    ) -> float | None:
        """USD for this usage at ``rates``; None when the record has none (the model is unpriced)."""
        if self.rates is None:
            return None
        rate_in, rate_out = self.rates
        per_mtok = (
            input_tokens * rate_in
            + output_tokens * rate_out
            + cache_read_tokens * rate_in * self.cache_read_factor
            + cache_creation_tokens * rate_in * self.cache_write_factor
        )
        return per_mtok / 1_000_000


ANTHROPIC_NAME = "anthropic"


def _non_negative(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _rate_pair(value: Any) -> tuple[float, float] | None:
    """``value`` as an (input, output) $/Mtok pair; None when it is not two finite rates of at least 0."""
    if not isinstance(value, list | tuple) or len(value) != 2 or not all(_non_negative(v) for v in value):
        return None
    return (float(value[0]), float(value[1]))


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
    # The (input, output) $/Mtok in force for a model routed to a provider
    # other than Anthropic, as a sandbox image that predates ``provider_record``
    # reads them (#173 PR 2). A runner that reads the record prices from it.
    pricing_usd_per_mtok: tuple[float, float] | None = None
    # #173 PR 3: the provider the model routes to, as the orchestrator resolved
    # it from the deployment's catalogue at the run's start (Anthropic's
    # included). The executors read it instead of the sandbox's own table, and
    # the trace records its pricing. None from an orchestrator that predates
    # it: the runner then reads its table, as before.
    provider_record: ProviderRecord | None = None
    # Local models: set when ``model`` is the job's local-first model, run in
    # place of the resolved one, as ``{"instead_of"}``. Orchestrator-side only.
    local_first: dict[str, Any] | None = None
    # True when a run-time narrowing left the agent no skill: every skill is
    # hidden, the repository's own included. False with an empty ``skills``
    # is no filter. The sandbox reads it as ``skills_hidden`` in its config;
    # a runner that predates it ignores the key and runs with no filter.
    skills_hidden: bool = False
    # False when the workspace is not this run's to trust (a pull request
    # review that runs nothing, #58): the CLI then reads no configuration from
    # it, no project settings, CLAUDE.md, commands, skills or agents, and the
    # Ring-2 rules reach it as flag settings and on its command line instead
    # of through the workspace's settings.json. The sandbox reads it as
    # ``workspace_config`` in its config; a runner that predates it ignores
    # the key and reads the workspace's configuration.
    workspace_config: bool = True
