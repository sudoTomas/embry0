"""triage_node should parse a SetQADecision tool call and update state["qa"].

Phase 5 Task 4. The triage prompt instructs the agent to emit a
``set_qa_decision`` tool call carrying ``needs_qa``, ``reason``, and
``acceptance_criteria``. ``triage_node`` collects all streamed events from
``run_agent_node`` via the ``on_event`` callback and, after the regular
pipeline-decision parse, scans those events for the QA tool call and
merges the result into ``state["qa"]`` (the QAStateBlock added in Task 1).
"""

from __future__ import annotations

import json as _json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_VALID_TRIAGE_DECISION_JSON = _json.dumps(
    {
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
        "reasoning": "Standard task",
    }
)


def _make_agent_output() -> dict[str, Any]:
    return {
        "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": _VALID_TRIAGE_DECISION_JSON}],
        "total_cost_usd": 0.02,
        "current_stage": "triage_complete",
    }


def _make_state() -> dict[str, Any]:
    return {
        "job_id": "job-qa-decision",
        "repo": "owner/repo",
        "task": "Add a new dashboard widget",
        "sandbox_container_id": "container-abc",
        "agent_outputs": [],
        "errors": [],
    }


def _make_config() -> dict[str, Any]:
    return {
        "configurable": {
            "agent_runner": MagicMock(),
            "credentials": {},
            "repo_preferences_repo": None,
        }
    }


def _runner_emitting(events: list[dict[str, Any]]) -> AsyncMock:
    """AsyncMock for run_agent_node that calls on_event(...) with each event
    before returning a successful triage output. Mirrors the production
    contract: events stream through on_event during the run, then the final
    AgentOutput is returned."""

    async def _impl(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        cb = kwargs.get("on_event")
        if cb is not None:
            for ev in events:
                cb(ev)
        return _make_agent_output()

    return AsyncMock(side_effect=_impl)


def _triage_decision_json_with_qa(set_qa_decision: dict[str, Any] | None) -> str:
    """Build a TriageDecisionModel-shaped JSON string with set_qa_decision inlined.
    Mirrors how the triage agent embeds the QA decision when it follows the
    prompt's primary path (inline JSON field) rather than emitting a tool call."""
    payload = _json.loads(_VALID_TRIAGE_DECISION_JSON)
    if set_qa_decision is not None:
        payload["set_qa_decision"] = set_qa_decision
    return _json.dumps(payload)


@pytest.mark.asyncio
async def test_triage_parses_inline_set_qa_decision_true() -> None:
    """When triage embeds set_qa_decision inline in its JSON output (the prompt's
    primary path), triage_node copies needs_qa/reason/acceptance_criteria onto
    state["qa"] without needing a tool_call event."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    inline_json = _triage_decision_json_with_qa(
        {
            "needs_qa": True,
            "reason": "Backend route changed",
            "acceptance_criteria": ["health endpoint returns 200"],
        }
    )

    async def _run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": inline_json}],
            "total_cost_usd": 0.02,
            "current_stage": "triage_complete",
        }

    runner_mock = AsyncMock(side_effect=_run)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_state(), _make_config())

    qa = (result.get("qa") or {}) if isinstance(result, dict) else {}
    assert qa.get("needs_qa") is True
    assert qa.get("qa_required_reason") == "Backend route changed"
    assert qa.get("acceptance_criteria") == ["health endpoint returns 200"]


@pytest.mark.asyncio
async def test_triage_parses_set_qa_decision_true() -> None:
    """When the agent emits set_qa_decision(needs_qa=True), triage_node writes
    needs_qa=True, qa_required_reason, and acceptance_criteria onto state.qa."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    qa_event = {
        "type": "tool_call",
        "tool_name": "set_qa_decision",
        "tool_id": "tool-1",
        "tool_input": {
            "needs_qa": True,
            "reason": "Frontend file touched",
            "acceptance_criteria": ["dashboard renders"],
        },
        "input": "...",
        "node": "triage",
    }

    runner_mock = _runner_emitting([qa_event])
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_state(), _make_config())

    assert isinstance(result, dict)
    qa = result.get("qa") or {}
    assert qa.get("needs_qa") is True
    assert qa.get("qa_required_reason") == "Frontend file touched"
    assert qa.get("acceptance_criteria") == ["dashboard renders"]


