from athanor.orchestration.nodes.output import build_output


def test_build_output_success():
    state = {
        "job_id": "job-123",
        "pr_url": "https://github.com/o/r/pull/1",
        "total_cost_usd": 2.5,
        "agent_outputs": [
            {"agent_type": "developer", "is_error": False},
            {"agent_type": "validator", "is_error": False},
        ],
        "errors": [],
    }
    result = build_output(state)
    assert result["current_stage"] == "completed"
    assert "pull/1" in result["result_summary"]


def test_build_output_with_errors():
    state = {
        "job_id": "job-123",
        "total_cost_usd": 1.0,
        "agent_outputs": [{"agent_type": "developer", "is_error": True}],
        "errors": ["developer: Timeout"],
        "pr_url": None,
    }
    result = build_output(state)
    assert result["current_stage"] == "failed"
    assert "error" in result["result_summary"].lower() or "fail" in result["result_summary"].lower()
