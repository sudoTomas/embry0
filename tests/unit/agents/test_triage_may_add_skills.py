"""The skills an agent row lets triage add (``agent_definitions.triage_may_add_skills``).

The operator names them per agent. Triage may add one only from a source the
agent's own list already loads, so the column never makes triage load a new
source, its plugin or its hooks. The developer's row decides what the triage
prompt offers: none leaves the "Skill selection for the developer" section out.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from embry0.agents.resolver import resolve_agent_config

CHOICES = ["superpowers:brainstorming", "superpowers:writing-plans"]


def _row(skills: list[str], may_add: Any = None) -> dict[str, Any]:
    row: dict[str, Any] = {"model": "m", "tools": ["Read", "Bash"], "skills": skills, "system_prompt": ""}
    if may_add is not None:
        row["triage_may_add_skills"] = may_add
    return row


def _resolved_skills(agent_type: str, row: dict[str, Any], request: Any) -> list[str]:
    pipeline_config = {"agent_skills": {agent_type: request}}
    return resolve_agent_config(agent_type, row, pipeline_config=pipeline_config).skills


# ---- the resolver reads the target agent's row ----------------------------------------


def test_triage_adds_a_skill_the_row_lets_it_add() -> None:
    row = _row(["superpowers:test-driven-development"], ["superpowers:brainstorming"])
    skills = _resolved_skills("review", row, {"add": ["superpowers:brainstorming"]})
    assert skills == ["superpowers:test-driven-development", "superpowers:brainstorming"], (
        f"a skill the row lets triage add was refused: {skills}"
    )


def test_a_row_that_names_nothing_lets_triage_add_nothing() -> None:
    row = _row(["superpowers:test-driven-development"])
    skills = _resolved_skills("developer", row, {"add": ["superpowers:brainstorming"]})
    assert skills == ["superpowers:test-driven-development"], f"a developer row with no column took {skills}"


def test_the_column_never_brings_a_source_the_agent_does_not_load() -> None:
    row = _row(["local:house-style"], CHOICES)
    with structlog.testing.capture_logs() as logs:
        skills = _resolved_skills("developer", row, {"add": ["superpowers:brainstorming"]})
    assert skills == ["local:house-style"], f"the column loaded a new source: {skills}"
    refused = [e for e in logs if e["event"] == "triage_skills_not_granted"]
    assert refused and refused[0]["skills"] == ["superpowers:brainstorming"], f"the refusal must be logged: {logs}"


def test_a_skill_the_row_does_not_name_is_still_refused() -> None:
    row = _row(["superpowers:test-driven-development"], ["superpowers:brainstorming"])
    skills = _resolved_skills("developer", row, {"add": ["superpowers:writing-plans"]})
    assert skills == ["superpowers:test-driven-development"], f"triage added a skill the row does not name: {skills}"


@pytest.mark.parametrize(
    "stored",
    [
        "superpowers:brainstorming",
        ["Superpowers Brainstorming"],
        {"a": 1},
        ["superpowers:brainstorming", "not a source:skill name"],
    ],
    ids=["a-string", "bad-shape", "a-dict", "one-bad-entry"],
)
def test_a_malformed_column_lets_triage_add_nothing(stored: Any) -> None:
    row = _row(["superpowers:test-driven-development"], stored)
    skills = _resolved_skills("developer", row, {"add": ["superpowers:brainstorming"]})
    assert skills == ["superpowers:test-driven-development"], f"a malformed column {stored!r} let triage add {skills}"


def test_the_developer_seed_lets_triage_add_the_two_design_skills() -> None:
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    seed = BUILTIN_SEED["developer"]
    assert seed.get("triage_may_add_skills") == CHOICES, f"the developer seed: {seed.get('triage_may_add_skills')}"
    others = {t: s.get("triage_may_add_skills") for t, s in BUILTIN_SEED.items() if t != "developer"}
    assert all(v == [] for v in others.values()), f"every other built-in lets triage add none: {others}"
    skills = _resolved_skills("developer", dict(seed), {"add": ["superpowers:brainstorming"]})
    assert skills[-1] == "superpowers:brainstorming", f"the seeded developer did not take the choice: {skills}"


# ---- the triage prompt ------------------------------------------------------------------

# The section as the prompt has always carried it: the developer's seed reproduces it word for word.
DESIGN_SECTION = """Skill selection for the developer (CRITICAL):
Decide whether the issue needs an upfront DESIGN pass or can jump straight to
implementation, then attach exactly ONE of these two skills to the developer:

