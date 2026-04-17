from legion.orchestration.state import (
    AgentOutputEntry,
    JobState,
    PipelineConfig,
    TriageAction,
    TriageDecision,
    make_pipeline_config,
)


def test_triage_action_values():
    assert TriageAction.PROCEED == "proceed"
    assert TriageAction.NEEDS_INFO == "needs_info"
    assert TriageAction.SPLIT == "split"


def test_pipeline_config_defaults():
    config = make_pipeline_config(
        sandbox_profile="python-3.12",
        agent_models={"developer": "claude-sonnet-4-6"},
        budget_usd=10.0,
    )
    assert config["reviewer_enabled"] is True
    assert config["max_feedback_loops"] == 3
    assert config["validator_modes"] == ["test", "lint", "typecheck"]


def test_triage_decision_structure():
    decision = TriageDecision(
        action="proceed",
        confidence=0.9,
        pipeline_template="standard",
        pipeline_config=PipelineConfig(
            sandbox_profile="python-3.12",
            agent_models={"developer": "claude-sonnet-4-6"},
            budget_usd=10.0,
        ),
        reasoning="High confidence, standard pipeline appropriate.",
    )
    assert decision["action"] == "proceed"
    assert decision["confidence"] == 0.9


def test_agent_output_entry():
    entry = AgentOutputEntry(
        agent_type="developer",
        is_error=False,
        output="Fixed the bug",
        cost_usd=0.42,
        duration_ms=15000,
    )
    assert entry["agent_type"] == "developer"
    assert entry["cost_usd"] == 0.42


def test_job_state_has_required_keys():
    annotations = JobState.__annotations__
    required_keys = [
        "job_id",
        "repo",
        "task",
        "sandbox_container_id",
        "pipeline_config",
        "global_context",
        "repo_context",
        "agent_outputs",
        "errors",
        "current_stage",
        "total_cost_usd",
        "budget_overrun_usd",
        "pr_url",
        "result_summary",
        "user_retry_rounds",
    ]
    for key in required_keys:
        assert key in annotations, f"Missing key: {key}"


def test_job_state_has_cycle_guard_fields():
    """Both cycle-guard fields exist for agent questions and user retries."""
    annotations = JobState.__annotations__
    assert "agent_question_rounds" in annotations
    assert "agent_questions_exhausted" in annotations
    assert "user_retry_rounds" in annotations
