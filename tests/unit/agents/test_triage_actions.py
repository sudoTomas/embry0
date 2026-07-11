import pytest
from pydantic import ValidationError

from embry0.agents.triage_actions import SetQADecision


def test_minimal_positive_decision():
    d = SetQADecision(needs_qa=True, reason="Frontend touched", acceptance_criteria=["home loads"])
    assert d.needs_qa is True


def test_negative_decision_with_empty_criteria():
    d = SetQADecision(needs_qa=False, reason="Docs only", acceptance_criteria=[])
    assert d.needs_qa is False


def test_positive_decision_can_have_empty_criteria():
    """Means: use qa.yaml.acceptance_criteria_template."""
    d = SetQADecision(needs_qa=True, reason="Backend route changed", acceptance_criteria=[])
    assert d.acceptance_criteria == []


def test_reason_required():
    with pytest.raises(ValidationError):
        SetQADecision(needs_qa=True, acceptance_criteria=[])