- `superpowers:brainstorming` — use when the issue is OPEN-ENDED or has
  meaningful design choices: new features, multi-file refactors, anything
  with a "should we…" or "how should this work" component. The skill walks
  the developer through clarifying questions → approach options →
  agreed-on design → spec doc → then writing-plans → then implementation.

- `superpowers:writing-plans` — use when the issue is a CLEAR
  implementation task with no real design decisions: bug fixes with a
  diagnosed root cause, well-scoped enhancements, asset additions, config
  edits, dependency bumps, single-file changes you can describe in one
  sentence. The skill goes straight to writing a step-by-step
  implementation plan, then to executing it.

Set the chosen skill as an ADDITION in `pipeline_config.agent_skills` (the
developer keeps its configured skills; you add one):
  {"developer": {"add": ["superpowers:brainstorming"]}}     # or
  {"developer": {"add": ["superpowers:writing-plans"]}}

Defaults if you can't decide: prefer brainstorming. Better to spend a few
minutes designing than to ship the wrong thing.
"""


def _prompt(skill_choices: list[str] | None) -> str:
    import inspect

    from embry0.orchestration.nodes.triage import build_triage_system_prompt
    from embry0.settings.sections.done_when import DONE_WHEN

    if skill_choices is not None:
        params = inspect.signature(build_triage_system_prompt).parameters
        assert "skill_choices" in params, "build_triage_system_prompt takes no skill_choices: it cannot follow the row"
    from embry0.settings.sections.slop_meter import SLOP_METER
    from embry0.settings.sections.slop_policy import SLOP_POLICY

    kwargs: dict[str, Any] = {"default_model": "m", "tier_models": {}}
    kwargs["ticket_sections"] = DONE_WHEN.defaults().ticket_sections
    kwargs["slop_policy"] = SLOP_POLICY.defaults()
    kwargs["level_names"] = SLOP_METER.defaults().level_names
    if skill_choices is not None:
        kwargs["skill_choices"] = skill_choices
    return build_triage_system_prompt(**kwargs)


def test_the_prompt_offers_the_developers_two_skills_as_it_always_has() -> None:
    assert DESIGN_SECTION in _prompt(CHOICES), "the developer's seed must give the prompt its old section"


@pytest.mark.parametrize("choices", [None, []], ids=["unset", "empty"])
def test_a_developer_that_lets_triage_add_nothing_leaves_the_section_out(choices: list[str] | None) -> None:
    prompt = _prompt(choices)
    assert "Skill selection for the developer" not in prompt, "triage is told to add a skill it would be refused"
    assert "superpowers:brainstorming" not in prompt and "<<" not in prompt, "a skill or a slot is left in the prompt"


def test_the_prompt_lists_only_the_allowed_names() -> None:
    prompt = _prompt(["superpowers:writing-plans", "local:house-style"])
    assert "Skill selection for the developer (CRITICAL):" in prompt, "the section is missing"
    assert "`superpowers:writing-plans` — use when the issue is a CLEAR" in prompt, "a known skill keeps its guidance"
    assert "- `local:house-style` — a skill this deployment lets you add" in prompt, "another skill gets a line"
    assert '{"developer": {"add": ["local:house-style"]}}' in prompt, "the example names the allowed skill"
    assert "brainstorming" not in prompt, f"a skill the row does not allow is offered: {prompt.count('brainstorming')}"


async def test_the_triage_node_offers_what_the_developer_row_allows() -> None:
    """The node reads the developer's row: only a skill from a source that row loads is offered."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from embry0.workflows.issue_to_pr.nodes import triage_node

    rows = {
        "developer": _row(["superpowers:test-driven-development"], ["superpowers:writing-plans", "local:x"]),
        "triage": None,
    }
    run = AsyncMock(return_value={"agent_outputs": [], "total_cost_usd": 0.0})
    state = {"job_id": "j", "repo": "acme/widgets", "task": "t", "sandbox_container_id": "c", "agent_outputs": []}
    config = {"configurable": {"agent_runner": MagicMock(), "credentials": {}}}
    with (
        patch(
            "embry0.orchestration.nodes.agent.load_agent_definition_or_none",
            AsyncMock(side_effect=lambda _config, agent_type: rows.get(agent_type)),
        ),
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        await triage_node(state, config)
    prompt = run.call_args.kwargs["agent_definition"]["system_prompt"]
    assert "`superpowers:writing-plans`" in prompt, "the row's skill from a loaded source must be offered"
    assert "local:x" not in prompt and "brainstorming" not in prompt, "a skill triage would be refused is offered"
