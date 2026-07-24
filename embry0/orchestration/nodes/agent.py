"""Agent execution node — bridges LangGraph state to AgentExecutor."""

from __future__ import annotations

import os
from typing import Any

import structlog

from embry0.agents.resolver import resolve_agent_invocation
from embry0.agents.session import AgentSession
from embry0.execution.auth_provider import AuthConfigError
from embry0.orchestration.state import AgentOutputEntry

logger = structlog.get_logger(__name__)


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
    # RAV-604 non-code agents: research/analysis are bounded read-and-report
    # passes; ops performs workspace mutations and gets the developer budget.
    "research": 25,
    "analysis": 25,
    "ops": 40,
}


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


async def run_agent_node(
    state: dict[str, Any],
    agent_runner: Any,
    agent_type: str,
    prompt: str,
    model: str = "claude-sonnet-4-6",
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
    # defaults that preserve today's runtime behavior (oauth + empty api_key).
    credentials = credentials or {"api_key": "", "oauth_token": ""}
    global_defaults = global_defaults or {"execution_mode": "sdk", "auth_mode": "oauth"}

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
            template_config=template_config,
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
        "provider": invocation.provider,
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
            resume_session=resume_session,
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
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
    )
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
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cache_read_tokens=result.cache_read_tokens,
                cache_creation_tokens=result.cache_creation_tokens,
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
