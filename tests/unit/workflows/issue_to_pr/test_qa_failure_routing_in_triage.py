"""Phase 5 Task 7 — triage_node QA-failure routing.

When triage is re-invoked after the QA agent reported ``final_status="failed"``,
the agent must emit a ``qa_failure_action`` field on its inline JSON output
choosing one of three actions:

  - ``retry_developer`` → route to ``developer`` with a prompt addendum +
    focus files, so the developer can fix the QA-identified defect.
  - ``rerun_qa``        → route back into ``init_qa`` unchanged (flaky /
    environmental failure).
  - ``ask_user``        → route to ``ask_user_interrupt`` with a question.

Missing or malformed action ends the job with
``ErrorCode.QA_FAILURES_UNRESOLVED`` and ``state["qa"]["final_status"] =
"exhausted"``.

``state["qa"]["failure_rounds"]`` is bumped by the graph's
``_qa_failure_bookkeeping_node`` BEFORE this node is re-entered, so triage
does NOT bump it again.
"""

from __future__ import annotations

import json as _json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph import END
from langgraph.types import Command


def _triage_decision_with_qa_failure_action(
    qa_failure_action: dict[str, Any] | None,
) -> str:
    """Build a TriageDecisionModel-shaped JSON string with qa_failure_action inlined."""
    payload: dict[str, Any] = {
        "action": "proceed",
        "confidence": 0.9,
        "pipeline_template": "standard",
        "pipeline_config": {
            "sandbox_profile": "default",
            "max_feedback_loops": 3,
            "reviewer_enabled": True,
            "validator_modes": [],
            "agent_models": {"developer": "claude-sonnet-4-6"},
            "agent_tools": {},
            "agent_skills": {},
            "budget_usd": 10.0,
            "execution_modes": {},
            "auth_modes": {},
            "system_prompts": {},
            "mcp_servers": {},
        },
        "questions": [],
        "sub_tasks": [],
        "reasoning": "Re-invoked after QA failure",
    }
    if qa_failure_action is not None:
        payload["qa_failure_action"] = qa_failure_action
    return _json.dumps(payload)


def _make_qa_failed_state() -> dict[str, Any]:
    """State as it would arrive at triage after qa_report → qa_failure_bookkeeping
    bumped failure_rounds and routed back to triage."""
    return {
        "job_id": "job-qa-fail",
        "repo": "owner/repo",
        "task": "Add a dashboard widget",
        "sandbox_container_id": "container-abc",
        "agent_outputs": [],
        "errors": [],
        "qa": {
            "needs_qa": True,
            "final_status": "failed",
            "failure_rounds": 1,
            "max_qa_failure_rounds": 2,
            "acceptance_criteria": ["dashboard renders"],
            "attempts": [
                {
                    "attempt_n": 1,
                    "result_summary": {"e2e": {"passed": False, "failures": 1}},
                }
            ],
        },
    }


def _make_config() -> dict[str, Any]:
    return {
        "configurable": {
            "agent_runner": MagicMock(),
            "credentials": {},
            "repo_preferences_repo": None,
        }
    }


def _runner_returning(output: str) -> AsyncMock:
    """Mock run_agent_node that returns a successful triage agent output."""

    async def _impl(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": output}],
            "total_cost_usd": 0.02,
            "current_stage": "triage_complete",
        }

    return AsyncMock(side_effect=_impl)


@pytest.mark.asyncio
async def test_triage_routes_retry_developer_after_qa_failure() -> None:
    """When qa.final_status == "failed" and the agent emits
    ``qa_failure_action.kind = retry_developer``, triage_node returns a
    Command(goto="developer") with developer_prompt_addendum and
    developer_focus_files populated."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    output = _triage_decision_with_qa_failure_action(
        {
            "kind": "retry_developer",
            "prompt": "QA failed: dashboard widget shows 500. Fix the data fetch in widget.tsx.",
            "focus_files": ["frontend/widget.tsx", "api/widget.py"],
        }
    )
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == "developer"
    update = result.update or {}
    assert (
        update.get("developer_prompt_addendum")
        == "QA failed: dashboard widget shows 500. Fix the data fetch in widget.tsx."
    )
    assert update.get("developer_focus_files") == ["frontend/widget.tsx", "api/widget.py"]
    assert update.get("current_stage") == "qa_retry_developer"


@pytest.mark.asyncio
async def test_triage_routes_rerun_qa_after_qa_failure() -> None:
    """When qa.final_status == "failed" and the agent emits
    ``qa_failure_action.kind = rerun_qa``, triage_node routes to init_qa
    with qa_rerun_reason populated."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    output = _triage_decision_with_qa_failure_action(
        {
            "kind": "rerun_qa",
            "reason": "Boot timed out and prior attempt had partial success — likely flaky DinD startup.",
        }
    )
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == "init_qa"
    update = result.update or {}
    assert update.get("qa_rerun_reason") == (
        "Boot timed out and prior attempt had partial success — likely flaky DinD startup."
    )
    assert update.get("current_stage") == "qa_rerun"


