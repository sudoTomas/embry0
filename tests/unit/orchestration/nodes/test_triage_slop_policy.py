"""Triage's "Bug or slop" section says what the job's ``slop_policy`` says (#169).

With the defaults the system prompt is the one ``origin/main`` built, to the
byte (its digest was taken on ``c80ba42e`` with the same arguments). With an
organization's policy it carries that organization's guide and heading, says
what a verdict does to the route, and leans by the meter's own level names;
an operator's text is never read for a slot's marker.
"""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from embry0.orchestration.nodes.triage import build_triage_system_prompt
from embry0.services.job_settings import STATE_KEY
from embry0.settings.sections import CATALOG
from embry0.settings.sections.done_when import DONE_WHEN
from embry0.settings.sections.slop_meter import SLOP_METER

TIERS = {"model_heavy": "tier-heavy", "model_medium": "tier-medium", "model_light": "tier-light"}
SKILLS = ("superpowers:brainstorming", "superpowers:writing-plans")
#: ``sha256`` of the prompt ``build_triage_system_prompt`` built on ``c80ba42e`` (origin/main before the section)
#: with these tiers, the default ticket headings and these skills; and with one default model instead.
MAIN_PROMPT = "282bde4bc3087d13fc0ee3bb72e37e6710d6ce1705e2185a8540c252ea1cb3d2"
MAIN_PROMPT_ONE_MODEL = "70db086d7eeef80c48f73873c2c593821fdc5e808ab1e5973634699563abd965"
#: The paragraph after the guide on ``c80ba42e``.
MAIN_PARAGRAPH = """\
Fill `work_nature` for every code job, with at most 8 evidence entries naming
files you read. When the task carries a watcher "Slop, not a bug" section,
weigh it, then make your own call. A slop verdict routes the job to the
Slopmaster's cleanup (keep behavior, remove or merge code) whatever template
you name; leave pipeline_template empty for it. The task context may give the
area's Slop-O-Meter reading (how much of its code landed beyond human
attention); when your verdict is borderline and it reads Sloppy Joe or Just
The Slop, lean toward slop."""
DEFAULT_NAMES = ("Buttered Noodles", "Spaghetti Bolognese", "Lasagna", "Sloppy Joe", "Just The Slop")


def _policy(values: dict[str, Any] | None = None) -> Any:
    section = CATALOG.get("slop_policy")
    if section is None:
        pytest.fail("the settings document has no slop_policy section: triage's words about slop are code")
    return section.validate(values or {})


def _prompt(policy: dict[str, Any] | None = None, *, names: tuple[str, ...] = DEFAULT_NAMES, model: str = "") -> str:
    try:
        return build_triage_system_prompt(
            default_model=model,
            tier_models=TIERS,
            ticket_sections=DONE_WHEN.defaults().ticket_sections,
            slop_policy=_policy(policy),
            level_names=names,
            skill_choices=SKILLS,
        )
    except TypeError as exc:
        pytest.fail(f"triage's prompt is built from no slop_policy: {exc}")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _flat(text: str) -> str:
    """The words of ``text``, wrapped lines joined: the paragraph is wrapped wherever its words fall."""
    return " ".join(text.split())


def test_the_default_prompt_is_the_one_triage_was_given_before_the_policy() -> None:
    assert SLOP_METER.defaults().level_names == DEFAULT_NAMES
    prompt = _prompt()
    assert MAIN_PARAGRAPH in prompt
    assert _digest(prompt) == MAIN_PROMPT, "the default triage prompt differs from the one before #169"
    assert _digest(_prompt(model="one-model")) == MAIN_PROMPT_ONE_MODEL


