from legion.orchestration.nodes.validation import evaluate_validation


def test_full_pass():
    result = evaluate_validation(agent_output={"output": "All tests passed. Lint clean. Types OK.", "is_error": False})
    assert result["category"] == "full_pass"
    assert result["passed"] is True


def test_full_fail():
    result = evaluate_validation(agent_output={"output": "3 tests failed.", "is_error": False})
    assert result["passed"] is False


def test_error_output():
    result = evaluate_validation(agent_output={"is_error": True, "error_message": "Timeout"})
    assert result["passed"] is False
    assert result["category"] == "error"
