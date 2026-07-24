"""Generic agent node — executes non-code route-plan steps (RAV-604).

``research``/``analysis``/``ops`` template steps all map to this one
physical node. Unlike developer/review — whose prompts, output parsing,
and retry loops are node code — a generic step's behavior comes entirely
from its DB ``agent_definitions`` row (system prompt + toolset), so one
node serves every non-code agent type: it reads the CURRENT route step to
learn which agent to run, runs it single-pass, and advances the cursor.

Deliberately minimal control flow (v1): no ask-user interrupt (the
interrupt loop re-enters at the developer node by design; the non-code
system prompts direct agents to state assumptions instead of asking), no
retry loop, no session resume. An agent error fails the job — the
executor's final-status pass reads the appended ``errors``.
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from embry0.orchestration.nodes.agent import load_agent_definition, run_agent_node
from embry0.workflows.issue_to_pr.route_plan import (
    _step_target,
    advance,
    next_route,
    step_template_config,
)

logger = structlog.get_logger(__name__)


def _build_generic_prompt(state: dict[str, Any], agent_type: str) -> str:
    """Task prompt for a generic agent step.

    Role instructions live in the agent's system prompt (its DB row); this
    is only the job-specific context: task, prior context, and triage's
    read of the issue.
    """
    parts: list[str] = []
    repo = state.get("repo", "")
    if repo:
        parts.append(f"Repository: {repo}")
    issue_number = state.get("issue_number")
    if issue_number:
        parts.append(f"GitHub Issue: #{issue_number}")
    parts.append(f"\nTask:\n{state.get('task', '')}")
    additional_context = state.get("additional_context", "")
    if additional_context:
        parts.append(f"\nAdditional Context:\n{additional_context}")
    reasoning = (state.get("triage_decision") or {}).get("reasoning", "")
    if reasoning:
        parts.append(f"\nTriage Analysis:\n{reasoning}")
    parts.append(
        "\nThe job's source material (if any) is staged in /workspace. Your final message is the job's deliverable."
    )
    return "\n".join(parts)


async def generic_agent_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Run the agent named by the current route step and advance the cursor.

    Success advances ``route_cursor`` past the step (so the
    ``route_after_generic_agent`` conditional edge dispatches the next
    step); failure keeps the cursor and sets the ``*_failed`` stage the
    routing function terminates on.
    """
    plan = state.get("route_plan") or []
    cursor = int(state.get("route_cursor", 0) or 0)
    step = plan[cursor] if 0 <= cursor < len(plan) else None
    if step is None or _step_target(step) != "agent":
        # A dispatch bug, not an agent failure — next_route only routes here
        # for research/analysis/ops steps. Fail loudly rather than running
        # the wrong agent.
        logger.error(
            "generic_agent_step_mismatch",
            job_id=state.get("job_id"),
            cursor=cursor,
            step=step,
        )
        return {
            "current_stage": "generic_agent_failed",
            "errors": [f"generic_agent: route cursor {cursor} does not point at a generic step"],
        }

    agent_type = str(step["agent_type"])
    writer = get_stream_writer()
    writer({"type": "node_started", "node": "generic_agent", "agent": agent_type})

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    agent_runner = configurable.get("agent_runner")
    credentials = configurable.get("credentials") or {}

    # BUILTIN_SEED carries the canonical definition as the no-DB fallback
    # (tests / minimal setups) — the DB row wins when present. Imported
    # lazily like the other nodes' repository imports.
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    definition = await load_agent_definition(config, agent_type, dict(BUILTIN_SEED[agent_type]))

    result = await run_agent_node(
        state=state,
        agent_runner=agent_runner,
        agent_type=agent_type,
        prompt=_build_generic_prompt(state, agent_type),
        agent_definition=definition,
        template_config=step_template_config(state, agent_type),
        timeout_seconds=600,
        on_event=writer,
        credentials=credentials,
        config=config,
    )

    writer({"type": "node_completed", "node": "generic_agent", "agent": agent_type})

    updates: dict[str, Any] = {**result}
    if updates.get("current_stage") == f"{agent_type}_complete":
        # Step done — advance so route_after_generic_agent reads the
        # post-step position. Failures keep the cursor (and the *_failed
        # stage run_agent_node already set).
        updates.update(advance(state))
    return updates


def route_after_generic_agent(state: dict[str, Any]) -> str:
    """Next route key after a generic step: terminal on failure, else the plan."""
    if str(state.get("current_stage", "")).endswith("_failed"):
        return "end"
    return next_route(state)
