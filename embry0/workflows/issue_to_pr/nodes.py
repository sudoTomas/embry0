"""Issue-to-PR workflow nodes — real implementations with LangGraph native patterns.

Each node that runs an agent:
1. Receives infrastructure via config["configurable"] (RunnableConfig injection)
2. Emits live events via get_stream_writer()
3. Can pause for human input via interrupt()
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END
from langgraph.types import Command, interrupt

from embry0.orchestration.nodes.agent import (
    BRAINSTORMING_ASK_USER_CAP,
    DEFAULT_ASK_USER_CAP,
    run_agent_node,
)

logger = structlog.get_logger(__name__)


def _filter_user_env_for_sandbox(user_env: list[dict[str, str]] | dict[str, str], *, qa_active: bool) -> dict[str, str]:
    """Merge user env vars into the sandbox environment, filtering by scope.

    - scope='app' (or unspecified): always included.
    - scope='qa':  included only when qa_active=True.
    - Reserved keys/prefixes: always dropped (defense-in-depth vs API validation).

    Accepts either the new list-of-dicts shape or the legacy plain dict
    (treated as all scope='app').
    """
    from embry0.api.schemas.environment import RESERVED_ENV_KEYS
    from embry0.execution.auth_provider import RESERVED_ENV_PREFIXES

    # Normalize to list-of-dicts
    if isinstance(user_env, dict):
        rows: list[dict[str, str]] = [{"key": k, "value": v, "scope": "app"} for k, v in user_env.items()]
    else:
        rows = list(user_env)

    out: dict[str, str] = {}
    for row in rows:
        key = row["key"]
        if key in RESERVED_ENV_KEYS:
            logger.warning("user_env_reserved_key_dropped", key=key)
            continue
        if any(key.startswith(p) for p in RESERVED_ENV_PREFIXES):
            logger.warning("user_env_reserved_prefix_dropped", key=key)
            continue
        scope = row.get("scope", "app")
        if scope == "qa" and not qa_active:
            continue
        out[key] = row["value"]
    return out


def _build_clone_shell(repo: str) -> str:
    """Shell for the init clone; ONLY the main:main fetch is best-effort.

    The ``|| true`` must be brace-scoped to the fetch: ``a && b || true``
    rescues the WHOLE chain, which let a failed clone exit 0 and report
    "Repository cloned" over an empty /workspace (2026-07-06 access
    smoke). The fetch stays best-effort because it fails benignly
    when the clone already checked out main.
    """
    return (
        f"git clone --depth=50 https://github.com/{repo}.git /workspace"
        f" && {{ git -C /workspace fetch origin main:main --depth=50 || true; }}"
    )


async def init_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Create sandbox container and clone the repo."""
    # Context guard: only git contexts have an init strategy today. Non-git
    # jobs (http/local/none) validate + persist but are not executable until
    # init strategies for the other branches of this switch land. Raise BEFORE
    # touching the stream writer or sandbox manager so no sandbox is created.
    context = state.get("context") or {"type": "git", "repo": state.get("repo", "")}
    ctx_type = context.get("type", "git")
    if ctx_type != "git":
        from embry0.orchestration.state import UnsupportedContextError

        raise UnsupportedContextError(ctx_type)

    writer = get_stream_writer()
    writer({"type": "node_started", "node": "init"})

    job_id = state.get("job_id", "")
    repo = state.get("repo", "")

    sandbox_mgr = config["configurable"].get("sandbox_manager")
    docker = config["configurable"].get("docker")
    proxy_mgr = config["configurable"].get("proxy_manager")

    if not sandbox_mgr:
        raise RuntimeError("No sandbox manager available — cannot create sandbox")

    # Git proxy URL (may be empty if orchestrator has no GITHUB_TOKEN configured).
    # The sandbox uses this URL as a credential helper; the proxy injects the
    # orchestrator's GitHub token on each request. No token ever enters the sandbox env.
    git_proxy_url = getattr(proxy_mgr, "git_proxy_url", "") if proxy_mgr else ""

    container_id = None
    try:
        # Merge user env FIRST, then infrastructure vars last so they win.
        # _filter_user_env_for_sandbox provides defense-in-depth vs the API-layer
        # validators: stored rows with reserved keys/prefixes (from pre-fix data
        # or a bypassed API) must not escape to the sandbox. It also filters
        # scope='qa' rows unless qa_active=True (Phase 2 QA pipeline sets this).
        env: dict[str, str] = _filter_user_env_for_sandbox(
            state.get("user_env_vars") or [],
            qa_active=bool(state.get("qa_active", False)),
        )
        if git_proxy_url:
            env["EMBRY0_GIT_PROXY_URL"] = git_proxy_url
        container_id, sandbox_token = await sandbox_mgr.create(job_id, env=env, repo=repo)
        logger.info("sandbox_created", job_id=job_id, container_id=container_id)
        writer({"type": "progress", "message": "Sandbox container created"})

        # EMB-45: when the xai-proxy is running, deliver the per-sandbox proxy bearer
        # to a 0600 file the DirectXaiExecutor reads to authenticate to the proxy. The
        # token never rides the sandbox env; this mirrors the git credential helper.
        xai_proxy_url = getattr(proxy_mgr, "xai_proxy_url", "") if proxy_mgr else ""
        if xai_proxy_url and docker:
            import shlex

            token_cmd = [
                "bash",
                "-c",
                'mkdir -p "$HOME/.embry0" && '
                f'printf %s {shlex.quote(sandbox_token)} > "$HOME/.embry0/xai_proxy_token" && '
                'chmod 600 "$HOME/.embry0/xai_proxy_token"',
            ]
            await docker.run_cmd(docker.build_exec_cmd(container_id, token_cmd), timeout=10)

        # Clone repo inside container. Git auth flows via credential proxy — the
        # helper curls $EMBRY0_GIT_PROXY_URL/git-credentials with the per-sandbox
        # bearer token, which returns the orchestrator's token without the token
        # ever being visible to agent code.
        if repo and docker:
            if git_proxy_url:
                import shlex

                from embry0.branding import GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME
                from embry0.sandbox.github.git_ops import build_sandbox_credential_config_cmd

                cred_cmd = build_sandbox_credential_config_cmd(git_proxy_url, sandbox_token)
                setup_cmd = [
                    "bash",
                    "-c",
                    f"{cred_cmd} && "
                    f"git config --global user.email {shlex.quote(GIT_AUTHOR_EMAIL)} && "
                    f"git config --global user.name {shlex.quote(GIT_AUTHOR_NAME)}",
                ]
                await docker.run_cmd(
                    docker.build_exec_cmd(container_id, setup_cmd),
                    timeout=10,
                )
            else:
                logger.warning(
                    "git_proxy_unavailable",
                    job_id=job_id,
                    msg="No git proxy URL — skipping credential helper setup; "
                    "clone and push to private repos will fail.",
                )

            # Fail loudly on clone error — if /workspace isn't a git repo, every
            # downstream agent call silently misbehaves. A non-zero exit from
            # `git clone` makes DockerClient.run_cmd raise RuntimeError.
            clone_cmd = ["bash", "-c", _build_clone_shell(repo)]
            try:
                # 300s (was 120s): large monorepos cloned via the git-proxy at
                # --depth=50 can exceed two minutes when the proxy adds latency
                # or the repo has many large files. Verified against a large repo
                # which timed out at exactly 120s on first attempt.
                await docker.run_cmd(
                    docker.build_exec_cmd(container_id, clone_cmd),
                    timeout=300,
                )
            except RuntimeError as exc:
                logger.error("repo_clone_failed", job_id=job_id, repo=repo, error=str(exc))
                writer({"type": "error", "message": f"Repository clone failed for {repo}: {exc}"})
                raise RuntimeError(f"Repository clone failed for {repo}: {exc}") from exc
            writer({"type": "progress", "message": f"Repository {repo} cloned"})
            logger.info("repo_cloned", job_id=job_id, repo=repo)
    except Exception as exc:
        logger.error("sandbox_init_failed", job_id=job_id, error=str(exc))
        writer({"type": "error", "message": f"Sandbox init failed: {exc}"})
        # Clean up partially-created container to avoid leaks
        if container_id:
            try:
                await sandbox_mgr.destroy(container_id)
            except Exception:
                logger.warning("sandbox_cleanup_failed", container_id=container_id)
        raise RuntimeError(f"Sandbox initialization failed: {exc}") from exc

    writer({"type": "node_completed", "node": "init"})
    return {
        "sandbox_container_id": container_id,
        "current_stage": "initialized",
        "retry_count": 0,
    }


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

    writer = get_stream_writer()
    writer({"type": "node_started", "node": "triage", "agent": "triage"})

    # EMB-35: TRIAGE_MODEL lets triage tier independently of the pipeline
    # default (it's a bounded classification pass — a haiku-class experiment
    # only needs an env flip, no deploy-coupled code change).
    model = os.environ.get("TRIAGE_MODEL") or os.environ.get("DEFAULT_MODEL", "claude-sonnet-4-6")
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
    from embry0.orchestration.nodes.triage import _TRIAGE_SYSTEM_PROMPT, parse_triage_response

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

    prompt = f"Repository: {repo}\n"
    if issue_number:
        prompt += f"Issue #{issue_number}\n"
    prompt += f"\nTask:\n{task}"
    if additional:
        prompt += f"\n\nPrevious Q&A:\n{additional}"
    if qa_failure_block:
        prompt += f"\n\n{qa_failure_block}"

    # EMB-35: the triage instructions ride as a real system prompt (appended
    # to the CLI's preset system block, which its prompt caching covers)
    # instead of being prepended into the user turn and re-billed uncached
    # on every run. Plumbing: agent_definition.system_prompt →
    # invocation.system_prompt → sandbox runner → config_builder.
    triage_agent_definition: dict[str, Any] = {
        "model": model,
        "tools": ["Read", "Glob", "Grep"],
        "skills": [],
        "system_prompt": _TRIAGE_SYSTEM_PROMPT,
        "mcp_servers": {},
        "execution_mode": None,
        "auth_mode": None,
    }

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
        if qa_failure_block:
            prompt = (
                "You are re-invoked after a QA failure on this job.\n\n"
                f"{qa_failure_block}\n\n"
                "Re-emit your full triage decision JSON with the qa_failure_action included."
            )
        else:
            prompt = "Re-emit your full triage decision JSON exactly as instructed previously."

    result = await run_agent_node(
        state=state,
        agent_runner=agent_runner,
        agent_type="triage",
        prompt=prompt,
        agent_definition=triage_agent_definition,
        timeout_seconds=180,
        on_event=_forward_event,
        credentials=credentials,
        config=config,
        resume_session=resume_session,
    )

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
            from embry0.orchestration.nodes.triage import apply_repo_preferences_override
            from embry0.orchestration.state import TriageParseError
            from embry0.safety.error_codes import ErrorCode

            try:
                from typing import cast as _cast

                triage_dict: dict[str, Any] = _cast(dict[str, Any], parse_triage_response(last.get("output", "")))
                triage_dict = await apply_repo_preferences_override(
                    triage_dict,
                    state.get("repo", ""),
                    config["configurable"].get("repo_preferences_repo"),
                )
                result["pipeline_config"] = triage_dict.get("pipeline_config") or {}
                result["triage_decision"] = triage_dict
                result["current_stage"] = "triage_complete"
            except TriageParseError as exc:
                logger.error("triage_parse_error", error=str(exc))
                writer({"type": "error", "message": f"Triage parse failed: {exc}"})
                writer({"type": "node_completed", "node": "triage", "action": "failed"})
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

        _err_msg = _last_triage_output.get("error_message", "Triage agent returned no output")
        logger.error("triage_agent_error", error=_err_msg)
        writer({"type": "error", "message": f"Triage agent failed: {_err_msg}"})
        writer({"type": "node_completed", "node": "triage", "action": "failed"})
        return Command(
            goto=END,
            update={
                "current_stage": "failed",
                "errors": [f"triage_agent_error: {_err_msg}"],
                "error_code": ErrorCode.TRIAGE_MALFORMED.value,
            },
        )

    # Enforce the job-wide ask_user cap on any agent_ask_user events from
    # the triage agent before processing the needs_info action.
    pending_questions = _extract_ask_user_events({"events": collected_events}, calling_node="triage")
    if pending_questions:
        from embry0.orchestration.nodes.agent import _enforce_ask_user_cap

        exhausted, cap_updates = _enforce_ask_user_cap(state, pending_questions, job_id_for_log=state.get("job_id"))
        if exhausted:
            writer({"type": "node_completed", "node": "triage", "action": "failed"})
            return Command(goto="max_retries", update=cap_updates)
        # Only divert to ask_user_interrupt when there are blocking questions.
        # Auto-answerable-only emissions don't pause; fall through to the
        # normal needs_info / proceed routing.
        if cap_updates.get("pending_agent_questions"):
            writer({"type": "node_completed", "node": "triage", "action": "ask_user"})
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
        # Mirror the 5-round cap used by agent_question_rounds / ask_user.
        _triage_rounds = int(state.get("triage_question_rounds", 0) or 0)
        if _triage_rounds >= 5:
            from embry0.safety.error_codes import ErrorCode

            logger.warning(
                "triage_question_rounds_exceeded",
                rounds=_triage_rounds,
                job_id=state.get("job_id"),
            )
            writer({"type": "node_completed", "node": "triage", "action": "failed"})
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
        writer({"type": "interrupt", "node": "triage", "questions": questions})
        logger.info("triage_needs_info", questions=len(questions))

        answers = interrupt(
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
        writer({"type": "progress", "message": "Re-running triage with answers"})

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

        result = await run_agent_node(
            state=updated,
            agent_runner=agent_runner,
            agent_type="triage",
            prompt=rerun_prompt,
            agent_definition=triage_agent_definition,
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
                from embry0.orchestration.nodes.triage import apply_repo_preferences_override
                from embry0.orchestration.state import TriageParseError
                from embry0.safety.error_codes import ErrorCode

                try:
                    from typing import cast as _cast

                    triage_dict2: dict[str, Any] = _cast(dict[str, Any], parse_triage_response(last.get("output", "")))
                    triage_dict2 = await apply_repo_preferences_override(
                        triage_dict2,
                        updated.get("repo", ""),
                        config["configurable"].get("repo_preferences_repo"),
                    )
                    result["pipeline_config"] = triage_dict2.get("pipeline_config") or {}
                    result["triage_decision"] = triage_dict2
                    result["current_stage"] = "triage_complete"
                except TriageParseError as exc:
                    logger.error("triage_parse_error", error=str(exc))
                    writer({"type": "error", "message": f"Triage parse failed: {exc}"})
                    writer({"type": "node_completed", "node": "triage", "action": "failed"})
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

            _err_msg = _last_resume_output.get("error_message", "Triage agent returned no output on resume")
            logger.error("triage_agent_error_resume", error=_err_msg)
            writer({"type": "error", "message": f"Triage agent failed on resume: {_err_msg}"})
            writer({"type": "node_completed", "node": "triage", "action": "failed"})
            return Command(
                goto=END,
                update={
                    "current_stage": "failed",
                    "errors": [f"triage_agent_error: {_err_msg}"],
                    "error_code": ErrorCode.TRIAGE_MALFORMED.value,
                },
            )

        # Increment triage_question_rounds so the next interrupt cycle's cycle
        # guard sees the updated counter. This persists via the result dict which
        # LangGraph merges into state on node completion.
        result["triage_question_rounds"] = _triage_rounds + 1

        # Enforce the cap again on the re-run's events (state now has updated rounds).
        pending_questions = _extract_ask_user_events({"events": collected_events}, calling_node="triage")
        if pending_questions:
            from embry0.orchestration.nodes.agent import _enforce_ask_user_cap

            exhausted, cap_updates = _enforce_ask_user_cap(
                {**state, **result}, pending_questions, job_id_for_log=state.get("job_id")
            )
            if exhausted:
                writer({"type": "node_completed", "node": "triage", "action": "failed"})
                return Command(goto="max_retries", update=cap_updates)
            if cap_updates.get("pending_agent_questions"):
                writer({"type": "node_completed", "node": "triage", "action": "ask_user"})
                return Command(
                    goto="ask_user_interrupt",
                    update={**result, **cap_updates, "current_stage": "triage_asked_user"},
                )
            # Auto-answerable-only — keep the cap_updates on result and
            # continue the post-triage routing below.
            result = {**result, **cap_updates}

    # Phase 5 Task 4: parse the optional set_qa_decision the triage agent
    # emits per the QA Decision section in _TRIAGE_SYSTEM_PROMPT. The prompt
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
    # malformed action ends the job with ERR_QA_FAILURES_UNRESOLVED. The
    # qa.failure_rounds counter is bumped by _qa_failure_bookkeeping_node in
    # graph.py BEFORE this node is re-entered, so we do not double-bump here.
    qa_state_in = state.get("qa") or {}
    if qa_state_in.get("final_status") == "failed":
        # Prefer the freshest parsed decision (resume path updates result["triage_decision"]);
        # fall back to the first-pass triage_dict so the lookup never NPEs.
        decision_dict = (
            result.get("triage_decision") if isinstance(result.get("triage_decision"), dict) else triage_dict
        )
        return _route_qa_failure_action(
            state=state,
            result=result,
            triage_decision=decision_dict if isinstance(decision_dict, dict) else {},
            writer=writer,
        )

    writer({"type": "node_completed", "node": "triage", "action": result.get("triage_decision", {}).get("action")})
    return result


def _format_question_text(question: str, category: str | None, options: list[str] | None) -> str:
    """Inline category + options into the question string so they survive the
    trip through _handle_needs_info → issue_inputs (which only persists the
    `question` TEXT column). Prevents the dashboard from showing a bare prompt
    when the agent offered a category/options context.
    """
    parts: list[str] = []
    if category and category != "general":
        parts.append(f"[{category}]")
    parts.append(question)
    if options:
        parts.append("\nOptions: " + " | ".join(str(o) for o in options))
    return " ".join(parts[:-1]) + (
        parts[-1] if parts[-1].startswith("\n") else (" " + parts[-1] if parts[:-1] else parts[-1])
    )


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
        → ``Command(goto="init_qa", update={"qa_rerun_reason": ...})``
        (re-enters the QA subpath without changing the developer's diff).
      - ``{"kind": "ask_user", "question": str}``
        → ``Command(goto="ask_user_interrupt", update={...})`` with
        ``pending_user_question`` set.

    On missing / malformed / unknown-kind action, sets
    ``state["qa"]["final_status"] = "exhausted"`` and
    ``state["error_code"] = ErrorCode.QA_FAILURES_UNRESOLVED.value`` and
    routes to ``END`` (mirroring the qa_exhausted node so the dashboard
    sees the same shape). ``qa.failure_rounds`` is NOT bumped here — the
    ``_qa_failure_bookkeeping_node`` in graph.py owns that counter and
    has already incremented it before this node fires.
    """
    from pydantic import ValidationError

    from embry0.agents.triage_actions import AskUser, RerunQA, RetryDeveloper
    from embry0.safety.error_codes import ErrorCode

    raw_action = triage_decision.get("qa_failure_action") if isinstance(triage_decision, dict) else None

    def _terminate_unresolved(log_event: str, **log_fields: Any) -> Command[str]:
        existing_qa = state.get("qa") or {}
        merged_qa = {**existing_qa, "final_status": "exhausted"}
        logger.warning(log_event, job_id=state.get("job_id"), **log_fields)
        writer({"type": "node_completed", "node": "triage", "action": "qa_failure_unresolved"})
        return Command(
            goto=END,
            update={
                **result,
                "qa": merged_qa,
                "error_code": ErrorCode.QA_FAILURES_UNRESOLVED.value,
                "current_stage": "failed",
                "errors": [f"qa_failure_unresolved: {log_event}"],
            },
        )

    if not isinstance(raw_action, dict):
        return _terminate_unresolved(
            "triage_qa_failure_action_missing",
            payload_type=type(raw_action).__name__,
        )

    kind = raw_action.get("kind")
    if kind not in ("retry_developer", "rerun_qa", "ask_user"):
        return _terminate_unresolved(
            "triage_qa_failure_action_unknown_kind",
            kind=kind,
        )

    # Strip kind before model validation; the action models don't carry it.
    payload = {k: v for k, v in raw_action.items() if k != "kind"}

    try:
        if kind == "retry_developer":
            action = RetryDeveloper.model_validate(payload)
            writer({"type": "node_completed", "node": "triage", "action": "qa_retry_developer"})
            return Command(
                goto="developer",
                update={
                    **result,
                    "developer_prompt_addendum": action.prompt,
                    "developer_focus_files": list(action.focus_files),
                    "current_stage": "qa_retry_developer",
                },
            )
        if kind == "rerun_qa":
            action_rerun = RerunQA.model_validate(payload)
            writer({"type": "node_completed", "node": "triage", "action": "qa_rerun"})
            return Command(
                goto="init_qa",
                update={
                    **result,
                    "qa_rerun_reason": action_rerun.reason,
                    "current_stage": "qa_rerun",
                },
            )
        # kind == "ask_user"
        action_ask = AskUser.model_validate(payload)
        writer({"type": "node_completed", "node": "triage", "action": "qa_ask_user"})
        return Command(
            goto="ask_user_interrupt",
            update={
                **result,
                "pending_user_question": action_ask.question,
                "current_stage": "qa_ask_user",
            },
        )
    except ValidationError as exc:
        return _terminate_unresolved(
            "triage_qa_failure_action_invalid_payload",
            kind=kind,
            error=str(exc)[:300],
        )


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
        decision = SetQADecision.model_validate(raw)
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
        decision = SetQADecision.model_validate(matches[-1])
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


def _extract_ask_user_events(
    agent_output: dict[str, Any],
    calling_node: str,
) -> list[dict[str, Any]]:
    """Pull any agent_ask_user events out of the agent's streamed events.

    Returns a list of normalized question dicts: {question, category, options,
    asking_node, importance, auto_answer}. The ``question`` string is formatted
    with inline category/options markers so those context hints survive
    persistence through issue_inputs (which only has a plain ``question`` TEXT
    column).

    ``calling_node`` is embedded in each question dict so that
    ``ask_user_interrupt``, WS events, and persisted ``issue_inputs`` rows
    correctly attribute the questions to the node that produced them
    (``"triage"``, ``"developer"``, or ``"review"``).

    ``importance`` defaults to ``"blocking"`` when the event omits the field
    (backward-compatible). When ``importance == "auto_answerable"`` the event's
    ``suggested_answer`` is stored under ``auto_answer`` so the caller can
    persist it as the answer instead of pausing the workflow.
    """
    events = agent_output.get("events", []) if isinstance(agent_output, dict) else []
    pending: list[dict[str, Any]] = []
    for e in events:
        if isinstance(e, dict) and e.get("type") == "agent_ask_user":
            raw_q = e.get("question", "")
            category = e.get("category", "general")
            options = e.get("options", []) or []
            importance = e.get("importance", "blocking")
            auto_answer = e.get("suggested_answer") if importance == "auto_answerable" else None
            pending.append(
                {
                    "question": _format_question_text(raw_q, category, options),
                    "category": category,
                    "options": options,
                    "asking_node": calling_node,
                    "importance": importance,
                    "auto_answer": auto_answer,
                }
            )
    return pending


def _format_user_answers_block(user_answers: Any) -> str:
    """Format resumed user answers as a prompt-ready Q&A block.

    Handles string (pre-formatted), list[dict(question, answer)], and dict[q -> a] shapes.
    Returns empty string if no answers.
    """
    if not user_answers:
        return ""
    lines: list[str] = []
    if isinstance(user_answers, str):
        lines.append(user_answers)
    elif isinstance(user_answers, dict):
        for q, a in user_answers.items():
            lines.append(f"Q: {q}\nA: {a}")
    elif isinstance(user_answers, list):
        for item in user_answers:
            if isinstance(item, dict):
                q = item.get("question", "")
                a = item.get("answer", "")
                lines.append(f"Q: {q}\nA: {a}")
            else:
                lines.append(str(item))
    if not lines:
        return ""
    return "The user has answered your prior questions as follows:\n" + "\n\n".join(lines)


def _developer_branch_name(state: dict[str, Any]) -> str:
    """Compute the developer's git-ref-safe working branch for this job.

    git rejects refs containing .., ~, ^, :, ?, *, [, \\, control chars,
    or names ending in .lock. re.sub strips all non-safe chars in one pass.
    """
    import re as _re

    issue_id = state.get("issue_id", "")
    task = state.get("task", "")
    short_id = issue_id[:12] if issue_id else "unknown"
    slug = _re.sub(r"[^a-z0-9-]+", "-", task[:30].lower()).strip("-") or "task"
    return f"embry0/{short_id}-{slug}"


def _developer_delta_sections(state: dict[str, Any]) -> list[str]:
    """The one-shot context blocks that are NEW since the developer's last
    turn: user answers, review feedback, user retry guidance, and the QA
    failure addendum. Shared by the full prompt (where they extend the
    task context) and the delta prompt (where they ARE the prompt).
    """
    parts: list[str] = []
    answers_block = _format_user_answers_block(state.get("user_answers"))
    if answers_block:
        parts.append(f"\n{answers_block}")
    feedback_context = state.get("feedback_context", "")
    if feedback_context:
        parts.append(f"\nReview Feedback (address these issues):\n{feedback_context}")
    user_retry_guidance = state.get("user_retry_guidance")
    if user_retry_guidance:
        parts.append(f"\nUser guidance:\n{user_retry_guidance}")
    qa_addendum = state.get("developer_prompt_addendum")
    if qa_addendum:
        parts.append(f"\nQA follow-up (from the QA failure analysis):\n{qa_addendum}")
    focus_files = state.get("developer_focus_files") or []
    if focus_files:
        parts.append("\nFocus files:\n" + "\n".join(f"- {f}" for f in focus_files))
    return parts


def _build_developer_full_prompt(state: dict[str, Any], branch_name: str) -> str:
    """Complete task prompt for a FRESH developer conversation."""
    repo = state.get("repo", "")
    task = state.get("task", "")
    issue_number = state.get("issue_number")
    triage_decision = state.get("triage_decision", {})
    additional_context = state.get("additional_context", "")

    prompt_parts = [f"Repository: {repo}"]
    if issue_number:
        prompt_parts.append(f"GitHub Issue: #{issue_number}")
    prompt_parts.append(f"\nTask:\n{task}")

    if additional_context:
        prompt_parts.append(f"\nAdditional Context:\n{additional_context}")

    prompt_parts.extend(_developer_delta_sections(state))

    reasoning = triage_decision.get("reasoning", "")
    if reasoning:
        prompt_parts.append(f"\nTriage Analysis:\n{reasoning}")

    prompt_parts.append("\nInstructions:")
    prompt_parts.append(f"1. Create branch: {branch_name}")
    prompt_parts.append("2. Implement the changes")
    prompt_parts.append("3. Write or update tests")
    prompt_parts.append("4. Commit your changes")
    prompt_parts.append("5. Push the branch")
    prompt_parts.append(
        f"6. Create a PR with 'Closes #{issue_number}' in the body" if issue_number else "6. Create a PR"
    )
    prompt_parts.append(
        "\nIf you need information only the user can provide, ask with:\n"
        "  python -c \"from embry0.sandbox.ask_user import ask_user; ask_user('your question')\"\n"
        "then finish your turn cleanly (commit work in progress first) — the "
        "pipeline pauses, collects the answer, and re-invokes you with it. "
        "Questions asked as plain response text never reach the user."
    )
    prompt_parts.append("\nReturn a JSON object at the end with: pr_url, branch, summary, files_changed")
    return "\n".join(prompt_parts)


def _build_developer_delta_prompt(state: dict[str, Any], branch_name: str) -> str:
    """Delta-only prompt for a RESUMED developer conversation (EMB-35).

    The prior session already carries the repo, task, and all earlier
    context — re-sending them re-bills the whole prompt as input and
    invalidates the CLI's cache. Send only what is new, plus a short
    closing contract so the response shape stays parseable.
    """
    prompt_parts = ["You are resuming your previous session on this task. New information since your last turn:"]
    delta = _developer_delta_sections(state)
    if delta:
        prompt_parts.extend(delta)
    else:
        prompt_parts.append("\n(no new information — continue where you left off)")
    prompt_parts.append(
        f"\nContinue the work: address the above, commit, push to branch {branch_name}, "
        "and update the PR (create one if none exists)."
    )
    prompt_parts.append(
        "\nIf you need information only the user can provide, ask with:\n"
        "  python -c \"from embry0.sandbox.ask_user import ask_user; ask_user('your question')\"\n"
        "then finish your turn cleanly (commit work in progress first) — the "
        "pipeline pauses, collects the answer, and re-invokes you with it. "
        "Questions asked as plain response text never reach the user."
    )
    prompt_parts.append("\nReturn a JSON object at the end with: pr_url, branch, summary, files_changed")
    return "\n".join(prompt_parts)


async def _verify_branch_pushed(
    *,
    docker: Any,
    container_id: str | None,
    branch: str,
) -> bool:
    """Verify that the developer's branch exists on the remote.

    Returns True if the branch is on remote OR if verification cannot run
    (no docker / no container / branch is "main" / docker call raised) —
    the goal is to catch the LLM-forgot-to-push case, not to block on
    infra failures. False ONLY when we can definitively confirm the
    branch is missing from origin.
    """
    if not docker or not container_id or not branch or branch == "main":
        return True
    try:
        out = await docker.run_cmd(
            docker.build_exec_cmd(
                container_id,
                [
                    "git",
                    "-C",
                    "/workspace",
                    "ls-remote",
                    "--heads",
                    "origin",
                    branch,
                ],
            ),
            timeout=15,
        )
    except Exception as exc:
        logger.warning(
            "dev_branch_verify_failed",
            branch=branch,
            error=str(exc),
            note="benefit of doubt — proceeding without blocking",
        )
        return True
    # `git ls-remote --heads origin <branch>` returns:
    #   - empty stdout if the branch doesn't exist on remote
    #   - "<sha>\trefs/heads/<branch>\n" if it does
    return bool(out and branch in out)


async def developer_node(state: dict[str, Any], config: RunnableConfig) -> Command[str]:
    """Run developer agent — write code, create branch, commit, push, open PR.

    Uses AgentRunner.run() to execute a full Claude Code session in the sandbox.
    Returns a Command that self-routes to review / ask_user_interrupt / max_retries
    based on the executor output, pending agent questions, and budget state.
    """
    writer = get_stream_writer()
    writer({"type": "node_started", "node": "developer", "agent": "developer"})

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    agent_runner = configurable.get("agent_runner")
    credentials = configurable.get("credentials") or {}
    agent_sessions_repo = configurable.get("agent_sessions_repo")

    user_answers = state.get("user_answers")
    branch_name = _developer_branch_name(state)

    # Collect agent events locally so we can scan for agent_ask_user events
    # after the agent finishes. We still forward every event to the graph
    # stream writer for live streaming.
    collected_events: list[dict[str, Any]] = []

    def _forward_event(event: dict[str, Any]) -> None:
        collected_events.append(event)
        writer(event)

    # Plan C Task 6: load any prior AgentSession for this (job, agent) so the
    # in-sandbox executor can resume the same Claude conversation. Restore
    # failures must NEVER block the agent run — fall back to a fresh session.
    from embry0.agents.session import AgentSession, merged_resume_messages, session_resumable

    resume_session: AgentSession | None = None
    if agent_sessions_repo is not None:
        try:
            prior = await agent_sessions_repo.get(
                job_id=state.get("job_id", ""),
                agent_type="developer",
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
                "developer_session_restore_failed",
                job_id=state.get("job_id"),
                exc_info=True,
            )
            resume_session = None

    # EMB-35: resume engaged ⇒ send only the delta (the resumed session
    # already carries the task + earlier context); fresh run ⇒ full prompt.
    if session_resumable(resume_session):
        prompt = _build_developer_delta_prompt(state, branch_name)
    else:
        prompt = _build_developer_full_prompt(state, branch_name)

    result = await run_agent_node(
        state=state,
        agent_runner=agent_runner,
        agent_type="developer",
        prompt=prompt,
        model=state.get("pipeline_config", {}).get("agent_models", {}).get("developer", "claude-sonnet-4-6"),
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        timeout_seconds=1800,
        on_event=_forward_event,
        credentials=credentials,
        config=config,
        resume_session=resume_session,
    )

    # Plan C Task 6: capture post-run session state so the next invocation of
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
                        agent_type="developer",
                        mode=mode,
                        messages=new_messages,
                        session_id=new_session_id,
                        session_blob=new_session_blob,
                    )
                except Exception:
                    logger.warning(
                        "developer_session_persist_failed",
                        job_id=state.get("job_id"),
                        exc_info=True,
                    )

    # Scan the agent's event stream for `agent_ask_user` events. If present,
    # surface them to the graph so the router can divert to ask_user_interrupt.
    pending_questions = _extract_ask_user_events({"events": collected_events}, calling_node="developer")

    # Try to extract PR URL from agent output
    pr_url = None
    branch = branch_name
    if result.get("agent_outputs"):
        last_output = result["agent_outputs"][-1] if isinstance(result.get("agent_outputs"), list) else None
        if last_output:
            output_text = last_output.get("output", "")
            import json as json_mod

            try:
                for line in output_text.split("\n"):
                    line = line.strip()
                    if line.startswith("{") and "pr_url" in line:
                        data = json_mod.loads(line)
                        pr_url = data.get("pr_url")
                        branch = data.get("branch", branch)
                        break
            except (json_mod.JSONDecodeError, TypeError):
                pass
            # Fallback: search for GitHub PR URL pattern
            if not pr_url:
                import re

                match = re.search(r"https://github\.com/[^/]+/[^/]+/pull/\d+", output_text)
                if match:
                    pr_url = match.group(0)

    if pr_url:
        writer({"type": "pr_created", "pr_url": pr_url, "branch": branch})

    writer({"type": "node_completed", "node": "developer"})

    updates: dict[str, Any] = {
        **result,
        "pr_url": pr_url,
        "branch_name": branch,
        "current_stage": "developer_complete",
    }

    if pending_questions:
        from embry0.orchestration.nodes.agent import _enforce_ask_user_cap

        # Brainstorming sessions can legitimately need many turns of design
        # back-and-forth. Bump the cap when triage attached the brainstorming
        # skill; everything else stays at the default of 5.
        skills_for_dev = (state.get("pipeline_config", {}) or {}).get("agent_skills", {}).get("developer", [])
        ask_cap = BRAINSTORMING_ASK_USER_CAP if "superpowers:brainstorming" in skills_for_dev else DEFAULT_ASK_USER_CAP

        exhausted, cap_updates = _enforce_ask_user_cap(
            state,
            pending_questions,
            max_rounds=ask_cap,
            job_id_for_log=state.get("job_id"),
        )
        updates.update(cap_updates)
        if exhausted:
            pass  # current_stage and error fields already set in updates
        elif updates.get("pending_agent_questions"):
            # Blocking questions exist — the cap was incremented and the
            # workflow will pause at ask_user_interrupt.
            updates["current_stage"] = "developer_asked_user"
            logger.info(
                "developer_asked_user",
                question_count=len(updates["pending_agent_questions"]),
                rounds=updates["agent_question_rounds"],
                job_id=state.get("job_id"),
            )
        else:
            # All questions were auto-answerable — workflow proceeds without
            # pausing. Log so the auto-answers remain observable in the trace.
            logger.info(
                "developer_auto_answered",
                question_count=len(updates.get("auto_answered_agent_questions", [])),
                job_id=state.get("job_id"),
            )

    # Clear consumed one-shot context so it doesn't bleed into future runs —
    # with delta prompts (EMB-35) a stale block would be re-sent verbatim on
    # every subsequent resumed re-entry.
    if user_answers:
        updates["user_answers"] = None
    if state.get("feedback_context"):
        updates["feedback_context"] = ""
    if state.get("user_retry_guidance"):
        updates["user_retry_guidance"] = None
    if state.get("developer_prompt_addendum"):
        updates["developer_prompt_addendum"] = None
    if state.get("developer_focus_files"):
        updates["developer_focus_files"] = []

    # Self-routing: decide the next node based on the updates we just produced
    # and the state we were handed. Priority:
    #   1. agent_questions_exhausted wins — terminal failure (max_retries).
    #   2. Pending agent questions divert to ask_user_interrupt.
    #   3. Budget overrun also diverts to max_retries.
    #   4. Otherwise proceed to review.
    pipeline = state.get("pipeline_config", {}) or {}
    budget = pipeline.get("budget_usd", 10.0) if isinstance(pipeline, dict) else 10.0

    if updates.get("agent_questions_exhausted") or state.get("agent_questions_exhausted"):
        goto = "max_retries"
    elif updates.get("pending_agent_questions") or state.get("pending_agent_questions"):
        goto = "ask_user_interrupt"
    elif updates.get("total_cost_usd", state.get("total_cost_usd", 0.0)) > budget:
        goto = "max_retries"
    else:
        # Programmatic verification: the LLM is INSTRUCTED to push the branch
        # (prompt step 5) but doesn't always follow through. If we proceed to
        # review→init_qa with no branch on remote, init_qa hits a generic
        # "Remote branch not found" clone error after spinning up the QA
        # sandbox — wastes time + cost + obscures the real cause. Verify
        # here and fail fast with a clear error code.
        push_ok = await _verify_branch_pushed(
            docker=configurable.get("docker"),
            container_id=state.get("sandbox_container_id"),
            branch=branch,
        )
        if not push_ok:
            from embry0.safety.error_codes import ErrorCode

            logger.error(
                "dev_branch_not_pushed",
                branch=branch,
                job_id=state.get("job_id"),
            )
            updates["errors"] = (state.get("errors") or []) + [
                f"Developer agent did not push branch {branch!r} to origin. "
                f"This breaks the QA handoff: init_qa_node cannot clone what was never pushed. "
                f"Restart the job after the dev agent has been corrected, or push the branch manually."
            ]
            updates["error_code"] = ErrorCode.DEV_BRANCH_NOT_PUSHED.value
            updates["current_stage"] = "dev_branch_missing"
            goto = "max_retries"
        else:
            goto = "review"

    return Command(goto=goto, update=updates)


async def review_node(state: dict[str, Any], config: RunnableConfig) -> Command[str] | dict[str, Any]:
    """Run review agent — tests, lint, typecheck, code review.

    Returns structured JSON with decision, validation results, and comments.

    Routing (Phase 5 Task 5):
      - The "happy path" decisions (``approved`` / ``changes_requested``) set
        ``current_stage`` to ``review_passed`` / ``review_failed`` and return
        a plain dict, letting the conditional edge ``route_after_review`` in
        ``graph.py`` dispatch to ``init_qa`` / ``retry`` / ``END`` based on
        the triage-set ``state["qa"]["needs_qa"]`` flag.
      - The control-flow exits (no runner, ask_user cap exhausted, pending
        agent questions, retry cap hit) keep self-routing via
        ``Command(goto=...)`` because they bypass ``route_after_review``.
    """
    writer = get_stream_writer()
    writer({"type": "node_started", "node": "review", "agent": "review"})

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    agent_runner = configurable.get("agent_runner")
    credentials = configurable.get("credentials") or {}
    agent_sessions_repo = configurable.get("agent_sessions_repo")
    if not agent_runner:
        logger.error("review_node_no_runner")
        from embry0.safety.error_codes import ErrorCode

        return Command(
            goto="max_retries",
            update={
                "current_stage": "review_complete",
                "errors": ["No agent runner available"],
                "error_code": ErrorCode.UNKNOWN.value,
            },
        )

    repo = state.get("repo", "")
    pr_url = state.get("pr_url", "")
    branch = state.get("branch_name", "")
    base_branch = state.get("base_branch") or "main"

    # EMB-35: scope the review input by the branch's changed-file set instead
    # of pointing the agent at an unscoped `git diff main...HEAD`. Best-effort
    # — an empty/failed diff keeps today's unscoped prompt.
    from embry0.execution.workspace_diff import compute_changed_files_via_diff

    changed_files: list[str] = []
    docker = configurable.get("docker")
    container_id = state.get("sandbox_container_id")
    if docker is not None and container_id:
        try:
            changed_files = await compute_changed_files_via_diff(
                docker=docker,
                container_id=container_id,
                base_branch=base_branch,
            )
        except Exception:
            logger.warning("review_changed_files_diff_failed", job_id=state.get("job_id"), exc_info=True)
            changed_files = []

    if changed_files:
        scope_block = (
            f"Changed files in this branch ({len(changed_files)}):\n"
            + "\n".join(f"- {f}" for f in changed_files)
            + "\n\nFocus your review on these files. The full diff remains available via "
            f"`git diff {base_branch}...HEAD` when context demands it."
        )
        diff_step = f"4. Review the changes to the files listed above (`git diff {base_branch}...HEAD -- <path>`)"
    else:
        scope_block = ""
        diff_step = f"4. Review the git diff: `git diff {base_branch}...HEAD`"

    prompt = f"""Repository: {repo}
Branch: {branch}
PR: {pr_url or "N/A"}
{scope_block}

You are reviewing code changes made by the developer agent. Your job:

1. Run the test suite (if tests exist): find and run pytest, npm test, or equivalent
2. Run linting (if configured): ruff, eslint, or equivalent
3. Run type checking (if configured): mypy, tsc, or equivalent
{diff_step}
5. Assess code quality, correctness, and completeness
6. Check if documentation needs updating: read README.md, CLAUDE.md, docs/architecture.md,
   and any other docs files. If the code changes affect documented features, APIs, project
   structure, configuration, or architecture, flag the specific docs that need updating.

If a review decision genuinely requires information only the user can provide, ask with:
  python -c "from embry0.sandbox.ask_user import ask_user; ask_user('your question')"
then finish your turn (still returning the JSON below) — the pipeline pauses for the answer.
Questions asked as plain response text never reach the user.

Return ONLY a JSON object:
{{
    "decision": "approved" | "changes_requested",
    "validation": {{
        "tests": {{"status": "pass"|"fail"|"skipped", "output": "first 500 chars of output"}},
        "lint": {{"status": "pass"|"fail"|"skipped", "output": "..."}},
        "typecheck": {{"status": "pass"|"fail"|"skipped", "output": "..."}}
    }},
    "review_comments": [
        {{"file": "path/to/file", "line": 42, "severity": "critical"|"important"|"suggestion", "comment": "..."}}
    ],
    "docs_review": {{
        "needs_update": true | false,
        "files": ["README.md", "docs/architecture.md"],
        "suggestions": ["Add new endpoint X to API section", "Update project structure diagram"]
    }},
    "summary": "Overall assessment"
}}"""

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
                agent_type="review",
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
                "review_session_restore_failed",
                job_id=state.get("job_id"),
                exc_info=True,
            )
            resume_session = None

    # EMB-35: resumed session (re-review after the developer addressed
    # feedback) ⇒ delta prompt. The response-shape contract is re-sent in
    # full — it's cheap and guarantees the output stays parseable.
    if session_resumable(resume_session):
        schema_start = prompt.index("Return ONLY a JSON object:")
        prompt = (
            "The developer pushed a new revision addressing your review "
            "feedback. Re-run the checks (tests, lint, typecheck), re-review "
            "the updated diff, and decide again.\n\n" + prompt[schema_start:]
        )

    result = await run_agent_node(
        state=state,
        agent_runner=agent_runner,
        agent_type="review",
        prompt=prompt,
        model=state.get("pipeline_config", {}).get("agent_models", {}).get("review", "claude-sonnet-4-6"),
        tools=["Read", "Bash", "Glob", "Grep"],
        timeout_seconds=300,
        on_event=_forward_event,
        credentials=credentials,
        config=config,
        resume_session=resume_session,
    )

    # Plan C Task 7: capture post-run session state so the next invocation of
    # this node (after an ask_user pause, retry, etc.) resumes the same
    # conversation. Skip on agent error and skip when both messages and
    # session_id are absent. Persist failures are best-effort.
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
                        agent_type="review",
                        mode=mode,
                        messages=new_messages,
                        session_id=new_session_id,
                        session_blob=new_session_blob,
                    )
                except Exception:
                    logger.warning(
                        "review_session_persist_failed",
                        job_id=state.get("job_id"),
                        exc_info=True,
                    )

    # Extract validation and decision from output for streaming
    if result.get("agent_outputs"):
        last_output = result["agent_outputs"][-1] if isinstance(result.get("agent_outputs"), list) else None
        if last_output:
            output_text = last_output.get("output", "")
            import json as json_mod

            try:
                data = json_mod.loads(output_text)
                writer({"type": "validation_result", **data.get("validation", {})})
                writer({"type": "review_decision", "decision": data.get("decision"), "summary": data.get("summary")})
            except (json_mod.JSONDecodeError, TypeError):
                pass

    writer({"type": "node_completed", "node": "review"})

    updates: dict[str, Any] = {**result, "current_stage": "review_complete"}
    if changed_files:
        # EMB-35: persist the computed change set so later phases can reuse
        # it without re-diffing. Top-level JobState key (reducer constraint).
        updates["changed_files"] = changed_files

    # Scan the agent's event stream for `agent_ask_user` events and enforce
    # the job-wide cap before routing.
    pending_questions = _extract_ask_user_events({"events": collected_events}, calling_node="review")
    if pending_questions:
        from embry0.orchestration.nodes.agent import _enforce_ask_user_cap

        exhausted, cap_updates = _enforce_ask_user_cap(state, pending_questions, job_id_for_log=state.get("job_id"))
        updates.update(cap_updates)
        if exhausted:
            pass  # terminal failure shape already in updates
        elif updates.get("pending_agent_questions"):
            updates["current_stage"] = "review_asked_user"
        # Auto-answerable-only: leave current_stage as "review_complete" so
        # the normal route_after_review path picks up the review decision.

    # Routing:
    #   1. Control-flow exits (cap exhausted, pending questions) keep
    #      Command(goto=...) — they bypass route_after_review.
    #   2. The retry-cap exhausted case (decision=changes_requested but
    #      retry budget gone) also routes via Command(goto="max_retries").
    #   3. The plain approved / changes_requested decisions set
    #      current_stage and return a plain dict; route_after_review in
    #      graph.py then dispatches to init_qa / retry / END.
    if updates.get("agent_questions_exhausted") or state.get("agent_questions_exhausted"):
        return Command(goto="max_retries", update=updates)
    if updates.get("pending_agent_questions") or state.get("pending_agent_questions"):
        return Command(goto="ask_user_interrupt", update=updates)

    from embry0.orchestration.routing.conditions import check_review_decision

    merged_outputs = list(state.get("agent_outputs", []) or []) + list(result.get("agent_outputs", []) or [])
    merged_state = {**state, "agent_outputs": merged_outputs}
    decision = check_review_decision(merged_state)

    if decision == "max_retries":
        # Retry cap exhausted — bypass route_after_review.
        return Command(goto="max_retries", update=updates)

    # Approved or changes_requested: set the stage marker route_after_review
    # consults and return a plain dict so the conditional edge can dispatch.
    if decision == "approved":
        updates["current_stage"] = "review_passed"
    else:
        # decision == "changes_requested" (or anything else that maps to retry)
        updates["current_stage"] = "review_failed"
    return updates


async def retry_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Inject review feedback into state and increment retry count."""
    writer = get_stream_writer()
    retry_count = state.get("retry_count", 0) + 1
    writer({"type": "progress", "message": f"Retry {retry_count}: addressing review feedback"})
    logger.info("retry_developer", retry_count=retry_count)

    # Extract feedback from the latest review output
    feedback = ""
    outputs = state.get("agent_outputs", [])
    review_outputs = [o for o in outputs if o.get("agent_type") == "review"]
    if review_outputs:
        feedback = review_outputs[-1].get("output", "")

    return {
        "retry_count": retry_count,
        "feedback_context": feedback,
        "current_stage": "developer_retry",
    }


async def max_retries_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Max retries reached — pause pipeline and ask user what to do.

    Special case: if the job arrives here because the agent_question_rounds
    cap was exhausted (ERR_MAX_AGENT_QUESTIONS), do NOT offer 'continue_retrying'
    — the cap is already tripped, so continuing would loop. Offer only
    'merge_as_is' (if a PR was produced) or 'abandon'.
    """
    writer = get_stream_writer()
    retry_count = state.get("retry_count", 0)
    pr_url = state.get("pr_url", "")
    exhausted = bool(state.get("agent_questions_exhausted"))

    if exhausted:
        message = "Agent exceeded the ask_user round cap — cannot continue."
        reason = "agent_questions_exhausted"
        options = ["merge_as_is", "abandon"] if pr_url else ["abandon"]
    else:
        message = f"Review failed after {retry_count} attempts"
        reason = "max_retries"
        options = ["continue_retrying", "merge_as_is", "abandon"]

    summary = {
        "message": message,
        "reason": reason,
        "pr_url": pr_url,
        "retry_count": retry_count,
        "options": options,
    }

    outputs = state.get("agent_outputs", [])
    review_outputs = [o for o in outputs if o.get("agent_type") == "review"]
    if review_outputs:
        summary["latest_review"] = review_outputs[-1].get("output", "")[:1000]

    writer({"type": "interrupt", "node": "max_retries", "reason": "max_retries", "summary": summary})
    logger.info("max_retries_reached", retry_count=retry_count)

    user_choice = interrupt(summary)

    if isinstance(user_choice, str):
        choice = user_choice
        guidance = ""
    elif isinstance(user_choice, dict):
        choice = user_choice.get("choice", "abandon")
        guidance = user_choice.get("guidance", "")
    else:
        choice = "abandon"
        guidance = ""

    # Refuse continue_retrying when agent_questions were exhausted — the cap
    # is still tripped and continuing would immediately fail again.
    if exhausted and choice == "continue_retrying":
        logger.warning(
            "continue_retrying_rejected_agent_questions_exhausted",
            job_id=state.get("job_id"),
        )
        choice = "abandon"

    if choice == "continue_retrying":
        user_rounds = int(state.get("user_retry_rounds", 0) or 0)
        max_user_retries = 3
        if user_rounds >= max_user_retries:
            logger.warning("user_retry_rounds_exceeded", job_id=state.get("job_id"))
            return {
                "current_stage": "abandoned",
                "errors": [f"Exceeded {max_user_retries} user-initiated retries"],
            }
        updates: dict[str, Any] = {
            "retry_count": 0,
            "current_stage": "developer_retry",
            "user_retry_rounds": user_rounds + 1,
        }
        if guidance:
            existing = state.get("additional_context", "") or ""
            updates["additional_context"] = existing + f"\n\nUser guidance:\n{guidance}"
            # EMB-35: also carry the guidance as a one-shot field — the
            # delta prompt sends just this on a resumed session instead of
            # re-parsing it out of the accumulated additional_context.
            updates["user_retry_guidance"] = guidance
        return updates
    elif choice == "merge_as_is":
        return {"current_stage": "completed"}
    else:
        return {"current_stage": "abandoned", "errors": [f"Abandoned after {retry_count} retries"]}


async def ask_user_interrupt(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Pause the pipeline — raise an interrupt with the pending questions.

    When resumed via Command(resume=answers), the pending questions are cleared
    and the user's answers are stored on state so the developer node's next
    invocation can include them in its prompt.
    """
    writer = get_stream_writer()

    questions = state.get("pending_agent_questions", []) or []
    asking_node = questions[0].get("asking_node", "developer") if questions else "developer"

    writer({"type": "agent_paused_for_input", "node": asking_node, "count": len(questions)})
    logger.info("agent_ask_user_interrupt", node=asking_node, count=len(questions))

    # interrupt() raises; the graph halts here until Command(resume=...) arrives.
    answers = interrupt(
        {
            "asking_node": asking_node,
            "questions": questions,
            "reason": "agent_needs_info",
        }
    )

    return {
        "pending_agent_questions": [],
        "user_answers": answers,
    }
