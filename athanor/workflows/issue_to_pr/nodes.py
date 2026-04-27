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

from athanor.orchestration.nodes.agent import run_agent_node

logger = structlog.get_logger(__name__)


async def init_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Create sandbox container and clone the repo."""
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
        # Defense-in-depth vs the API-layer RESERVED_ENV_KEYS check: a stored
        # row with a reserved key (from pre-fix data) must not escape to the
        # sandbox. Drop any user keys that collide.
        from athanor.api.schemas.environment import RESERVED_ENV_KEYS

        env: dict[str, str] = {}
        user_env = state.get("user_env_vars") or {}
        for k, v in user_env.items():
            if k in RESERVED_ENV_KEYS:
                logger.warning(
                    "user_env_var_reserved_key_dropped",
                    key=k,
                    job_id=job_id,
                    msg="Reserved key in user env var rejected at sandbox injection. Remove it via the environment API.",
                )
                continue
            env[k] = v
        if git_proxy_url:
            env["ATHANOR_GIT_PROXY_URL"] = git_proxy_url
        # Note: github_proxy_url is available on proxy_mgr but not yet consumed
        # by sandbox/github/client.py (it takes proxy_url directly as a constructor
        # arg; no env-var hook exists). Wiring it as a sandbox env var requires
        # adding a base-URL env hook inside the client. Tracked as a follow-up.
        container_id = await sandbox_mgr.create(job_id, env=env)
        logger.info("sandbox_created", job_id=job_id, container_id=container_id)
        writer({"type": "progress", "message": "Sandbox container created"})

        # Clone repo inside container. Git auth flows via credential proxy — the
        # helper curls $ATHANOR_GIT_PROXY_URL/git-credentials which returns the
        # orchestrator's token without the token ever being visible to agent code.
        if repo and docker:
            if git_proxy_url:
                from athanor.sandbox.github.git_ops import build_sandbox_credential_config_cmd

                cred_cmd = build_sandbox_credential_config_cmd(git_proxy_url)
                setup_cmd = [
                    "bash",
                    "-c",
                    f"{cred_cmd} && "
                    'git config --global user.email "[removed]" && '
                    'git config --global user.name "Athanor Bot"',
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
            # downstream agent call silently misbehaves. `set -e` surfaces a
            # non-zero exit from `git clone` which DockerClient.run_cmd then
            # raises as RuntimeError.
            clone_cmd = [
                "bash",
                "-c",
                f"set -e && git clone --depth=1 https://github.com/{repo}.git /workspace",
            ]
            try:
                await docker.run_cmd(
                    docker.build_exec_cmd(container_id, clone_cmd),
                    timeout=120,
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


async def triage_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Run triage agent — via AgentRunner in sandbox if available, else direct SDK."""
    import os

    from athanor.orchestration.nodes.triage import run_triage_node

    writer = get_stream_writer()
    writer({"type": "node_started", "node": "triage", "agent": "triage"})

    model = os.environ.get("DEFAULT_MODEL", "claude-sonnet-4-6")
    agent_runner = config["configurable"].get("agent_runner")
    credentials = config["configurable"].get("credentials") or {}
    container_id = state.get("sandbox_container_id")

    if agent_runner and container_id:
        # Run triage inside sandbox via AgentRunner
        from athanor.orchestration.nodes.triage import _TRIAGE_SYSTEM_PROMPT, parse_triage_response

        repo = state.get("repo", "")
        task = state.get("task", "")
        issue_number = state.get("issue_number")
        additional = state.get("additional_context", "")

        prompt = f"Repository: {repo}\n"
        if issue_number:
            prompt += f"Issue #{issue_number}\n"
        prompt += f"\nTask:\n{task}"
        if additional:
            prompt += f"\n\nPrevious Q&A:\n{additional}"

        # Prepend system prompt since sandbox runner doesn't receive it separately
        prompt = _TRIAGE_SYSTEM_PROMPT + "\n\n" + prompt

        def _forward_event(event: dict) -> None:
            writer(event)

        result = await run_agent_node(
            state=state,
            agent_runner=agent_runner,
            agent_type="triage",
            prompt=prompt,
            model=model,
            tools=["Read", "Glob", "Grep"],
            timeout_seconds=180,
            on_event=_forward_event,
            credentials=credentials,
        )

        # Parse triage decision from agent output
        if result.get("agent_outputs"):
            last = result["agent_outputs"][-1]
            if not last.get("is_error"):
                from athanor.orchestration.nodes.triage import apply_repo_preferences_override
                from athanor.orchestration.state import TriageParseError
                from athanor.safety.error_codes import ErrorCode

                try:
                    decision = parse_triage_response(last.get("output", ""))
                    decision = await apply_repo_preferences_override(
                        decision,
                        state.get("repo", ""),
                        config["configurable"].get("repo_preferences_repo"),
                    )
                    result["pipeline_config"] = decision
                    result["current_stage"] = "triage_complete"
                except TriageParseError as exc:
                    logger.error("triage_parse_error", error=str(exc))
                    writer({"type": "error", "message": f"Triage parse failed: {exc}"})
                    return {
                        "current_stage": "failed",
                        "errors": [f"triage_malformed: {exc}"],
                        "error_code": ErrorCode.TRIAGE_MALFORMED.value,
                    }
    else:
        # Fallback: run triage directly via Agent SDK (no sandbox)
        result = await run_triage_node(state, config=config, model=model)

    # Check needs_info → interrupt
    pipeline_config = result.get("pipeline_config", {})
    action = pipeline_config.get("action", "proceed")

    if action == "needs_info":
        questions = pipeline_config.get("questions", [])
        reasoning = pipeline_config.get("reasoning", "")
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

        # Resumed — add answers to context and re-run
        additional = state.get("additional_context", "") or ""
        if isinstance(answers, str):
            additional += f"\n\n{answers}"
        elif isinstance(answers, dict):
            for q, a in answers.items():
                additional += f"\n\nQ: {q}\nA: {a}"
        elif isinstance(answers, list):
            for item in answers:
                if isinstance(item, dict):
                    additional += f"\n\nQ: {item.get('question', '')}\nA: {item.get('answer', '')}"

        updated = {**state, "additional_context": additional}
        writer({"type": "progress", "message": "Re-running triage with answers"})

        if agent_runner and container_id:
            prompt += f"\n\nPrevious Q&A:\n{additional}"
            result = await run_agent_node(
                state=updated,
                agent_runner=agent_runner,
                agent_type="triage",
                prompt=prompt,
                model=model,
                tools=["Read", "Glob", "Grep"],
                timeout_seconds=180,
                on_event=_forward_event,
                credentials=credentials,
            )
            if result.get("agent_outputs"):
                last = result["agent_outputs"][-1]
                if not last.get("is_error"):
                    from athanor.orchestration.nodes.triage import apply_repo_preferences_override
                    from athanor.orchestration.state import TriageParseError
                    from athanor.safety.error_codes import ErrorCode

                    try:
                        decision = parse_triage_response(last.get("output", ""))
                        decision = await apply_repo_preferences_override(
                            decision,
                            updated.get("repo", ""),
                            config["configurable"].get("repo_preferences_repo"),
                        )
                        result["pipeline_config"] = decision
                        result["current_stage"] = "triage_complete"
                    except TriageParseError as exc:
                        logger.error("triage_parse_error", error=str(exc))
                        writer({"type": "error", "message": f"Triage parse failed: {exc}"})
                        return {
                            "current_stage": "failed",
                            "errors": [f"triage_malformed: {exc}"],
                            "error_code": ErrorCode.TRIAGE_MALFORMED.value,
                        }
        else:
            result = await run_triage_node(updated, config=config, model=model)

    writer({"type": "node_completed", "node": "triage", "action": result.get("pipeline_config", {}).get("action")})
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


