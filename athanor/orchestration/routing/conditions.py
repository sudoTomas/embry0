"""Conditional edge functions for LangGraph graph routing."""

from __future__ import annotations

import json
from typing import Any, Literal


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


def check_review_decision(state: dict[str, Any]) -> Literal["approved", "changes_requested", "max_retries"]:
    """Route after review based on structured JSON decision."""
    outputs = state.get("agent_outputs", [])
    review_outputs = [o for o in outputs if o.get("agent_type") == "review"]
    if not review_outputs:
        return "changes_requested"

    latest = review_outputs[-1]
    output_text = latest.get("output", "")

    # Parse structured JSON from review output
    decision = "approved"
    data = None
    try:
        data = json.loads(output_text)
        decision = data.get("decision", "approved")
    except (json.JSONDecodeError, TypeError):
        # Try extracting JSON from markdown
        if "```" in output_text:
            lines = output_text.split("```")
            for block in lines[1::2]:
                block = block.strip()
                if block.startswith("json"):
                    block = block[4:].strip()
                try:
                    data = json.loads(block)
                    decision = data.get("decision", "approved")
                    break
                except (json.JSONDecodeError, TypeError):
                    continue
        # Fallback: keyword matching
        if decision == "approved" and "changes_requested" in output_text.lower():
            decision = "changes_requested"

    # If code is approved but docs need updating, treat as changes_requested
    if decision == "approved" and data:
        docs_review = data.get("docs_review", {})
        if isinstance(docs_review, dict) and docs_review.get("needs_update"):
            decision = "changes_requested"

    if decision == "changes_requested":
        retry_count = state.get("retry_count", 0)
        config = state.get("pipeline_config", {})
        max_loops = config.get("max_feedback_loops", 3) if isinstance(config, dict) else 3
        if retry_count >= max_loops:
            return "max_retries"
        return "changes_requested"

    return "approved"


def check_budget(state: dict[str, Any]) -> Literal["within_budget", "over_budget"]:
    cost = state.get("total_cost_usd", 0.0)
    config = state.get("pipeline_config", {})
    max_budget = config.get("budget_usd", 10.0) if isinstance(config, dict) else 10.0
    if cost > max_budget:
        return "over_budget"
    return "within_budget"
