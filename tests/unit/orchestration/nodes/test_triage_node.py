import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.orchestration.nodes.triage import parse_triage_response


def test_omitted_sandbox_profile_is_not_named_default():
    """There is no profile named "default". Filling that word in when triage
    omits the field makes the job look like it selected a profile it did not."""
    raw = json.dumps({"action": "proceed", "confidence": 0.9, "reasoning": "Clear task."})
    decision = parse_triage_response(raw)
    assert decision["pipeline_config"]["sandbox_profile"] == "", (
        "an omitted sandbox_profile must stay empty, not the name 'default'"
    )


def test_triage_word_default_is_not_stored_as_a_profile():
    raw = json.dumps(
        {
            "action": "proceed",
            "confidence": 0.9,
            "pipeline_config": {"sandbox_profile": "default"},
            "reasoning": "echoed the schema example",
        }
    )
    decision = parse_triage_response(raw)
    assert decision["pipeline_config"]["sandbox_profile"] == "", (
        "the word default is not a sandbox profile and must not be stored"
    )


def test_triage_prompt_asks_for_a_skills_addition():
    from embry0.orchestration.nodes.triage import _TRIAGE_SYSTEM_PROMPT_TEMPLATE

    assert '{"developer": {"add": ["superpowers:brainstorming"]}}' in _TRIAGE_SYSTEM_PROMPT_TEMPLATE
    assert '{"developer": ["superpowers:brainstorming"]}' not in _TRIAGE_SYSTEM_PROMPT_TEMPLATE, (
        "the replace form would drop the developer's configured skills"
    )


def test_a_skills_delta_from_triage_parses_and_adds_to_the_definition():
    from embry0.agents.resolver import resolve_agent_config

    raw = json.dumps(
        {
            "action": "proceed",
            "confidence": 0.9,
            "pipeline_config": {"agent_skills": {"developer": {"add": ["superpowers:brainstorming"]}}},
            "reasoning": "open-ended",
        }
    )
    decision = parse_triage_response(raw)
    definition = {"model": "m", "tools": [], "skills": ["local:house-style"], "system_prompt": ""}
    resolved = resolve_agent_config("developer", definition, pipeline_config=dict(decision["pipeline_config"]))
    assert resolved.skills == ["local:house-style", "superpowers:brainstorming"]


def test_a_legacy_skills_list_from_triage_still_parses():
    raw = json.dumps(
        {
            "action": "proceed",
            "confidence": 0.9,
            "pipeline_config": {"agent_skills": {"developer": ["superpowers:writing-plans"]}},
            "reasoning": "clear",
        }
    )
    decision = parse_triage_response(raw)
    assert decision["pipeline_config"]["agent_skills"] == {"developer": ["superpowers:writing-plans"]}


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
    # RAV-601: empty = "use the job_kind's default template".
    assert result["pipeline_template"] == ""
    assert result["job_kind"] == "code"


def test_parse_triage_response_accepts_duplicate_of_pr():
    """RAV-1079: the optional duplicate_of_pr flag parses at the boundary
    (extra=forbid would reject an undeclared key)."""
    result = parse_triage_response(
        '{"action": "proceed", "confidence": 0.9,'
        ' "duplicate_of_pr": {"pr_number": 366, "reason": "same fix as PR 366"}}'
    )
    assert result["duplicate_of_pr"] == {"pr_number": 366, "reason": "same fix as PR 366"}


def test_parse_triage_response_duplicate_of_pr_defaults_none():
    result = parse_triage_response('{"action": "proceed", "confidence": 0.9}')
    assert result.get("duplicate_of_pr") is None


def test_parse_triage_response_job_kind_enum():
    """job_kind is a closed vocabulary validated at the parse boundary."""
    from embry0.orchestration.state import TriageParseError

    ok = parse_triage_response('{"action": "proceed", "confidence": 0.9, "job_kind": "research"}')
    assert ok["job_kind"] == "research"
    with pytest.raises(TriageParseError):
        parse_triage_response('{"action": "proceed", "confidence": 0.9, "job_kind": "deploy"}')


_JSON_TYPES = (dict, list, str, int, float, bool, type(None))


def _non_json_leaves(value, path="decision"):
    if type(value) not in _JSON_TYPES:
        return [f"{path}: {type(value).__module__}.{type(value).__qualname__}"]
    if isinstance(value, dict):
        return [leaf for key, item in value.items() for leaf in _non_json_leaves(item, f"{path}[{key!r}]")]
    if isinstance(value, list):
        return [leaf for i, item in enumerate(value) for leaf in _non_json_leaves(item, f"{path}[{i}]")]
    return []