def _extract_ask_user_events(agent_output: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull any agent_ask_user events out of the agent's streamed events.

    Returns a list of normalized question dicts: {question, category, options,
    asking_node, importance}. The ``question`` string is formatted with inline
    category/options markers so those context hints survive persistence
    through issue_inputs (which only has a plain ``question`` TEXT column).
    """
    events = agent_output.get("events", []) if isinstance(agent_output, dict) else []
    pending: list[dict[str, Any]] = []
    for e in events:
        if isinstance(e, dict) and e.get("type") == "agent_ask_user":
            raw_q = e.get("question", "")
            category = e.get("category", "general")
            options = e.get("options", []) or []
            pending.append(
                {
                    "question": _format_question_text(raw_q, category, options),
                    "category": category,
                    "options": options,
                    "asking_node": "developer",
                    "importance": "blocking",
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


async def developer_node(state: dict[str, Any], config: RunnableConfig) -> Command:
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

    # Build the developer prompt
    repo = state.get("repo", "")
    task = state.get("task", "")
    issue_number = state.get("issue_number")
    issue_id = state.get("issue_id", "")
    triage_decision = state.get("pipeline_config", {})
    additional_context = state.get("additional_context", "")
    feedback_context = state.get("feedback_context", "")
    user_answers = state.get("user_answers")

    prompt_parts = [f"Repository: {repo}"]
    if issue_number:
        prompt_parts.append(f"GitHub Issue: #{issue_number}")
    prompt_parts.append(f"\nTask:\n{task}")

    # If the user just answered prior agent questions, prepend the Q&A block so
    # the agent sees the answers in its freshly-built context.
    answers_block = _format_user_answers_block(user_answers)
    if answers_block:
        prompt_parts.append(f"\n{answers_block}")

    if additional_context:
        prompt_parts.append(f"\nAdditional Context:\n{additional_context}")

    if feedback_context:
        prompt_parts.append(f"\nReview Feedback (address these issues):\n{feedback_context}")

    reasoning = triage_decision.get("reasoning", "")
    if reasoning:
        prompt_parts.append(f"\nTriage Analysis:\n{reasoning}")

    # Branch naming
    short_id = issue_id[:12] if issue_id else "unknown"
    slug = task[:30].lower().replace(" ", "-").replace("/", "-")
    branch_name = f"athanor/{short_id}-{slug}"

    prompt_parts.append("\nInstructions:")
    prompt_parts.append(f"1. Create branch: {branch_name}")
    prompt_parts.append("2. Implement the changes")
    prompt_parts.append("3. Write or update tests")
    prompt_parts.append("4. Commit your changes")
    prompt_parts.append("5. Push the branch")
    prompt_parts.append(
        f"6. Create a PR with 'Closes #{issue_number}' in the body" if issue_number else "6. Create a PR"
    )
    prompt_parts.append("\nReturn a JSON object at the end with: pr_url, branch, summary, files_changed")

    prompt = "\n".join(prompt_parts)

    # Collect agent events locally so we can scan for agent_ask_user events
    # after the agent finishes. We still forward every event to the graph
    # stream writer for live streaming.
    collected_events: list[dict[str, Any]] = []

    def _forward_event(event: dict) -> None:
        collected_events.append(event)
        writer(event)

    result = await run_agent_node(
        state=state,
        agent_runner=agent_runner,
        agent_type="developer",
        prompt=prompt,
        model=triage_decision.get("pipeline_config", {}).get("agent_models", {}).get("developer", "claude-sonnet-4-6"),
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        timeout_seconds=1800,
        on_event=_forward_event,
        credentials=credentials,
    )

    # Scan the agent's event stream for `agent_ask_user` events. If present,
    # surface them to the graph so the router can divert to ask_user_interrupt.
    pending_questions = _extract_ask_user_events({"events": collected_events})

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
        # Cycle guard: cap agent-question rounds at 5 so a pathological agent
        # can't loop forever on human-in-the-loop. After the cap, treat it as
        # a workflow failure with a clear error.
        current_rounds = int(state.get("agent_question_rounds", 0) or 0)
        max_rounds = 5
        if current_rounds >= max_rounds:
            from athanor.safety.error_codes import ErrorCode

            logger.warning(
                "agent_question_rounds_exceeded",
                rounds=current_rounds,
                max_rounds=max_rounds,
                job_id=state.get("job_id"),
            )
            updates["current_stage"] = "failed"
            updates["agent_questions_exhausted"] = True
            updates["error_code"] = ErrorCode.MAX_AGENT_QUESTIONS.value
            updates["errors"] = [f"Agent exceeded {max_rounds} rounds of asking the user — giving up."]
        else:
            updates["pending_agent_questions"] = pending_questions
            updates["current_stage"] = "developer_asked_user"
            updates["agent_question_rounds"] = current_rounds + 1
            logger.info(
                "developer_asked_user",
                question_count=len(pending_questions),
                rounds=current_rounds + 1,
                job_id=state.get("job_id"),
            )

    # Clear consumed user answers so they don't bleed into future runs.
    if user_answers:
        updates["user_answers"] = None

    # Self-routing: decide the next node based on the updates we just produced
    # and the state we were handed. Priority:
    #   1. agent_questions_exhausted wins — terminal failure (max_retries).
    #   2. Pending agent questions divert to ask_user_interrupt.
    #   3. Budget overrun also diverts to max_retries.
    #   4. Otherwise proceed to review.
    pipeline = state.get("pipeline_config", {}) or {}
    if isinstance(pipeline, dict) and "pipeline_config" in pipeline:
        pipeline = pipeline.get("pipeline_config") or {}
    budget = pipeline.get("budget_usd", 10.0) if isinstance(pipeline, dict) else 10.0

    if updates.get("agent_questions_exhausted") or state.get("agent_questions_exhausted"):
        goto = "max_retries"
    elif updates.get("pending_agent_questions") or state.get("pending_agent_questions"):
        goto = "ask_user_interrupt"
    elif updates.get("total_cost_usd", state.get("total_cost_usd", 0.0)) > budget:
        goto = "max_retries"
    else:
        goto = "review"

    return Command(goto=goto, update=updates)


async def review_node(state: dict[str, Any], config: RunnableConfig) -> Command:
    """Run review agent — tests, lint, typecheck, code review.

    Returns structured JSON with decision, validation results, and comments,
    and self-routes via Command to approved (END), changes_requested (retry),
    or max_retries based on the review decision.
    """
    writer = get_stream_writer()
    writer({"type": "node_started", "node": "review", "agent": "review"})

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    agent_runner = configurable.get("agent_runner")
    credentials = configurable.get("credentials") or {}
    if not agent_runner:
        logger.error("review_node_no_runner")
        from athanor.safety.error_codes import ErrorCode

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
    triage_decision = state.get("pipeline_config", {})

    prompt = f"""Repository: {repo}
Branch: {branch}
PR: {pr_url or "N/A"}

You are reviewing code changes made by the developer agent. Your job:

1. Run the test suite (if tests exist): find and run pytest, npm test, or equivalent
2. Run linting (if configured): ruff, eslint, or equivalent
3. Run type checking (if configured): mypy, tsc, or equivalent
4. Review the git diff: `git diff main...HEAD`
5. Assess code quality, correctness, and completeness
6. Check if documentation needs updating: read README.md, CLAUDE.md, docs/architecture.md,
   and any other docs files. If the code changes affect documented features, APIs, project
   structure, configuration, or architecture, flag the specific docs that need updating.

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

    def _forward_event(event: dict) -> None:
        writer(event)

    result = await run_agent_node(
        state=state,
        agent_runner=agent_runner,
        agent_type="review",
        prompt=prompt,
        model=triage_decision.get("pipeline_config", {}).get("agent_models", {}).get("review", "claude-sonnet-4-6"),
        tools=["Read", "Bash", "Glob", "Grep"],
        timeout_seconds=300,
        on_event=_forward_event,
        credentials=credentials,
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

    # Self-routing: inline check_review_decision against the merged agent_outputs
    # (existing state plus whatever this review run appended). Approved → END,
    # changes_requested → retry (or max_retries if retry cap hit).
    from athanor.orchestration.routing.conditions import check_review_decision

    merged_outputs = list(state.get("agent_outputs", []) or []) + list(result.get("agent_outputs", []) or [])
    merged_state = {**state, "agent_outputs": merged_outputs}
    decision = check_review_decision(merged_state)

    if decision == "approved":
        goto: Any = END
    elif decision == "max_retries":
        goto = "max_retries"
    else:
        goto = "retry"

    return Command(goto=goto, update=updates)


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