@pytest.mark.asyncio
async def test_triage_routes_ask_user_after_qa_failure() -> None:
    """When qa.final_status == "failed" and the agent emits
    ``qa_failure_action.kind = ask_user``, triage_node routes to
    ask_user_interrupt with pending_user_question populated."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    output = _triage_decision_with_qa_failure_action(
        {
            "kind": "ask_user",
            "question": "Acceptance criterion 'X is visible' conflicts with the change that hides X. Which is intended?",
        }
    )
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == "ask_user_interrupt"
    update = result.update or {}
    assert update.get("pending_user_question") == (
        "Acceptance criterion 'X is visible' conflicts with the change that hides X. Which is intended?"
    )
    assert update.get("current_stage") == "qa_ask_user"


@pytest.mark.asyncio
async def test_triage_qa_failure_no_action_ends_with_error_code() -> None:
    """When qa.final_status == "failed" but the agent omits qa_failure_action,
    triage_node ends the job with ErrorCode.QA_FAILURES_UNRESOLVED and sets
    qa.final_status = "exhausted"."""
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import triage_node

    # No qa_failure_action — agent forgot the contract.
    output = _triage_decision_with_qa_failure_action(None)
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == END
    update = result.update or {}
    assert update.get("error_code") == ErrorCode.QA_FAILURES_UNRESOLVED.value
    qa_block = update.get("qa") or {}
    assert qa_block.get("final_status") == "exhausted"


@pytest.mark.asyncio
async def test_triage_qa_failure_malformed_payload_ends_with_error_code() -> None:
    """A qa_failure_action with the right kind but a payload that fails
    Pydantic validation (e.g. ``retry_developer`` without ``prompt``) should
    also terminate with QA_FAILURES_UNRESOLVED rather than crashing or
    silently routing on garbage."""
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import triage_node

    # retry_developer requires prompt with min_length=10; "hi" is too short.
    output = _triage_decision_with_qa_failure_action({"kind": "retry_developer", "prompt": "hi"})
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == END
    update = result.update or {}
    assert update.get("error_code") == ErrorCode.QA_FAILURES_UNRESOLVED.value
    qa_block = update.get("qa") or {}
    assert qa_block.get("final_status") == "exhausted"


@pytest.mark.asyncio
async def test_triage_qa_failure_unknown_kind_ends_with_error_code() -> None:
    """A qa_failure_action with an unknown kind should terminate with
    QA_FAILURES_UNRESOLVED rather than fall through silently."""
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import triage_node

    output = _triage_decision_with_qa_failure_action({"kind": "halt_universe", "reason": "no"})
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == END
    update = result.update or {}
    assert update.get("error_code") == ErrorCode.QA_FAILURES_UNRESOLVED.value


@pytest.mark.asyncio
async def test_triage_does_not_bump_failure_rounds() -> None:
    """failure_rounds is owned by the graph's _qa_failure_bookkeeping_node;
    triage_node must not increment it on top. The state arriving at triage
    already has the bumped count."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    state = _make_qa_failed_state()
    # bookkeeping bumped this from 0 → 1 before re-entering triage.
    assert state["qa"]["failure_rounds"] == 1

    output = _triage_decision_with_qa_failure_action(
        {
            "kind": "rerun_qa",
            "reason": "Likely flaky network blip during seed phase.",
        }
    )
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(state, _make_config())

    assert isinstance(result, Command)
    update = result.update or {}
    qa_in_update = update.get("qa")
    # If triage emitted a qa block at all (e.g. via set_qa_decision), it must
    # not have re-incremented failure_rounds. The happy-path rerun_qa update
    # doesn't touch qa, so failure_rounds stays at the pre-triage value via
    # the unmodified state.
    if qa_in_update is not None:
        assert qa_in_update.get("failure_rounds", 1) == 1