def test_the_prompt_says_what_the_organization_s_policy_says() -> None:
    prompt = _prompt(
        {
            "classifier_guide": "Our slop is code that no test reaches.\nKinds: untested.",
            "kinds": ["untested"],
            "ticket_heading": "Shape, not behaviour",
            "min_score_to_route": 60,
        }
    )
    assert "Our slop is code that no test reaches.\nKinds: untested." in prompt
    assert "Tell slop apart from bugs" not in prompt, "the organization's guide replaces the default"
    assert 'watcher "Shape, not behaviour" section' in _flat(prompt)
    assert "A slop verdict with a slop_score of 60 or more routes the job" in _flat(prompt)
    assert "Below that score, pick its template as for a bug." in _flat(prompt)


def test_triage_is_told_the_route_its_policy_names() -> None:
    """Only the built-in cleanup, whose graph is seeded at every start, is said to be the Slopmaster's."""
    prompt = _flat(_prompt({"route_template": "tidy-up"}))
    assert "routes the job to the cleanup pipeline `tidy-up` (keep behavior, remove or merge code)" in prompt, (
        "triage is told every slop route is the Slopmaster's cleanup"
    )
    assert "Slopmaster's cleanup" not in prompt, "a route of the operator's own may be coded by the developer"


def test_with_routing_off_triage_is_not_told_the_verdict_routes() -> None:
    prompt = _flat(_prompt({"enabled": False}))
    assert "routes the job to the" not in prompt
    assert "does not change the job's route here" in prompt


def test_triage_leans_from_the_policy_s_level_by_the_meter_s_names() -> None:
    names = ("Clean", "Fine", "Mixed", "Sloppy", "Slop")
    assert "it reads Mixed, Sloppy or Slop, lean toward slop" in _flat(
        _prompt({"triage_lean_min_level": 3}, names=names)
    )
    assert "it reads Slop, lean toward slop" in _flat(_prompt({"triage_lean_min_level": 5}, names=names))
    never = _flat(_prompt({"triage_lean_min_level": None}))
    assert "lean toward slop" not in never
    assert "landed beyond human attention)." in never, "the reading is still explained"


def test_an_operator_s_text_is_never_read_for_a_slot() -> None:
    prompt = _prompt({"classifier_guide": "Keep <<MODEL_GUIDANCE>> and <<DONE_WHEN_GUIDE>> as written."})
    assert "Keep <<MODEL_GUIDANCE>> and <<DONE_WHEN_GUIDE>> as written." in prompt
    assert prompt.count("Model selection (CRITICAL)") == 1


@pytest.mark.asyncio
async def test_triage_is_built_from_the_policy_its_job_read() -> None:
    """The node passes the job's own slop_policy (job_settings) and the meter's names for its repository."""
    from embry0.workflows.issue_to_pr.nodes import triage_node
    from tests.unit.workflows.test_policy_efficiency import TRIAGE_STATE, _sessions_repo, _triage_result

    captured: dict[str, Any] = {}

    async def _run(*args: Any, **kwargs: Any) -> Any:
        captured["agent_definition"] = kwargs.get("agent_definition")
        return _triage_result()

    policy = _policy({"classifier_guide": "Acme calls dead flags slop.", "ticket_heading": "Shape"})
    state = {**TRIAGE_STATE, STATE_KEY: {"slop_policy": policy.model_dump(mode="json")}}
    asked: list[str] = []

    async def acme_names(config: Any, repo: str) -> tuple[str, ...]:
        asked.append(repo)
        return ("Clean", "Fine", "Mixed", "Sloppy", "Slop")

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch("embry0.services.slop_meter.context.level_names", new=acme_names, create=True),
    ):
        await triage_node(
            state,
            {
                "configurable": {
                    "agent_runner": object(),
                    "credentials": {},
                    "agent_sessions_repo": _sessions_repo(None),
                }
            },
        )
    system = captured["agent_definition"]["system_prompt"]
    assert "Acme calls dead flags slop." in system, "triage is told the guide its job read"
    assert 'watcher "Shape" section' in _flat(system)
    assert "Tell slop apart from bugs" not in system
    assert asked == [TRIAGE_STATE["repo"]], "the meter's level names are read for the job's repository"
    assert "it reads Sloppy or Slop, lean toward slop" in _flat(system), "triage leans by the meter's own names"
