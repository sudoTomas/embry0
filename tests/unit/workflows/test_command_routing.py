"""developer_node / review_node / triage_node Command routing tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph import END
from langgraph.types import Command

from embry0.settings.sections.agent_questions import AGENT_QUESTIONS
from embry0.workflows.issue_to_pr.nodes import developer_node, review_node, triage_node

#: The cap a job reads when no layer sets agent_questions.rounds_cap.
ROUNDS_CAP = AGENT_QUESTIONS.defaults().rounds_cap


@pytest.fixture(autouse=True)
def _stub_stream_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace get_stream_writer so nodes run outside a LangGraph runtime."""
    import embry0.workflows.issue_to_pr.nodes as nodes_module

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
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
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
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
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
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
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
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
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
    """RAV-1152: approved → Command with ``current_stage="review_passed"``
    — review self-routes exclusively (no conditional edge). An approved
    change goes to ``verify`` (#298/#299), which makes the route-plan dispatch
    review used to make itself; with verification off, review still does."""
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
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

    assert isinstance(result, Command), f"Expected Command, got {type(result)}"
    assert result.update.get("current_stage") == "review_passed"
    assert result.goto == "verify"


@pytest.mark.asyncio
async def test_review_node_dispatches_itself_when_verification_is_off() -> None:
    from types import SimpleNamespace

    config = _make_review_config(object())
    config["configurable"]["embry0_config"] = SimpleNamespace(verification_mode="off")
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
        mock_rn.return_value = {
            "agent_outputs": [
                {"agent_type": "review", "is_error": False, "output": '{"decision": "approved"}', "cost_usd": 0.05}
            ],
            "total_cost_usd": 0.05,
            "current_stage": "review_complete",
        }
        result = await review_node(_state(agent_outputs=[], pr_url="http://x"), config=config)

    assert isinstance(result, Command)
    assert result.goto == END  # no route plan, no needs_qa


@pytest.mark.asyncio
async def test_review_node_marks_review_failed_on_changes_requested() -> None:
    """RAV-1152: changes_requested → Command goto="retry" with
    ``current_stage="review_failed"`` (the review-fail feedback loop)."""
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
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

    assert isinstance(result, Command), f"Expected Command, got {type(result)}"
    assert result.update.get("current_stage") == "review_failed"
    assert result.goto == "retry"


@pytest.mark.asyncio
async def test_review_node_goes_to_max_retries_when_capped() -> None:
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
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
    assert result.update["error_code"] == "ERR_AGENT_RUNNER_UNAVAILABLE"


# ---------------------------------------------------------------------------
# developer_node ask_user extraction test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_developer_node_extracts_ask_user_from_events() -> None:
    """When the executor emits agent_ask_user events, questions get extracted."""
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock()) as mock_rn:
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
    when the job-wide agent_question_rounds cap is already exhausted."""
    ask_event = {"type": "agent_ask_user", "question": "clarify?", "category": "general"}
    mock_runner = object()  # non-None so the sandbox branch runs
    mock_run_agent = _make_run_agent_node_with_event(ask_event)

    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=mock_run_agent):
        # Also stub parse_triage_response so we don't need real agent output parsing
        with patch(
            "embry0.orchestration.nodes.triage.parse_triage_response",
            return_value={"action": "proceed"},
        ):
            state = _state(
                sandbox_container_id="c1",
                agent_question_rounds=ROUNDS_CAP,  # cap already reached
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
    """pipeline_config.agent_models['review'] must reach the resolved invocation.

    Historical regression: review_node once read the old TriageDecision-wrapper
    chain, silently got {}, and always fell back to claude-sonnet-4-6. Since
    RAV-602 the node passes an agent_definition (DB row or code fallback) and
    run_agent_node's resolver applies pipeline_config on top — so the
    assertion resolves the captured kwargs the same way run_agent_node does.
    """
    from embry0.agents.resolver import resolve_agent_config

    captured: dict[str, Any] = {}

    async def fake_run_agent_node(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
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

    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=fake_run_agent_node):
        state = _state(
            agent_outputs=[],
            pr_url="http://x",
            pipeline_config={"agent_models": {"review": "claude-opus-4-7"}},
        )
        await review_node(state, config=_make_review_config(object()))

    assert captured.get("agent_type") == "review"
    resolved = resolve_agent_config(
        agent_type="review",
        agent_definition=captured["agent_definition"],
        template_config=captured.get("template_config"),
        pipeline_config=captured["state"].get("pipeline_config"),
    )
    assert resolved.model == "claude-opus-4-7", (
        f"Expected claude-opus-4-7 from pipeline_config, got {resolved.model!r} — "
        "pipeline_config.agent_models must win over the agent_definition model."
    )


@pytest.mark.asyncio
async def test_review_node_enforces_ask_user_cap() -> None:
    """review_node must call _enforce_ask_user_cap and self-route to max_retries
    when the job-wide agent_question_rounds cap is already exhausted."""
    ask_event = {"type": "agent_ask_user", "question": "is this safe?", "category": "general"}
    mock_runner = object()  # non-None so review_node doesn't short-circuit
    mock_run_agent = _make_run_agent_node_with_event(ask_event)

    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=mock_run_agent):
        state = _state(
            agent_outputs=[],
            agent_question_rounds=ROUNDS_CAP,  # cap already reached
            agent_questions_exhausted=False,
            pr_url="http://x",
        )
        result = await review_node(state, config=_make_review_config(mock_runner))

    assert isinstance(result, Command), f"Expected Command, got {type(result)}"
    assert result.goto == "max_retries", f"Expected max_retries, got {result.goto}"
    assert result.update.get("current_stage") == "failed"
    assert result.update.get("error_code") == "ERR_MAX_AGENT_QUESTIONS"
    assert result.update.get("agent_questions_exhausted") is True


# ---- RAV-1271: developer forward-progression vs re-entry cursor semantics ----


def _dev_happy_output() -> dict[str, Any]:
    return {
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


_PLAN_DEV_REVIEW_QA = [
    {"agent_type": "developer", "node_id": "developer", "config": {}},
    {"agent_type": "reviewer", "node_id": "reviewer", "config": {}},
    {"agent_type": "qa", "node_id": "qa", "config": {}},
]


@pytest.mark.asyncio
async def test_developer_advances_cursor_when_on_developer_step() -> None:
    """Fresh forward progression: cursor ON the developer step ⇒ advance past
    it and dispatch the next step (review)."""
    state = _state(
        route_plan=list(_PLAN_DEV_REVIEW_QA),
        route_cursor=0,
        qa={"needs_qa": True},
    )
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=_dev_happy_output())):
        result = await developer_node(state, config=None)

    assert isinstance(result, Command)
    assert result.goto == "review"
    assert result.update.get("route_cursor") == 1


@pytest.mark.asyncio
async def test_developer_reentry_does_not_skip_the_parked_step() -> None:
    """RAV-1271 regression (job-ce281fced9ff): re-entry paths (review retry
    loop, max_retries continue_retrying) park the cursor at the REVIEWER step.
    The developer must NOT advance past it — the old blind +1 sent the flow
    developer → QA with no re-review of the new diff."""
    state = _state(
        route_plan=list(_PLAN_DEV_REVIEW_QA),
        route_cursor=1,  # parked on the reviewer step by the retry loop
        qa={"needs_qa": True},
    )
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=_dev_happy_output())):
        result = await developer_node(state, config=None)

    assert isinstance(result, Command)
    assert result.goto == "review", "the parked reviewer step must run again after a developer re-entry"
    assert "route_cursor" not in (result.update or {}), "re-entry must not move the cursor"
