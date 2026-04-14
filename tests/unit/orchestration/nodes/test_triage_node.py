import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from legion.orchestration.nodes.triage import parse_triage_response, run_triage_node


def test_parse_triage_response_proceed():
    raw = json.dumps(
        {
            "action": "proceed",
            "confidence": 0.9,
            "pipeline_template": "standard",
            "pipeline_config": {
                "sandbox_profile": "python-3.12",
                "agent_models": {"developer": "claude-sonnet-4-6"},
                "budget_usd": 10.0,
            },
            "reasoning": "Clear task, standard pipeline.",
        }
    )
    decision = parse_triage_response(raw)
    assert decision["action"] == "proceed"
    assert decision["confidence"] == 0.9
    assert decision["pipeline_config"]["sandbox_profile"] == "python-3.12"


def test_parse_triage_response_needs_info():
    raw = json.dumps(
        {
            "action": "needs_info",
            "confidence": 0.3,
            "questions": [
                {"question": "What file contains the auth logic?", "importance": "blocking"},
                {"question": "Which version of the API?", "importance": "blocking"},
            ],
            "reasoning": "Insufficient context.",
        }
    )
    decision = parse_triage_response(raw)
    assert decision["action"] == "needs_info"
    assert len(decision["questions"]) == 2


def test_parse_triage_response_split():
    raw = json.dumps(
        {
            "action": "split",
            "confidence": 0.8,
            "sub_tasks": [
                {"task": "Fix auth bug", "description": "..."},
                {"task": "Update tests", "description": "..."},
            ],
            "reasoning": "Too large for one job.",
        }
    )
    decision = parse_triage_response(raw)
    assert decision["action"] == "split"
    assert len(decision["sub_tasks"]) == 2


def test_parse_triage_response_invalid_json():
    """Strict parser now raises TriageParseError on invalid JSON."""
    from legion.orchestration.state import TriageParseError

    with pytest.raises(TriageParseError):
        parse_triage_response("not json at all")


def test_parse_triage_response_raises_on_invalid_schema():
    """Schema violation (bad action value) raises TriageParseError."""
    from legion.orchestration.state import TriageParseError

    with pytest.raises(TriageParseError):
        parse_triage_response('{"action": "chaos", "confidence": 0.5}')


def test_parse_triage_response_accepts_valid_minimal():
    """Minimal valid payload (action + confidence) parses OK with defaults."""
    result = parse_triage_response('{"action": "proceed", "confidence": 0.9}')
    assert result["action"] == "proceed"
    assert result["confidence"] == 0.9
    assert result["pipeline_template"] == "standard"


@dataclass
class MockAgentResult:
    success: bool = True
    raw_output: str = ""
    error: str | None = None
    usage: dict | None = None


@pytest.mark.asyncio
async def test_run_triage_node_success():
    """Verify triage node calls Agent SDK and parses the response."""
    triage_output = json.dumps(
        {
            "action": "proceed",
            "confidence": 0.9,
            "pipeline_template": "standard",
            "pipeline_config": {
                "sandbox_profile": "python-3.12",
                "agent_models": {"developer": "claude-sonnet-4-6"},
                "budget_usd": 10.0,
            },
            "reasoning": "Clear task.",
        }
    )

    mock_result = MockAgentResult(
        success=True, raw_output=triage_output, usage={"input_tokens": 100, "output_tokens": 50}
    )

    state = {
        "repo": "owner/repo",
        "task": "Fix the auth bug in login.py",
        "issue_number": 42,
    }

    with patch("legion.agents.sdk.run_agent", new_callable=AsyncMock, return_value=mock_result):
        result = await run_triage_node(state=state)

    assert result["pipeline_config"]["action"] == "proceed"
    assert result["current_stage"] == "triage_complete"


@pytest.mark.asyncio
async def test_repo_preferences_override_sandbox_profile():
    """A repo_preferences row with sandbox_profile must override the LLM-chosen
    value in ``pipeline_config.sandbox_profile``, regardless of what triage
    decided."""
    triage_output = json.dumps(
        {
            "action": "proceed",
            "confidence": 0.9,
            "pipeline_template": "standard",
            "pipeline_config": {
                "sandbox_profile": "python-3.12",
                "agent_models": {"developer": "claude-sonnet-4-6"},
                "budget_usd": 10.0,
            },
            "reasoning": "Clear task.",
        }
    )

    mock_result = MockAgentResult(
        success=True, raw_output=triage_output, usage={"input_tokens": 100, "output_tokens": 50}
    )

    prefs_repo = AsyncMock()
    prefs_repo.get = AsyncMock(
        return_value={
            "repo": "acme/widgets",
            "sandbox_profile": "java-17",
            "language_hint": "java",
            "notes": "",
            "updated_at": None,
        }
    )

    state = {"repo": "acme/widgets", "task": "Do the thing", "issue_number": 7}
    graph_config = {"configurable": {"repo_preferences_repo": prefs_repo}}

    with patch("legion.agents.sdk.run_agent", new_callable=AsyncMock, return_value=mock_result):
        result = await run_triage_node(state=state, config=graph_config)

    prefs_repo.get.assert_awaited_once_with("acme/widgets")
    assert result["pipeline_config"]["pipeline_config"]["sandbox_profile"] == "java-17"


