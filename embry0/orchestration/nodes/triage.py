"""Triage node — LLM-based pipeline configuration.

Analyzes the issue/task and determines:
- proceed: configure pipeline and execute
- needs_info: pause and request more information
- split: break into sub-tasks
"""

import json
import re
from collections.abc import Mapping
from typing import Any, cast

import structlog

from embry0.agents.model_policy import TIER_KEYS
from embry0.orchestration.state import TriageDecision
from embry0.services.slop import CLASSIFIER_GUIDE

logger = structlog.get_logger(__name__)

# Where the model guidance goes in the template below. A plain marker, not a
# str.format field: the prompt is full of literal JSON braces.
_MODEL_GUIDANCE_SLOT = "<<MODEL_GUIDANCE>>"

# The triage instructions, minus the model guidance. That section names the
# deployment's models, so it is built from configuration per job
# (``build_triage_system_prompt``) — never written here as literal model ids,
# which pinned every triaged job to them whatever the operator configured
# (#465).
_TRIAGE_SYSTEM_PROMPT_TEMPLATE = """You are a triage agent for an autonomous coding system.
Analyze the given task and determine the optimal pipeline configuration.

Respond with a JSON object containing:
- action: "proceed" | "needs_info" | "split"
- confidence: 0.0-1.0 (how confident you are in the implementation approach)
- job_kind: "code" | "research" | "analysis" | "ops" (when action=proceed).
  code = changes to a repository; research = information gathering/synthesis;
  analysis = examining material provided in the workspace; ops = operational /
  environment tasks. Default to "code" when the task modifies a repo.
- pipeline_template: optional; the NAME of one of the available pipeline
  templates listed in the task context. Omit (or leave empty) to use the
  default route for the job_kind.
- pipeline_config: object with agent_models (see "Model selection" below),
  max_feedback_loops, reviewer_enabled, validator_modes, budget_usd.
  Do NOT set sandbox_profile. The sandbox is created from the repository
  preference before you run, and there is no profile named "default".
  Omit the field.
- questions: list of objects (when action=needs_info). Each object has:
  - question: the question text
  - importance: "blocking" (must wait for human answer) or "auto_answerable" (you can suggest an answer)
  - suggested_answer: your best-guess answer (only when importance=auto_answerable)
- sub_tasks: list of {task, description} objects (when action=split)
- reasoning: explanation of your decision
- work_nature: the slopometer's verdict (when job_kind="code"; see "Bug or
  slop" below): {"nature": "bug"|"slop"|"noise", "slop_score": 0-100,
  "kinds": [...], "evidence": [{"path": "...", "why": "..."}]}

Guidelines:
- Pick a lighter template (e.g. quick-fix) for simple, well-defined changes
  (typos, small fixes, config changes); the kind default covers everything else
- Set confidence < 0.5 and action="needs_info" when the task is ambiguous
- Set action="split" when the task involves multiple independent changes
- Always include reasoning

## Bug or slop

Before you route a code job, read the code the task points at and judge its
nature. A developer handed slop as a bug patches the symptom and adds more
slop, so this verdict decides who works the job and how they are briefed.
<<SLOP_GUIDE>>
Fill `work_nature` for every code job, with at most 8 evidence entries naming
files you read. When the task carries a watcher "Slop, not a bug" section,
weigh it, then make your own call. A slop verdict routes the job to the
Slopmaster's cleanup (keep behavior, remove or merge code) whatever template
you name; leave pipeline_template empty for it.

<<MODEL_GUIDANCE>>

Turn budgets (optional): `pipeline_config.agent_max_turns` overrides the
per-agent turn caps (defaults: triage 15, developer 40, review 25, qa 40).
Raise the developer's only for genuinely large multi-file work, e.g.
  {"developer": 60}

Skill selection for the developer (CRITICAL):
Decide whether the issue needs an upfront DESIGN pass or can jump straight to
implementation, then attach exactly ONE of these two skills to the developer:

- `superpowers:brainstorming` — use when the issue is OPEN-ENDED or has
  meaningful design choices: new features, multi-file refactors, anything
  with a "should we…" or "how should this work" component. The skill walks
  the developer through clarifying questions → approach options →
  agreed-on design → spec doc → then writing-plans → then implementation.

- `superpowers:writing-plans` — use when the issue is a CLEAR
  implementation task with no real design decisions: bug fixes with a
  diagnosed root cause, well-scoped enhancements, asset additions, config
  edits, dependency bumps, single-file changes you can describe in one
  sentence. The skill goes straight to writing a step-by-step
  implementation plan, then to executing it.

Set the chosen skill as an ADDITION in `pipeline_config.agent_skills` (the
developer keeps its configured skills; you add one):
  {"developer": {"add": ["superpowers:brainstorming"]}}     # or
  {"developer": {"add": ["superpowers:writing-plans"]}}

Defaults if you can't decide: prefer brainstorming. Better to spend a few
minutes designing than to ship the wrong thing.

Budget guidelines (CRITICAL — wrong budget hard-stops the workflow):
- `pipeline_config.budget_usd` is a HARD CAP. The orchestrator routes to
  max_retries the moment cumulative agent spend exceeds it. Set it ABOVE
  realistic per-run cost.
- Floor of 5.0 USD for any "proceed" action. A single developer call
  on a small change typically costs $0.20–$2.00; review another $0.10–$0.50;
  QA (when needs_qa=true) another $0.50–$2.00. Anything below $5.00 will
  almost certainly trip the cap.
- Use 10.0–25.0 for standard pipelines, 25.0–50.0 for complex/multi-file work.
- NEVER set `reviewer_enabled: false` — the workflow always routes through
  review on the happy path, and disabling it sends the workflow to a dead
  end (no review → no edge to take).

## Duplicate-PR check

The task context may include a list titled "Existing OPEN pull requests" for
the target repository. If one of those PRs already implements what this task
asks for (it names the same ticket id, or is clearly the same underlying
change), the job must NOT produce a second PR. Emit:
  "duplicate_of_pr": {
    "pr_number": <a number from the provided list>,
    "reason": "<1-2 sentences: why it is the same work>"
  }
alongside action="proceed" — the orchestrator then ends the job linking the
existing PR instead of running the developer. Rules:
- Only flag a PR that appears in the provided list.
- When in doubt (similar area but a different change), do NOT flag — proceed
  normally.
- Omit the field entirely when no list was provided or nothing matches.

## QA Decision

After you decide whether to proceed with developer work, also decide whether
the resulting PR should be validated by the QA agent.

Inputs available:
- The diff of the developer's intended (or actual, in the failure-routing case)
  changes.
- The repo's QA config stored in embry0 (if any), including its qa_required
  flag ("auto", "always", or "never").
- An optional acceptance_criteria_template from that config.

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
    "prompt": "<10-4000 chars; what specifically failed (criterion +
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
    "reason": "<max 500 chars; why you think this was environmental/flaky>"
  }
  Use when the failure looks environmental/flaky:
    - boot timed out and the prior attempt had partial success
    - a single criterion failed with a screenshot diff that's clearly cosmetic
    - DinD or network blips
  Don't use this just because you don't know what went wrong — that's ask_user.

  "qa_failure_action": {
    "kind": "ask_user",
    "question": "<10-2000 chars; what you need to know>"
  }
  Use when you can't make a confident judgment:
    - acceptance criteria conflict ("X should be visible" but the change is hiding X)
    - the failure is ambiguous and you'd be guessing
    - failure_rounds is at the cap; you must end with a question rather than retry
  The user's response will appear on the next triage invocation.

You MUST emit exactly one of these actions when re-invoked after a QA
failure, with no fields beyond the ones shown for its kind. An invalid
action ends the job with ERR_TRIAGE_QA_ACTION_INVALID.
Omit `qa_failure_action` entirely on the initial invocation (when QA
hasn't run yet).

Respond ONLY with the JSON object, no markdown fences or extra text.""".replace("<<SLOP_GUIDE>>", CLASSIFIER_GUIDE)

