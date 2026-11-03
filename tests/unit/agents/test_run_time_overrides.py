"""What a job's run-time config can change about an agent, and what an empty tool list means.

Triage is a model reading the ticket. Its ``agent_tools`` only narrows the
tools the operator gave an agent, and its ``system_prompts`` is ignored. Tools
that are not named (None) are the agent type's defaults; an empty list is no
tools, and a list a run-time change empties keeps the read-only tools.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog

from embry0.agents.resolver import resolve_agent_config, resolve_agent_invocation
from embry0.execution.agent_runner import AgentOutput
from embry0.orchestration.nodes.agent import run_agent_node
from embry0.orchestration.nodes.triage import parse_triage_response

# Literal, so a test fails on its own assertion on a tree without these names.
DEVELOPER_DEFAULTS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
READ_ONLY = ["Read", "Glob", "Grep"]  # the type defaults of triage
TYPE_DEFAULTS = {"triage": READ_ONLY, "custom-agent": DEVELOPER_DEFAULTS}


def _definition(tools: Any = None, *, system_prompt: str = "the operator's prompt") -> dict[str, Any]:
    definition: dict[str, Any] = {"model": "claude-sonnet-4-6", "skills": [], "system_prompt": system_prompt}
    if tools is not None:
        definition["tools"] = tools
    return definition


def _triage(**pipeline_config: Any) -> dict[str, Any]:
    """The pipeline_config of a decision as triage_node stores it on state."""
    decision = parse_triage_response(
        json.dumps({"action": "proceed", "confidence": 0.9, "pipeline_config": pipeline_config})
    )
    return dict(decision["pipeline_config"])


def _tools(agent_type: str, definition: dict[str, Any], **kwargs: Any) -> list[str]:
    return resolve_agent_config(agent_type, definition, **kwargs).tools


class _Runner:
    def __init__(self) -> None:
        self.config: dict[str, Any] = {}

    async def run(self, container: str, config: dict[str, Any], *args: Any, **kwargs: Any) -> AgentOutput:
        self.config = dict(config)
        return AgentOutput(agent_type=str(config.get("agent_type")), is_error=False, output="ok")


async def _shipped(
    agent_type: str, definition: dict[str, Any] | None, pipeline_config: dict[str, Any]
) -> dict[str, Any]:
    """The config run_agent_node sends the sandbox."""
    runner = _Runner()
    await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": pipeline_config},
        agent_runner=runner,
        agent_type=agent_type,
        prompt="do it",
        agent_definition=definition,
        credentials={"api_key": "", "oauth_token": "oat-000000000001"},
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
    )
    assert runner.config, "the runner was never called"
    return runner.config


# ---- triage's agent_tools only narrows --------------------------------------------


def test_triage_cannot_add_bash_to_an_agent_without_it() -> None:
    tools = _tools(
        "review",
        _definition(["Read", "Glob", "Grep"]),
        pipeline_config=_triage(agent_tools={"review": ["Read", "Bash"]}),
    )
    assert tools == ["Read"], f"triage gave the agent {tools}"


def test_triage_cannot_add_a_tool_the_template_step_lacks() -> None:
    template = {"agent_tools": {"review": ["Read", "Bash"]}}
    tools = _tools(
        "review",
        _definition(["Read"]),
        template_config=template,
        pipeline_config=_triage(agent_tools={"review": ["Bash", "Write"]}),
    )
    assert tools == ["Bash"], f"triage widened the template step's tools to {tools}"


def test_triage_narrows_within_the_type_defaults_when_no_tools_are_named() -> None:
    tools = _tools(
        "custom-agent", _definition(), pipeline_config=_triage(agent_tools={"custom-agent": ["Bash", "WebFetch"]})
    )
    assert tools == ["Bash"], f"triage widened the type's defaults to {tools}"


def test_triage_cannot_give_tools_to_an_agent_that_has_none() -> None:
    tools = _tools("custom-agent", _definition([]), pipeline_config=_triage(agent_tools={"custom-agent": ["Bash"]}))
    assert tools == [], f"triage gave a toolless agent {tools}"


@pytest.mark.parametrize("asked", [["WebFetch"], []], ids=["only-new-tools", "empty"])
def test_a_list_triage_empties_keeps_the_read_only_tools_it_had(asked: list[str]) -> None:
    tools = _tools(
        "review", _definition(["Read", "Bash", "Write"]), pipeline_config=_triage(agent_tools={"review": asked})
    )
    assert tools == ["Read"], f"a list triage emptied became {tools}"


@pytest.mark.parametrize("asked", [[], ["Bash"]], ids=["empty", "only-new-tools"])
def test_the_fallback_never_adds_a_read_only_tool_the_agent_lacked(asked: list[str]) -> None:
    tools = _tools("review", _definition(["WebFetch"]), pipeline_config={"agent_tools": {"review": asked}})
    assert tools == [], f"a WebFetch-only agent narrowed to nothing was given {tools}"


@pytest.mark.parametrize("asked", ["bash", '["Bash"]', {"Bash": 1}], ids=["bare-string", "json-string", "dict"])
def test_a_malformed_triage_request_narrows_like_an_empty_one(asked: Any) -> None:
    with structlog.testing.capture_logs() as logs:
        tools = _tools(
            "review", _definition(["Read", "Grep", "Bash"]), pipeline_config={"agent_tools": {"review": asked}}
        )
    assert tools == ["Read", "Grep"], f"a malformed request {asked!r} gave {tools}"
    assert any(e["event"] == "triage_tools_malformed" for e in logs), "a malformed request must be logged"


def test_triage_still_narrows_an_agents_tools() -> None:
    tools = _tools(
        "developer",
        _definition(DEVELOPER_DEFAULTS),
        pipeline_config=_triage(agent_tools={"developer": ["Grep", "Read"]}),
    )
    assert tools == ["Read", "Grep"], f"narrowing gave {tools}"


def test_a_template_step_still_replaces_the_definitions_tools() -> None:
    tools = _tools("review", _definition(["Read"]), template_config={"agent_tools": {"review": ["Read", "Bash"]}})
    assert tools == ["Read", "Bash"], f"the operator's template step gave {tools}"


def test_a_tool_triage_is_not_granted_is_logged() -> None:
    with structlog.testing.capture_logs() as logs:
        _tools("review", _definition(["Read"]), pipeline_config=_triage(agent_tools={"review": ["Read", "Bash"]}))
    refused = [e for e in logs if e["event"] == "triage_tools_not_granted"]
    assert refused, f"a tool triage was refused must be logged: {[e['event'] for e in logs]}"
    assert refused[0]["tools"] == ["Bash"] and refused[0]["agent_type"] == "review", refused[0]


async def test_a_triage_decision_never_adds_bash_to_what_the_sandbox_is_sent() -> None:
    shipped = await _shipped(
        "review", _definition(["Read", "Glob", "Grep"]), _triage(agent_tools={"review": ["Read", "Bash"]})
    )
    assert "Bash" not in shipped["tools"], f"the sandbox was sent {shipped['tools']}"


# ---- triage's system_prompts is ignored ---------------------------------------------


def _invocation(pipeline_config: dict[str, Any]) -> Any:
    return resolve_agent_invocation(
        agent_type="developer",
        prompt="do it",
        system_context="",
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        repo_prefs=None,
        job_overrides=None,
        agent_definition=_definition(DEVELOPER_DEFAULTS),
        pipeline_config=pipeline_config,
        max_turns=10,
        timeout_seconds=60,
        credentials={"api_key": "", "oauth_token": "oat-000000000001"},
    )


async def test_a_triage_system_prompt_is_ignored() -> None:
    shipped = await _shipped(
        "developer", _definition(DEVELOPER_DEFAULTS), _triage(system_prompts={"developer": "other"})
    )
    assert shipped["system_prompt"] == "the operator's prompt", f"triage replaced it: {shipped['system_prompt']!r}"


def test_an_ignored_system_prompt_is_logged_by_agent_name_only() -> None:
    with structlog.testing.capture_logs() as logs:
        _invocation(_triage(system_prompts={"developer": "a prompt-0001 from the decision"}))
    ignored = [e for e in logs if e["event"] == "triage_system_prompt_ignored"]
    assert ignored, f"an ignored system prompt must be logged: {[e['event'] for e in logs]}"
    assert ignored[0]["agent_type"] == "developer", ignored[0]
    assert "prompt-0001" not in json.dumps(ignored[0]), "the log names the agent, never the prompt's text"


# ---- None is the defaults, [] is no tools --------------------------------------------


@pytest.mark.parametrize("agent_type", ["triage", "custom-agent"])
def test_unset_tools_are_the_agent_types_defaults(agent_type: str) -> None:
    tools = _tools(agent_type, _definition())
    assert tools == TYPE_DEFAULTS[agent_type], f"{agent_type} with no tools named got {tools}"


def test_an_empty_tool_list_stays_empty() -> None:
    assert _tools("custom-agent", _definition([])) == [], "an empty list is no tools, never the defaults"


async def test_a_node_naming_no_tools_runs_with_the_type_defaults() -> None:
    shipped = await _shipped("custom-agent", None, {})
    assert shipped["tools"] == DEVELOPER_DEFAULTS, f"a node that named no tools sent {shipped['tools']}"


async def test_an_empty_tool_list_reaches_the_sandbox_empty() -> None:
    shipped = await _shipped("custom-agent", _definition([]), {})
    assert shipped["tools"] == [], f"an empty list was sent as {shipped['tools']}"


@pytest.mark.parametrize("stored", ["not json", {"Bash": True}], ids=["bad-json", "not-a-list"])
def test_a_malformed_tool_list_grants_nothing(stored: Any) -> None:
    tools = _tools("custom-agent", _definition(stored))
    assert tools == [], f"a malformed tool list {stored!r} became {tools}"
