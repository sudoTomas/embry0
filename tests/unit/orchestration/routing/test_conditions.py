"""Tests for routing condition functions."""

import json

from embry0.orchestration.routing.conditions import (
    check_budget,
    check_review_decision,
    check_triage_action,
)

# --- check_triage_action ---


def test_triage_action_proceed():
    # action lives in triage_decision (D4: pipeline_config is now the flat dict)
    state = {"triage_decision": {"action": "proceed"}}
    assert check_triage_action(state) == "proceed"


def test_triage_action_split():
    state = {"triage_decision": {"action": "split"}}
    assert check_triage_action(state) == "split"


def test_triage_action_needs_info_routes_to_proceed():
    """needs_info is handled by interrupt, not routing — should map to proceed."""
    state = {"triage_decision": {"action": "needs_info"}}
    assert check_triage_action(state) == "proceed"


def test_triage_action_empty_state():
    state = {}
    assert check_triage_action(state) == "proceed"


# --- check_review_decision ---


def test_review_decision_no_outputs():
    state = {"agent_outputs": []}
    assert check_review_decision(state) == "changes_requested"


def test_review_decision_no_review_outputs():
    state = {"agent_outputs": [{"agent_type": "developer", "output": "done"}]}
    assert check_review_decision(state) == "changes_requested"


def test_review_decision_approved_json():
    review_data = json.dumps({"decision": "approved", "summary": "LGTM"})
    state = {"agent_outputs": [{"agent_type": "review", "output": review_data}]}
    assert check_review_decision(state) == "approved"


def test_review_decision_changes_requested_under_max_retries():
    review_data = json.dumps({"decision": "changes_requested", "summary": "Needs fixes"})
    state = {
        "agent_outputs": [{"agent_type": "review", "output": review_data}],
        "retry_count": 1,
        "pipeline_config": {"max_feedback_loops": 3},  # flat dict (D4)
    }
    assert check_review_decision(state) == "changes_requested"


def test_review_decision_changes_requested_at_max_retries():
    review_data = json.dumps({"decision": "changes_requested", "summary": "Still broken"})
    state = {
        "agent_outputs": [{"agent_type": "review", "output": review_data}],
        "retry_count": 3,
        "pipeline_config": {"max_feedback_loops": 3},  # flat dict (D4)
    }
    assert check_review_decision(state) == "max_retries"


def test_review_decision_changes_requested_over_max_retries():
    review_data = json.dumps({"decision": "changes_requested"})
    state = {
        "agent_outputs": [{"agent_type": "review", "output": review_data}],
        "retry_count": 5,
        "pipeline_config": {"max_feedback_loops": 3},  # flat dict (D4)
    }
    assert check_review_decision(state) == "max_retries"


def test_review_decision_approved_but_docs_need_update():
    review_data = json.dumps(
        {
            "decision": "approved",
            "docs_review": {"needs_update": True, "files": ["README.md"]},
        }
    )
    state = {
        "agent_outputs": [{"agent_type": "review", "output": review_data}],
        "retry_count": 0,
        "pipeline_config": {"max_feedback_loops": 3},  # flat dict (D4)
    }
    assert check_review_decision(state) == "changes_requested"


def test_review_decision_approved_docs_no_update():
    review_data = json.dumps(
        {
            "decision": "approved",
            "docs_review": {"needs_update": False},
        }
    )
    state = {"agent_outputs": [{"agent_type": "review", "output": review_data}]}
    assert check_review_decision(state) == "approved"


# --- check_budget ---


def test_budget_within():
    # pipeline_config is now the flat dict (D4: no nested "pipeline_config" wrapper)
    state = {"total_cost_usd": 5.0, "pipeline_config": {"budget_usd": 10.0}}
    assert check_budget(state) == "within_budget"


def test_budget_over():
    state = {"total_cost_usd": 15.0, "pipeline_config": {"budget_usd": 10.0}}
    assert check_budget(state) == "over_budget"


def test_budget_at_boundary():
    """Exactly at budget should be within (not strictly over)."""
    state = {"total_cost_usd": 10.0, "pipeline_config": {"budget_usd": 10.0}}
    assert check_budget(state) == "within_budget"


def test_review_decision_errored_agent_fails_closed():
    """An errored review agent must NOT auto-approve — fail closed to max_retries
    (regression: errored review was silently treated as 'approved')."""
    state = {"agent_outputs": [{"agent_type": "review", "is_error": True, "output": ""}]}
    assert check_review_decision(state) == "max_retries"
