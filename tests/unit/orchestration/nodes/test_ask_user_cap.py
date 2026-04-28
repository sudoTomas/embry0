"""Tests for the job-wide ask_user round cap."""

from athanor.orchestration.nodes.agent import _enforce_ask_user_cap


def test_first_round_not_exhausted():
    state = {"job_id": "job-1", "agent_question_rounds": 0}
    questions = [{"q": "Q1"}]
    exhausted, updates = _enforce_ask_user_cap(state, questions)
    assert not exhausted
    assert updates["agent_question_rounds"] == 1
    assert updates["pending_agent_questions"] == questions


def test_below_cap_increments():
    state = {"job_id": "job-1", "agent_question_rounds": 4}
    exhausted, updates = _enforce_ask_user_cap(state, [{"q": "Q5"}])
    assert not exhausted
    assert updates["agent_question_rounds"] == 5


def test_at_cap_is_exhausted():
    state = {"job_id": "job-1", "agent_question_rounds": 5}
    exhausted, updates = _enforce_ask_user_cap(state, [{"q": "Q6"}])
    assert exhausted
    assert updates["current_stage"] == "failed"
    assert updates["error_code"] == "ERR_MAX_AGENT_QUESTIONS"
    assert updates["agent_questions_exhausted"] is True
    assert "exceeded 5 rounds" in updates["errors"][0]


def test_explicit_max_rounds_param():
    state = {"job_id": "job-1", "agent_question_rounds": 2}
    exhausted, updates = _enforce_ask_user_cap(state, [{"q": "Q3"}], max_rounds=2)
    assert exhausted
