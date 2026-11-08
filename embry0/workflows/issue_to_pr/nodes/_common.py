"""Shared helpers for the issue-to-PR node modules (RAV-1235 split)."""

from __future__ import annotations

from typing import Any

import structlog

# Shared seams (run_agent_node, get_stream_writer, interrupt,
# _verify_branch_pushed) are resolved through the package namespace at CALL
# time so test patches on `embry0.workflows.issue_to_pr.nodes.<seam>` reach
# this module. The self-import is safe: the package __init__ binds the seams
# before importing the node modules.

logger = structlog.get_logger(__name__)

# RAV-1260: budget for the one-shot decide-now resume after a turn-cap
# death — enough for a couple of closing tool calls plus the final output,
# small enough that a second spelunking spree is impossible.
DECIDE_NOW_MAX_TURNS = 4


def job_repository(state: dict[str, Any]) -> str:
    """The repository the job's sandbox cloned: its git context's ``repo``, else the job's.

    One expression for the clone (``init_node``) and for every later read
    of that repository on the forge (the verify node), so the two always
    name the same one.
    """
    context = state.get("context") or {}
    return str((context.get("repo") if isinstance(context, dict) else None) or state.get("repo") or "")


def _max_turns_recovery_session(
    result: dict[str, Any],
    *,
    agent_type: str,
    job_id: str | None,
    credentials: dict[str, str],
) -> Any | None:
    """Build the resume session for the decide-now cap recovery (RAV-1260).

    Returns an ``AgentSession`` when the last agent output is a turn-cap
    death whose session state survived (the executor keeps messages /
    session_id / blob for exactly this error class), else ``None``.
    The caller re-runs the agent ONCE with this session and a decide-now
    prompt; only a second failure ends the job.
    """
    from typing import Literal, cast

    from embry0.agents.session import AgentSession, is_max_turns_error, session_resumable

    outputs = result.get("agent_outputs") or []
    last = outputs[-1] if outputs else None
    if not last or not last.get("is_error") or not is_max_turns_error(last.get("error_message")):
        return None
    mode = cast(
        "Literal['anthropic_api', 'claude_max']",
        last.get("mode") or ("claude_max" if credentials.get("oauth_token") else "anthropic_api"),
    )
    session = AgentSession(
        job_id=job_id or "",
        agent_type=agent_type,
        mode=mode,
        messages=last.get("messages"),
        session_id=last.get("session_id"),
        session_blob=last.get("session_blob"),
    )
    if not session_resumable(session):
        logger.warning(
            "decide_now_resume_unavailable",
            agent_type=agent_type,
            job_id=job_id,
            has_session_id=bool(session.session_id),
            has_blob=bool(session.session_blob),
            has_messages=bool(session.messages),
        )
        return None
    return session


def _filter_user_env_for_sandbox(user_env: list[dict[str, str]] | dict[str, str], *, qa_active: bool) -> dict[str, str]:
    """Merge user env vars into the sandbox environment, filtering by scope.

    - scope='app' (or unspecified): always included.
    - scope='qa':  included only when qa_active=True.
    - Keys that are not variable names, reserved keys/prefixes, and guarded
      keys whose value is outside their allowed flags: always dropped
      (defense-in-depth vs API validation, for rows stored before a key was
      reserved or written around the API).

    Accepts either the new list-of-dicts shape or the legacy plain dict
    (treated as all scope='app').
    """
    from embry0.execution.auth_provider import env_name_refusal, env_override_refusal, shown_env_key

    # Normalize to list-of-dicts
    if isinstance(user_env, dict):
        rows: list[dict[str, str]] = [{"key": k, "value": v, "scope": "app"} for k, v in user_env.items()]
    else:
        rows = list(user_env)

    out: dict[str, str] = {}
    for row in rows:
        key = row["key"]
        name_refusal = env_name_refusal(key)
        if name_refusal is not None:
            shown, cut = shown_env_key(key)
            logger.warning("user_env_invalid_name_dropped", key=shown, key_cut_at_equals=cut, reason=name_refusal)
            continue
        # The same case-insensitive check the API applies (auth_provider).
        refusal = env_override_refusal(key, row.get("value", ""))
        if refusal is not None:
            logger.warning("user_env_reserved_key_dropped", key=key, reason=refusal)
            continue
        scope = row.get("scope", "app")
        if scope == "qa" and not qa_active:
            continue
        out[key] = row["value"]
    return out


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


def _format_user_answers_block(user_answers: Any) -> str:
    """Format resumed user answers as a prompt-ready Q&A block.

    Handles string (pre-formatted), list[dict(question, answer)], and dict[q -> a] shapes.
    Returns empty string if no answers. Shared by the developer/review/triage
    nodes — each asker folds the block into its own prompt when
    ``ask_user_interrupt`` routes the resume back to it (RAV-1271).
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
