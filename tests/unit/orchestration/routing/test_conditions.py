from legion.orchestration.routing.conditions import (
    check_triage_action,
    check_validation_result,
    check_review_result,
    check_budget,
)


def test_triage_proceed():
    state = {"pipeline_config": {"action": "proceed"}}
    assert check_triage_action(state) == "proceed"


def test_triage_needs_info():
    state = {"pipeline_config": {"action": "needs_info"}}
    assert check_triage_action(state) == "needs_info"


def test_triage_split():
    state = {"pipeline_config": {"action": "split"}}
    assert check_triage_action(state) == "split"


def test_validation_pass():
    state = {"validation_result": {"passed": True, "category": "full_pass"}}
    assert check_validation_result(state) == "pass"


def test_validation_fail():
    state = {"validation_result": {"passed": False, "category": "full_fail"}}
    assert check_validation_result(state) == "fail"


def test_validation_missing():
    state = {}
    assert check_validation_result(state) == "fail"


def test_review_approved():
    state = {"agent_outputs": [{"agent_type": "reviewer", "output": "APPROVED. Code looks good."}]}
    assert check_review_result(state) == "approved"


def test_review_rejected():
    state = {"agent_outputs": [{"agent_type": "reviewer", "output": "REJECTED. Missing error handling."}]}
    assert check_review_result(state) == "rejected"


def test_budget_ok():
    state = {"total_cost_usd": 5.0, "pipeline_config": {"pipeline_config": {"budget_usd": 10.0}}}
    assert check_budget(state) == "within_budget"


def test_budget_exceeded():
    state = {"total_cost_usd": 15.0, "pipeline_config": {"pipeline_config": {"budget_usd": 10.0}}}
    assert check_budget(state) == "over_budget"
