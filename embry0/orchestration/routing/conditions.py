"""Conditional edge functions for LangGraph graph routing."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from embry0.orchestration.budget import budget_allowance, resolve_job_cap


def check_triage_action(state: dict[str, Any]) -> Literal["proceed", "split"]:
    """Route after triage. needs_info is handled by interrupt(), not routing.

    action lives in triage_decision (the full TriageDecision dict stored on state
    after D4 shape standardization). state["pipeline_config"] is the flat
    PipelineConfig dict and does NOT carry an "action" key.
    """
    triage_decision = state.get("triage_decision", {})
    action = triage_decision.get("action", "proceed") if isinstance(triage_decision, dict) else "proceed"
    if action == "split":
        return "split"
    return "proceed"


#: The only two verdicts this router acts on. Anything else — an unknown
#: string, a missing key, prose, ``null``, truncated JSON — is no verdict at all.
REVIEW_DECISIONS: frozenset[str] = frozenset({"approved", "changes_requested"})


def _json_from_markdown(output_text: str) -> Any:
    """First JSON value parsed out of a fenced block, else None."""
    if "```" not in output_text:
        return None
    for block in output_text.split("```")[1::2]:
        block = block.strip()
        if block.startswith("json"):
            block = block[4:].strip()
        try:
            return json.loads(block)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


# A skipped test whose runner never started. Matched against the reviewer's
# own words when they did not set reason=no_toolchain. Kept narrow so
# "postgres connection refused" stays a service skip, not a toolchain pause.
_TOOLCHAIN_TEXT = re.compile(
    r"no jvm|no jdk|no java\b|java: not found|java not found|"
    r"\bmvnw?: not found|"
    r"compiling isn.?t possible|cannot compile|can'?t compile|"
    r"no compiler|toolchain|command not found",
    re.IGNORECASE,
)

# The reviewer may approve a skip only when the runner started and a service
# this sandbox does not have is required, or the diff has nothing to test.
_ALLOWED_TEST_SKIP_REASONS = frozenset({"needs_service", "not_applicable"})


def _last_verdict_object(text: str) -> dict[str, Any] | None:
    """The last JSON object in ``text`` that has a ``decision`` key, else None.

    A reviewer that narrates each step (grok-4.7) writes its verdict as bare JSON
    after the prose, with no fence, and the executors join every message into the
    output. Narration may quote an example verdict, so the last one is the
    reviewer's own. A truncated object does not decode and is not read.
    """
    from embry0.orchestration.agent_json import last_object_with

    return last_object_with(text, "decision")


def _review_payload(output_text: Any) -> dict[str, Any] | None:
    """The review JSON object, or None when the text is not one.

    The whole text as JSON, else a fenced block, else the last bare verdict
    object after prose.
    """
    text = output_text if isinstance(output_text, str) else ""
    try:
        data: Any = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        data = _json_from_markdown(text)
        if data is None:
            data = _last_verdict_object(text)
    return data if isinstance(data, dict) else None


def tests_block_reason(output_text: Any) -> str | None:
    """Why an ``approved`` verdict cannot stand, or None when it can.

    ``failed_checks`` — the reviewer reported a check as fail.
    ``toolchain_missing`` — the compiler or test runner is not installed.
    Retrying the developer in this sandbox cannot fix that.
    ``tests_not_run`` — tests were skipped without an allowed reason.
    """
    data = _review_payload(output_text)
    if data is None:
        return None
    validation = data.get("validation")
    if not isinstance(validation, dict):
        return None
    for name in ("tests", "lint", "typecheck"):
        entry = validation.get(name)
        status = entry.get("status") if isinstance(entry, dict) else None
        if isinstance(status, str) and status.lower() == "fail":
            return "failed_checks"
    tests = validation.get("tests")
    if not isinstance(tests, dict):
        return None
    if str(tests.get("status") or "").lower() != "skipped":
        return None
    reason = str(tests.get("reason") or "").strip().lower()
    if reason in _ALLOWED_TEST_SKIP_REASONS:
        return None
    blob = " ".join(str(tests.get(key) or "") for key in ("output", "reason"))
    blob = f"{blob} {data.get('summary') or ''}"
    if reason == "no_toolchain" or _TOOLCHAIN_TEXT.search(blob):
        return "toolchain_missing"
    return "tests_not_run"


def parse_review_decision(output_text: Any) -> str | None:
    """One review output's text -> ``approved`` / ``changes_requested`` / None.

    None means the text carries **no verdict this router will act on**, and is
    deliberately not a synonym for "approved" (#356). Before that fix the
    decision was seeded ``"approved"``, so every unreadable shape inherited an
    approval: empty output, prose (even prose reading ``VERDICT: REJECT``), a
    fenced block with no ``decision`` key, an unknown decision string, ``null``,
    truncated JSON, a missing ``output`` key. Each completed the job, closed the
    issue and could open a PR.

    The keyword fallback is kept because it can only ever *downgrade* to
    ``changes_requested`` — it never manufactures an approval. It is consulted
    only when no usable verdict was parsed, so a valid ``approved`` whose
    summary merely mentions the phrase keeps its verdict.
    """
    data = _review_payload(output_text)
    text = output_text if isinstance(output_text, str) else ""
    decision = data.get("decision") if isinstance(data, dict) else None
    if decision not in REVIEW_DECISIONS and "changes_requested" in text.lower():
        decision = "changes_requested"
    if decision not in REVIEW_DECISIONS:
        return None

    # Approved code with stale docs is a change request, not an approval.
    if decision == "approved" and isinstance(data, dict):
        docs_review = data.get("docs_review", {})
        if isinstance(docs_review, dict) and docs_review.get("needs_update"):
            return "changes_requested"
    return str(decision)


def check_review_decision(state: dict[str, Any]) -> Literal["approved", "changes_requested", "max_retries"]:
    """Route after review based on structured JSON decision."""
    outputs = state.get("agent_outputs", [])
    review_outputs = [o for o in outputs if o.get("agent_type") == "review"]
    if not review_outputs:
        return "changes_requested"

    latest = review_outputs[-1]
    if latest.get("is_error"):
        # The review agent itself errored (no valid verdict). Fail closed —
        # never auto-approve an errored review. Routes to max_retries (terminal),
        # mirroring how triage/developer handle their own agent errors.
        return "max_retries"

    output = latest.get("output", "")
    decision = parse_review_decision(output)
    if decision is None:
        # #356: the reviewer ran and returned something unreadable. Pause for a
        # human rather than approve — and rather than spend developer rounds on
        # a "change request" that carries no findings. Deliberately the same
        # arm as the is_error precedent above.
        return "max_retries"

    if decision == "approved":
        block = tests_block_reason(output)
        if block == "toolchain_missing":
            # The developer cannot install a JDK by editing the repo. Pause
            # now instead of spending feedback rounds in the same sandbox.
            return "max_retries"
        if block in ("failed_checks", "tests_not_run"):
            decision = "changes_requested"

    if decision == "changes_requested":
        retry_count = state.get("retry_count", 0)
        config = state.get("pipeline_config", {})
        max_loops = config.get("max_feedback_loops", 5) if isinstance(config, dict) else 5
        if retry_count >= max_loops:
            return "max_retries"
        return "changes_requested"

    return "approved"


def check_budget(state: dict[str, Any]) -> Literal["within_budget", "over_budget"]:
    """Soft-mode routing gate: has the job's per-job cap been crossed?

    Only ``soft`` overrun mode ever reaches this — ``hard`` aborts the run
    inside the agent node the moment the ceiling is crossed. The agent node
    records the cap it enforced (``budget_cap_usd``: triage's cap bounded by
    the fleet ceiling, plus an allowance a person granted, bounded again when
    the ceiling is a hard limit) and the spend it counted (``budget_spent_usd``,
    the job's traces included); with them, that cap decides (#283). States no
    agent node has checked since this was recorded fall back to the node's
    overrun verdict, and once a person let the job spend past its cap, to the
    comparison against the raised cap.
    """
    cap = state.get("budget_cap_usd")
    if isinstance(cap, int | float) and not isinstance(cap, bool):
        spent = max(float(state.get("total_cost_usd") or 0.0), float(state.get("budget_spent_usd") or 0.0))
        return "over_budget" if spent > cap else "within_budget"
    if state.get("budget_overrun_usd", 0.0) > 0 and budget_allowance(state) <= 0:
        return "over_budget"
    cost = state.get("total_cost_usd", 0.0)
    if cost > resolve_job_cap(state):
        return "over_budget"
    return "within_budget"