_SUB_AGENT_MODELS_NOTE = """\
- The developer agent itself can dispatch sub-agents (via the Agent tool) for
  parallel work. You DO NOT control the sub-agent models from triage; the
  developer chooses per-task. Don't try to set them here."""


def build_triage_model_guidance(*, default_model: str, tier_models: Mapping[str, str]) -> str:
    """The "Model selection" section of the triage prompt, from configuration.

    With a deployment-wide ``default_model`` triage is told not to choose
    models at all, and the section names no model id — the resolver ignores
    triage's picks in that case, so naming one would only invite an answer
    that is thrown away. Without one, triage picks among the configured tier
    models: heavy for the developer, medium for review, light for routine
    review. ``tier_models`` must carry all three tiers.
    """
    if default_model:
        return (
            "Model selection:\n"
            "- Do NOT choose models. This deployment runs every agent on one model\n"
            "  that the operator configured. Omit `agent_models` from\n"
            "  `pipeline_config` — any model you name there is ignored.\n"
            f"{_SUB_AGENT_MODELS_NOTE}"
        )
    missing = [tier for tier in TIER_KEYS if not tier_models.get(tier)]
    if missing:
        raise ValueError(f"triage model guidance needs a model for every tier; missing: {missing}")
    heavy, medium, light = (tier_models[tier] for tier in TIER_KEYS)
    return (
        "Model selection (CRITICAL):\n"
        f'- ALWAYS use "{heavy}" for the developer agent. The developer is the\n'
        "  primary code-writing role — pay for the better model. Do NOT downshift.\n"
        f"{_SUB_AGENT_MODELS_NOTE}\n"
        f'- For the review agent, "{medium}" is the default. For ROUTINE\n'
        "  changes (docs-only, typos, config edits, log messages, comment fixes) tier\n"
        f'  the review down to "{light}" — a routine diff does not need the\n'
        f'  default review model. Reach for "{heavy}" on review only for\n'
        "  security-sensitive diffs.\n"
        "- For the triage agent (yourself), the model is fixed at the orchestrator\n"
        "  level — don't try to override it.\n"
        "- When in doubt, prefer the more capable model.\n"
        "\n"
        "Set the chosen models in `pipeline_config.agent_models`:\n"
        f'  {{"developer": "{heavy}", "review": "{medium}"}}'
    )


