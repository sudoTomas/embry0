"""triage node + QA-decision / failure-routing helpers (RAV-1235 split of nodes.py)."""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command

# Shared seams (run_agent_node, get_stream_writer, interrupt,
# _verify_branch_pushed) are resolved through the package namespace at CALL
# time so test patches on `embry0.workflows.issue_to_pr.nodes.<seam>` reach
# this module. The self-import is safe: the package __init__ binds the seams
# before importing the node modules.
import embry0.workflows.issue_to_pr.nodes as _nodes
from embry0.execution import events
from embry0.orchestration.nodes.agent import (
    DEFAULT_ASK_USER_CAP,
    agent_attempt,
)
from embry0.workflows.issue_to_pr.nodes._common import (
    DECIDE_NOW_MAX_TURNS,
    _extract_ask_user_events,
    _format_user_answers_block,
    _max_turns_recovery_session,
)
from embry0.workflows.issue_to_pr.specialist_agents import SPECIALIST_TEMPLATE_NAMES

logger = structlog.get_logger(__name__)


def _log_triage_agent_failure(event: str, output: dict[str, Any], *, fallback: str) -> str:
    """Log a failed triage run with the fields the next tick can diagnose.

    The event used to carry only the SDK's closing sentence. An is_error result
    whose subtype is ``success`` then looked like a contradiction and hid the
    result text, the error code, and the agent output. Those fields are
    redacted: this line ships to the log store.
    """
    from embry0.safety.redaction import redacted_log_field

    message = str(output.get("error_message") or fallback)
    logger.error(
        event,
        error=redacted_log_field(message),
        error_message=redacted_log_field(message),
        error_code=output.get("error_code") or None,
        result=redacted_log_field(str(output.get("output") or "")),
    )
    return message


