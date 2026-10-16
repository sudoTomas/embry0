"""Agent execution node — bridges LangGraph state to AgentExecutor."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import structlog

from embry0.agents.model_policy import TIER_KEYS, build_model_policy
from embry0.agents.resolver import resolve_agent_invocation
from embry0.agents.session import AgentSession
from embry0.execution.auth_provider import AuthConfigError
from embry0.orchestration.budget import (
    BudgetCheckResult,
    BudgetOverrunError,
    check_budget,
    effective_overrun_mode,
    resolve_job_cap,
)
from embry0.orchestration.state import AgentOutputEntry
from embry0.safety.redaction import redact_bounded, redact_secrets, secret_env_values

logger = structlog.get_logger(__name__)

TRACE_SUMMARY_CAP = 500


def _redact_trace_text(text: str) -> str:
    return redact_secrets(text, secret_env_values())


def _env_cap(name: str, default: int) -> int:
    """Integer cap from the environment, falling back on missing/invalid."""
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


# Cap on agent ask-user rounds per job. Env-tunable (RAV-605) because the
# right ceiling differs between untrusted SaaS and a trusted self-hosted
# box. Brainstorming floors at a higher value because design conversations
# legitimately need more turns.
DEFAULT_ASK_USER_CAP = _env_cap("ASK_USER_ROUNDS_CAP", 10)
BRAINSTORMING_ASK_USER_CAP = max(15, DEFAULT_ASK_USER_CAP)
# User-initiated retry rounds after max_retries (consumed by the
# issue_to_pr max_retries node).
USER_RETRY_ROUNDS_CAP = _env_cap("USER_RETRY_ROUNDS_CAP", 5)

# EMB-35: per-role turn budgets. The flat 40 was sized for the developer
# (repo exploration + implementation); triage is a bounded classification
# pass and review a bounded check-and-judge pass — neither should be able
# to burn a developer-sized budget when stuck. Overridable per job via
# pipeline_config.agent_max_turns; unknown agent types fall back to 40.
DEFAULT_AGENT_MAX_TURNS: dict[str, int] = {
    "triage": 15,
    "developer": 40,
    "review": 25,
    "qa": 40,
    # RAV-604 non-code agents: research is a bounded read-and-report pass;
    # ops performs workspace mutations and gets the developer budget.
    "research": 25,
    # RAV-1055: analysis gets the full budget — the watcher feeds it up to
    # WATCHER_MAX_LOG_LINES of production log excerpts, and busy windows
    # repeatedly blew the original 25-turn cap (read-only agent, so the
    # job budget bounds cost, not the turn cap).
    "analysis": 40,
    "ops": 40,
}


def agent_attempt(state: dict[str, Any], agent_type: str) -> int:
    """1 + the number of earlier runs of this agent in the job (agent_outputs is append-only).

    The count is of prior ``agent_outputs`` entries of this ``agent_type``, not of
    node re-entries: a decide-now resume appends two entries in one node entry,
    so the next attempt advances by two, and two generic steps that share an
    agent type read as attempts 1 and 2 of the same node.
    """
    outputs = state.get("agent_outputs") or []
    return 1 + sum(1 for entry in outputs if isinstance(entry, dict) and entry.get("agent_type") == agent_type)


def _crashed_trace_fields(exc: BaseException, error_code: str | None) -> dict[str, Any]:
    """finish() fields for a run whose exec raised: nothing spent, the exception as the summary."""
    return {
        "result": "error",
        "cost_usd": 0.0,
        "duration_ms": 0,
        "tools_called": {},
        "result_summary": str(exc),
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "error_code": error_code,
        "stderr_tail": None,
    }


def _str_or_none(value: Any) -> str | None:
    """A non-empty str, else None — runner doubles in tests hand back mocks."""
    return value if isinstance(value, str) and value else None


class SandboxRequiredError(RuntimeError):
    """Raised when run_agent_node is invoked without an agent_runner.

    There is no in-process fallback by design: running the agent inside the
    orchestrator process would expose the orchestrator's full credential set
    to the agent, defeating the sandbox trust boundary.
    """


def _auth_error_updates(
    agent_type: str,
    exc: AuthConfigError,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Build the failure-state dict for an AuthConfigError from any call site."""
    logger.error(
        "agent_config_error",
        agent_type=agent_type,
        error_code=exc.error_code.value,
        error=str(exc),
    )
    return {
        "agent_outputs": [
            AgentOutputEntry(
                agent_type=agent_type,
                is_error=True,
                error_message=str(exc),
                output="",
                cost_usd=0.0,
                duration_ms=0,
                tools_called={},
            )
        ],
        "errors": [f"{agent_type}: {exc}"],
        "current_stage": f"{agent_type}_failed",
        "total_cost_usd": state.get("total_cost_usd", 0.0),
        "error_code": exc.error_code.value,
    }


