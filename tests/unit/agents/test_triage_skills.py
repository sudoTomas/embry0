"""Triage's agent_skills only narrows the skills the operator gave an agent.

The operator's list is the definition's, with the template step's delta over
it; empty means no filter (the repository's own skills, no source's). Triage
may take skills away, never add a skill or a source: a source's plugin, and its
SessionStart hook, load only for a skill the operator listed. The one
exception is the skill choice the triage prompt offers the developer, from a
source the developer's list already loads. A request that leaves nothing hides
every skill.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog

from embry0.agents.config_builder import build_sdk_options
from embry0.agents.resolver import resolve_agent_config, resolve_agent_invocation
from embry0.orchestration.nodes.triage import parse_triage_response

OPERATOR = ["sp:a", "sp:b"]


def _definition(skills: list[str]) -> dict[str, Any]:
    return {"model": "claude-sonnet-4-6", "tools": ["Read", "Bash"], "skills": skills, "system_prompt": ""}


def _skills(agent_type: str, operator: list[str], request: Any) -> tuple[list[str], bool]:
    pipeline_config = {"agent_skills": {agent_type: request}}
    try:
        resolved = resolve_agent_config(agent_type, _definition(operator), pipeline_config=pipeline_config)
    except ValueError as exc:
        pytest.fail(f"triage's {request!r} failed the run instead of narrowing: {exc}")
    return resolved.skills, getattr(resolved, "skills_hidden", False)


@pytest.mark.parametrize(
    ("request_", "expected"),
    [
        ({"add": ["evil-repo-skill", "other:b"]}, ["sp:a", "sp:b"]),
        (["sp:b", "other:c"], ["sp:b"]),
        ({"remove": ["sp:a"], "add": ["other:c"]}, ["sp:b"]),
        ({"remove": "*", "add": ["sp:a", "repo-skill"]}, ["sp:a"]),
    ],
    ids=["adds", "replace", "remove-and-add", "remove-all-and-add"],
)
def test_triage_never_adds_a_skill_to_an_operators_list(request_: Any, expected: list[str]) -> None:
    skills, hidden = _skills("review", OPERATOR, request_)
    assert skills == expected and not hidden, f"triage's {request_!r} left {skills} (hidden={hidden})"


@pytest.mark.parametrize("request_", [[], {"remove": "*"}, {"remove": ["sp:a", "sp:b"]}, {"add": "not-a-list"}])
def test_a_request_that_keeps_nothing_hides_every_skill(request_: Any) -> None:
    skills, hidden = _skills("review", OPERATOR, request_)
    assert (skills, hidden) == ([], True), f"triage's {request_!r} must leave no skill at all, got {skills}"


def test_no_filter_stays_no_filter_when_triage_adds_a_source() -> None:
    skills, hidden = _skills("review", [], {"add": ["other:b", "repo-skill"]})
    assert (skills, hidden) == ([], False), f"an operator's empty list became {skills} (hidden={hidden})"


def test_triage_narrows_no_filter_to_repository_skills_only() -> None:
    skills, hidden = _skills("review", [], ["repo-skill", "other:b"])
    assert (skills, hidden) == (["repo-skill"], False), f"narrowing no filter gave {skills}"


@pytest.mark.parametrize("request_", [[], {"remove": "*"}])
def test_triage_can_hide_every_skill_of_an_agent_with_no_filter(request_: Any) -> None:
    skills, hidden = _skills("review", [], request_)
    assert (skills, hidden) == ([], True), f"triage's {request_!r} must hide every skill, got hidden={hidden}"


def test_a_refused_skill_is_logged() -> None:
    with structlog.testing.capture_logs() as logs:
        _skills("review", OPERATOR, {"add": ["other:b"]})
    refused = [e for e in logs if e["event"] == "triage_skills_not_granted"]
    assert refused and refused[0]["skills"] == ["other:b"], f"a refused skill must be logged: {logs}"


# ---- the developer's skill choice, which the triage prompt offers ----------------


def test_triage_still_adds_its_skill_choice_to_the_developer() -> None:
    skills, _ = _skills("developer", ["superpowers:test-driven-development"], {"add": ["superpowers:brainstorming"]})
    assert skills == ["superpowers:test-driven-development", "superpowers:brainstorming"], skills


def test_the_skill_choice_never_brings_a_source_the_developer_does_not_load() -> None:
    skills, _ = _skills("developer", ["local:house-style"], {"add": ["superpowers:brainstorming"]})
    assert skills == ["local:house-style"], f"triage's choice loaded a new source: {skills}"


def test_the_skill_choice_is_the_developers_only() -> None:
    skills, _ = _skills("review", ["superpowers:test-driven-development"], {"add": ["superpowers:brainstorming"]})
    assert skills == ["superpowers:test-driven-development"], f"another agent took the developer's choice: {skills}"


def test_the_triage_prompt_offers_exactly_the_choices_it_may_add() -> None:
    import embry0.agents.skills as skills_module
    from embry0.orchestration.nodes.triage import build_triage_system_prompt
    from embry0.settings.sections.done_when import DONE_WHEN

    sections = DONE_WHEN.defaults().ticket_sections
    prompt = build_triage_system_prompt(default_model="m", tier_models={}, ticket_sections=sections)
    choices = getattr(skills_module, "TRIAGE_SKILL_CHOICES", {}).get("developer", frozenset())
    assert choices == {"superpowers:brainstorming", "superpowers:writing-plans"}, f"the developer's choices: {choices}"
    for name in choices:
        assert f"`{name}`" in prompt, f"the prompt no longer offers {name}"


# ---- what reaches the sandbox and the CLI --------------------------------------------


def _invocation(definition: dict[str, Any], pipeline_config: dict[str, Any]) -> Any:
    return resolve_agent_invocation(
        agent_type="review",
        prompt="do it",
        system_context="",
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        repo_prefs=None,
        job_overrides=None,
        agent_definition=definition,
        pipeline_config=pipeline_config,
        max_turns=10,
        timeout_seconds=60,
        credentials={"api_key": "", "oauth_token": "oat-000000000001"},
    )


def _triage(**pipeline_config: Any) -> dict[str, Any]:
    decision = parse_triage_response(
        json.dumps({"action": "proceed", "confidence": 0.9, "pipeline_config": pipeline_config})
    )
    return dict(decision["pipeline_config"])


def _mounts() -> tuple[dict[str, str], ...]:
    return (
        {"source_id": "s1", "plugin_name": "sp", "container_path": "/opt/skills/sp"},
        {"source_id": "s2", "plugin_name": "other", "container_path": "/opt/skills/other"},
    )


def test_another_sources_plugin_never_loads_from_a_triage_request() -> None:
    import dataclasses

    inv = _invocation(_definition(OPERATOR), _triage(agent_skills={"review": {"add": ["other:b"]}}))
    options = build_sdk_options(dataclasses.replace(inv, skill_mounts=_mounts()))
    paths = [p["path"] for p in options.plugins]
    assert paths == ["/opt/skills/sp"], f"a triage request loaded {paths}"


def test_hidden_skills_reach_the_cli_as_no_skill_and_no_plugin() -> None:
    import dataclasses

    inv = _invocation(_definition(OPERATOR), _triage(agent_skills={"review": []}))
    options = build_sdk_options(dataclasses.replace(inv, skill_mounts=_mounts()))
    assert options.skills == [] and options.plugins == [], f"skills={options.skills} plugins={options.plugins}"


def test_the_sandbox_reads_hidden_skills_from_its_config() -> None:
    from embry0.sandbox.runner import _invocation_from_config

    inv = _invocation_from_config({"prompt": "p", "agent_type": "review", "skills": [], "skills_hidden": True})
    assert build_sdk_options(inv).skills == [], "a runner must hide every skill when its config says so"
    plain = _invocation_from_config({"prompt": "p", "agent_type": "review", "skills": []})
    assert build_sdk_options(plain).skills is None, "an empty list without the flag stays no filter"


async def test_hidden_skills_are_sent_to_the_sandbox() -> None:
    from embry0.execution.agent_runner import AgentOutput
    from embry0.orchestration.nodes.agent import run_agent_node

    shipped: dict[str, Any] = {}

    class _Runner:
        async def run(self, container: str, config: dict[str, Any], *args: Any, **kwargs: Any) -> AgentOutput:
            shipped.update(config)
            return AgentOutput(agent_type="review", is_error=False, output="ok")

    await run_agent_node(
        state={
            "sandbox_container_id": "c1",
            "total_cost_usd": 0.0,
            "pipeline_config": {"agent_skills": {"review": []}},
        },
        agent_runner=_Runner(),
        agent_type="review",
        prompt="do it",
        agent_definition=_definition(OPERATOR),
        credentials={"api_key": "", "oauth_token": "oat-000000000001"},
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
    )
    assert shipped.get("skills_hidden") is True, f"the sandbox was sent {shipped.get('skills')} without hiding them"


def test_hidden_skills_win_over_any_list_the_config_carries() -> None:
    from embry0.sandbox.runner import _invocation_from_config

    cfg = {"prompt": "p", "agent_type": "review", "skills": ["sp:a"], "skills_hidden": True}
    options = build_sdk_options(_invocation_from_config({**cfg, "skill_mounts": list(_mounts())}))
    assert options.skills == [] and options.plugins == [], f"skills={options.skills} plugins={options.plugins}"
