"""developer_node returns Command with correct goto based on executor output."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.types import Command

from athanor.workflows.issue_to_pr.nodes import developer_node, review_node


@pytest.fixture(autouse=True)
def _stub_stream_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace get_stream_writer so nodes run outside a LangGraph runtime."""
    import athanor.workflows.issue_to_pr.nodes as nodes_module

    monkeypatch.setattr(nodes_module, "get_stream_writer", lambda: lambda _e: None)


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
    with patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
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
    with patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
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
    with patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
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

    assert result.goto == "max_retries"  # budget exceeded


@pytest.mark.asyncio
async def test_developer_node_goes_to_max_retries_when_questions_exhausted() -> None:
    state = _state(agent_questions_exhausted=True)
    with patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
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


# ---------------------------------------------------------------------------
# review_node routing tests
# NOTE: review_node signature is (state, config) — agent_runner is passed via
# config["configurable"]["agent_runner"], not as a direct kwarg.
# ---------------------------------------------------------------------------


def _make_review_config(agent_runner: object | None) -> dict:
    """Build a config dict that review_node accepts."""
    if agent_runner is None:
        return {}
    return {"configurable": {"agent_runner": agent_runner}}


@pytest.mark.asyncio
async def test_review_node_goes_to_end_on_approved() -> None:
    with patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
        mock_rn.return_value = {
            "agent_outputs": [
                {
                    "agent_type": "review",
                    "is_error": False,
                    "output": '{"decision": "approved", "reasoning": "ok"}',
                    "cost_usd": 0.05,
                    "duration_ms": 100,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 0.05,
            "current_stage": "review_complete",
        }
        result = await review_node(
            _state(agent_outputs=[], pr_url="http://x"),
            config=_make_review_config(object()),
        )

    from langgraph.graph import END

    assert result.goto == END


@pytest.mark.asyncio
async def test_review_node_goes_to_retry_on_changes_requested() -> None:
    with patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
        mock_rn.return_value = {
            "agent_outputs": [
                {
                    "agent_type": "review",
                    "is_error": False,
                    "output": '{"decision": "changes_requested", "feedback": "fix X"}',
                    "cost_usd": 0.05,
                    "duration_ms": 100,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 0.05,
            "current_stage": "review_complete",
        }
        result = await review_node(
            _state(agent_outputs=[], retry_count=0),
            config=_make_review_config(object()),
        )

    assert result.goto == "retry"


@pytest.mark.asyncio
async def test_review_node_goes_to_max_retries_when_capped() -> None:
    with patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
        mock_rn.return_value = {
            "agent_outputs": [
                {
                    "agent_type": "review",
                    "is_error": False,
                    "output": '{"decision": "changes_requested", "feedback": "still broken"}',
                    "cost_usd": 0.05,
                    "duration_ms": 100,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 0.05,
            "current_stage": "review_complete",
        }
        state = _state(
            agent_outputs=[],
            retry_count=10,  # exceeds max_feedback_loops default
        )
        state["pipeline_config"] = {"pipeline_config": {"max_feedback_loops": 2}}
        result = await review_node(
            state,
            config=_make_review_config(object()),
        )

    assert result.goto == "max_retries"


@pytest.mark.asyncio
async def test_review_node_no_runner_sets_error_code() -> None:
    """Regression for commit c0cfdaa: every failure path must carry error_code."""
    result = await review_node(
        _state(),
        config=_make_review_config(None),
    )
    assert result.goto == "max_retries"
    assert "error_code" in result.update
    assert result.update["error_code"] == "ERR_UNKNOWN"


# ---------------------------------------------------------------------------
# developer_node ask_user extraction test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_developer_node_extracts_ask_user_from_events() -> None:
    """When the executor emits agent_ask_user events, questions get extracted."""
    with patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
        mock_rn.return_value = {
            "agent_outputs": [
                {
                    "agent_type": "developer",
                    "is_error": False,
                    "output": "",
                    "cost_usd": 0.01,
                    "duration_ms": 10,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 0.01,
            "current_stage": "developer_complete",
            # Simulate what run_agent_node does when it sees an agent_ask_user event:
            # it surfaces the question on pending_agent_questions.
            "pending_agent_questions": [{"question": "which db?", "options": ["pg", "mysql"]}],
        }
        result = await developer_node(_state(), config=None)

    assert result.goto == "ask_user_interrupt"
    assert result.update.get("pending_agent_questions") == [{"question": "which db?", "options": ["pg", "mysql"]}]
