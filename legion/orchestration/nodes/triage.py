"""Triage node — LLM-based pipeline configuration.

Analyzes the issue/task and determines:
- proceed: configure pipeline and execute
- needs_info: pause and request more information
- split: break into sub-tasks
"""

import json
from typing import Any

import structlog

from legion.orchestration.state import PipelineConfig, TriageDecision

logger = structlog.get_logger(__name__)

_TRIAGE_SYSTEM_PROMPT = """You are a triage agent for an autonomous coding system.
Analyze the given task and determine the optimal pipeline configuration.

Respond with a JSON object containing:
- action: "proceed" | "needs_info" | "split"
- confidence: 0.0-1.0 (how confident you are in the implementation approach)
- pipeline_template: "routine" | "standard" (when action=proceed)
- pipeline_config: object with sandbox_profile, agent_models, max_feedback_loops,
  reviewer_enabled, validator_modes, budget_usd
- questions: list of strings (when action=needs_info)
- sub_tasks: list of {task, description} objects (when action=split)
- reasoning: explanation of your decision

Guidelines:
- Use "routine" for simple, well-defined changes (typos, small fixes, config changes)
- Use "standard" for features, refactors, multi-file changes
- Set confidence < 0.5 and action="needs_info" when the task is ambiguous
- Set action="split" when the task involves multiple independent changes
- Always include reasoning

Respond ONLY with the JSON object, no markdown fences or extra text."""


def parse_triage_response(raw: str) -> TriageDecision:
    """Parse LLM response into a TriageDecision. Falls back to safe defaults."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        data = json.loads(text)
    except (json.JSONDecodeError, IndexError):
        logger.warning("triage_parse_failed", raw=raw[:200])
        return TriageDecision(
            action="proceed",
            confidence=0.0,
            pipeline_template="standard",
            pipeline_config=PipelineConfig(
                sandbox_profile="default",
                agent_models={"developer": "claude-sonnet-4-6"},
                budget_usd=10.0,
                max_feedback_loops=2,
                reviewer_enabled=True,
                validator_modes=["test", "lint", "typecheck"],
            ),
            reasoning="Fallback: could not parse triage response.",
        )

    return TriageDecision(
        action=data.get("action", "proceed"),
        confidence=data.get("confidence", 0.5),
        pipeline_template=data.get("pipeline_template", "standard"),
        pipeline_config=data.get("pipeline_config", {}),
        questions=data.get("questions", []),
        sub_tasks=data.get("sub_tasks", []),
        reasoning=data.get("reasoning", ""),
    )


async def run_triage_node(
    state: dict[str, Any],
    model: str = "claude-sonnet-4-6",
    confidence_threshold: float = 0.5,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Execute the triage via Claude Agent SDK and return state updates.

    Uses the Claude CLI subprocess with stored OAuth credentials.
    No API key required.
    """
    from legion.agents.sdk import run_agent

    repo = state.get("repo", "")
    task = state.get("task", "")
    issue_number = state.get("issue_number")

    user_prompt = f"Repository: {repo}\n"
    if issue_number:
        user_prompt += f"Issue #{issue_number}\n"
    user_prompt += f"\nTask:\n{task}"

    try:
        result = await run_agent(
            prompt=user_prompt,
            agent_name="triage",
            model=model,
            system_prompt=_TRIAGE_SYSTEM_PROMPT,
            timeout_seconds=120,
        )

        if not result.success or not result.raw_output:
            logger.error("triage_llm_failed", error=result.error)
            return {
                "current_stage": "triage_failed",
                "errors": [f"Triage failed: {result.error}"],
            }

        decision = parse_triage_response(result.raw_output)

        if decision.get("confidence", 0.0) < confidence_threshold and decision.get("action") == "proceed":
            orig_confidence = decision.get("confidence", 0.0)
            orig_reasoning = decision.get("reasoning", "")
            decision = TriageDecision(
                action="needs_info",
                confidence=orig_confidence,
                questions=[
                    "Low confidence in implementation approach. "
                    "Please provide more details about the expected behavior."
                ],
                reasoning=(
                    f"Confidence {orig_confidence} below threshold {confidence_threshold}. "
                    f"Original reasoning: {orig_reasoning}"
                ),
            )

    except Exception as exc:
        logger.error("triage_llm_failed", error=str(exc))
        return {
            "current_stage": "triage_failed",
            "errors": [f"Triage failed: {exc}"],
        }

    logger.info(
        "triage_complete",
        action=decision.get("action"),
        confidence=decision.get("confidence"),
        pipeline_template=decision.get("pipeline_template"),
    )

    return {
        "pipeline_config": decision,
        "current_stage": "triage_complete",
    }
