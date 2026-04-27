"""Triage node — LLM-based pipeline configuration.

Analyzes the issue/task and determines:
- proceed: configure pipeline and execute
- needs_info: pause and request more information
- split: break into sub-tasks
"""

import json
from typing import Any

import structlog

from athanor.orchestration.state import TriageDecision

logger = structlog.get_logger(__name__)

_TRIAGE_SYSTEM_PROMPT = """You are a triage agent for an autonomous coding system.
Analyze the given task and determine the optimal pipeline configuration.

Respond with a JSON object containing:
- action: "proceed" | "needs_info" | "split"
- confidence: 0.0-1.0 (how confident you are in the implementation approach)
- pipeline_template: "routine" | "standard" (when action=proceed)
- pipeline_config: object with sandbox_profile, agent_models, max_feedback_loops,
  reviewer_enabled, validator_modes, budget_usd
- questions: list of objects (when action=needs_info). Each object has:
  - question: the question text
  - importance: "blocking" (must wait for human answer) or "auto_answerable" (you can suggest an answer)
  - suggested_answer: your best-guess answer (only when importance=auto_answerable)
- sub_tasks: list of {task, description} objects (when action=split)
- reasoning: explanation of your decision

Guidelines:
- Use "routine" for simple, well-defined changes (typos, small fixes, config changes)
- Use "standard" for features, refactors, multi-file changes
- Set confidence < 0.5 and action="needs_info" when the task is ambiguous
- Set action="split" when the task involves multiple independent changes
- Always include reasoning

Model selection (CRITICAL):
- DEFAULT to "claude-sonnet-4-6" for the developer agent unless the task is genuinely trivial.
- Use "claude-opus-4-6" for complex tasks: parsers, algorithms, multi-file refactors, security-sensitive code, tasks requiring deep reasoning.
- ONLY use "claude-haiku-4-5" for trivial single-file changes like typos, log messages, simple config updates, or cosmetic CSS tweaks. NEVER use Haiku for new features, parsers, multi-file changes, or anything requiring careful design decisions.
- For the review agent, use "claude-sonnet-4-6" by default (Haiku is acceptable only for trivial changes).
- When in doubt, prefer the more capable model.

Set the chosen models in `pipeline_config.agent_models`:
  {"developer": "claude-sonnet-4-6", "review": "claude-sonnet-4-6"}

Respond ONLY with the JSON object, no markdown fences or extra text."""


def parse_triage_response(raw: str) -> TriageDecision:
    """Parse LLM response into a TriageDecision.

    Strict: raises TriageParseError on invalid JSON or schema mismatch.
    Callers must handle it (typically by failing the job with
    ErrorCode.TRIAGE_MALFORMED and logging the raw output).
    """
    from athanor.orchestration.state import TriageDecisionModel, TriageParseError

    try:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        data = json.loads(text)
    except (json.JSONDecodeError, IndexError) as exc:
        logger.warning("triage_parse_failed_json", raw=raw[:500])
        raise TriageParseError(f"Invalid JSON: {exc}") from exc

    try:
        model = TriageDecisionModel.model_validate(data)
    except Exception as exc:  # pydantic ValidationError subclass
        logger.warning("triage_parse_failed_schema", raw=raw[:500], error=str(exc))
        raise TriageParseError(f"Schema validation failed: {exc}") from exc

    # Return as TypedDict-shaped dict for downstream dict-based code.
    return TriageDecision(**model.model_dump())


async def apply_repo_preferences_override(
    decision: dict[str, Any],
    repo: str,
    prefs_repo: Any,
) -> dict[str, Any]:
    """If ``prefs_repo`` has a non-null ``sandbox_profile`` for ``repo``, override
    ``decision['pipeline_config']['sandbox_profile']`` in place.

    Safe against exceptions: on fetch failure, logs a warning and leaves the
    decision untouched.
    """
    if prefs_repo is None or not repo:
        return decision
    try:
        pref = await prefs_repo.get(repo)
    except Exception:
        logger.warning("repo_prefs_fetch_failed", repo=repo, exc_info=True)
        return decision
    if pref and pref.get("sandbox_profile"):
        pipeline_cfg = decision.get("pipeline_config") or {}
        if not isinstance(pipeline_cfg, dict):
            pipeline_cfg = {}
        pipeline_cfg["sandbox_profile"] = pref["sandbox_profile"]
        decision["pipeline_config"] = pipeline_cfg
        logger.info(
            "triage_sandbox_profile_overridden_by_prefs",
            repo=repo,
            profile=pref["sandbox_profile"],
        )
    return decision


async def run_triage_node(
    state: dict[str, Any],
    config: dict[str, Any] | None = None,
    model: str = "claude-sonnet-4-6",
    confidence_threshold: float = 0.5,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Execute the triage via Claude Agent SDK and return state updates.

    Uses the Claude CLI subprocess with stored OAuth credentials.
    No API key required.

    If the repo has a stored ``sandbox_profile`` preference
    (``repo_preferences`` table, injected via
    ``configurable.repo_preferences_repo``), that value overrides whatever the
    LLM picked for ``pipeline_config.sandbox_profile``.
    """
    from athanor.agents.sdk import run_agent

    repo = state.get("repo", "")
    task = state.get("task", "")
    issue_number = state.get("issue_number")

    user_prompt = f"Repository: {repo}\n"
    if issue_number:
        user_prompt += f"Issue #{issue_number}\n"
    user_prompt += f"\nTask:\n{task}"

    additional = state.get("additional_context", "")
    if additional:
        user_prompt += f"\n\nPrevious Q&A (answers from the user):\n{additional}"

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

    # Honour per-repo sandbox_profile preference if configured.
    # The repo_preferences_repo is plumbed through IssueExecutor._build_graph_config.
    prefs_repo = None
    if config and isinstance(config.get("configurable"), dict):
        prefs_repo = config["configurable"].get("repo_preferences_repo")
    decision = await apply_repo_preferences_override(decision, repo, prefs_repo)

    # Estimate cost from usage (Claude Sonnet ~$3/M input, $15/M output)
    triage_cost = 0.0
    if result.usage:
        input_tokens = result.usage.get("input_tokens", 0)
        output_tokens = result.usage.get("output_tokens", 0)
        triage_cost = (input_tokens * 3.0 / 1_000_000) + (output_tokens * 15.0 / 1_000_000)

    logger.info(
        "triage_complete",
        action=decision.get("action"),
        confidence=decision.get("confidence"),
        pipeline_template=decision.get("pipeline_template"),
        cost_usd=round(triage_cost, 4),
    )

    return {
        "pipeline_config": decision,
        "current_stage": "triage_complete",
        "total_cost_usd": state.get("total_cost_usd", 0.0) + triage_cost,
    }
