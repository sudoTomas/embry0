"""developer_node returns Command with correct goto based on executor output."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.types import Command

from legion.workflows.issue_to_pr.nodes import developer_node


@pytest.fixture(autouse=True)
def _stub_stream_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace get_stream_writer so nodes run outside a LangGraph runtime."""
    import legion.workflows.issue_to_pr.nodes as nodes_module

    monkeypatch.setattr(nodes_module, "get_stream_writer", lambda: (lambda _e: None))


def _state(**over) -> dict[str, Any]:  # noqa: ANN002
    base: dict[str, Any] = {
        "job_id": "j1",
        "repo": "r",
        "task": "t",
        "pipeline_config": {},
        "total_cost_usd": 0.0,
        "pending_agent_questions": [],
        "agent_question_rounds": 0,
        "agent_questions_exhausted": False,
        "retry_count": 0,
        "sandbox_container_id": "c1",
        "global_context": "",
        "repo_context": "",
        "branch_name": None,
        "pr_url": None,
        "agent_models_override": {},
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_developer_node_goes_to_review_on_happy_path() -> None:
    with patch(
        "legion.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()
    ) as mock_rn:
        mock_rn.return_value = {
            "agent_outputs": [
                {
                    "agent_type": "developer",
                    "is_error": False,
                    "output": '{"pr_url": "http://x", "branch": "b", "summary": "s", "files_changed": []}',
                    "cost_usd": 0.1,
                    "duration_ms": 200,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 0.1,
            "current_stage": "developer_complete",
        }
        result = await developer_node(_state(), config=None)

    assert isinstance(result, Command)
    assert result.goto == "review"
    assert result.update["total_cost_usd"] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_developer_node_goes_to_ask_user_when_questions_pending() -> None:
    with patch(
        "legion.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()
    ) as mock_rn:
        mock_rn.return_value = {
            "agent_outputs": [
                {
                    "agent_type": "developer",
                    "is_error": False,
                    "output": "",
                    "cost_usd": 0.0,
                    "duration_ms": 10,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 0.0,
            "current_stage": "developer_complete",
            "pending_agent_questions": [{"question": "q1"}],
        }
        result = await developer_node(_state(), config=None)

    assert result.goto == "ask_user_interrupt"


@pytest.mark.asyncio
async def test_developer_node_goes_to_max_retries_when_budget_over() -> None:
    state = _state(total_cost_usd=1000.0)
    state["pipeline_config"] = {"pipeline_config": {"budget_usd": 1.0}}
    with patch(
        "legion.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()
    ) as mock_rn:
        mock_rn.return_value = {
            "agent_outputs": [
                {
                    "agent_type": "developer",
                    "is_error": False,
                    "output": "",
                    "cost_usd": 0.0,
                    "duration_ms": 10,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 1000.0,
            "current_stage": "developer_complete",
        }
        result = await developer_node(state, config=None)

    assert result.goto == "max_retries"


@pytest.mark.asyncio
async def test_developer_node_goes_to_max_retries_when_questions_exhausted() -> None:
    state = _state(agent_questions_exhausted=True)
    with patch(
        "legion.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()
    ) as mock_rn:
        mock_rn.return_value = {
            "agent_outputs": [
                {
                    "agent_type": "developer",
                    "is_error": True,
                    "output": "",
                    "cost_usd": 0.0,
                    "duration_ms": 10,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 0.0,
            "current_stage": "developer_failed",
        }
        result = await developer_node(state, config=None)

    assert result.goto == "max_retries"