async def load_agent_definition(config: Any, agent_type: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """The DB agent_definitions row for ``agent_type``, or the code fallback.

    RAV-602: workflow nodes load their definition (model/tools/skills/
    system_prompt) from the DB so operators can edit them per deployment,
    instead of hardcoding tool lists inline. Best-effort — tests and minimal
    setups without a ``db`` in configurable keep the code defaults, and a
    load failure must never block the agent run.
    """
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    db = configurable.get("db")
    if db is None:
        return fallback
    try:
        from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

        row = await AgentDefinitionsRepository(db).get(agent_type)
    except Exception:
        logger.warning("agent_definition_load_failed", agent_type=agent_type, exc_info=True)
        return fallback
    return row or fallback


async def _provider_config_row(config: Any, *, failure_event: str) -> dict[str, Any] | None:
    """The provider_config row (or its env defaults); None without a DB or on a failed read."""
    db = _graph_configurable(config).get("db")
    if db is None:
        return None
    try:
        from embry0.storage.repositories.provider_config import ProviderConfigRepository

        row = await ProviderConfigRepository(db).get()
    except Exception:
        logger.warning(failure_event, exc_info=True)
        return None
    return row if isinstance(row, dict) else None


async def load_model_policy(config: Any) -> dict[str, Any] | None:
    """The model policy from provider_config, best-effort.

    Shape (``embry0.agents.model_policy``): {"default_model": ...,
    "disabled_models": [...], "tier_models": {"model_heavy": ...,
    "model_medium": ..., "model_light": ...}} — consumed by the resolver: the
    deployment-wide default model (#465) and the RAV-1297 disabled-model tier
    fallback. None (test/minimal setups, load failure, or a row that sets
    neither) means the resolver applies no policy; a provider setting must
    never block an agent run.
    """
    cfg = await _provider_config_row(config, failure_event="model_policy_load_failed")
    if cfg is None:
        return None
    policy = build_model_policy(cfg)
    if not policy["disabled_models"] and not policy["default_model"]:
        return None
    return policy


async def load_triage_model_settings(config: Any) -> tuple[str, dict[str, str]]:
    """``(default_model, tier_models)`` for triage's model guidance (#465).

    Tiers resolve the way ``GET /config/models`` resolves them — the
    provider_config row, then ``Embry0Config`` — and end at the config class
    defaults so the prompt always has a model to name (no-DB callers: unit
    tests, minimal setups). Best-effort like ``load_model_policy``.
    """
    from embry0.config import Embry0Config

    cfg = await _provider_config_row(config, failure_event="triage_model_settings_load_failed") or {}
    policy = build_model_policy(cfg)
    app_config = _graph_configurable(config).get("embry0_config")
    tiers: dict[str, str] = {}
    for tier in TIER_KEYS:
        configured = policy["tier_models"][tier] or getattr(app_config, tier, None)
        tiers[tier] = (
            configured if isinstance(configured, str) and configured else Embry0Config.model_fields[tier].default
        )
    return policy["default_model"], tiers


async def load_budget_policy(config: Any) -> tuple[float, str]:
    """``(per-job ceiling, overrun mode)`` for the job this node belongs to.

    ``budget_config`` is the source of truth: its ``get()`` already resolves
    the operator-edited row over the ``MAX_BUDGET_USD`` / ``BUDGET_OVERRUN_MODE``
    env defaults. ``Settings`` covers the no-DB callers (unit tests, legacy
    setups). Best-effort like ``load_agent_definition`` — an unreadable row
    degrades to the config defaults, never to no cap.

    The ceiling bounds whatever triage (or a job-create override) picked: an
    operator who lowers the fleet-wide per-job cap must not be outvoted by an
    LLM's ``budget_usd``. 0 means unlimited, matching the settings UI.
    """
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    app_config = configurable.get("embry0_config")
    ceiling = _as_float(getattr(app_config, "max_budget_usd", 0.0))
    mode = getattr(app_config, "budget_overrun_mode", "soft")
    db = configurable.get("db")
    if db is not None:
        try:
            from embry0.storage.repositories.budget_config import BudgetConfigRepository

            row = await BudgetConfigRepository(db).get()
        except Exception:
            logger.warning("budget_policy_load_failed", exc_info=True)
        else:
            ceiling = _as_float(row.get("max_budget_per_job_usd")) or ceiling
            mode = row.get("overrun_mode") or mode
    return ceiling, (mode if mode in ("soft", "hard") else "soft")


def _as_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


@dataclass(frozen=True)
class JobCapCheck:
    """Layer 1's verdict on a job's spend so far (#264)."""

    total: float
    cap: float
    mode: str
    decision: BudgetCheckResult


async def evaluate_job_cap(state: dict[str, Any], config: Any, state_total: float) -> JobCapCheck:
    """The job's spend so far against its per-job cap.

    Spend is the sum of the job's trace rows, not graph state: the QA fan-out
    runs agents in sub-graphs whose cost never reaches the parent state, but
    every agent turn writes a trace under the parent job id, so the sum counts
    them (#264). ``state_total`` is the floor — a turn whose trace write
    failed still counts. ``state`` supplies the cap and the job's overrun
    mode; the job id and the trace repository come from the graph config.
    """
    from embry0.services._cost import resolve_job_total_cost

    configurable = _graph_configurable(config)
    traces_repo = configurable.get("traces_repo")
    job_id = configurable.get("job_id", "")
    total = state_total
    if traces_repo is not None and job_id:
        try:
            total = max(state_total, await resolve_job_total_cost(traces_repo, job_id, state_total))
        except Exception:
            logger.warning("job_cost_sum_failed", job_id=job_id, exc_info=True)
    ceiling, overrun_mode = await load_budget_policy(config)
    overrun_mode = effective_overrun_mode(overrun_mode, state.get("budget_overrun_mode_override"))
    cap = resolve_job_cap(state, ceiling=ceiling)
    return JobCapCheck(total=total, cap=cap, mode=overrun_mode, decision=check_budget(total, cap, overrun_mode))


def _graph_configurable(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    configurable = config.get("configurable") or {}
    return configurable if isinstance(configurable, dict) else {}


def _stamp_loaded_preferences(
    updates: dict[str, Any],
    state: dict[str, Any],
    loaded: dict[str, Any] | None,
) -> dict[str, Any]:
    """Put a row this call loaded onto the state update. A row already on state stays there."""
    if isinstance(loaded, dict) and not isinstance(state.get("repo_preferences"), dict):
        return {**updates, "repo_preferences": loaded}
    return updates


async def _load_repo_preferences(state: dict[str, Any], config: Any) -> dict[str, Any] | None:
    """The prefs row for this repo, once. Later nodes read it off state."""
    existing = state.get("repo_preferences")
    if isinstance(existing, dict):
        return existing
    repo = state.get("repo")
    prefs_repo = _graph_configurable(config).get("repo_preferences_repo")
    if not isinstance(repo, str) or not repo or prefs_repo is None:
        return None
    try:
        row = await prefs_repo.get(repo)
    except Exception:
        logger.warning("repo_preferences_load_failed", repo=repo, exc_info=True)
        return None
    return row if isinstance(row, dict) else None


def _global_defaults(passed: dict[str, Any] | None, config: Any) -> dict[str, Any]:
    """Caller-supplied defaults win. Otherwise the deployment config, then oauth/sdk."""
    if passed:
        return passed
    embry0_config = _graph_configurable(config).get("embry0_config")
    return {
        "execution_mode": getattr(embry0_config, "default_execution_mode", None) or "sdk",
        "auth_mode": getattr(embry0_config, "default_auth_mode", None) or "oauth",
    }


def _credentials_for_resolve(credentials: dict[str, str], config: Any) -> dict[str, str]:
    """Copy the credential strings and attach the resolved Anthropic kind, which is not a secret."""
    copied = dict(credentials)
    kinds = _graph_configurable(config).get("llm_credential_kinds") or {}
    if isinstance(kinds, dict):
        kind = kinds.get("anthropic")
        if isinstance(kind, str) and kind:
            copied["anthropic_kind"] = kind
    return copied


async def run_agent_node(
    state: dict[str, Any],
    agent_runner: Any,
    agent_type: str,
    prompt: str,
    model: str = "claude-sonnet-5",
    tools: list[str] | None = None,
    max_turns: int | None = None,
    timeout_seconds: int = 600,
    network: str | None = None,
    on_event: Any | None = None,
    agent_definition: dict[str, Any] | None = None,
    template_config: dict[str, Any] | None = None,
    credentials: dict[str, str] | None = None,
    global_defaults: dict[str, Any] | None = None,
    config: Any | None = None,
    resume_session: AgentSession | None = None,
    node: str | None = None,
) -> dict[str, Any]:
    """Resolve an AgentInvocation and run it via the sandbox executor.

    The fully-resolved invocation is serialized and shipped to the sandbox
    container via ``AgentRunner.run``; the in-container ``SdkAgentExecutor``
    performs the actual Claude call. Passing ``agent_runner=None`` raises
    ``SandboxRequiredError`` — there is no in-process fallback by design.

    ``node`` is the graph node id this run executes under, stamped on every
    event the sandbox emits; it defaults to ``agent_type`` for the nodes
    named after their agent.

    Returns a LangGraph state update dict. Does NOT return a Command here;
    Command returns live in the workflow nodes (see Task 16).
    """
    # Hard-fail immediately if no sandbox runner was supplied. This check must
    # come before any resolution work so that a None runner with bad credentials
    # raises SandboxRequiredError rather than silently returning a soft error.
    if agent_runner is None:
        logger.error(
            "sandbox_required_but_missing",
            agent_type=agent_type,
            job_id=state.get("job_id"),
        )
        raise SandboxRequiredError(
            "agent_runner not configured — refusing to run agent in-process. "
            "The in-process executor fallback was removed in the 2026-04-28 "
            "sandbox safety hardening; agents must run inside the sandbox."
        )

    # Build a default agent_definition if the caller didn't supply one. We
    # thread the model/tools kwargs through so legacy callers still get the
    # same effective configuration.
    agent_definition = agent_definition or {
        "model": model,
        "tools": tools or [],
        "skills": [],
        "system_prompt": "",
        "mcp_servers": {},
        "execution_mode": None,
        "auth_mode": None,
    }
    # If the caller didn't supply credentials/defaults, fall back to conservative
    # defaults. auth_mode comes from config.default_auth_mode when the caller
    # passes none, so a deployment set to api_key is not stuck on oauth.
    credentials = credentials or {"api_key": "", "oauth_token": ""}
    global_defaults = _global_defaults(global_defaults, config)
    loaded_prefs = await _load_repo_preferences(state, config)

    # pipeline_config on state is always the flat PipelineConfig dict (never a
    # TriageDecision wrapper) — triage_node writes the flat inner dict since D4.
    pipeline_config = state.get("pipeline_config") or {}

    # EMB-35: per-agent turn budget. Precedence: explicit caller kwarg (QA
    # scales its own with the criteria count) → triage-emitted
    # pipeline_config.agent_max_turns → role default → 40.
    if max_turns is None:
        configured = (pipeline_config.get("agent_max_turns") or {}).get(agent_type)
        if isinstance(configured, int) and configured > 0:
            max_turns = configured
        else:
            max_turns = DEFAULT_AGENT_MAX_TURNS.get(agent_type, 40)

    # Per-job agent_models override (JobCreateRequest.agent_models, a model
    # label on the ticket) — surfaced onto state by IssueExecutor. It wins
    # over every other level. Handed to the resolver on its own rather than
    # folded into pipeline_config.agent_models: those are triage's picks,
    # which a deployment-wide default model overrides and these do not (#465).
    override_models = state.get("agent_models_override")
    job_agent_models = override_models if isinstance(override_models, dict) else None

    # Model policy: the default model (#465) and the RAV-1297 enablement set
    # with its tier fallbacks, best-effort — None means no policy.
    model_policy = await load_model_policy(config)

    try:
        invocation = resolve_agent_invocation(
            agent_type=agent_type,
            prompt=prompt,
            system_context=state.get("global_context") or "",
            global_defaults=global_defaults,
            repo_prefs=loaded_prefs if isinstance(loaded_prefs, dict) else state.get("repo_preferences"),
            job_overrides={
                "execution_mode": state.get("execution_mode_override"),
                "auth_mode": state.get("auth_mode_override"),
            },
            agent_definition=agent_definition,
            template_config=template_config,
            pipeline_config=pipeline_config or {},
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            credentials=_credentials_for_resolve(credentials, config),
            model_policy=model_policy,
            job_agent_models=job_agent_models,
        )
    except AuthConfigError as exc:
        return _stamp_loaded_preferences(_auth_error_updates(agent_type, exc, state), state, loaded_prefs)

    # RAV-1260: turn-budget awareness. Agents with investigation tools kept
    # investigating until the harness killed them at max_turns with no
    # decision emitted (review job-54a05ee073d6, triage job-9a889c7c42c5).
    # The runner is the one place that knows the final budget for every
    # agent type, so the advisory rides here — appended to the user turn,
    # after any delta-prompt truncation the nodes perform, so prompt caching
    # of the system block is unaffected. Written after resolution: the
    # provider may have scaled the budget (grok), and the advisory must state
    # the one the agent really gets.
    invocation = replace(
        invocation,
        prompt=(
            f"{invocation.prompt}\n\n"
            f"Turn budget: you have {invocation.max_turns} turns for this task. Reserve your final 2 turns to "
            "emit the required final output (decision / verdict / report). If the budget nears exhaustion "
            "before your investigation feels complete, decide from what you already know — a final "
            "output on partial evidence beats hitting the turn cap with none."
        ),
    )

    # Spec 3.4: the run id is minted here, before the exec, so the trace row
    # and every event of this run share it; the sandbox stamps it on events.
    run_id = f"run-{uuid.uuid4().hex}"
    invocation = replace(invocation, run_id=run_id, node=node or agent_type)

    # Build the serialized invocation dict the sandbox runner receives.
    serialized = {
        "agent_type": invocation.agent_type,
        "prompt": invocation.prompt,
        "system_prompt": invocation.system_prompt,
        "system_context": invocation.system_context,
        "model": invocation.model,
        "tools": list(invocation.tools),
        "skills": list(invocation.skills),
        "mcp_servers": dict(invocation.mcp_servers),
        "max_turns": invocation.max_turns,
        "timeout_seconds": invocation.timeout_seconds,
        "execution_mode": invocation.execution_mode,
        "auth_mode": invocation.auth_mode,
        "provider": invocation.provider,
        "run_id": invocation.run_id,
        "node": invocation.node,
    }

    container = state.get("sandbox_container_id", "")

    # Write the trace row as running before the exec so a running agent has a
    # row the job-detail UI can show, and finalize it after. Best-effort —
    # failure to write a trace must not fail the agent run.
    configurable = _graph_configurable(config)
    traces_repo = configurable.get("traces_repo")
    trace_job_id = configurable.get("job_id", "")
    running_row_written = False
    if traces_repo is not None and trace_job_id:
        try:
            await traces_repo.create(
                job_id=trace_job_id, agent_type=agent_type, model=invocation.model, result="running", trace_id=run_id
            )
            running_row_written = True
        except Exception:
            logger.warning("trace_start_persist_failed", agent_type=agent_type, exc_info=True)

    async def _finish_trace(**fields: Any) -> None:
        if traces_repo is None or not trace_job_id:
            return
        # spec 3.6: the summary is redacted at write, and before the cap, so a
        # cut can never leave a fragment no pattern recognises.
        summary = str(fields.get("result_summary") or "")
        fields["result_summary"] = redact_bounded(summary, TRACE_SUMMARY_CAP, redact=_redact_trace_text)
        try:
            if running_row_written:
                await traces_repo.finish(run_id, **fields)
            else:
                # The start write failed, so there is no row to UPDATE; create
                # it now with the final fields under the same run id, so the
                # trace, its spend and its events still join up.
                await traces_repo.create(
                    job_id=trace_job_id, agent_type=agent_type, model=invocation.model, trace_id=run_id, **fields
                )
        except Exception:
            logger.warning("trace_finish_persist_failed", agent_type=agent_type, exc_info=True)

    try:
        # Production path: docker-exec the sandbox runner with the serialized
        # invocation; the in-container SdkAgentExecutor handles the actual
        # Claude call.
        result = await agent_runner.run(
            container=container,
            config=serialized,
            network=network,
            on_event=on_event,
            resume_session=resume_session,
        )
    except AuthConfigError as exc:
        await _finish_trace(**_crashed_trace_fields(exc, exc.error_code.value))
        return _stamp_loaded_preferences(_auth_error_updates(agent_type, exc, state), state, loaded_prefs)
    except Exception as exc:
        # The row says running until told otherwise; a crashed exec must not
        # leave it that way.
        await _finish_trace(**_crashed_trace_fields(exc, None))
        raise

    output_entry = AgentOutputEntry(
        agent_type=result.agent_type,
        is_error=result.is_error,
        error_message=result.error_message,
        output=result.output,
        cost_usd=result.cost_usd,
        duration_ms=result.duration_ms,
        tools_called=result.tools_called,
        policy_denials=getattr(result, "policy_denials", {}) or {},
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
    )
    if getattr(result, "error_code", None):
        output_entry["error_code"] = result.error_code
    resets_at = getattr(result, "provider_resets_at", None)
    if isinstance(resets_at, datetime):
        output_entry["provider_resets_at"] = resets_at.isoformat()
    # invocation.provider is None for Anthropic. The use ledger's key is
    # "anthropic", and that is what the outcome hook filters on.
    output_entry["provider"] = invocation.provider or "anthropic"
    # Plan C closeout: forward post-run conversation state so workflow nodes
    # (triage/developer/review) can persist via AgentSessionsRepository. The
    # values come from SdkAgentExecutor (messages/session_id) and from
    # AgentRunner's post-run docker-cp extraction (session_blob bytes).
    # Only set when present so the TypedDict's total=False semantics hold.
    if getattr(result, "messages", None) is not None:
        output_entry["messages"] = result.messages
    if getattr(result, "session_id", None) is not None:
        output_entry["session_id"] = result.session_id
    if getattr(result, "session_blob", None) is not None:
        output_entry["session_blob"] = result.session_blob

    await _finish_trace(
        result="error" if result.is_error else "success",
        cost_usd=result.cost_usd,
        duration_ms=result.duration_ms,
        tools_called=result.tools_called or {},
        result_summary=result.error_message or result.output or "",
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
        # #281: why THIS run failed, kept per run — a recovered
        # attempt's cause is otherwise gone by the time the job ends.
        # The tail is already redacted and capped by AgentRunner.
        error_code=_str_or_none(getattr(result, "error_code", None)) if result.is_error else None,
        stderr_tail=_str_or_none(getattr(result, "stderr_tail", None)) if result.is_error else None,
    )

    new_total = state.get("total_cost_usd", 0.0) + result.cost_usd
    updates: dict[str, Any] = {
        "agent_outputs": [output_entry],
        "total_cost_usd": new_total,
        "current_stage": f"{agent_type}_complete" if not result.is_error else f"{agent_type}_failed",
    }
    if result.is_error:
        updates["errors"] = [f"{agent_type}: {result.error_message}"]

    # Per-job cap (layer 1). Every agent turn in every workflow lands here, so
    # this is the finest granularity the orchestrator has: `hard` aborts the
    # run at the ceiling, `soft` only records the overrun and lets the node's
    # own routing stop the pipeline after it finishes. The spend is the job's
    # trace sum, which counts QA sub-task turns as well (#264).
    cap_check = await evaluate_job_cap(state, config, new_total)
    decision = cap_check.decision
    if decision.overrun > 0:
        updates["budget_overrun_usd"] = decision.overrun
        logger.warning(
            "job_budget_exceeded",
            agent_type=agent_type,
            total_cost_usd=cap_check.total,
            max_budget_usd=cap_check.cap,
            overrun_usd=decision.overrun,
            overrun_mode=cap_check.mode,
            job_id=(config or {}).get("configurable", {}).get("job_id") if isinstance(config, dict) else None,
        )
    if not decision.allowed:
        raise BudgetOverrunError(cap_check.total, cap_check.cap)

    logger.info(
        "agent_node_complete",
        agent_type=agent_type,
        is_error=result.is_error,
        cost_usd=result.cost_usd,
        execution_mode=invocation.execution_mode,
        auth_mode=invocation.auth_mode,
    )
    return _stamp_loaded_preferences(updates, state, loaded_prefs)


def _enforce_ask_user_cap(
    state: dict[str, Any],
    pending_questions: list[dict[str, Any]],
    *,
    max_rounds: int = DEFAULT_ASK_USER_CAP,
    job_id_for_log: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Apply the job-wide ask_user round cap.

    Returns ``(exhausted, updates)``:
    - ``exhausted=True`` means the cap was hit; ``updates`` carries the
      terminal-failure shape (``current_stage="failed"``, ``error_code``,
      ``errors``, ``agent_questions_exhausted=True``).
    - ``exhausted=False`` means the cap is not yet hit; ``updates`` carries
      ``pending_agent_questions``, ``auto_answered_agent_questions``, and the
      (possibly incremented) ``agent_question_rounds``.

    Auto-answerable questions (``importance == "auto_answerable"``) are split
    out of the blocking set: they don't pause the workflow and don't count
    toward the round cap. They're returned under
    ``auto_answered_agent_questions`` so the caller can persist them as
    ``status='auto_answered'`` rows. If only auto-answerables were emitted the
    cap is neither incremented nor checked for exhaustion — the workflow
    proceeds normally.

    The cap is incremented on every node invocation that produces *blocking*
    questions (triage, developer, review), so the cap is job-wide rather than
    per-node.

    Reads ``agent_question_rounds`` from ``state``, not from any prior call's
    output. Calling twice in the same node body increments only once because
    ``state`` is captured at function entry — the second call sees the same
    snapshot value, not the value written by the first call.
    """
    from embry0.safety.error_codes import ErrorCode

    blocking = [q for q in pending_questions if q.get("importance") != "auto_answerable"]
    auto = [q for q in pending_questions if q.get("importance") == "auto_answerable"]

    # Auto-answerables alone never pause and never count. Persisted as answers,
    # not as pending questions. The cap-exhaustion check is also skipped — an
    # already-exhausted job should still record the auto-answers.
    if not blocking:
        return False, {
            "pending_agent_questions": [],
            "auto_answered_agent_questions": auto,
        }

    current_rounds = int(state.get("agent_question_rounds", 0) or 0)
    if current_rounds >= max_rounds:
        logger.warning(
            "agent_question_rounds_exceeded",
            rounds=current_rounds,
            max_rounds=max_rounds,
            job_id=job_id_for_log or state.get("job_id"),
        )
        return True, {
            "current_stage": "failed",
            "agent_questions_exhausted": True,
            "error_code": ErrorCode.MAX_AGENT_QUESTIONS.value,
            "errors": [f"Agent exceeded {max_rounds} rounds of asking the user — giving up."],
        }
    return False, {
        "pending_agent_questions": blocking,
        "auto_answered_agent_questions": auto,
        "agent_question_rounds": current_rounds + 1,
    }