def test_parsed_decision_holds_only_plain_json_types():
    """#498: the decision is stored in the job's checkpoint. A JobKind member
    there is an embry0 class that LangGraph's msgpack serde revives by import
    path: since langgraph-checkpoint 4 every load logs "Deserializing
    unregistered type", and its strict mode blocks it. The value is the same
    string either way; only the type changes."""
    decision = parse_triage_response(
        json.dumps(
            {
                "action": "proceed",
                "confidence": 0.9,
                "job_kind": "research",
                "pipeline_config": {"budget_usd": 3, "mcp_servers": {"docs": {"args": [1, None]}}},
                "questions": [{"id": "q-1", "question": "Which branch?"}],
            }
        )
    )
    assert decision["job_kind"] == "research"
    assert _non_json_leaves(decision) == [], "the triage decision must hold only plain JSON types"


def test_parse_triage_response_tolerates_trailing_prose():
    """RAV-1155: decision JSON followed by extra prose must still parse."""
    raw = '{"action": "proceed", "confidence": 0.9}\nNote: this task will also require updating the docs.'
    decision = parse_triage_response(raw)
    assert decision["action"] == "proceed"


def test_parse_triage_response_tolerates_fenced_block_with_trailing_prose():
    """Fenced payload followed by prose after the closing fence."""
    raw = '```json\n{"action": "proceed", "confidence": 0.9}\n```\nLet me know if you\'d like anything adjusted.'
    decision = parse_triage_response(raw)
    assert decision["action"] == "proceed"


def test_parse_triage_response_tolerates_leading_prose_before_fence():
    """Prose before the fence, not just after (old code only stripped a
    fence at the very start of the string)."""
    raw = 'Here is my decision:\n```json\n{"action": "proceed", "confidence": 0.9}\n```'
    decision = parse_triage_response(raw)
    assert decision["action"] == "proceed"


def test_parse_triage_response_preserves_escaped_quotes_in_strings():
    """Brace-depth scanning must not treat an escaped quote as a string
    terminator. The reasoning value below embeds a literal `{`/`}` pair
    behind escaped quotes — if an escaped quote were mistaken for the
    real string terminator, that inner `}` would be counted as closing
    the outer object early and truncate the payload."""
    raw = (
        '{"action": "proceed", "confidence": 0.9, '
        '"reasoning": "looked like \\"{\\"ok\\": true}\\" to me"}\n'
        "trailing prose"
    )
    decision = parse_triage_response(raw)
    assert decision["reasoning"] == 'looked like "{"ok": true}" to me'


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

    # RAV-1152: triage self-routes — successful triage returns a Command
    # whose goto is plan_route; the state update rides .update.
    from langgraph.types import Command

    assert isinstance(result, Command)
    assert result.goto == "plan_route"
    result = result.update

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


# ---- RAV-1058: apply_job_config_overrides ----------------------------------


def test_apply_job_config_overrides_budget_wins():
    from embry0.orchestration.nodes.triage import apply_job_config_overrides

    cfg = apply_job_config_overrides(
        {"budget_usd": 10.0, "sandbox_profile": "default"},
        {"max_budget_usd": 42.5},
    )
    assert cfg["budget_usd"] == 42.5
    assert cfg["sandbox_profile"] == "default"  # other keys untouched


def test_apply_job_config_overrides_noop_without_user_cap():
    from embry0.orchestration.nodes.triage import apply_job_config_overrides

    original = {"budget_usd": 10.0}
    cfg = apply_job_config_overrides(original, {})
    assert cfg == {"budget_usd": 10.0}


def test_parse_triage_response_drops_unknown_extra_fields():
    """RAV-1250 regression (job-ddd3c5913b47): triage volunteering an
    advisory extra key must not kill the job — unknown top-level keys are
    dropped with a warning, and the rest of the decision parses."""
    raw = json.dumps(
        {
            "action": "proceed",
            "confidence": 0.9,
            "reasoning_note": "no duplicate PR check applicable, no repo provided",
        }
    )
    decision = parse_triage_response(raw)
    assert decision["action"] == "proceed"
    assert "reasoning_note" not in decision


def test_parse_triage_response_known_field_typos_still_fail():
    """The filter only rescues UNKNOWN keys — a known field with a bad value
    still raises (strictness on declared fields is unchanged)."""
    from embry0.orchestration.state import TriageParseError

    with pytest.raises(TriageParseError):
        parse_triage_response('{"action": "proceed", "confidence": "not-a-number"}')
