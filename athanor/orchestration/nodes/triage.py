"""Triage node — LLM-based pipeline configuration.

Analyzes the issue/task and determines:
- proceed: configure pipeline and execute
- needs_info: pause and request more information
- split: break into sub-tasks
"""

import json
from typing import Any, cast

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

Budget guidelines (CRITICAL — wrong budget hard-stops the workflow):
- `pipeline_config.budget_usd` is a HARD CAP. The orchestrator routes to
  max_retries the moment cumulative agent spend exceeds it. Set it ABOVE
  realistic per-run cost.
- Floor of 5.0 USD for any "proceed" action. A single Sonnet developer call
  on a small change typically costs $0.20–$2.00; review another $0.10–$0.50;
  QA (when needs_qa=true) another $0.50–$2.00. Anything below $5.00 will
  almost certainly trip the cap.
- Use 10.0–25.0 for standard pipelines, 25.0–50.0 for complex/multi-file work.
- NEVER set `reviewer_enabled: false` — the workflow always routes through
  review on the happy path, and disabling it sends the workflow to a dead
  end (no review → no edge to take).

## QA Decision

After you decide whether to proceed with developer work, also decide whether
the resulting PR should be validated by the QA agent.

Inputs available:
- The diff of the developer's intended (or actual, in the failure-routing case)
  changes.
- The repo's .athanor/qa.yaml if it exists, including its qa_required flag
  ("auto", "always", or "never").
- An optional acceptance_criteria_template from qa.yaml.

Decision rules:
- qa_required="always"  -> needs_qa = True, regardless of diff.
- qa_required="never"   -> needs_qa = False.
- qa_required="auto"    -> apply heuristics:
    - Frontend file changed (.tsx, .jsx, .vue, .svelte, .css, .scss, .html) -> True.
    - Backend route/controller/handler changed (e.g., paths matching
      *Controller.java, *handler.go, routes/*.py, api/*.ts) -> True.
    - Pure docs change (only .md, .rst, LICENSE, README.*) -> False.
    - Pure dependency bump (only package*.json, pyproject.toml, requirements*.txt,
      pom.xml, build.gradle) -> False.
    - Pure test change (only files under tests/, __tests__/, *_test.py) -> False.
    - Anything else -> default to True (better to run QA than skip silently).

Embed the decision in your JSON output as a `set_qa_decision` field:
  "set_qa_decision": {
    "needs_qa": bool,
    "reason": "<1-2 sentences explaining why>",
    "acceptance_criteria": ["<criterion>", ...]   // only when needs_qa=True;
                                                  // empty list means use qa.yaml.acceptance_criteria_template
  }
Omit the field entirely if the QA decision doesn't apply to this job (e.g.
needs_info / split actions, where developer work hasn't been scoped yet).

## QA Failure Handling

You may also be re-invoked after the QA agent failed. When that happens,
state.qa.attempts[-1].result_summary contains:
  - boot/seed/e2e results
  - per-criterion acceptance results (passed/failed/inconclusive)
  - anomalies (console errors, network failures, crashes)
  - log_artifact_url, screenshot evidence paths

You also have:
  - The original issue and the developer's diff
  - state.qa.failure_rounds: how many round trips have happened so far
    (max is state.qa.max_qa_failure_rounds, default 2)

Choose ONE action and embed it under a single `qa_failure_action` field
on your JSON output. Each `kind` carries its own action-specific fields:

  "qa_failure_action": {
    "kind": "retry_developer",
    "prompt": "<at least 10 chars; what specifically failed (criterion +
               evidence) and what the developer should fix>",
    "focus_files": ["<path>", ...]   // optional; files to focus on
  }
  Use when the QA failure clearly indicates a code defect we can describe.
  prompt MUST include:
    - what specifically failed (criterion + evidence)
    - what the developer should fix
    - which files to focus on (focus_files)
  Example: "QA failed: 'portfolio renders' returned 500 from /api/v1/portfolio
  with TypeError 'symbol' undefined. Investigate gateway/PortfolioController.java
  and frontend/portfolio.tsx."

  "qa_failure_action": {
    "kind": "rerun_qa",
    "reason": "<why you think this was environmental/flaky>"
  }
  Use when the failure looks environmental/flaky:
    - boot timed out and the prior attempt had partial success
    - a single criterion failed with a screenshot diff that's clearly cosmetic
    - DinD or network blips
  Don't use this just because you don't know what went wrong — that's ask_user.

  "qa_failure_action": {
    "kind": "ask_user",
    "question": "<at least 10 chars; what you need to know>"
  }
  Use when you can't make a confident judgment:
    - acceptance criteria conflict ("X should be visible" but the change is hiding X)
    - the failure is ambiguous and you'd be guessing
    - failure_rounds is at the cap; you must end with a question rather than retry
  The user's response will appear on the next triage invocation.

You MUST emit exactly one of these actions when re-invoked after a QA
failure. Failing to do so ends the job with ERR_QA_FAILURES_UNRESOLVED.
Omit `qa_failure_action` entirely on the initial invocation (when QA
hasn't run yet).

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
    # cast() avoids the TypedDict(**dict[str, Any]) unsafe-expansion error;
    # model_validate guarantees the dict has the correct shape.
    return cast(TriageDecision, model.model_dump())


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