async def triage_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any] | Command[str]:
    """Run triage agent via AgentRunner inside the sandbox.

    Returns a plain dict in the normal case (``route_after_triage`` decides the
    next node via conditional edge). Returns a ``Command`` to self-route when
    pending agent questions exist (→ ``ask_user_interrupt``) or the question
    cap is exhausted (→ ``max_retries``), mirroring the pattern used by
    ``developer_node`` and ``review_node``.

    Raises SandboxRequiredError if agent_runner or container_id is absent —
    there is no in-process fallback. See Plan A § 4.4.
    """
    import os

    from embry0.orchestration.nodes.agent import SandboxRequiredError

    writer = _nodes.get_stream_writer()
    attempt = agent_attempt(state, "triage")
    writer(events.node_started(node="triage", agent="triage", attempt=attempt))

    # EMB-35: TRIAGE_MODEL lets triage tier independently of the pipeline
    # default (it's a bounded classification pass — a haiku-class experiment
    # only needs an env flip, no deploy-coupled code change).
    model = os.environ.get("TRIAGE_MODEL") or os.environ.get("DEFAULT_MODEL", "claude-sonnet-5")
    agent_runner = config["configurable"].get("agent_runner")
    credentials = config["configurable"].get("credentials") or {}
    agent_sessions_repo = config["configurable"].get("agent_sessions_repo")
    container_id = state.get("sandbox_container_id")

    if agent_runner is None or container_id is None:
        raise SandboxRequiredError(
            "triage_node requires a sandbox (agent_runner and container_id). "
            "The legacy in-process triage fallback was removed in Plan A finalisation "
            "(2026-04-28). Ensure the sandbox is initialised before triage runs."
        )

    # Run triage inside sandbox via AgentRunner
    from embry0.orchestration.nodes.triage import build_triage_system_prompt, parse_triage_response

    repo = state.get("repo", "")
    task = state.get("task", "")
    issue_number = state.get("issue_number")
    additional = state.get("additional_context", "")

    # QA-failure re-entry context: when the QA loop routed back to triage,
    # the agent must see WHAT failed to choose a qa_failure_action. Included
    # in both the full prompt and the resumed-session delta prompt.
    qa_state_pre = state.get("qa") or {}
    qa_failure_block = ""
    if qa_state_pre.get("final_status") == "failed":
        outcome = qa_state_pre.get("outcome") or {}
        failure_summary = outcome.get("failure_summary") or "no failure summary recorded"
        rounds = qa_state_pre.get("failure_rounds", 0)
        max_rounds = qa_state_pre.get("max_qa_failure_rounds", 2)
        qa_failure_block = (
            f"QA FAILED (failure round {rounds} of max {max_rounds}).\n"
            f"Failure summary:\n{failure_summary}\n\n"
            "Decide and emit exactly one qa_failure_action "
            "(retry_developer | rerun_qa | ask_user) as instructed."
        )

    # RAV-1271: a triage-asked question answered via ask_user_interrupt
    # re-enters HERE (the interrupt node routes the resume back to the asking
    # node). Fold the answers into the prompt so the re-decision sees them.
    user_answers_block = _format_user_answers_block(state.get("user_answers"))

    prompt = f"Repository: {repo}\n"
    if issue_number:
        prompt += f"Issue #{issue_number}\n"
    prompt += f"\nTask:\n{task}"
    if additional:
        prompt += f"\n\nPrevious Q&A:\n{additional}"
    if qa_failure_block:
        prompt += f"\n\n{qa_failure_block}"
    if user_answers_block:
        prompt += f"\n\n{user_answers_block}"

    # RAV-601: list available templates so triage can pick one by NAME. Rides
    # the user prompt — the system prompt carries nothing job-specific, so
    # CLI prompt caching keeps covering it (EMB-35).
    templates_repo = config["configurable"].get("pipeline_templates_repo")
    if templates_repo is not None:
        try:
            template_rows = await templates_repo.list_all()
        except Exception:
            logger.warning("triage_template_list_failed", exc_info=True)
            template_rows = []
        template_lines = []
        for row in template_rows:
            # A specialist runs only when the job names its template.
            if row["name"] in SPECIALIST_TEMPLATE_NAMES:
                continue
            entry = f"- {row['name']}"
            if row.get("default_for_kind"):
                entry += f" [default for job_kind={row['default_for_kind']}]"
            if row.get("description"):
                entry += f": {row['description']}"
            template_lines.append(entry)
        if template_lines:
            prompt += "\n\nAvailable pipeline templates:\n" + "\n".join(template_lines)

    # RAV-1079: surface the repo's open PRs so triage can flag a duplicate
    # (see the Duplicate-PR check section of the system prompt). Rides the
    # user turn like the template list — the system prompt stays cacheable.
    # Absent for jobs that already target an existing PR (the executor only
    # injects open_prs for fresh, non-followup jobs).
    from embry0.services.github_prs import format_open_prs_block

    open_prs_block = format_open_prs_block(state.get("open_prs") or [])
    if open_prs_block:
        prompt += f"\n\n{open_prs_block}"

    # EMB-35: the triage instructions ride as a real system prompt (appended
    # to the CLI's preset system block, which its prompt caching covers)
    # instead of being prepended into the user turn and re-billed uncached
    # on every run. Plumbing: agent_definition.system_prompt →
    # invocation.system_prompt → sandbox runner → config_builder.
    # RAV-602: model/tools/skills come from the DB agent_definitions row when
    # available (operator-editable); this dict is the code fallback.
    from embry0.orchestration.nodes.agent import load_agent_definition, load_triage_model_settings

    # #465: the prompt's model guidance comes from the provider settings — the
    # configured tier models, or "do not choose models" when the deployment
    # sets one default model for every agent. Same text for every job until
    # those settings change, so it still caches.
    default_model, tier_models = await load_triage_model_settings(config)
    triage_system_prompt = build_triage_system_prompt(default_model=default_model, tier_models=tier_models)

    triage_fallback: dict[str, Any] = {
        "model": model,
        "tools": ["Read", "Glob", "Grep"],
        "skills": [],
        "system_prompt": triage_system_prompt,
        "mcp_servers": {},
        "execution_mode": None,
        "auth_mode": None,
    }
    triage_agent_definition = dict(await load_agent_definition(config, "triage", triage_fallback))
    # The triage system prompt is code, not data: it defines the JSON contract
    # TriageDecisionModel parses, so a DB-edited prompt could silently break
    # the parser. Always use the prompt built in code.
    triage_agent_definition["system_prompt"] = triage_system_prompt
    if not triage_agent_definition.get("tools"):
        triage_agent_definition["tools"] = ["Read", "Glob", "Grep"]
    # An explicit TRIAGE_MODEL/DEFAULT_MODEL env still wins over the DB row
    # (the env flip is the documented no-deploy model-tier experiment path).
    # Both only set the DEFINITION's model: the provider settings' default
    # model, when set, outranks it in the resolver like any definition (#465).
    if os.environ.get("TRIAGE_MODEL") or os.environ.get("DEFAULT_MODEL"):
        triage_agent_definition["model"] = model
    elif not triage_agent_definition.get("model"):
        triage_agent_definition["model"] = model
    # #468: a template the job named up front is known before triage runs,
    # so its triage node's config (model, tools, skills) applies here the way
    # a step's config applies to its agent.
    from embry0.workflows.issue_to_pr.plan_route import pinned_triage_template_config

    triage_template_config = await pinned_triage_template_config(state, templates_repo)

    # Collect agent events locally so we can scan for agent_ask_user events
    # after the agent finishes. We still forward every event to the graph
    # stream writer for live streaming.
    collected_events: list[dict[str, Any]] = []

    def _forward_event(event: dict[str, Any]) -> None:
        collected_events.append(event)
        writer(event)

    # Plan C Task 7: load any prior AgentSession for this (job, agent) so the
    # in-sandbox executor can resume the same Claude conversation. Restore
    # failures must NEVER block the agent run — fall back to a fresh session.
    from embry0.agents.session import AgentSession, merged_resume_messages, session_resumable

    resume_session: AgentSession | None = None
    if agent_sessions_repo is not None:
        try:
            prior = await agent_sessions_repo.get(
                job_id=state.get("job_id", ""),
                agent_type="triage",
            )
            if prior is not None:
                resume_session = AgentSession(
                    job_id=prior["job_id"],
                    agent_type=prior["agent_type"],
                    mode=prior["mode"],
                    messages=prior.get("messages"),
                    session_id=prior.get("session_id"),
                    session_blob=prior.get("session_blob"),
                )
        except Exception:
            logger.warning(
                "triage_session_restore_failed",
                job_id=state.get("job_id"),
                exc_info=True,
            )
            resume_session = None

    # EMB-35: resumed session ⇒ delta-only prompt. Two ways to get here with
    # a prior session: (a) QA-failure re-entry — send the failure block only;
    # (b) LangGraph replaying this node after an interrupt resume — the
    # conversation already holds the full context, so just ask for the
    # decision again (the answers themselves ride the post-interrupt re-run
    # below).
    if session_resumable(resume_session):
        answers_delta = f"{user_answers_block}\n\n" if user_answers_block else ""
        if qa_failure_block:
            prompt = (
                "You are re-invoked after a QA failure on this job.\n\n"
                f"{qa_failure_block}\n\n"
                f"{answers_delta}"
                "Re-emit your full triage decision JSON with the qa_failure_action included."
            )
        else:
            prompt = f"{answers_delta}Re-emit your full triage decision JSON exactly as instructed previously."

    result = await _nodes.run_agent_node(
        state=state,
        agent_runner=agent_runner,
        agent_type="triage",
        prompt=prompt,
        agent_definition=triage_agent_definition,
        template_config=triage_template_config,
        timeout_seconds=180,
        on_event=_forward_event,
        credentials=credentials,
        config=config,
        resume_session=resume_session,
    )

    # RAV-1260: decide-now cap recovery. A triage that spelunks the repo for
    # its whole turn budget dies with no decision JSON (job-9a889c7c42c5,
    # $0.49 wasted + manual re-triage). Resume the SAME session once with a
    # decide-now nudge — session resume is cheap post-EMB-35 — and only a
    # second failure ends the job via the existing error guard below.
    recovery_session = _max_turns_recovery_session(
        result,
        agent_type="triage",
        job_id=state.get("job_id"),
        credentials=credentials,
    )
    if recovery_session is not None:
        logger.warning("triage_max_turns_decide_now_resume", job_id=state.get("job_id"))
        writer(
            events.progress(
                message="Triage hit its turn cap — resuming once to decide from evidence gathered",
                node="triage",
            )
        )
        first_outputs = list(result.get("agent_outputs") or [])
        retry_state = {**state, "total_cost_usd": result.get("total_cost_usd", state.get("total_cost_usd", 0.0))}
        result = await _nodes.run_agent_node(
            state=retry_state,
            agent_runner=agent_runner,
            agent_type="triage",
            prompt=(
                "Do not investigate further. From what you have already learned, "
                "emit your full triage decision JSON now, exactly as instructed"
                + (" — including the qa_failure_action." if qa_failure_block else ".")
            ),
            agent_definition=triage_agent_definition,
            template_config=triage_template_config,
            max_turns=DECIDE_NOW_MAX_TURNS,
            timeout_seconds=180,
            on_event=_forward_event,
            credentials=credentials,
            config=config,
            resume_session=recovery_session,
        )
        result["agent_outputs"] = first_outputs + list(result.get("agent_outputs") or [])
        resume_session = recovery_session

    # Plan C Task 7: capture post-run session state so the next invocation of
    # this node (after an ask_user pause, retry, etc.) resumes the same
    # conversation. Skip on agent error (no useful state to persist) and skip
    # when both messages and session_id are absent (avoids writing empty rows
    # before the executor learns to extract these fields). Persist failures
    # are best-effort; never block the workflow.
    if agent_sessions_repo is not None:
        last_output = (result.get("agent_outputs") or [None])[-1]
        if last_output and not last_output.get("is_error"):
            new_messages = merged_resume_messages(resume_session, last_output.get("messages"))
            new_session_id = last_output.get("session_id")
            new_session_blob = last_output.get("session_blob")
            if new_messages or new_session_id:
                mode = last_output.get("mode") or ("claude_max" if credentials.get("oauth_token") else "anthropic_api")
                try:
                    await agent_sessions_repo.upsert(
                        job_id=state.get("job_id", ""),
                        agent_type="triage",
                        mode=mode,
                        messages=new_messages,
                        session_id=new_session_id,
                        session_blob=new_session_blob,
                    )
                except Exception:
                    logger.warning(
                        "triage_session_persist_failed",
                        job_id=state.get("job_id"),
                        exc_info=True,
                    )

    # Parse triage decision from agent output
    if result.get("agent_outputs"):
        last = result["agent_outputs"][-1]
        if not last.get("is_error"):
            from typing import cast as _cast

            from embry0.orchestration.nodes.triage import apply_job_config_overrides
            from embry0.orchestration.state import TriageParseError
            from embry0.safety.error_codes import ErrorCode

            parsed: Any
            try:
                parsed = parse_triage_response(last.get("output", ""))
            except TriageParseError as first_error:
                # #455: one schema slip gets one repair turn, not a failed job.
                result, parsed = await _repair_triage_decision(
                    error=first_error,
                    state=state,
                    result=result,
                    full_prompt=prompt,
                    agent_runner=agent_runner,
                    agent_definition=triage_agent_definition,
                    on_event=_forward_event,
                    credentials=credentials,
                    config=config,
                    agent_sessions_repo=agent_sessions_repo,
                    prior_session=resume_session,
                    writer=writer,
                    template_config=triage_template_config,
                )
            try:
                if isinstance(parsed, Exception):
                    raise parsed
                triage_dict: dict[str, Any] = _cast(dict[str, Any], parsed)
                result["pipeline_config"] = apply_job_config_overrides(triage_dict.get("pipeline_config") or {}, state)
                result["triage_decision"] = triage_dict
                result["current_stage"] = "triage_complete"
            except TriageParseError as exc:
                logger.error("triage_parse_error", error=str(exc))
                writer(events.error(message=f"Triage parse failed: {exc}", node="triage"))
                writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="failed"))
                return Command(
                    goto=END,
                    update={
                        "current_stage": "failed",
                        "errors": [f"triage_malformed: {exc}"],
                        "error_code": ErrorCode.TRIAGE_MALFORMED.value,
                    },
                )

    # Guard: if the agent produced no output or an error output, fail fast.
    # Returning a plain dict here would let route_after_triage default to "proceed"
    # and send the job to developer_node with an empty pipeline_config.
    _last_triage_output = (result.get("agent_outputs") or [{}])[-1]
    if not result.get("agent_outputs") or _last_triage_output.get("is_error"):
        from embry0.safety.error_codes import ErrorCode

        _err_msg = _log_triage_agent_failure(
            "triage_agent_error",
            _last_triage_output,
            fallback="Triage agent returned no output",
        )
        writer(events.error(message=f"Triage agent failed: {_err_msg}", node="triage"))
        writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="failed"))
        return Command(
            goto=END,
            update={
                "current_stage": "failed",
                "errors": [f"triage_agent_error: {_err_msg}"],
                "error_code": ErrorCode.TRIAGE_MALFORMED.value,
                # #466: the failed run itself, so the job outcome sees its
                # code, provider and reset — a spent usage window must reach
                # the job as ERR_PROVIDER_QUOTA_EXHAUSTED, not as this node's
                # generic ERR_TRIAGE_MALFORMED.
                "agent_outputs": list(result.get("agent_outputs") or []),
            },
        )

    # RAV-1271: the resumed answers were consumed into this run's prompt —
    # clear the one-shot key so later re-entries don't re-send stale answers.
    if user_answers_block:
        result["user_answers"] = None

    # Enforce the job-wide ask_user cap on any agent_ask_user events from
    # the triage agent before processing the needs_info action.
    pending_questions = _extract_ask_user_events({"events": collected_events}, calling_node="triage")
    if pending_questions:
        from embry0.orchestration.nodes.agent import _enforce_ask_user_cap

        exhausted, cap_updates = _enforce_ask_user_cap(state, pending_questions, job_id_for_log=state.get("job_id"))
        if exhausted:
            writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="failed"))
            return Command(goto="max_retries", update=cap_updates)
        # Only divert to ask_user_interrupt when there are blocking questions.
        # Auto-answerable-only emissions don't pause; fall through to the
        # normal needs_info / proceed routing.
        if cap_updates.get("pending_agent_questions"):
            writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="ask_user"))
            return Command(
                goto="ask_user_interrupt",
                update={**result, **cap_updates, "current_stage": "triage_asked_user"},
            )
        # Auto-answerables: fold into the result so downstream nodes can see
        # them, then continue normal triage routing below.
        result = {**result, **cap_updates}

    # Check needs_info → interrupt
    # action lives in triage_decision (the full TriageDecision dict), not in the
    # flat pipeline_config dict written to state["pipeline_config"].
    triage_decision_result = result.get("triage_decision", {})
    action = triage_decision_result.get("action", "proceed")

    if action == "needs_info":
        # Cycle guard: prevent infinite triage interrupt loops.
        # Mirrors the ask_user round cap (env-tunable via ASK_USER_ROUNDS_CAP).
        _triage_rounds = int(state.get("triage_question_rounds", 0) or 0)
        if _triage_rounds >= DEFAULT_ASK_USER_CAP:
            from embry0.safety.error_codes import ErrorCode

            logger.warning(
                "triage_question_rounds_exceeded",
                rounds=_triage_rounds,
                job_id=state.get("job_id"),
            )
            writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="failed"))
            return Command(
                goto=END,
                update={
                    "current_stage": "failed",
                    "triage_question_rounds": _triage_rounds,
                    "error_code": ErrorCode.MAX_TRIAGE_QUESTIONS.value,
                    "errors": ["Triage exceeded 5 interrupt rounds — giving up."],
                },
            )

        questions = triage_decision_result.get("questions", [])
        reasoning = triage_decision_result.get("reasoning", "")
        writer(events.interrupt(node="triage", questions=questions))
        logger.info("triage_needs_info", questions=len(questions))

        answers = _nodes.interrupt(
            {
                "questions": questions,
                "reasoning": reasoning,
                "asking_node": "triage",
                "issue_id": state.get("issue_id"),
            }
        )

        # Resumed — format the NEW answers, fold them into the accumulated
        # context, and re-run.
        answers_text = ""
        if isinstance(answers, str):
            answers_text = answers
        elif isinstance(answers, dict):
            answers_text = "\n\n".join(f"Q: {q}\nA: {a}" for q, a in answers.items())
        elif isinstance(answers, list):
            answers_text = "\n\n".join(
                f"Q: {item.get('question', '')}\nA: {item.get('answer', '')}"
                for item in answers
                if isinstance(item, dict)
            )

        additional = state.get("additional_context", "") or ""
        if answers_text:
            additional += f"\n\n{answers_text}"

        updated = {**state, "additional_context": additional}
        writer(events.progress(message="Re-running triage with answers", node="triage"))

        # Plan C Task 7: reload the session before the resume re-run — it may
        # have been persisted by the initial pass above. Persist again after.
        resume_session = None
        if agent_sessions_repo is not None:
            try:
                prior = await agent_sessions_repo.get(
                    job_id=state.get("job_id", ""),
                    agent_type="triage",
                )
                if prior is not None:
                    resume_session = AgentSession(
                        job_id=prior["job_id"],
                        agent_type=prior["agent_type"],
                        mode=prior["mode"],
                        messages=prior.get("messages"),
                        session_id=prior.get("session_id"),
                        session_blob=prior.get("session_blob"),
                    )
            except Exception:
                logger.warning(
                    "triage_session_restore_failed",
                    job_id=state.get("job_id"),
                    exc_info=True,
                )
                resume_session = None

        # EMB-35: resumed session ⇒ send only the new answers; fresh session
        # ⇒ rebuild the full prompt (system prompt + task + all context).
        if session_resumable(resume_session):
            rerun_prompt = (
                "The user has answered your questions:\n\n"
                f"{answers_text or additional}\n\n"
                "Re-evaluate with this new information and re-emit your full "
                "triage decision JSON exactly as instructed previously."
            )
        else:
            rerun_prompt = prompt + f"\n\nPrevious Q&A:\n{additional}"

        result = await _nodes.run_agent_node(
            state=updated,
            agent_runner=agent_runner,
            agent_type="triage",
            prompt=rerun_prompt,
            agent_definition=triage_agent_definition,
            template_config=triage_template_config,
            timeout_seconds=180,
            on_event=_forward_event,
            credentials=credentials,
            config=config,
            resume_session=resume_session,
        )

        if agent_sessions_repo is not None:
            last_output = (result.get("agent_outputs") or [None])[-1]
            if last_output and not last_output.get("is_error"):
                new_messages = merged_resume_messages(resume_session, last_output.get("messages"))
                new_session_id = last_output.get("session_id")
                new_session_blob = last_output.get("session_blob")
                if new_messages or new_session_id:
                    mode = last_output.get("mode") or (
                        "claude_max" if credentials.get("oauth_token") else "anthropic_api"
                    )
                    try:
                        await agent_sessions_repo.upsert(
                            job_id=state.get("job_id", ""),
                            agent_type="triage",
                            mode=mode,
                            messages=new_messages,
                            session_id=new_session_id,
                            session_blob=new_session_blob,
                        )
                    except Exception:
                        logger.warning(
                            "triage_session_persist_failed",
                            job_id=state.get("job_id"),
                            exc_info=True,
                        )

        if result.get("agent_outputs"):
            last = result["agent_outputs"][-1]
            if not last.get("is_error"):
                from embry0.orchestration.state import TriageParseError
                from embry0.safety.error_codes import ErrorCode

                try:
                    from typing import cast as _cast

                    triage_dict2: dict[str, Any] = _cast(dict[str, Any], parse_triage_response(last.get("output", "")))
                    from embry0.orchestration.nodes.triage import apply_job_config_overrides

                    result["pipeline_config"] = apply_job_config_overrides(
                        triage_dict2.get("pipeline_config") or {}, state
                    )
                    result["triage_decision"] = triage_dict2
                    result["current_stage"] = "triage_complete"
                except TriageParseError as exc:
                    logger.error("triage_parse_error", error=str(exc))
                    writer(events.error(message=f"Triage parse failed: {exc}", node="triage"))
                    writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="failed"))
                    return Command(
                        goto=END,
                        update={
                            "current_stage": "failed",
                            "errors": [f"triage_malformed: {exc}"],
                            "error_code": ErrorCode.TRIAGE_MALFORMED.value,
                        },
                    )

        # Guard: if the resume re-run produced no output or an error, fail fast.
        _last_resume_output = (result.get("agent_outputs") or [{}])[-1]
        if not result.get("agent_outputs") or _last_resume_output.get("is_error"):
            from embry0.safety.error_codes import ErrorCode

            _err_msg = _log_triage_agent_failure(
                "triage_agent_error_resume",
                _last_resume_output,
                fallback="Triage agent returned no output on resume",
            )
            writer(events.error(message=f"Triage agent failed on resume: {_err_msg}", node="triage"))
            writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="failed"))
            return Command(
                goto=END,
                update={
                    "current_stage": "failed",
                    "errors": [f"triage_agent_error: {_err_msg}"],
                    "error_code": ErrorCode.TRIAGE_MALFORMED.value,
                    # #466: as on the first pass — the job outcome reads the failed run.
                    "agent_outputs": list(result.get("agent_outputs") or []),
                },
            )

        # Increment triage_question_rounds so the next interrupt cycle's cycle
        # guard sees the updated counter. This persists via the result dict which
        # LangGraph merges into state on node completion.
        result["triage_question_rounds"] = _triage_rounds + 1

        # RAV-1271: re-clear on the interrupt-resume re-run — run_agent_node
        # returned a fresh result dict, dropping the earlier clear.
        if user_answers_block:
            result["user_answers"] = None

        # Enforce the cap again on the re-run's events (state now has updated rounds).
        pending_questions = _extract_ask_user_events({"events": collected_events}, calling_node="triage")
        if pending_questions:
            from embry0.orchestration.nodes.agent import _enforce_ask_user_cap

            exhausted, cap_updates = _enforce_ask_user_cap(
                {**state, **result}, pending_questions, job_id_for_log=state.get("job_id")
            )
            if exhausted:
                writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="failed"))
                return Command(goto="max_retries", update=cap_updates)
            if cap_updates.get("pending_agent_questions"):
                writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="ask_user"))
                return Command(
                    goto="ask_user_interrupt",
                    update={**result, **cap_updates, "current_stage": "triage_asked_user"},
                )
            # Auto-answerable-only — keep the cap_updates on result and
            # continue the post-triage routing below.
            result = {**result, **cap_updates}

    # Phase 5 Task 4: parse the optional set_qa_decision the triage agent
    # emits per the QA Decision section of the triage system prompt. The prompt
    # asks for it as an inline JSON field on TriageDecisionModel; we also
    # accept a streamed tool_call event as a fallback (the agent SDK may emit
    # one if the model decides to, even though set_qa_decision isn't in the
    # tools allowlist). When neither path produced one, leave state["qa"]
    # alone — downstream callers treat absent needs_qa as False.
    qa_update = _extract_qa_decision_from_triage_dict(triage_dict) or _extract_qa_decision_from_events(collected_events)
    if qa_update is not None:
        existing_qa = state.get("qa") or {}
        merged_qa: dict[str, Any] = {**existing_qa}
        merged_qa["needs_qa"] = qa_update["needs_qa"]
        merged_qa["qa_required_reason"] = qa_update["qa_required_reason"]
        if qa_update["needs_qa"]:
            merged_qa["acceptance_criteria"] = qa_update["acceptance_criteria"]
        result["qa"] = merged_qa

    # Phase 5 Task 7: when this triage invocation is a re-invocation after a
    # QA failure (state.qa.final_status == "failed"), the agent must emit a
    # qa_failure_action under the inline JSON field of the same name. Validate
    # via the kind-specific Pydantic model (RetryDeveloper / RerunQA / AskUser)
    # and return a Command routing the workflow accordingly. Missing or
    # malformed action gets one repair retry, then ends the job with
    # ERR_TRIAGE_QA_ACTION_INVALID (#304). The
    # qa.failure_rounds counter is bumped by _qa_failure_bookkeeping_node in
    # graph.py BEFORE this node is re-entered, so we do not double-bump here.
    qa_state_in = state.get("qa") or {}
    if qa_state_in.get("final_status") == "failed":
        # Prefer the freshest parsed decision (resume path updates result["triage_decision"]);
        # fall back to the first-pass triage_dict so the lookup never NPEs.
        decision_dict = (
            result.get("triage_decision") if isinstance(result.get("triage_decision"), dict) else triage_dict
        )
        decision_dict = decision_dict if isinstance(decision_dict, dict) else {}
        # #304: a schema-invalid action used to be job-terminal AFTER the PR
        # existed. Give the agent exactly one chance to fix it, with the
        # validation error in hand; _route_qa_failure_action re-validates and
        # terminates with ERR_TRIAGE_QA_ACTION_INVALID if it is still wrong.
        from embry0.workflows.issue_to_pr.nodes._qa_failure_action import (
            QAFailureActionInvalid,
            parse_qa_failure_action,
        )

        try:
            parse_qa_failure_action(decision_dict)
        except QAFailureActionInvalid as invalid:
            result, decision_dict = await _repair_qa_failure_action(
                invalid=invalid,
                state=state,
                result=result,
                triage_decision=decision_dict,
                full_prompt=prompt,
                agent_runner=agent_runner,
                agent_definition=triage_agent_definition,
                template_config=triage_template_config,
                on_event=_forward_event,
                credentials=credentials,
                config=config,
                agent_sessions_repo=agent_sessions_repo,
                prior_session=resume_session,
                writer=writer,
            )
        return _route_qa_failure_action(
            state=state,
            result=result,
            triage_decision=decision_dict,
            writer=writer,
        )

    # RAV-1079: triage may flag the task as a duplicate of an open PR
    # (semantic dedup — the executor's deterministic pre-graph check only
    # catches exact ticket matches). Validate the flag against the open-PR
    # list the executor injected: an unknown number is ignored with a
    # warning (never fail a job on a bad dup flag). Skip+link is the house
    # default policy: the job ends ``skipped`` (#474) and the executor links
    # the existing PR to the case — never to the job's own ``pr_url``, which
    # is what says a job opened a PR (_skip_as_duplicate).
    _final_decision = result.get("triage_decision") or {}
    _dup_flag = _final_decision.get("duplicate_of_pr") if isinstance(_final_decision, dict) else None
    if isinstance(_dup_flag, dict) and _dup_flag.get("pr_number") is not None:
        _dup_match = next(
            (p for p in (state.get("open_prs") or []) if p.get("number") == _dup_flag.get("pr_number")),
            None,
        )
        if _dup_match is not None:
            _dup_reason = str(_dup_flag.get("reason") or "").strip()
            _dup_summary = (
                f"Skipped: a pull request for this issue already exists: #{_dup_match['number']} "
                f"({_dup_match['url']}); triage judged this task a duplicate of it."
                + (f" {_dup_reason}" if _dup_reason else "")
                + " No new pull request was opened; review or merge the existing one instead."
            )
            logger.info(
                "triage_flagged_duplicate_pr",
                job_id=state.get("job_id"),
                pr_number=_dup_match["number"],
                reason=_dup_reason[:200],
            )
            writer(events.progress(message=_dup_summary, node="triage"))
            writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="duplicate"))
            return Command(
                goto=END,
                update={
                    **result,
                    "current_stage": "skipped_duplicate",
                    "result_summary": _dup_summary,
                },
            )
        logger.warning(
            "triage_duplicate_flag_unmatched",
            job_id=state.get("job_id"),
            flagged_pr_number=_dup_flag.get("pr_number"),
        )

    writer(
        events.node_completed(
            node="triage", agent="triage", attempt=attempt, action=result.get("triage_decision", {}).get("action")
        )
    )

    # RAV-1152: triage self-routes via Command EXCLUSIVELY — its conditional
    # edge is removed. This node has 13 Command returns (caps, interrupts,
    # QA-failure routing); with a conditional edge attached, every one of
    # them ALSO activated the router's pick in the same superstep (fan-out;
    # crash class before #95's reducers). Same single-dispatch shape as
    # developer/review.
    from embry0.orchestration.routing.conditions import check_triage_action

    action = check_triage_action({**state, **result})
    return Command(goto=(END if action == "split" else "plan_route"), update=result)


