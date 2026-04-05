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
    proxy_mgr = config["configurable"].get("proxy_manager")
    docker = config["configurable"].get("docker")

    container_id = None
    if sandbox_mgr:
        env = {}
        if proxy_mgr:
            if proxy_mgr.auth_proxy_url:
                env["ANTHROPIC_BASE_URL"] = proxy_mgr.auth_proxy_url
            if proxy_mgr.git_proxy_url:
                env["GIT_PROXY_URL"] = proxy_mgr.git_proxy_url
        try:
            container_id = await sandbox_mgr.create(job_id, env=env)
            logger.info("sandbox_created", job_id=job_id, container_id=container_id)
            writer({"type": "progress", "message": "Sandbox container created"})

            # Clone repo inside container
            if repo and docker and proxy_mgr and proxy_mgr.git_proxy_url:
                clone_cmd = [
                    "bash",
                    "-c",
                    f'GIT_PROXY_URL="{proxy_mgr.git_proxy_url}" '
                    f"git clone --depth=1 https://github.com/{repo}.git /workspace 2>&1 || "
                    f'echo "Clone failed"',
                ]
                await docker.run_cmd(
                    docker.build_exec_cmd(container_id, clone_cmd),
                    timeout=120,
                )
                writer({"type": "progress", "message": f"Repository {repo} cloned"})
                logger.info("repo_cloned", job_id=job_id, repo=repo)
        except Exception as exc:
            logger.warning("sandbox_init_failed", job_id=job_id, error=str(exc))
            writer({"type": "error", "message": f"Sandbox init failed: {exc}"})

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
        from legion.orchestration.nodes.triage import parse_triage_response

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

        result = await run_agent_node(
            state=state,
            agent_runner=agent_runner,
            agent_type="triage",
            prompt=prompt,
            model=model,
            tools=["Read", "Glob", "Grep"],
            timeout_seconds=180,
        )

        # Parse triage decision from agent output
        if result.get("agent_outputs"):
            last = result["agent_outputs"][-1]
            if not last.get("is_error"):
                decision = parse_triage_response(last.get("output", ""))
                result["pipeline_config"] = decision
                result["current_stage"] = "triage_complete"
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

        answers = interrupt({
            "questions": questions,
            "reasoning": reasoning,
            "asking_node": "triage",
            "issue_id": state.get("issue_id"),
        })

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
                state=updated, agent_runner=agent_runner, agent_type="triage",
                prompt=prompt, model=model, tools=["Read", "Glob", "Grep"], timeout_seconds=180,
            )
            if result.get("agent_outputs"):
                last = result["agent_outputs"][-1]
                if not last.get("is_error"):
                    decision = parse_triage_response(last.get("output", ""))
                    result["pipeline_config"] = decision
                    result["current_stage"] = "triage_complete"
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

    result = await run_agent_node(
        state=state,
        agent_runner=agent_runner,
        agent_type="developer",
        prompt=prompt,
        model=triage_decision.get("pipeline_config", {}).get("agent_models", {}).get("developer", "claude-sonnet-4-6"),
        tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        timeout_seconds=600,
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

    result = await run_agent_node(
        state=state,
        agent_runner=agent_runner,
        agent_type="review",
        prompt=prompt,
        model=triage_decision.get("pipeline_config", {}).get("agent_models", {}).get("reviewer", "claude-sonnet-4-6"),
        tools=["Read", "Bash", "Glob", "Grep"],
        timeout_seconds=300,
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
    """Max retries reached — interrupt to ask user what to do."""
    writer = get_stream_writer()
    retry_count = state.get("retry_count", 0)
    pr_url = state.get("pr_url", "")

    summary = {
        "message": f"Review failed after {retry_count} attempts",
        "pr_url": pr_url,
        "retry_count": retry_count,
        "options": ["continue_retrying", "merge_as_is", "abandon"],
    }

    # Get the latest review feedback for context
    outputs = state.get("agent_outputs", [])
    review_outputs = [o for o in outputs if o.get("agent_type") == "review"]
    if review_outputs:
        summary["latest_review"] = review_outputs[-1].get("output", "")[:1000]

    writer({"type": "interrupt", "node": "max_retries", "summary": summary})
    logger.info("max_retries_reached", retry_count=retry_count)

    user_choice = interrupt(summary)

    if isinstance(user_choice, str):
        choice = user_choice
    elif isinstance(user_choice, dict):
        choice = user_choice.get("choice", "abandon")
    else:
        choice = "abandon"

    if choice == "continue_retrying":
        return {"retry_count": 0, "current_stage": "developer_retry"}
    elif choice == "merge_as_is":
        return {"current_stage": "completed"}
    else:  # abandon
        return {"current_stage": "abandoned", "errors": [f"Abandoned after {retry_count} retries"]}
