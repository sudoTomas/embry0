import json
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from athanor.orchestration.nodes.triage import parse_triage_response


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
    from athanor.orchestration.state import TriageParseError

    with pytest.raises(TriageParseError):
        parse_triage_response("not json at all")


def test_parse_triage_response_raises_on_invalid_schema():
    """Schema violation (bad action value) raises TriageParseError."""
    from athanor.orchestration.state import TriageParseError

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


@pytest.mark.skip(reason="In-process triage fallback removed in Plan A finalisation (2026-04-28). "
                  "Rewrite using a real agent_runner mock + container_id if coverage of "
                  "run_triage_node is needed.")
@pytest.mark.asyncio
async def test_run_triage_node_success():
    """Skipped — run_triage_node (in-process SDK path) was deleted."""


@pytest.mark.skip(reason="In-process triage fallback removed in Plan A finalisation (2026-04-28). "
                  "apply_repo_preferences_override is covered directly below.")
@pytest.mark.asyncio
async def test_repo_preferences_override_sandbox_profile():
    """Skipped — run_triage_node (in-process SDK path) was deleted."""


@pytest.mark.skip(reason="In-process triage fallback removed in Plan A finalisation (2026-04-28). "
                  "apply_repo_preferences_override is covered directly below.")
@pytest.mark.asyncio
async def test_no_repo_preferences_leaves_llm_choice_intact():
    """Skipped — run_triage_node (in-process SDK path) was deleted."""


@pytest.mark.skip(reason="In-process triage fallback removed in Plan A finalisation (2026-04-28). "
                  "apply_repo_preferences_override is covered directly below.")
@pytest.mark.asyncio
async def test_repo_preferences_without_sandbox_profile_keeps_llm_choice():
    """Skipped — run_triage_node (in-process SDK path) was deleted."""


@pytest.mark.skip(reason="In-process triage fallback removed in Plan A finalisation (2026-04-28). "
                  "Low-confidence escalation logic lives in triage_node (sandbox path).")
@pytest.mark.asyncio
async def test_low_confidence_triggers_needs_info():
    """Skipped — run_triage_node (in-process SDK path) was deleted."""


# ---------------------------------------------------------------------------
# Direct unit tests for apply_repo_preferences_override (still live)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_repo_preferences_override_sets_sandbox_profile():
    """apply_repo_preferences_override replaces sandbox_profile when prefs have one."""
    from athanor.orchestration.nodes.triage import apply_repo_preferences_override

    prefs_repo = AsyncMock()
    prefs_repo.get = AsyncMock(return_value={"repo": "acme/w", "sandbox_profile": "java-17"})
    decision = {"pipeline_config": {"sandbox_profile": "python-3.12"}}

    result = await apply_repo_preferences_override(decision, "acme/w", prefs_repo)

    prefs_repo.get.assert_awaited_once_with("acme/w")
    assert result["pipeline_config"]["sandbox_profile"] == "java-17"


@pytest.mark.asyncio
async def test_apply_repo_preferences_override_skips_null_profile():
    """A preferences row with sandbox_profile=None must not overwrite the LLM's pick."""
    from athanor.orchestration.nodes.triage import apply_repo_preferences_override

    prefs_repo = AsyncMock()
    prefs_repo.get = AsyncMock(return_value={"repo": "acme/w", "sandbox_profile": None})
    decision = {"pipeline_config": {"sandbox_profile": "python-3.12"}}

    result = await apply_repo_preferences_override(decision, "acme/w", prefs_repo)

    assert result["pipeline_config"]["sandbox_profile"] == "python-3.12"


@pytest.mark.asyncio
async def test_apply_repo_preferences_override_no_prefs_repo():
    """Without a prefs_repo, the decision is returned unchanged."""
    from athanor.orchestration.nodes.triage import apply_repo_preferences_override

    decision = {"pipeline_config": {"sandbox_profile": "python-3.12"}}
    result = await apply_repo_preferences_override(decision, "acme/w", None)
    assert result == decision
