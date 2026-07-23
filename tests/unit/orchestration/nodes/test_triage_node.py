import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.orchestration.nodes.triage import parse_triage_response


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
    from embry0.orchestration.state import TriageParseError

    with pytest.raises(TriageParseError):
        parse_triage_response("not json at all")


def test_parse_triage_response_raises_on_invalid_schema():
    """Schema violation (bad action value) raises TriageParseError."""
    from embry0.orchestration.state import TriageParseError

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


@pytest.mark.skip(
    reason="In-process triage fallback removed in Plan A finalisation (2026-04-28). "
    "Rewrite using a real agent_runner mock + container_id if coverage of "
    "run_triage_node is needed."
)
@pytest.mark.asyncio
async def test_run_triage_node_success():
    """Skipped — run_triage_node (in-process SDK path) was deleted."""


@pytest.mark.skip(
    reason="In-process triage fallback removed in Plan A finalisation (2026-04-28). "
    "apply_repo_preferences_override is covered directly below."
)
@pytest.mark.asyncio
async def test_repo_preferences_override_sandbox_profile():
    """Skipped — run_triage_node (in-process SDK path) was deleted."""


@pytest.mark.skip(
    reason="In-process triage fallback removed in Plan A finalisation (2026-04-28). "
    "apply_repo_preferences_override is covered directly below."
)
@pytest.mark.asyncio
async def test_no_repo_preferences_leaves_llm_choice_intact():
    """Skipped — run_triage_node (in-process SDK path) was deleted."""


@pytest.mark.skip(
    reason="In-process triage fallback removed in Plan A finalisation (2026-04-28). "
    "apply_repo_preferences_override is covered directly below."
)
@pytest.mark.asyncio
async def test_repo_preferences_without_sandbox_profile_keeps_llm_choice():
    """Skipped — run_triage_node (in-process SDK path) was deleted."""


@pytest.mark.skip(
    reason="In-process triage fallback removed in Plan A finalisation (2026-04-28). "
    "Low-confidence escalation logic lives in triage_node (sandbox path)."
)
@pytest.mark.asyncio
async def test_low_confidence_triggers_needs_info():
    """Skipped — run_triage_node (in-process SDK path) was deleted."""


# ---------------------------------------------------------------------------
# Direct unit tests for apply_repo_preferences_override (still live)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_repo_preferences_override_sets_sandbox_profile():
    """apply_repo_preferences_override replaces sandbox_profile when prefs have one."""
    from embry0.orchestration.nodes.triage import apply_repo_preferences_override

    prefs_repo = AsyncMock()
    prefs_repo.get = AsyncMock(return_value={"repo": "acme/w", "sandbox_profile": "java-17"})
    decision = {"pipeline_config": {"sandbox_profile": "python-3.12"}}

    result = await apply_repo_preferences_override(decision, "acme/w", prefs_repo)

    prefs_repo.get.assert_awaited_once_with("acme/w")
    assert result["pipeline_config"]["sandbox_profile"] == "java-17"


@pytest.mark.asyncio
async def test_apply_repo_preferences_override_skips_null_profile():
    """A preferences row with sandbox_profile=None must not overwrite the LLM's pick."""
    from embry0.orchestration.nodes.triage import apply_repo_preferences_override

    prefs_repo = AsyncMock()
    prefs_repo.get = AsyncMock(return_value={"repo": "acme/w", "sandbox_profile": None})
    decision = {"pipeline_config": {"sandbox_profile": "python-3.12"}}

    result = await apply_repo_preferences_override(decision, "acme/w", prefs_repo)

    assert result["pipeline_config"]["sandbox_profile"] == "python-3.12"


@pytest.mark.asyncio
async def test_apply_repo_preferences_override_no_prefs_repo():
    """Without a prefs_repo, the decision is returned unchanged."""
    from embry0.orchestration.nodes.triage import apply_repo_preferences_override

    decision = {"pipeline_config": {"sandbox_profile": "python-3.12"}}
    result = await apply_repo_preferences_override(decision, "acme/w", None)
    assert result == decision


@pytest.mark.asyncio
async def test_triage_node_writes_flat_pipeline_config_to_state() -> None:
    """triage_node must write the flat PipelineConfig dict (not TriageDecision wrapper)
    to state['pipeline_config'], and the full decision to state['triage_decision']."""
    import json as _json

    from embry0.workflows.issue_to_pr.nodes import triage_node

    valid_decision_json = _json.dumps(
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

    agent_output = {
        "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": valid_decision_json}],
        "total_cost_usd": 0.02,
        "current_stage": "triage_complete",
    }

    state = {
        "job_id": "job-shape-test",
        "repo": "owner/repo",
        "task": "Fix the thing",
        "sandbox_container_id": "container-abc",
        "agent_outputs": [],
        "errors": [],
    }
    config = {
        "configurable": {
            "agent_runner": MagicMock(),
            "credentials": {},
            "repo_preferences_repo": None,
        }
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=agent_output)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(state, config)

    # Result must be a plain dict (successful triage returns dict, not Command)
    assert isinstance(result, dict)

    # pipeline_config must be the flat inner dict (no "action" key)
    pc = result.get("pipeline_config", {})
    assert "action" not in pc, "pipeline_config must be flat PipelineConfig, not TriageDecision wrapper"
    assert "budget_usd" in pc

    # triage_decision must carry the full TriageDecision
    td = result.get("triage_decision", {})
    assert td.get("action") == "proceed"
    assert "reasoning" in td


@pytest.mark.asyncio
async def test_triage_cycle_guard_terminates_at_cap() -> None:
    """At the triage round cap, triage_node must return Command(goto=END)."""
    from langgraph.graph import END
    from langgraph.types import Command

    from embry0.orchestration.nodes.agent import DEFAULT_ASK_USER_CAP
    from embry0.workflows.issue_to_pr.nodes import triage_node

    state = {
        "job_id": "job-cycle-test",
        "repo": "owner/repo",
        "task": "Fix the thing",
        "sandbox_container_id": "container-abc",
        "agent_outputs": [],
        "errors": [],
        "triage_question_rounds": DEFAULT_ASK_USER_CAP,  # already at cap
        "total_cost_usd": 0.0,
    }
    config = {
        "configurable": {
            "agent_runner": MagicMock(),
            "credentials": {},
            "repo_preferences_repo": None,
        }
    }

    # The agent returns needs_info (would trigger interrupt in normal path)
    needs_info_output = {
        "agent_outputs": [
            {
                "agent_type": "triage",
                "is_error": False,
                "output": '{"action":"needs_info","confidence":0.3,"questions":[],"reasoning":"x"}',
            }
        ],
        "total_cost_usd": 0.01,
        "current_stage": "triage_complete",
    }

    # The cycle guard fires BEFORE the interrupt() call, so interrupt should
    # not be called at all when rounds >= the cap.
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=needs_info_output)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch("embry0.workflows.issue_to_pr.nodes.interrupt") as mock_interrupt,
    ):
        result = await triage_node(state, config)

    assert isinstance(result, Command)
    assert result.goto == END
    assert "ERR_MAX_TRIAGE_QUESTIONS" in str(result.update.get("error_code", ""))
    mock_interrupt.assert_not_called()