@pytest.mark.asyncio
async def test_no_repo_preferences_leaves_llm_choice_intact():
    """Without a preferences row, the LLM-picked sandbox_profile is preserved."""
    triage_output = json.dumps(
        {
            "action": "proceed",
            "confidence": 0.9,
            "pipeline_template": "standard",
            "pipeline_config": {
                "sandbox_profile": "python-3.12",
                "agent_models": {"developer": "claude-sonnet-4-6"},
            },
            "reasoning": "ok",
        }
    )
    mock_result = MockAgentResult(
        success=True, raw_output=triage_output, usage={"input_tokens": 10, "output_tokens": 5}
    )

    prefs_repo = AsyncMock()
    prefs_repo.get = AsyncMock(return_value=None)

    state = {"repo": "acme/widgets", "task": "noop", "issue_number": 1}
    graph_config = {"configurable": {"repo_preferences_repo": prefs_repo}}

    with patch("legion.agents.sdk.run_agent", new_callable=AsyncMock, return_value=mock_result):
        result = await run_triage_node(state=state, config=graph_config)

    assert result["pipeline_config"]["pipeline_config"]["sandbox_profile"] == "python-3.12"


@pytest.mark.asyncio
async def test_repo_preferences_without_sandbox_profile_keeps_llm_choice():
    """A preferences row with sandbox_profile=None must not overwrite the LLM's pick."""
    triage_output = json.dumps(
        {
            "action": "proceed",
            "confidence": 0.9,
            "pipeline_template": "standard",
            "pipeline_config": {"sandbox_profile": "python-3.12"},
            "reasoning": "ok",
        }
    )
    mock_result = MockAgentResult(
        success=True, raw_output=triage_output, usage={"input_tokens": 5, "output_tokens": 5}
    )

    prefs_repo = AsyncMock()
    prefs_repo.get = AsyncMock(
        return_value={
            "repo": "acme/widgets",
            "sandbox_profile": None,
            "language_hint": "python",
            "notes": "",
            "updated_at": None,
        }
    )

    state = {"repo": "acme/widgets", "task": "noop"}
    graph_config = {"configurable": {"repo_preferences_repo": prefs_repo}}

    with patch("legion.agents.sdk.run_agent", new_callable=AsyncMock, return_value=mock_result):
        result = await run_triage_node(state=state, config=graph_config)

    assert result["pipeline_config"]["pipeline_config"]["sandbox_profile"] == "python-3.12"


@pytest.mark.asyncio
async def test_low_confidence_triggers_needs_info():
    """Verify that a 'proceed' decision below confidence_threshold is escalated to needs_info."""
    triage_output = json.dumps(
        {
            "action": "proceed",
            "confidence": 0.3,
            "pipeline_template": "standard",
            "pipeline_config": {
                "sandbox_profile": "python-3.12",
                "agent_models": {"developer": "claude-sonnet-4-6"},
                "budget_usd": 10.0,
            },
            "reasoning": "Vague requirements, low confidence.",
        }
    )

    mock_result = MockAgentResult(
        success=True, raw_output=triage_output, usage={"input_tokens": 100, "output_tokens": 50}
    )

    state = {
        "repo": "owner/repo",
        "task": "Do something with the thing",
        "issue_number": 99,
    }

    with patch("legion.agents.sdk.run_agent", new_callable=AsyncMock, return_value=mock_result):
        result = await run_triage_node(state=state, confidence_threshold=0.5)

    assert result["current_stage"] == "triage_complete"
    pipeline_config = result["pipeline_config"]
    assert pipeline_config["action"] == "needs_info"
    assert pipeline_config["confidence"] == 0.3
    assert len(pipeline_config["questions"]) == 1
    assert "0.3" in pipeline_config["reasoning"]
    assert "0.5" in pipeline_config["reasoning"]
