"""Agent execution node — bridges LangGraph state to AgentExecutor."""

from __future__ import annotations

from typing import Any

import structlog

from athanor.agents.resolver import resolve_agent_invocation
from athanor.execution.auth_provider import AuthConfigError
from athanor.orchestration.state import AgentOutputEntry

logger = structlog.get_logger(__name__)


# Default cap on agent ask-user rounds per job. Brainstorming overrides
# to a higher value (see BRAINSTORMING_ASK_USER_CAP) because design
# conversations legitimately need more turns.
DEFAULT_ASK_USER_CAP = 5
BRAINSTORMING_ASK_USER_CAP = 15


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


async def run_agent_node(
    state: dict[str, Any],
    agent_runner: Any,
    agent_type: str,
    prompt: str,
    model: str = "claude-sonnet-4-6",
    tools: list[str] | None = None,
    max_turns: int = 40,
    timeout_seconds: int = 300,
    network: str | None = None,
    on_event: Any | None = None,
    agent_definition: dict[str, Any] | None = None,
    credentials: dict[str, str] | None = None,
    global_defaults: dict[str, Any] | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    """Resolve an AgentInvocation and run it via the sandbox executor.

    The fully-resolved invocation is serialized and shipped to the sandbox
    container via ``AgentRunner.run``; the in-container ``SdkAgentExecutor``
    performs the actual Claude call. Passing ``agent_runner=None`` raises
    ``SandboxRequiredError`` — there is no in-process fallback by design.

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
            "The in-process executor fallback was removed in 2026-04-28 sandbox "
            "hardening. See docs/superpowers/specs/2026-04-28-sandbox-safety-hardening-design.md."
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
    # defaults that preserve today's runtime behavior (oauth + empty api_key).
    credentials = credentials or {"api_key": "", "oauth_token": ""}
    global_defaults = global_defaults or {"execution_mode": "sdk", "auth_mode": "oauth"}

    # pipeline_config on state is always the flat PipelineConfig dict (never a
    # TriageDecision wrapper) — triage_node writes the flat inner dict since D4.
    pipeline_config = state.get("pipeline_config") or {}

    # Per-job agent_models override (JobCreateRequest.agent_models) — surfaced
    # onto state by IssueExecutor. It wins over template/definition precedence,
    # so fold it into the pipeline_config.agent_models map the resolver reads.
    override_models = state.get("agent_models_override") or {}
    if isinstance(override_models, dict) and agent_type in override_models:
        merged_models = dict(pipeline_config.get("agent_models") or {})
        merged_models[agent_type] = override_models[agent_type]
        pipeline_config = {**pipeline_config, "agent_models": merged_models}

    try:
        invocation = resolve_agent_invocation(
            agent_type=agent_type,
            prompt=prompt,
            system_context=state.get("global_context") or "",
            global_defaults=global_defaults,
            repo_prefs=state.get("repo_preferences"),
            job_overrides={
                "execution_mode": state.get("execution_mode_override"),
                "auth_mode": state.get("auth_mode_override"),
            },
            agent_definition=agent_definition,
            pipeline_config=pipeline_config or {},
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            credentials=credentials,
        )
    except AuthConfigError as exc:
        return _auth_error_updates(agent_type, exc, state)

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
    }

    container = state.get("sandbox_container_id", "")

    try:
        # Production path: docker-exec the sandbox runner with the serialized
        # invocation; the in-container SdkAgentExecutor handles the actual
        # Claude call.
        result = await agent_runner.run(
            container=container,
            config=serialized,
            network=network,
            on_event=on_event,
        )
    except AuthConfigError as exc:
        return _auth_error_updates(agent_type, exc, state)

    output_entry = AgentOutputEntry(
        agent_type=result.agent_type,
        is_error=result.is_error,
        error_message=result.error_message,
        output=result.output,
        cost_usd=result.cost_usd,
        duration_ms=result.duration_ms,
        tools_called=result.tools_called,
    )

    # Persist a trace row so the job-detail UI's TracesTable / cost_breakdown
    # endpoint actually has data. Best-effort — failure to write a trace must
    # not fail the agent run.
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    traces_repo = configurable.get("traces_repo")
    trace_job_id = configurable.get("job_id", "")
    if traces_repo is not None and trace_job_id:
        try:
            await traces_repo.create(
                job_id=trace_job_id,
                agent_type=result.agent_type,
                model=invocation.model,
                result="error" if result.is_error else "success",
                cost_usd=result.cost_usd,
                duration_ms=result.duration_ms,
                tools_called=result.tools_called or {},
                result_summary=(result.error_message or result.output or "")[:500],
            )
        except Exception:
            logger.warning("trace_persist_failed", agent_type=result.agent_type, exc_info=True)

    updates: dict[str, Any] = {
        "agent_outputs": [output_entry],
        "total_cost_usd": state.get("total_cost_usd", 0.0) + result.cost_usd,
        "current_stage": f"{agent_type}_complete" if not result.is_error else f"{agent_type}_failed",
    }
    if result.is_error:
        updates["errors"] = [f"{agent_type}: {result.error_message}"]

    logger.info(
        "agent_node_complete",
        agent_type=agent_type,
        is_error=result.is_error,
        cost_usd=result.cost_usd,
        execution_mode=invocation.execution_mode,
        auth_mode=invocation.auth_mode,
    )
    return updates


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
      ``pending_agent_questions`` and the incremented ``agent_question_rounds``.

    The cap is incremented on every node invocation that produces questions
    (triage, developer, review), so the cap is job-wide rather than per-node.

    Reads ``agent_question_rounds`` from ``state``, not from any prior call's
    output. Calling twice in the same node body increments only once because
    ``state`` is captured at function entry — the second call sees the same
    snapshot value, not the value written by the first call.
    """
    from athanor.safety.error_codes import ErrorCode

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
        "pending_agent_questions": pending_questions,
        "agent_question_rounds": current_rounds + 1,
    }
