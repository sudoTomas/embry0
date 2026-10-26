"""review node (RAV-1235 split of nodes.py)."""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

# Shared seams (run_agent_node, get_stream_writer, interrupt,
# _verify_branch_pushed) are resolved through the package namespace at CALL
# time so test patches on `embry0.workflows.issue_to_pr.nodes.<seam>` reach
# this module. The self-import is safe: the package __init__ binds the seams
# before importing the node modules.
import embry0.workflows.issue_to_pr.nodes as _nodes
from embry0.execution import events
from embry0.orchestration.nodes.agent import agent_attempt
from embry0.workflows.issue_to_pr.nodes._common import (
    DECIDE_NOW_MAX_TURNS,
    _extract_ask_user_events,
    _max_turns_recovery_session,
)

logger = structlog.get_logger(__name__)

# RAV-1405: hard cap on the changed-file paths embedded in the review prompt.
# An unbounded list (3994 files when the diff base was wrong) rode the sandbox
# runner's --config JSON as a single exec argv element, blew past ARG_MAX and
# killed the job with "[Errno 7] Argument list too long". The config now
# travels on stdin (#304), so the cap is prompt hygiene, not a crash guard.
SCOPE_FILES_CAP = 200


def format_review_scope(changed_files: list[str], base_branch: str) -> tuple[str, str]:
    """Build the (scope_block, diff_step) prompt fragments, size-bounded."""
    if not changed_files:
        return "", f"4. Review the git diff: `git diff {base_branch}...HEAD`"
    shown = changed_files[:SCOPE_FILES_CAP]
    truncated = len(changed_files) - len(shown)
    lines = "\n".join(f"- {f}" for f in shown)
    if truncated:
        lines += f"\n- … and {truncated} more (list truncated — use `git diff --name-only` for the full set)"
    scope_block = (
        f"Changed files in this branch ({len(changed_files)}):\n"
        + lines
        + "\n\nFocus your review on these files. The full diff remains available via "
        f"`git diff {base_branch}...HEAD` when context demands it."
    )
    diff_step = f"4. Review the changes to the files listed above (`git diff {base_branch}...HEAD -- <path>`)"
    return scope_block, diff_step


# Reviewer task block bound (SH-P4.4). Prose, so truncation is safe; the block
# rides the runner config next to the changed-files list.
REVIEW_TASK_CHARS_CAP = 6000
# Wall-clock the review gets, first pass and the verdict-now retry alike. Told
# to the reviewer in its prompt (2026-09-27): a reviewer that starts the whole
# suite in a sandbox with no database burns the budget on ConnectionRefused
# and ends at the turn cap with no verdict — seen on the self stack, where the
# unit suite needs Postgres and takes 27 minutes with one.
REVIEW_TIMEOUT_SECONDS = 300


