import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from legion.orchestration.nodes.triage import run_triage_node, parse_triage_response


def test_parse_triage_response_proceed():
    raw = json.dumps({
        "action": "proceed",
        "confidence": 0.9,
        "pipeline_template": "standard",
        "pipeline_config": {
            "sandbox_profile": "python-3.12",
            "agent_models": {"developer": "claude-sonnet-4-6"},
            "budget_usd": 10.0,
        },
        "reasoning": "Clear task, standard pipeline.",
    })
    decision = parse_triage_response(raw)
    assert decision["action"] == "proceed"
    assert decision["confidence"] == 0.9
    assert decision["pipeline_config"]["sandbox_profile"] == "python-3.12"


def test_parse_triage_response_needs_info():
    raw = json.dumps({
        "action": "needs_info",
        "confidence": 0.3,
        "questions": ["What file contains the auth logic?", "Which version of the API?"],
        "reasoning": "Insufficient context.",
    })
    decision = parse_triage_response(raw)
    assert decision["action"] == "needs_info"
    assert len(decision["questions"]) == 2


def test_parse_triage_response_split():
    raw = json.dumps({
        "action": "split",
        "confidence": 0.8,
        "sub_tasks": [
            {"task": "Fix auth bug", "description": "..."},
            {"task": "Update tests", "description": "..."},
        ],
        "reasoning": "Too large for one job.",
    })
    decision = parse_triage_response(raw)
    assert decision["action"] == "split"
    assert len(decision["sub_tasks"]) == 2


def test_parse_triage_response_invalid_json():
    decision = parse_triage_response("not json at all")
    assert decision["action"] == "proceed"
    assert decision["confidence"] == 0.5


@pytest.mark.asyncio
async def test_run_triage_node_success():
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps({
        "action": "proceed",
        "confidence": 0.9,
        "pipeline_template": "standard",
        "pipeline_config": {
            "sandbox_profile": "python-3.12",
            "agent_models": {"developer": "claude-sonnet-4-6"},
            "budget_usd": 10.0,
        },
        "reasoning": "Clear task.",
    }))]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    state = {
        "repo": "owner/repo",
        "task": "Fix the auth bug in login.py",
        "issue_number": 42,
    }

    with patch("legion.orchestration.nodes.triage._create_anthropic_client", return_value=mock_client):
        result = await run_triage_node(state=state, api_key="sk-test")

    assert result["pipeline_config"]["action"] == "proceed"
    assert result["current_stage"] == "triage_complete"