def _route_qa_failure_action(
    *,
    state: dict[str, Any],
    result: dict[str, Any],
    triage_decision: dict[str, Any],
    writer: Any,
) -> Command[str]:
    """Validate triage's qa_failure_action and return a routing Command.

    Phase 5 Task 7. Called when ``state["qa"]["final_status"] == "failed"``,
    i.e. triage was re-invoked after a QA failure. The agent should have
    embedded one of three actions on its inline JSON output under the key
    ``qa_failure_action``:

      - ``{"kind": "retry_developer", "prompt": str, "focus_files": [str]}``
        → ``Command(goto="developer", update={...})`` with
        ``developer_prompt_addendum`` and ``developer_focus_files`` set.
      - ``{"kind": "rerun_qa", "reason": str}``
        → ``Command(goto="init_orchestrator", update={"qa_rerun_reason": ...})``
        (re-enters the QA subpath without changing the developer's diff).
      - ``{"kind": "ask_user", "question": str}``
        → ``Command(goto="ask_user_interrupt", update={...})`` with the
        question on ``pending_agent_questions`` in the standard shape
        (``asking_node="triage"``, ``importance="blocking"``) so the
        interrupt surfaces it and the resume re-enters triage (RAV-1271 —
        the old bespoke ``pending_user_question`` key was read by nobody,
        so the interrupt raised with zero questions and the job ended
        corrupt). The job-wide ask_user cap applies; on exhaustion the
        route goes to ``max_retries`` like every other capped ask path.

    On missing / malformed / unknown-kind action (the caller has already
    spent the one repair retry, #304), sets
    ``state["qa"]["final_status"] = "exhausted"`` and
    ``state["error_code"] = ErrorCode.TRIAGE_QA_ACTION_INVALID.value`` and
    routes to ``END`` (the qa block mirrors the qa_exhausted node so the
    dashboard sees the same shape; the code is distinct because the loop did
    not run out of rounds — the agent's answer was unusable).
    ``qa.failure_rounds`` is NOT bumped here — the
    ``_qa_failure_bookkeeping_node`` in graph.py owns that counter and
    has already incremented it before this node fires.
    """
    from embry0.agents.triage_actions import RerunQA, RetryDeveloper
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes._qa_failure_action import (
        QAFailureActionInvalid,
        parse_qa_failure_action,
    )

    attempt = agent_attempt(state, "triage")
    try:
        action = parse_qa_failure_action(triage_decision)
    except QAFailureActionInvalid as invalid:
        log_event = f"triage_qa_failure_action_{invalid.reason}"
        logger.warning(log_event, job_id=state.get("job_id"), kind=invalid.kind, error=invalid.detail[:300])
        writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="qa_failure_unresolved"))
        return Command(
            goto=END,
            update={
                **result,
                "qa": {**(state.get("qa") or {}), "final_status": "exhausted"},
                "error_code": ErrorCode.TRIAGE_QA_ACTION_INVALID.value,
                "current_stage": "failed",
                "errors": [f"qa_failure_unresolved: {log_event}: {invalid.detail[:300]}"],
            },
        )

    # RAV-601: these jumps bypass plan_route, so the route cursor must be
    # pointed back AT the step being re-entered — otherwise forward
    # progression would resume from the stale post-QA position and skip
    # the rest of the plan.
    from embry0.workflows.issue_to_pr.route_plan import cursor_at

    if isinstance(action, RetryDeveloper):
        writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="qa_retry_developer"))
        return Command(
            goto="developer",
            update={
                **result,
                "developer_prompt_addendum": action.prompt,
                "developer_focus_files": list(action.focus_files),
                "current_stage": "qa_retry_developer",
                "route_cursor": cursor_at(state, "developer"),
            },
        )
    if isinstance(action, RerunQA):
        writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="qa_rerun"))
        # goto targets the issue-to-pr graph's QA chain head. (This was
        # "init_qa" — a node that exists only in the QA sub-task graph —
        # so the rerun path could never actually dispatch.)
        return Command(
            goto="init_orchestrator",
            update={
                **result,
                "qa_rerun_reason": action.reason,
                "current_stage": "qa_rerun",
                "route_cursor": cursor_at(state, "qa"),
            },
        )
    # AskUser
    from embry0.orchestration.nodes.agent import _enforce_ask_user_cap

    question = {
        "question": action.question,
        "asking_node": "triage",
        "importance": "blocking",
    }
    exhausted, cap_updates = _enforce_ask_user_cap(state, [question], job_id_for_log=state.get("job_id"))
    if exhausted:
        writer(
            events.node_completed(node="triage", agent="triage", attempt=attempt, action="qa_ask_user_cap_exhausted")
        )
        return Command(goto="max_retries", update={**result, **cap_updates})
    writer(events.node_completed(node="triage", agent="triage", attempt=attempt, action="qa_ask_user"))
    return Command(
        goto="ask_user_interrupt",
        update={
            **result,
            **cap_updates,
            "current_stage": "qa_ask_user",
        },
    )