@pytest.mark.asyncio
async def test_triage_parses_set_qa_decision_false() -> None:
    """When the agent emits set_qa_decision(needs_qa=False), triage_node writes
    needs_qa=False on state.qa and does NOT write acceptance_criteria."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    qa_event = {
        "type": "tool_call",
        "tool_name": "set_qa_decision",
        "tool_id": "tool-1",
        "tool_input": {
            "needs_qa": False,
            "reason": "Docs-only change",
            "acceptance_criteria": [],
        },
        "input": "...",
        "node": "triage",
    }

    runner_mock = _runner_emitting([qa_event])
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_state(), _make_config())

    assert isinstance(result, dict)
    qa = result.get("qa") or {}
    assert qa.get("needs_qa") is False
    assert qa.get("qa_required_reason") == "Docs-only change"
    # acceptance_criteria is only written when needs_qa=True so the QA-pipeline
    # callers don't see a stale empty list and assume the agent picked criteria.
    assert "acceptance_criteria" not in qa


@pytest.mark.asyncio
async def test_triage_no_qa_decision_defaults_to_no_qa() -> None:
    """If the agent emits no set_qa_decision tool call, state.qa is not
    populated by triage_node and downstream readers see needs_qa as falsy."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    # Only an unrelated event in the stream — no set_qa_decision tool call.
    unrelated_event = {
        "type": "tool_call",
        "tool_name": "Read",
        "tool_id": "tool-2",
        "tool_input": {"file_path": "README.md"},
        "input": "README.md",
        "node": "triage",
    }

    runner_mock = _runner_emitting([unrelated_event])
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_state(), _make_config())

    assert isinstance(result, dict)
    # Triage didn't emit a decision -> no qa block written by this node.
    # Downstream conditional edge will read `state.get("qa", {}).get("needs_qa", False)`
    # and treat the absence as needs_qa=False.
    assert result.get("qa", {}).get("needs_qa", False) is False


@pytest.mark.asyncio
async def test_triage_set_qa_decision_invalid_payload_is_ignored() -> None:
    """Malformed tool_input (fails Pydantic validation) is logged and ignored —
    the node still returns a successful result with qa absent so the job can
    proceed (defaulting to no QA) rather than failing because the agent emitted
    a bad QA payload."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    bad_event = {
        "type": "tool_call",
        "tool_name": "set_qa_decision",
        "tool_id": "tool-3",
        # Missing required `reason` field — Pydantic validation will fail.
        "tool_input": {"needs_qa": True, "acceptance_criteria": []},
        "input": "...",
        "node": "triage",
    }

    runner_mock = _runner_emitting([bad_event])
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_state(), _make_config())

    assert isinstance(result, dict)
    assert result.get("qa", {}).get("needs_qa", False) is False


@pytest.mark.asyncio
async def test_triage_last_set_qa_decision_wins() -> None:
    """If the agent emits multiple set_qa_decision tool calls (e.g. across an
    initial pass and a needs_info → resume re-run), the LAST one wins so the
    final decision reflects the agent's most recent reasoning."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    earlier = {
        "type": "tool_call",
        "tool_name": "set_qa_decision",
        "tool_id": "tool-earlier",
        "tool_input": {
            "needs_qa": False,
            "reason": "Initial guess: docs only",
            "acceptance_criteria": [],
        },
        "input": "...",
        "node": "triage",
    }
    later = {
        "type": "tool_call",
        "tool_name": "set_qa_decision",
        "tool_id": "tool-later",
        "tool_input": {
            "needs_qa": True,
            "reason": "Reconsidered: backend route changed",
            "acceptance_criteria": ["POST /api/widgets returns 201"],
        },
        "input": "...",
        "node": "triage",
    }

    runner_mock = _runner_emitting([earlier, later])
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_state(), _make_config())

    qa = result.get("qa") or {}
    assert qa.get("needs_qa") is True
    assert qa.get("qa_required_reason") == "Reconsidered: backend route changed"
    assert qa.get("acceptance_criteria") == ["POST /api/widgets returns 201"]