def format_review_task(state: dict[str, Any], *, failure_step_number: int = 7) -> tuple[str, str, str]:
    """``(task_block, failure_step, verdict_field)`` for the review prompt (SH-P4.4).

    The reviewer used to get a diff with no statement of what it was for. The
    block states the task, its acceptance criteria and the logged failure; the
    step and verdict field are empty when the job has no log evidence.

    The task text is redacted: the developer's provider already receives the
    ticket, but the reviewer may run on a different one, and this prompt must
    not become a new path for raw log lines (#294).
    """
    from embry0.safety.redaction import redact_bounded
    from embry0.workflows.issue_to_pr.evidence import acceptance_criteria_in, format_evidence_block

    raw_task = str(state.get("task") or "").strip()
    # Bound BEFORE redacting: an issue body has no length cap at the API.
    task = redact_bounded(raw_task, REVIEW_TASK_CHARS_CAP)
    if len(raw_task) > REVIEW_TASK_CHARS_CAP:
        task += "\n… [task truncated]"
    criteria = redact_bounded(acceptance_criteria_in(raw_task), REVIEW_TASK_CHARS_CAP // 3)
    parts = [f"Task the developer was given:\n{task or '(none recorded)'}"]
    # Done when (spec 2026-10-26): the job's own finish line, with the
    # developer's report on it, replaces the ticket's criteria section.
    from embry0.services.done_when import review_block

    finish_line = redact_bounded(review_block(state.get("done_when")), REVIEW_TASK_CHARS_CAP // 2)
    if finish_line:
        parts.append(finish_line)
    else:
        parts.append(
            f"Acceptance criteria:\n{criteria}"
            if criteria
            else 'Acceptance criteria: none stated — derive what "done" means from the task.'
        )
    evidence_block = format_evidence_block(state.get("case_evidence"))
    if not evidence_block:
        return "\n\n".join(parts) + "\n", "", ""
    parts.append(evidence_block)
    failure_step = (
        f"\n{failure_step_number}. Check that the diff actually addresses the logged failure above — the code path that "
        "produces that signature — and not only a symptom of it or an unrelated nearby problem. "
        "Trace the failing path in the code. If the change cannot stop that failure from recurring, "
        "the decision is changes_requested, and say what the real cause appears to be."
    )
    verdict_field = (
        '\n    "addresses_logged_failure": {"verdict": "yes" | "no" | "unclear", '
        '"reason": "which code path produces the logged failure and how the diff changes it"},'
    )
    return "\n\n".join(parts) + "\n", failure_step, verdict_field


def _configured_base_branch(state: dict[str, Any]) -> str | None:
    """``state["base_branch"]`` when something actually chose it, else None.

    Spec 1A §3.7 made ``init_node`` populate ``state["base_branch"]`` for
    every job, and ``services.base_branch.resolve_base_branch`` never returns
    empty — so passing it straight through made ``workspace_diff.
    resolve_base_branch`` short-circuit on its first line and its
    ``origin/HEAD`` symref step unreachable. Where the forge step was
    unavailable (no token, API error, unregistered repo) that replaced a free
    and correct symref answer with the literal "main", silently undoing
    RAV-1405 on the review path.

    So a branch the resolver labelled ``FALLBACK_SOURCE`` counts as *no
    preference expressed*, exactly as ``qa/orchestrator._configured_base_
    branch`` treats an absent preference. An absent label (a job checkpointed
    before this key existed) is treated as configured: the old behaviour, and
    the safe side — a real preference must never be overridden by the symref.
    """
    from embry0.services.base_branch import FALLBACK_SOURCE

    if state.get("base_branch_source") == FALLBACK_SOURCE:
        return None
    configured = state.get("base_branch")
    return str(configured) if configured else None


def _slop_review_section(state: dict[str, Any]) -> str:
    """What the reviewer checks of a slop cleanup (spec 2026-10-25, slop dungeon); '' for any other change.

    Placed before the JSON contract, so a resumed review's delta prompt
    (which keeps only the contract) does not resend it.
    """
    from embry0.services.slop import SLOP_REVIEW_GUIDE, SLOPMASTER

    if state.get("coding_agent") != SLOPMASTER:
        return ""
    section = SLOP_REVIEW_GUIDE
    cleanup = (state.get("work_nature") or {}).get("cleanup")
    if isinstance(cleanup, dict) and {"added", "removed"} <= cleanup.keys():
        section += f"\nThe branch adds {cleanup['added']} lines and removes {cleanup['removed']} against its base."
    return section + "\n"


async def review_node(state: dict[str, Any], config: RunnableConfig) -> Command[str] | dict[str, Any]:
    """Run review agent — tests, lint, typecheck, code review.

    Returns structured JSON with decision, validation results, and comments.

    Routing (Phase 5 Task 5):
      - The "happy path" decisions (``approved`` / ``changes_requested``) set
        ``current_stage`` to ``review_passed`` / ``review_failed`` and return
        a plain dict, letting the conditional edge ``route_after_review`` in
        ``graph.py`` dispatch to ``init_orchestrator`` / ``retry`` / ``END`` based on
        the triage-set ``state["qa"]["needs_qa"]`` flag.
      - The control-flow exits (no runner, ask_user cap exhausted, pending
        agent questions, retry cap hit) keep self-routing via
        ``Command(goto=...)`` because they bypass ``route_after_review``.
    """
    writer = _nodes.get_stream_writer()
    attempt = agent_attempt(state, "review")
    writer(events.node_started(node="review", agent="review", attempt=attempt))

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
                "error_code": ErrorCode.AGENT_RUNNER_UNAVAILABLE.value,
            },
        )

    repo = state.get("repo", "")
    pr_url = state.get("pr_url", "")
    branch = state.get("branch_name", "")

    # EMB-35: scope the review input by the branch's changed-file set instead
    # of pointing the agent at an unscoped `git diff main...HEAD`. Best-effort
    # — an empty/failed diff keeps today's unscoped prompt. RAV-1405: the diff
    # base is the repo's real default branch (origin/HEAD) unless the job
    # configured one — the old hardcoded "main" diffed repos whose default is
    # master against an unrelated orphan branch.
    from embry0.execution.workspace_diff import (
        compute_changed_files_via_diff,
        resolve_base_branch,
    )

    changed_files: list[str] = []
    docker = configurable.get("docker")
    container_id = state.get("sandbox_container_id")
    base_branch = state.get("base_branch") or "main"
    if docker is not None and container_id:
        base_branch = await resolve_base_branch(
            docker=docker,
            container_id=container_id,
            configured=_configured_base_branch(state),
        )
        try:
            changed_files = await compute_changed_files_via_diff(
                docker=docker,
                container_id=container_id,
                base_branch=base_branch,
            )
        except Exception:
            logger.warning("review_changed_files_diff_failed", job_id=state.get("job_id"), exc_info=True)
            changed_files = []

    scope_block, diff_step = format_review_scope(changed_files, base_branch)

    # RAV-1079: the same open-PR list the developer saw, so the verdict can
    # flag overlap with an in-flight PR. Placed BEFORE the "Return ONLY a
    # JSON object:" marker — the resumed-session delta prompt truncates to
    # that marker and must not resend context blocks.
    from embry0.services.github_prs import format_open_prs_block

    open_prs_section = format_open_prs_block(state.get("open_prs") or [])
    if open_prs_section:
        open_prs_section = f"\n{open_prs_section}\n"
        overlap_step = (
            "\n7. If one of the open PRs listed above substantially overlaps these changes, "
            "set duplicates_open_pr in your JSON verdict (else null)."
        )
    else:
        open_prs_section = "\n"
        overlap_step = ""

    task_block, failure_step, failure_verdict_field = format_review_task(
        state, failure_step_number=8 if overlap_step else 7
    )
    slop_section = _slop_review_section(state)

    prompt = f"""Repository: {repo}
Branch: {branch}
PR: {pr_url or "N/A"}

{task_block}{scope_block}{open_prs_section}{slop_section}
You are reviewing code changes made by the developer agent against the task above. Your job:

1. Run the tests that cover the change — the test files the diff touches and the tests of
   the modules it changes (`pytest <paths>`, `npm test -- <paths>`, `./mvnw -Dtest=… test`,
   or equivalent). Do NOT run the whole suite: this sandbox has no database or other
   backing services, and the review has {REVIEW_TIMEOUT_SECONDS} seconds in total — a full
   run times out and leaves no verdict. When the runner starts and a test needs a service
   this sandbox does not have, mark tests "skipped" with reason "needs_service". When the
   diff has no code a test could cover, reason is "not_applicable". When the compiler or
   test runner is not installed (`java: not found`, `pytest: command not found`), reason
   is "no_toolchain" — do not approve. When you did not run the tests, reason is "not_run"
   — do not approve. A check you mark "fail" is never an approval.
2. Run linting on the changed files (if configured): ruff, eslint, or equivalent
3. Run type checking on the changed files (if configured): mypy, tsc, or equivalent
{diff_step}
5. Assess code quality, correctness, and completeness — completeness means the task and its
   acceptance criteria above, not only that the diff is internally consistent
6. Check if documentation needs updating: read README.md, CLAUDE.md, docs/architecture.md,
   and any other docs files. If the code changes affect documented features, APIs, project
   structure, configuration, or architecture, flag the specific docs that need updating.{overlap_step}{failure_step}

If a review decision genuinely requires information only the user can provide, ask with:
  python -c "from embry0.sandbox.ask_user import ask_user; ask_user('your question')"
then finish your turn (still returning the JSON below) — the pipeline pauses for the answer.
Questions asked as plain response text never reach the user.

Return ONLY a JSON object:
{{
    "decision": "approved" | "changes_requested",
    "validation": {{
        "tests": {{"status": "pass"|"fail"|"skipped", "reason": "needs_service"|"not_applicable"|"no_toolchain"|"not_run"|null, "output": "first 500 chars of output"}},
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
    "duplicates_open_pr": {{"number": 123, "reason": "why these changes overlap that open PR"}} | null,{failure_verdict_field}
    "summary": "Overall assessment"
}}
The JSON object is the last thing you output — nothing after it."""

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

    # RAV-1271: a review-asked question answered via ask_user_interrupt
    # re-enters HERE (the interrupt node routes the resume back to the asking
    # node). Fold the answers into whichever prompt shape this run uses.
    from embry0.workflows.issue_to_pr.nodes._common import _format_user_answers_block

    answers_block = _format_user_answers_block(state.get("user_answers"))

    # EMB-35: resumed session (re-review after the developer addressed
    # feedback, or re-entry with the user's answers) ⇒ delta prompt. The
    # response-shape contract is re-sent in full — it's cheap and guarantees
    # the output stays parseable.
    if session_resumable(resume_session):
        schema_start = prompt.index("Return ONLY a JSON object:")
        if answers_block:
            intro = f"{answers_block}\n\nWith this information, finish your review of the current diff and decide.\n\n"
        else:
            intro = (
                "The developer pushed a new revision addressing your review "
                "feedback. Re-run the checks (tests, lint, typecheck), re-review "
                "the updated diff, and decide again.\n\n"
            )
        prompt = intro + prompt[schema_start:]
    elif answers_block:
        schema_start = prompt.index("Return ONLY a JSON object:")
        prompt = prompt[:schema_start] + f"{answers_block}\n\n" + prompt[schema_start:]

    # RAV-602: definition from the DB row + per-step template overrides;
    # triage's pipeline_config still wins on top.
    from embry0.orchestration.nodes.agent import load_agent_definition
    from embry0.workflows.issue_to_pr.route_plan import step_template_config

    review_definition = await load_agent_definition(
        config,
        "review",
        {
            "model": "claude-sonnet-5",
            "tools": ["Read", "Bash", "Glob", "Grep"],
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
    )
    result = await _nodes.run_agent_node(
        state=state,
        agent_runner=agent_runner,
        agent_type="review",
        prompt=prompt,
        agent_definition=review_definition,
        template_config=step_template_config(state, "review"),
        timeout_seconds=REVIEW_TIMEOUT_SECONDS,
        on_event=_forward_event,
        credentials=credentials,
        config=config,
        resume_session=resume_session,
    )

    # RAV-1260: decide-now cap recovery. A reviewer that keeps investigating
    # (full suites, strict mypy on unrelated files, e2e reproductions) dies
    # at the cap mid-narration with no verdict (job-54a05ee073d6) — a guided
    # resume produced a thorough approve in one pass, so automate exactly
    # that: resume the SAME session once with a verdict-now nudge; only a
    # second failure reaches max_retries.
    recovery_session = _max_turns_recovery_session(
        result,
        agent_type="review",
        job_id=state.get("job_id"),
        credentials=credentials,
    )
    if recovery_session is not None:
        logger.warning("review_max_turns_verdict_now_resume", job_id=state.get("job_id"))
        writer(
            events.progress(
                message="Review hit its turn cap — resuming once for a verdict from checks already run",
                node="review",
            )
        )
        first_outputs = list(result.get("agent_outputs") or [])
        retry_state = {**state, "total_cost_usd": result.get("total_cost_usd", state.get("total_cost_usd", 0.0))}
        result = await _nodes.run_agent_node(
            state=retry_state,
            agent_runner=agent_runner,
            agent_type="review",
            prompt=(
                "Do not investigate further — no more test runs, installs, or "
                "file exploration. Based on what you already verified, emit your "
                "review verdict now as ONLY the JSON object specified earlier "
                '(decision "approved" or "changes_requested"). If you did not run '
                'the tests, set tests.status to "skipped" and tests.reason to '
                '"not_run", and do not approve.'
            ),
            agent_definition=review_definition,
            template_config=step_template_config(state, "review"),
            max_turns=DECIDE_NOW_MAX_TURNS,
            timeout_seconds=REVIEW_TIMEOUT_SECONDS,
            on_event=_forward_event,
            credentials=credentials,
            config=config,
            resume_session=recovery_session,
        )
        result["agent_outputs"] = first_outputs + list(result.get("agent_outputs") or [])
        resume_session = recovery_session

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
                writer(events.validation_result(validation=data.get("validation", {})))
                # RAV-1079: duplicates_open_pr rides the decision event so the
                # trace/dashboard show the overlap verdict (informational —
                # it does not change the approved/changes_requested routing).
                _review_dup = data.get("duplicates_open_pr")
                if isinstance(_review_dup, dict) and _review_dup.get("number") is not None:
                    logger.info(
                        "review_flagged_duplicate_pr",
                        job_id=state.get("job_id"),
                        pr_number=_review_dup.get("number"),
                    )
                writer(
                    events.review_decision(
                        decision=data.get("decision"),
                        summary=data.get("summary"),
                        duplicates_open_pr=_review_dup if isinstance(_review_dup, dict) else None,
                    )
                )
            except (json_mod.JSONDecodeError, TypeError):
                # #356: a bare json.loads was the ONLY thing that emitted a
                # verdict, so an unreadable review used to log nothing at all
                # while the router had already approved it. Say so — the
                # router's own reading is authoritative (it also accepts a
                # fenced block), so only report "no verdict" when it agrees.
                from embry0.orchestration.routing.conditions import parse_review_decision as _parse

                _fallback = _parse(output_text)
                writer(
                    events.review_decision(
                        decision=_fallback,
                        summary=None if _fallback else "Review emitted no readable verdict",
                    )
                )
                if _fallback is None:
                    logger.warning(
                        "review_verdict_unparseable",
                        job_id=state.get("job_id"),
                        output_chars=len(output_text or ""),
                        output_head=(output_text or "")[:200],
                    )

    writer(events.node_completed(node="review", agent="review", attempt=attempt))

    updates: dict[str, Any] = {**result, "current_stage": "review_complete"}
    # RAV-1271: the resumed answers were consumed into this run's prompt —
    # clear the one-shot key so later re-entries don't re-send stale answers.
    if answers_block:
        updates["user_answers"] = None
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
    #      graph.py then dispatches to init_orchestrator / retry / END.
    if updates.get("agent_questions_exhausted") or state.get("agent_questions_exhausted"):
        return Command(goto="max_retries", update=updates)
    if updates.get("pending_agent_questions") or state.get("pending_agent_questions"):
        return Command(goto="ask_user_interrupt", update=updates)

    from embry0.orchestration.routing.conditions import (
        check_review_decision,
        parse_review_decision,
        tests_block_reason,
    )

    merged_outputs = list(state.get("agent_outputs", []) or []) + list(result.get("agent_outputs", []) or [])
    merged_state = {**state, "agent_outputs": merged_outputs}
    decision = check_review_decision(merged_state)

    if decision == "max_retries":
        # The stage marker makes max_retries_node's message honest (RAV-1152).
        # RAV-1260: an ERRORED review (turn cap, crash — no verdict at all)
        # is not "requested changes with the retry budget exhausted"; give it
        # its own stage so the interrupt says what actually happened.
        review_outputs = [o for o in merged_outputs if o.get("agent_type") == "review"]
        latest_review_output = review_outputs[-1] if review_outputs else None
        if latest_review_output is not None and latest_review_output.get("is_error"):
            updates["current_stage"] = "review_errored"
        elif latest_review_output is not None and parse_review_decision(latest_review_output.get("output", "")) is None:
            # #356: the reviewer ran, did not error, and emitted no readable
            # verdict. Third distinct reason to pause — it is neither an errored
            # review nor an exhausted retry budget, and calling it either would
            # rejoin the RAV-1152 family of dishonest messages.
            updates["current_stage"] = "review_unparseable"
        elif (
            latest_review_output is not None
            and tests_block_reason(latest_review_output.get("output", "")) == "toolchain_missing"
        ):
            # The compiler or test runner is not in this sandbox. A developer
            # retry cannot install it, so this is not an exhausted review loop.
            updates["current_stage"] = "review_toolchain_missing"
        else:
            updates["current_stage"] = "review_retries_exhausted"
        return Command(goto="max_retries", update=updates)

    if decision == "approved":
        updates["current_stage"] = "review_passed"
        # RAV-601: the reviewer step is done — advance the route cursor so
        # route_after_review's next_route() dispatch reads the post-review
        # position. review_failed keeps the cursor (internal retry loop,
        # not a template edge).
        if state.get("route_plan"):
            from embry0.workflows.issue_to_pr.route_plan import advance

            updates.update(advance(state))
    else:
        # decision == "changes_requested" (or anything else that maps to retry)
        updates["current_stage"] = "review_failed"

    # RAV-1152: review self-routes via Command EXCLUSIVELY — its conditional
    # edge is removed from the graph. Mixing Command returns with conditional
    # edges makes LangGraph activate BOTH targets in one superstep: this
    # node's max_retries Command plus the edge's re-dispatch of `review` was
    # the observed fan-out (and, before #95's reducers, the
    # INVALID_CONCURRENT_GRAPH_UPDATE crash class). The router still owns
    # the decision; the node owns the single dispatch.
    from embry0.workflows.issue_to_pr.nodes._verify import goto_after_review, should_verify

    # #298/#299: an APPROVED change goes through the deterministic verify
    # step first; verify_node then makes the same route-plan dispatch.
    if updates["current_stage"] == "review_passed" and should_verify(state, configurable.get("embry0_config")):
        return Command(goto="verify", update=updates)
    return Command(goto=goto_after_review({**state, **updates}), update=updates)