async def _rerun_triage_once(
    *,
    state: dict[str, Any],
    result: dict[str, Any],
    repair_block: str,
    full_prompt: str,
    agent_runner: Any,
    agent_definition: dict[str, Any],
    on_event: Any,
    credentials: dict[str, str],
    config: RunnableConfig,
    agent_sessions_repo: Any,
    prior_session: Any,
    event_prefix: str,
    template_config: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """One repair turn of triage: ``repair_block`` sent on the conversation
    that produced the rejected answer (#304, #455).

    Resumes that conversation so the turn is a cheap delta (EMB-35); without
    a resumable session the full prompt is replayed ahead of the block. The
    repair run's output and cost are always folded into the returned result.
    Returns ``(merged_result, repair_output)``; ``repair_output`` is None when
    the run errored or produced nothing. Not a loop: each caller invokes it
    at most once per triage entry.
    """
    from typing import Literal, cast

    from embry0.agents.session import AgentSession, merged_resume_messages, session_resumable

    job_id = state.get("job_id", "")
    first_outputs = list(result.get("agent_outputs") or [])
    last = first_outputs[-1] if first_outputs else {}
    mode = cast(
        "Literal['anthropic_api', 'claude_max']",
        last.get("mode") or ("claude_max" if credentials.get("oauth_token") else "anthropic_api"),
    )
    session = AgentSession(
        job_id=job_id,
        agent_type="triage",
        mode=mode,
        messages=merged_resume_messages(prior_session, last.get("messages")),
        session_id=last.get("session_id"),
        session_blob=last.get("session_blob"),
    )
    resumable = session_resumable(session)

    repair = await _nodes.run_agent_node(
        state={**state, "total_cost_usd": result.get("total_cost_usd", state.get("total_cost_usd", 0.0))},
        agent_runner=agent_runner,
        agent_type="triage",
        prompt=repair_block if resumable else f"{full_prompt}\n\n{repair_block}",
        agent_definition=agent_definition,
        template_config=template_config,
        max_turns=DECIDE_NOW_MAX_TURNS,
        timeout_seconds=180,
        on_event=on_event,
        credentials=credentials,
        config=config,
        resume_session=session if resumable else None,
    )

    repair_outputs = list(repair.get("agent_outputs") or [])
    merged = {**result, "agent_outputs": first_outputs + repair_outputs}
    for cost_key in ("total_cost_usd", "budget_overrun_usd"):
        if cost_key in repair:
            merged[cost_key] = repair[cost_key]

    repair_last = repair_outputs[-1] if repair_outputs else None
    if not repair_last or repair_last.get("is_error"):
        logger.warning(f"{event_prefix}_repair_run_failed", job_id=job_id)
        return merged, None

    if agent_sessions_repo is not None:
        new_messages = merged_resume_messages(session, repair_last.get("messages"))
        new_session_id = repair_last.get("session_id")
        if new_messages or new_session_id:
            try:
                await agent_sessions_repo.upsert(
                    job_id=job_id,
                    agent_type="triage",
                    mode=repair_last.get("mode") or mode,
                    messages=new_messages,
                    session_id=new_session_id,
                    session_blob=repair_last.get("session_blob"),
                )
            except Exception:
                logger.warning("triage_session_persist_failed", job_id=job_id, exc_info=True)
    return merged, repair_last


async def _repair_qa_failure_action(
    *,
    invalid: Any,
    state: dict[str, Any],
    result: dict[str, Any],
    triage_decision: dict[str, Any],
    full_prompt: str,
    agent_runner: Any,
    agent_definition: dict[str, Any],
    on_event: Any,
    credentials: dict[str, str],
    config: RunnableConfig,
    agent_sessions_repo: Any,
    prior_session: Any,
    writer: Any,
    template_config: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-run triage ONCE with the validation error fed back (#304).

    Returns ``(result, triage_decision)`` for ``_route_qa_failure_action`` to
    validate again. Only the ``qa_failure_action`` is taken from the repair
    answer — the pipeline_config / QA decision of the original answer stand,
    so a repair turn can never silently re-plan the job. The repair run's
    output and cost are always folded into ``result``; when the run errors or
    its answer holds no action object, the original (invalid) decision is returned
    unchanged and the caller terminates with ERR_TRIAGE_QA_ACTION_INVALID.
    Not a loop: the caller invokes this at most once per triage entry.
    """
    from embry0.workflows.issue_to_pr.nodes._qa_failure_action import build_repair_prompt, extract_repaired_action

    job_id = state.get("job_id", "")
    logger.warning(
        "triage_qa_failure_action_repair_retry",
        job_id=job_id,
        reason=invalid.reason,
        error=invalid.detail[:300],
    )
    writer(
        events.progress(message="Triage's qa_failure_action failed validation — asking once for a fix", node="triage")
    )

    merged, repair_last = await _rerun_triage_once(
        state=state,
        result=result,
        repair_block=build_repair_prompt(invalid),
        full_prompt=full_prompt,
        agent_runner=agent_runner,
        agent_definition=agent_definition,
        template_config=template_config,
        on_event=on_event,
        credentials=credentials,
        config=config,
        agent_sessions_repo=agent_sessions_repo,
        prior_session=prior_session,
        event_prefix="triage_qa_failure_action",
    )
    if repair_last is None:
        return merged, triage_decision
    repaired_action = extract_repaired_action(repair_last.get("output", ""))
    if repaired_action is None:
        logger.warning("triage_qa_failure_action_repair_unparseable", job_id=job_id)
        return merged, triage_decision

    fixed_decision = {**triage_decision, "qa_failure_action": repaired_action}
    merged["triage_decision"] = fixed_decision
    return merged, fixed_decision


# #455: how much of the rejected answer the repair turn quotes back.
_REJECTED_DECISION_PREVIEW_CHARS = 4000


def _decision_repair_prompt(error: str, rejected: str) -> str:
    """The one-shot repair turn for a triage answer that failed to parse.
    Self-contained — on the non-resumable path the model has no memory of
    what it sent."""
    return (
        "Your triage decision was rejected and NOT applied. This is what you sent:\n"
        f"{rejected[-_REJECTED_DECISION_PREVIEW_CHARS:]}\n\n"
        "It failed with:\n"
        f"  {error[:1500]}\n\n"
        "Do not investigate further. Reply with ONLY your complete, corrected triage decision as one JSON "
        "object, exactly as instructed previously: every required field present, no fields the schema does not "
        "define, and nothing before or after the JSON."
    )


async def _repair_triage_decision(
    *,
    error: Exception,
    state: dict[str, Any],
    result: dict[str, Any],
    full_prompt: str,
    agent_runner: Any,
    agent_definition: dict[str, Any],
    on_event: Any,
    credentials: dict[str, str],
    config: RunnableConfig,
    agent_sessions_repo: Any,
    prior_session: Any,
    writer: Any,
    template_config: dict[str, Any] | None,
) -> tuple[dict[str, Any], Any]:
    """Re-ask triage ONCE for a decision that failed to parse (#455).

    One omitted field (a missing ``confidence``) used to fail the whole job
    with ERR_TRIAGE_MALFORMED — for a watcher analysis job, a paid tick
    recorded as an error. The model gets the validation error and its own
    answer back and one chance to correct it, the pattern the QA failure
    action uses (#304). Returns ``(result, decision)``: ``decision`` is the
    parsed repair answer, or the TriageParseError that still stands — the
    caller fails the job with it. The repair run's output and cost are in
    ``result`` either way.
    """
    from embry0.orchestration.nodes.triage import parse_triage_response
    from embry0.orchestration.state import TriageParseError

    job_id = state.get("job_id", "")
    rejected = str(((result.get("agent_outputs") or [{}])[-1]).get("output") or "")
    logger.warning("triage_decision_repair_retry", job_id=job_id, error=str(error)[:300])
    writer(events.progress(message="Triage's decision failed validation — asking once for a fix", node="triage"))

    merged, repair_last = await _rerun_triage_once(
        state=state,
        result=result,
        repair_block=_decision_repair_prompt(str(error), rejected),
        full_prompt=full_prompt,
        agent_runner=agent_runner,
        agent_definition=agent_definition,
        on_event=on_event,
        credentials=credentials,
        config=config,
        agent_sessions_repo=agent_sessions_repo,
        prior_session=prior_session,
        event_prefix="triage_decision",
        template_config=template_config,
    )
    if repair_last is None:
        return merged, error
    try:
        return merged, parse_triage_response(repair_last.get("output", ""))
    except TriageParseError as again:
        logger.warning("triage_decision_repair_rejected", job_id=job_id, error=str(again)[:300])
        return merged, again


def _extract_qa_decision_from_triage_dict(
    triage_dict: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Pull set_qa_decision out of the parsed triage JSON, if present.

    Phase 5 primary path: triage embeds the QA decision as an optional
    `set_qa_decision` field on TriageDecisionModel. Returns None if the
    field is missing/null/malformed (callers fall back to the tool_call
    event scan).
    """
    from pydantic import ValidationError

    from embry0.agents.triage_actions import SetQADecision

    if not isinstance(triage_dict, dict):
        return None
    raw = triage_dict.get("set_qa_decision")
    if not isinstance(raw, dict):
        return None
    try:
        # RAV-1248: an over-long reason must not silently drop the decision.
        from embry0.agents.triage_actions import clamp_advisory_strings

        decision = SetQADecision.model_validate(clamp_advisory_strings(SetQADecision, raw))
    except ValidationError:
        logger.warning(
            "triage_set_qa_decision_inline_validation_failed",
            payload=str(raw)[:500],
            exc_info=True,
        )
        return None
    return {
        "needs_qa": decision.needs_qa,
        "qa_required_reason": decision.reason,
        "acceptance_criteria": list(decision.acceptance_criteria),
    }


def _extract_qa_decision_from_events(
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Scan triage agent events for the most recent ``set_qa_decision`` tool call.

    Phase 5 Task 4. Triage's QA-decision section in the prompt instructs the
    agent to emit a ``set_qa_decision`` tool call carrying ``needs_qa``,
    ``reason``, and ``acceptance_criteria``. The full structured input is
    preserved on the streamed event in the ``tool_input`` field (the older
    ``input`` field is a short human-readable summary, kept for backwards
    compatibility with log/UI consumers).

    Returns a normalized dict ``{needs_qa, reason, acceptance_criteria}``
    validated by ``SetQADecision``, or ``None`` if no such tool call was
    emitted (in which case callers should treat the job as ``needs_qa=False``).

    Returns the LAST matching event so that, in the needs_info → resume flow,
    the resume-pass decision wins over the original-pass decision.
    """
    from pydantic import ValidationError

    from embry0.agents.triage_actions import SetQADecision

    matches: list[dict[str, Any]] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        if e.get("type") != "tool_call":
            continue
        if e.get("tool_name") != "set_qa_decision":
            continue
        raw_input = e.get("tool_input")
        if not isinstance(raw_input, dict):
            # Older event shape: only the summarized "input" field is present.
            # Skip — we can't safely re-validate a stringified payload.
            continue
        matches.append(raw_input)

    if not matches:
        return None

    try:
        # RAV-1248: an over-long reason must not silently drop the decision.
        from embry0.agents.triage_actions import clamp_advisory_strings

        decision = SetQADecision.model_validate(clamp_advisory_strings(SetQADecision, matches[-1]))
    except ValidationError:
        logger.warning(
            "triage_set_qa_decision_validation_failed",
            payload=str(matches[-1])[:500],
            exc_info=True,
        )
        return None

    return {
        "needs_qa": decision.needs_qa,
        "qa_required_reason": decision.reason,
        "acceptance_criteria": list(decision.acceptance_criteria),
    }
