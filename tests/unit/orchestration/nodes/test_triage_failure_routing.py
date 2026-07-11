"""Tests that triage parse failure and agent error route to END without reaching developer."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph import END
from langgraph.types import Command


@pytest.fixture()
def base_state() -> dict[str, Any]:
    return {
        "job_id": "job-test-001",
        "repo": "owner/repo",
        "task": "Fix the thing",
        "issue_number": 42,
        "issue_id": "iss-001",
        "sandbox_container_id": "container-abc",
        "agent_outputs": [],
        "errors": [],
        "retry_count": 0,
        "total_cost_usd": 0.0,
    }


@pytest.fixture()
def mock_config() -> dict[str, Any]:
    return {
        "configurable": {
            "agent_runner": MagicMock(),
            "credentials": {},
            "repo_preferences_repo": None,
        }
    }


@pytest.mark.asyncio
async def test_triage_parse_error_routes_to_end(
    base_state: dict[str, Any],
    mock_config: dict[str, Any],
) -> None:
    """TriageParseError must return Command(goto=END), not a plain dict."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    agent_output = {
        "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": "NOT VALID JSON <<<"}],
        "total_cost_usd": 0.01,
        "current_stage": "triage_complete",
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=agent_output)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(base_state, mock_config)

    assert isinstance(result, Command), f"Expected Command, got {type(result)}"
    assert result.goto == END
    assert result.update.get("current_stage") == "failed"
    assert "ERR_TRIAGE_MALFORMED" in str(result.update.get("error_code", ""))


@pytest.mark.asyncio
async def test_triage_agent_error_routes_to_end(
    base_state: dict[str, Any],
    mock_config: dict[str, Any],
) -> None:
    """Agent is_error=True must route to END without reaching developer node."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    agent_output = {
        "agent_outputs": [
            {
                "agent_type": "triage",
                "is_error": True,
                "error_message": "Sandbox exec failed",
                "output": "",
            }
        ],
        "total_cost_usd": 0.0,
        "current_stage": "triage_failed",
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=agent_output)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(base_state, mock_config)

    assert isinstance(result, Command)
    assert result.goto == END
    assert result.update.get("current_stage") == "failed"


@pytest.mark.asyncio
async def test_triage_empty_agent_outputs_routes_to_end(
    base_state: dict[str, Any],
    mock_config: dict[str, Any],
) -> None:
    """Empty agent_outputs must route to END."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    agent_output: dict[str, Any] = {
        "agent_outputs": [],
        "total_cost_usd": 0.0,
        "current_stage": "triage_complete",
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=agent_output)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(base_state, mock_config)

    assert isinstance(result, Command)
    assert result.goto == END
