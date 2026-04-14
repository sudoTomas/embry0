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
from langgraph.types import interrupt

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
        env: dict[str, str] = {}
        if git_proxy_url:
            env["LEGION_GIT_PROXY_URL"] = git_proxy_url
        container_id = await sandbox_mgr.create(job_id, env=env)
        logger.info("sandbox_created", job_id=job_id, container_id=container_id)
        writer({"type": "progress", "message": "Sandbox container created"})

        # Clone repo inside container. Git auth flows via credential proxy — the
        # helper curls $LEGION_GIT_PROXY_URL/git-credentials which returns the
        # orchestrator's token without the token ever being visible to agent code.
        if repo and docker:
            if git_proxy_url:
                from legion.sandbox.github.git_ops import build_sandbox_credential_config_cmd

                cred_cmd = build_sandbox_credential_config_cmd(git_proxy_url)
                setup_cmd = [
                    "bash",
                    "-c",
                    f"{cred_cmd} && "
                    'git config --global user.email "legion@alchymielabs.com" && '
                    'git config --global user.name "Legion Bot"',
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

    from legion.orchestration.nodes.triage import run_triage_node

    writer = get_stream_writer()
    writer({"type": "node_started", "node": "triage", "agent": "triage"})

    model = os.environ.get("DEFAULT_MODEL", "claude-sonnet-4-6")
    agent_runner = config["configurable"].get("agent_runner")
    container_id = state.get("sandbox_container_id")

    if agent_runner and container_id:
        # Run triage inside sandbox via AgentRunner
        from legion.orchestration.nodes.agent import run_agent_node
        from legion.orchestration.nodes.triage import _TRIAGE_SYSTEM_PROMPT, parse_triage_response

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
        )

        # Parse triage decision from agent output
        if result.get("agent_outputs"):
            last = result["agent_outputs"][-1]
            if not last.get("is_error"):
                from legion.orchestration.state import TriageParseError
                from legion.safety.error_codes import ErrorCode

                try:
                    decision = parse_triage_response(last.get("output", ""))
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
        result = await run_triage_node(state, model=model)

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
            )
            if result.get("agent_outputs"):
                last = result["agent_outputs"][-1]
                if not last.get("is_error"):
                    from legion.orchestration.state import TriageParseError
                    from legion.safety.error_codes import ErrorCode

                    try:
                        decision = parse_triage_response(last.get("output", ""))
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
            result = await run_triage_node(updated, model=model)

    writer({"type": "node_completed", "node": "triage", "action": result.get("pipeline_config", {}).get("action")})
    return result


async def developer_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Run developer agent — write code, create branch, commit, push, open PR.

    Uses AgentRunner.run() to execute a full Claude Code session in the sandbox.
    """
    from legion.orchestration.nodes.agent import run_agent_node

    writer = get_stream_writer()
    writer({"type": "node_started", "node": "developer", "agent": "developer"})

    agent_runner = config["configurable"].get("agent_runner")
    if not agent_runner:
        logger.error("developer_node_no_runner")
        return {"current_stage": "developer_complete", "errors": ["No agent runner available"]}

    # Build the developer prompt
    repo = state.get("repo", "")
    task = state.get("task", "")
    issue_number = state.get("issue_number")
    issue_id = state.get("issue_id", "")
    triage_decision = state.get("pipeline_config", {})
    additional_context = state.get("additional_context", "")
    feedback_context = state.get("feedback_context", "")

    prompt_parts = [f"Repository: {repo}"]
    if issue_number:
        prompt_parts.append(f"GitHub Issue: #{issue_number}")
    prompt_parts.append(f"\nTask:\n{task}")

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
    branch_name = f"legion/{short_id}-{slug}"

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

    def _forward_event(event: dict) -> None:
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
    )

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

    return {
        **result,
        "pr_url": pr_url,
        "branch_name": branch,
        "current_stage": "developer_complete",
    }


async def review_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Run review agent — tests, lint, typecheck, code review.

    Returns structured JSON with decision, validation results, and comments.
    """
    from legion.orchestration.nodes.agent import run_agent_node

    writer = get_stream_writer()
    writer({"type": "node_started", "node": "review", "agent": "review"})

    agent_runner = config["configurable"].get("agent_runner")
    if not agent_runner:
        logger.error("review_node_no_runner")
        return {"current_stage": "review_complete", "errors": ["No agent runner available"]}

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
    return {**result, "current_stage": "review_complete"}


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
    """Max retries reached — pause pipeline and ask user what to do."""
    writer = get_stream_writer()
    retry_count = state.get("retry_count", 0)
    pr_url = state.get("pr_url", "")

    summary = {
        "message": f"Review failed after {retry_count} attempts",
        "reason": "max_retries",
        "pr_url": pr_url,
        "retry_count": retry_count,
        "options": ["continue_retrying", "merge_as_is", "abandon"],
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

    if choice == "continue_retrying":
        updates: dict[str, Any] = {"retry_count": 0, "current_stage": "developer_retry"}
        if guidance:
            existing = state.get("additional_context", "") or ""
            updates["additional_context"] = existing + f"\n\nUser guidance:\n{guidance}"
        return updates
    elif choice == "merge_as_is":
        return {"current_stage": "completed"}
    else:
        return {"current_stage": "abandoned", "errors": [f"Abandoned after {retry_count} retries"]}
