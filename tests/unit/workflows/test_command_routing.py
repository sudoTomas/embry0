"""developer_node / review_node / triage_node Command routing tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.types import Command

from athanor.workflows.issue_to_pr.nodes import developer_node, review_node, triage_node


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
async def test_review_node_marks_review_passed_on_approved() -> None:
    """Phase 5 Task 5: approved → return plain dict with
    ``current_stage="review_passed"``. The conditional edge
    ``route_after_review`` in graph.py dispatches to init_qa or END
    based on state["qa"]["needs_qa"]."""
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

    assert isinstance(result, dict), f"Expected plain dict, got {type(result)}"
    assert result.get("current_stage") == "review_passed"


@pytest.mark.asyncio
async def test_review_node_marks_review_failed_on_changes_requested() -> None:
    """Phase 5 Task 5: changes_requested → return plain dict with
    ``current_stage="review_failed"``. The conditional edge maps
    "developer" → the existing retry node."""
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

    assert isinstance(result, dict), f"Expected plain dict, got {type(result)}"
    assert result.get("current_stage") == "review_failed"


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


# ---------------------------------------------------------------------------
# Node-level cap wiring: triage_node and review_node actually call the helper
# ---------------------------------------------------------------------------
#
# These tests feed each node a state with agent_question_rounds=5 and arrange
# for run_agent_node to emit an agent_ask_user event via the on_event callback.
# If a future refactor accidentally drops the _enforce_ask_user_cap call, the
# node returns the wrong shape and the assertions here catch it.
# The helper itself is unit-tested in tests/unit/orchestration/nodes/test_ask_user_cap.py.


def _make_run_agent_node_with_event(event: dict[str, Any]):
    """Return an AsyncMock for run_agent_node that fires ``event`` through the
    ``on_event`` callback before returning a minimal success result."""

    async def _side_effect(**kwargs):
        on_event = kwargs.get("on_event")
        if on_event is not None:
            on_event(event)
        return {
            "agent_outputs": [
                {
                    "agent_type": kwargs.get("agent_type", "unknown"),
                    "is_error": False,
                    "output": '{"decision": "proceed"}',
                    "cost_usd": 0.0,
                    "duration_ms": 10,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 0.0,
            "current_stage": "triage_complete",
        }

    return AsyncMock(side_effect=_side_effect)


def _triage_config(agent_runner: object) -> dict:
    """Build a minimal config dict that triage_node accepts."""
    return {
        "configurable": {
            "agent_runner": agent_runner,
            "credentials": {},
            "repo_preferences_repo": None,
        }
    }


@pytest.mark.asyncio
async def test_triage_node_enforces_ask_user_cap() -> None:
    """triage_node must call _enforce_ask_user_cap and self-route to max_retries
    when the job-wide cap (agent_question_rounds >= 5) is already exhausted."""
    ask_event = {"type": "agent_ask_user", "question": "clarify?", "category": "general"}
    mock_runner = object()  # non-None so the sandbox branch runs
    mock_run_agent = _make_run_agent_node_with_event(ask_event)

    with patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=mock_run_agent):
        # Also stub parse_triage_response so we don't need real agent output parsing
        with patch(
            "athanor.orchestration.nodes.triage.parse_triage_response",
            return_value={"action": "proceed"},
        ):
            state = _state(
                sandbox_container_id="c1",
                agent_question_rounds=5,  # cap already reached
                agent_questions_exhausted=False,
            )
            result = await triage_node(state, config=_triage_config(mock_runner))

    assert isinstance(result, Command), f"Expected Command, got {type(result)}"
    assert result.goto == "max_retries", f"Expected max_retries, got {result.goto}"
    assert result.update.get("current_stage") == "failed"
    assert result.update.get("error_code") == "ERR_MAX_AGENT_QUESTIONS"
    assert result.update.get("agent_questions_exhausted") is True


@pytest.mark.asyncio
async def test_review_node_uses_per_agent_model_from_pipeline_config() -> None:
    """review_node passes agent_models['review'] from the flat pipeline_config to run_agent_node.

    Regression for the bug where review_node used the old TriageDecision-wrapper chain
    triage_decision.get("pipeline_config", {}).get("agent_models", ...) after Task 6
    standardised on the flat PipelineConfig shape. The chain silently returned {} and
    review always fell back to claude-sonnet-4-6 regardless of the configured model.
    """
    captured: dict[str, Any] = {}

    async def fake_run_agent_node(**kwargs: Any) -> dict[str, Any]:
        captured["model"] = kwargs.get("model")
        captured["agent_type"] = kwargs.get("agent_type")
        return {
            "agent_outputs": [
                {
                    "agent_type": "review",
                    "is_error": False,
                    "output": '{"decision": "approved", "reasoning": "ok"}',
                    "cost_usd": 0.0,
                    "duration_ms": 10,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 0.0,
            "current_stage": "review_complete",
        }

    with patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=fake_run_agent_node):
        state = _state(
            agent_outputs=[],
            pr_url="http://x",
            pipeline_config={"agent_models": {"review": "claude-opus-4-7"}},
        )
        await review_node(state, config=_make_review_config(object()))

    assert captured.get("agent_type") == "review"
    assert captured.get("model") == "claude-opus-4-7", (
        f"Expected claude-opus-4-7 from pipeline_config, got {captured.get('model')!r}. "
        "The old wrapper chain `triage_decision.get('pipeline_config', {}).get('agent_models', ...)` "
        "would have returned the fallback 'claude-sonnet-4-6' instead."
    )


@pytest.mark.asyncio
async def test_review_node_enforces_ask_user_cap() -> None:
    """review_node must call _enforce_ask_user_cap and self-route to max_retries
    when the job-wide cap (agent_question_rounds >= 5) is already exhausted."""
    ask_event = {"type": "agent_ask_user", "question": "is this safe?", "category": "general"}
    mock_runner = object()  # non-None so review_node doesn't short-circuit
    mock_run_agent = _make_run_agent_node_with_event(ask_event)

    with patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=mock_run_agent):
        state = _state(
            agent_outputs=[],
            agent_question_rounds=5,  # cap already reached
            agent_questions_exhausted=False,
            pr_url="http://x",
        )
        result = await review_node(state, config=_make_review_config(mock_runner))

    assert isinstance(result, Command), f"Expected Command, got {type(result)}"
    assert result.goto == "max_retries", f"Expected max_retries, got {result.goto}"
    assert result.update.get("current_stage") == "failed"
    assert result.update.get("error_code") == "ERR_MAX_AGENT_QUESTIONS"
    assert result.update.get("agent_questions_exhausted") is True