def build_triage_system_prompt(*, default_model: str, tier_models: Mapping[str, str]) -> str:
    """The triage system prompt for one deployment configuration.

    Identical for every job while the provider settings are unchanged, so the
    CLI's prompt caching still covers it (EMB-35).
    """
    guidance = build_triage_model_guidance(default_model=default_model, tier_models=tier_models)
    return _TRIAGE_SYSTEM_PROMPT_TEMPLATE.replace(_MODEL_GUIDANCE_SLOT, guidance)


def _extract_json_payload(raw: str) -> str:
    """Pull a JSON object/array out of raw LLM text.

    Tolerates a fenced code block anywhere in the text, and — fenced or
    not — trailing prose after the payload (the model restating its
    answer, adding a caveat, etc). Falls back to the stripped input
    unchanged if no fence or balanced object/array is found, so callers
    still get a normal json.JSONDecodeError for genuinely non-JSON input.
    """
    text = raw.strip()

    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()

    start = next((i for i, ch in enumerate(text) if ch in "{["), None)
    if start is None:
        return text

    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text


def parse_triage_response(raw: str) -> TriageDecision:
    """Parse LLM response into a TriageDecision.

    Strict: raises TriageParseError on invalid JSON or schema mismatch.
    Callers must handle it (typically by failing the job with
    ErrorCode.TRIAGE_MALFORMED and logging the raw output).
    """
    from embry0.orchestration.state import TriageDecisionModel, TriageParseError

    try:
        text = _extract_json_payload(raw)
        data = json.loads(text)
    except (json.JSONDecodeError, IndexError) as exc:
        logger.warning("triage_parse_failed_json", raw=raw[:500])
        raise TriageParseError(f"Invalid JSON: {exc}") from exc

    # RAV-1250: drop unknown top-level keys before validation. Triage
    # occasionally volunteers advisory extras (a live probe died on
    # `reasoning_note`) and the model is extra="forbid" — a harmless extra
    # must not be job-terminal. Filtering HERE (not extra="ignore" on the
    # model) keeps every other consumer strict, and the warning names the
    # dropped keys so real schema drift stays visible.
    if isinstance(data, dict):
        known = set(TriageDecisionModel.model_fields)
        extras = sorted(k for k in data if k not in known)
        if extras:
            logger.warning("triage_extra_fields_dropped", fields=extras)
            data = {k: v for k, v in data.items() if k in known}

    try:
        model = TriageDecisionModel.model_validate(data)
    except Exception as exc:  # pydantic ValidationError subclass
        logger.warning("triage_parse_failed_schema", raw=raw[:500], error=str(exc))
        raise TriageParseError(f"Schema validation failed: {exc}") from exc

    # Return as TypedDict-shaped dict for downstream dict-based code.
    # cast() avoids the TypedDict(**dict[str, Any]) unsafe-expansion error;
    # model_validate guarantees the dict has the correct shape.
    # mode="json": the decision lives in the checkpoint, so it holds plain
    # JSON types — job_kind as its string, not a JobKind member that the
    # checkpointer would store as an embry0 class (#498). The only reader,
    # plan_route_node, already takes str() of it.
    decision = cast(TriageDecision, model.model_dump(mode="json"))
    # The model used to default this field to the string "default", and triage
    # echoed it. Nothing in the fleet is named that. A real profile name the
    # model volunteers is kept; it does not select the image (the sandbox
    # already exists) but it is an honest label. "default" is not.
    config = decision.get("pipeline_config") or {}
    if str(config.get("sandbox_profile") or "") == "default":
        logger.info("triage_sandbox_profile_default_ignored")
        config["sandbox_profile"] = ""
        decision["pipeline_config"] = config
    return decision


def apply_job_config_overrides(
    pipeline_config: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Fold explicit job-create overrides over triage's pipeline_config.

    RAV-1058: ``JobCreateRequest.max_budget_usd`` is an operator instruction —
    it must outrank whatever ``budget_usd`` triage chose. Applied at both
    triage passes (initial + QA-failure retriage) so every budget consumer
    (``check_budget``, the developer node's self-routing) sees the user cap
    without special-casing.
    """
    user_cap = state.get("max_budget_usd")
    if user_cap:
        pipeline_config = {**pipeline_config, "budget_usd": user_cap}
        logger.info("triage_budget_overridden_by_job", budget_usd=user_cap)
    return pipeline_config
