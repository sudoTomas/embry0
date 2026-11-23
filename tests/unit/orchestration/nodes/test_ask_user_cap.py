"""Tests for the job-wide ask_user round cap: the job's agent_questions settings, read when it started."""

from embry0.orchestration.nodes.agent import _enforce_ask_user_cap
from embry0.settings.sections.agent_questions import AGENT_QUESTIONS

#: The cap a job reads when no layer sets agent_questions.rounds_cap.
ROUNDS_CAP = AGENT_QUESTIONS.defaults().rounds_cap


def test_first_round_not_exhausted():
    state = {"job_id": "job-1", "agent_question_rounds": 0}
    questions = [{"q": "Q1"}]
    exhausted, updates = _enforce_ask_user_cap(state, questions)
    assert not exhausted
    assert updates["agent_question_rounds"] == 1
    assert updates["pending_agent_questions"] == questions


def test_below_cap_increments():
    state = {"job_id": "job-1", "agent_question_rounds": ROUNDS_CAP - 1}
    exhausted, updates = _enforce_ask_user_cap(state, [{"q": "Q5"}])
    assert not exhausted
    assert updates["agent_question_rounds"] == ROUNDS_CAP


def test_at_cap_is_exhausted():
    state = {"job_id": "job-1", "agent_question_rounds": ROUNDS_CAP}
    exhausted, updates = _enforce_ask_user_cap(state, [{"q": "Q6"}])
    assert exhausted
    assert updates["current_stage"] == "failed"
    assert updates["error_code"] == "ERR_MAX_AGENT_QUESTIONS"
    assert updates["agent_questions_exhausted"] is True
    assert f"exceeded {ROUNDS_CAP} rounds" in updates["errors"][0]


def test_the_cap_the_job_read_when_it_started():
    state = {"job_id": "job-1", "agent_question_rounds": 2, "job_settings": {"agent_questions": {"rounds_cap": 2}}}
    exhausted, updates = _enforce_ask_user_cap(state, [{"q": "Q3"}])
    assert exhausted, "the job's own rounds_cap of 2 was not applied"
    assert "agent_questions.rounds_cap" in updates["errors"][0], updates["errors"]
