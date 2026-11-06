"""The ``slop_policy`` section (#169): its defaults are what the code held, and a write is refused naming each key.

The defaults are pinned against the values ``origin/main`` held before the
section, copied here as text, never imported from the code that replaced
them: the kinds, the classifier's and the reviewer's words, the route, the
ticket heading and the two Slop-O-Meter levels.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from embry0.settings.document import DocumentInvalid, check
from embry0.settings.registry import Catalog
from embry0.settings.sections import CATALOG
from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES

# ---- what origin/main held (embry0/services/slop.py, slop_meter/context.py at 62e07507) ----

OLD_KINDS = (
    "duplication",
    "dead_code",
    "half_wired",
    "contradicting_layers",
    "symptom_patch",
    "misleading_docs",
    "off_pattern",
)
OLD_TEMPLATE = "slop-cleanup"
OLD_CLASSIFIER_GUIDE = """\
Tell slop apart from bugs:
- bug: the behavior differs from what the code, its docs or the ticket intend.
  Fixing it changes behavior.
- slop: the behavior is what the code says, and the trouble is the code's
  shape. Cleaning it keeps behavior. Kinds: duplication (the same logic in
  more than one place), dead_code (paths, flags or files nothing reaches),
  half_wired (a feature or abstraction started and not finished),
  contradicting_layers (two layers that disagree about one thing),
  symptom_patch (guards, retries, fallbacks or catch-alls stacked on a symptom
  instead of its cause), misleading_docs (comments or docs that say what the
  code does not do), off_pattern (code that ignores the repository's own way).
- noise: nothing to do.
A guard, retry or catch-all stacked on a symptom is slop when removing it and
fixing the shape keeps the outcome. When the code does the wrong thing (keeps
retrying what can never succeed, drops or duplicates data, answers wrongly),
it is a bug, even when sloppy code caused it: say bug, and name the slop in
the evidence. Do not call a real defect slop to make it look smaller.
slop_score (0-100) is how much of the trouble is shape rather than behavior:
0 for a pure bug, 100 for pure slop."""
OLD_REVIEW_GUIDE = """\
## This change is a slop cleanup

It was judged slop, not a bug: it must keep behavior and improve shape.
Approve only when all of these hold, and request changes otherwise, saying which:
- behavior is unchanged: the tests pass, and no public behavior, API or output
  moved (removing what nothing reaches is fine);
- the change removes or merges more than it adds, or is plainly simpler;
- it adds no new guard, retry, fallback, catch-all or flag to hide a symptom."""
#: ``ticket_section`` opened every slop ticket with ``## Slop, not a bug``.
OLD_TICKET_HEADING = "Slop, not a bug"
#: ``METER_DUNGEON_LEVEL`` and the never-read ``LEAN_LEVEL`` (Sloppy Joe, which triage's prompt named).
OLD_DUNGEON_LEVEL = 2
OLD_LEAN_LEVEL = 4


def _section() -> Any:
    section = CATALOG.get("slop_policy")
    assert section is not None, "the settings document has no slop_policy section: slop routing is code"
    return section


def test_the_defaults_are_what_the_code_held_before_the_section() -> None:
    defaults = _section().defaults()
    assert defaults.enabled is True, "every code job judged slop was routed"
    assert defaults.route_template == OLD_TEMPLATE
    assert defaults.min_score_to_route == 0, "a slop verdict of any score was routed"
    assert defaults.kinds == OLD_KINDS
    assert defaults.classifier_guide == OLD_CLASSIFIER_GUIDE, "the classifiers' words, to the byte"
    assert defaults.review_guide == OLD_REVIEW_GUIDE, "the reviewer's words, to the byte"
    assert defaults.ticket_heading == OLD_TICKET_HEADING
    assert defaults.dungeon_min_level == OLD_DUNGEON_LEVEL
    assert defaults.triage_lean_min_level == OLD_LEAN_LEVEL


def test_a_repository_sets_its_route_and_an_organization_everything() -> None:
    section = _section()
    assert section.repo_keys == {"enabled", "route_template", "min_score_to_route"}, (
        "a repository's layer, which a repository-scoped key writes, puts no text into a system prompt"
    )
    assert section.org_keys == set(section.fields)
    assert "xp" not in section.fields, "a cleanup's experience is the progression pack's outcomes.cleaned"


def _problems(raw: Mapping[str, Any], *, layer: str = "deployment", context: Mapping[str, Any] | None = None) -> dict:
    _section()
    try:
        check(
            CATALOG,
            {"slop_policy": dict(raw)},
            stored={},
            layer=layer,
            inherited={} if layer != "deployment" else None,
            context=context,
        )
    except DocumentInvalid as refused:
        return {".".join(map(str, problem.loc)): problem.msg for problem in refused.problems}
    pytest.fail(f"the {layer}'s write of {sorted(raw)} was taken")


def test_a_write_is_refused_naming_every_bad_key() -> None:
    problems = _problems(
        {
            "kinds": [],
            "classifier_guide": "x" * 4001,
            "review_guide": "   ",
            "ticket_heading": "## Slop",
            "route_template": " slop-cleanup",
            "min_score_to_route": 101,
            "triage_lean_min_level": 0,
            "dungeon_min_level": 6,
        }
    )
    assert set(problems) == {
        "slop_policy.kinds",
        "slop_policy.classifier_guide",
        "slop_policy.review_guide",
        "slop_policy.ticket_heading",
        "slop_policy.route_template",
        "slop_policy.min_score_to_route",
        "slop_policy.triage_lean_min_level",
        "slop_policy.dungeon_min_level",
    }, problems
    assert "at least one kind" in problems["slop_policy.kinds"]
    assert "4000" in problems["slop_policy.classifier_guide"]
    assert "without its # marks" in problems["slop_policy.ticket_heading"]


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        ({"kinds": ["Dead-Code"]}, "lowercase letters"),
        ({"kinds": ["dead_code", "dead_code"]}, "each kind once"),
        ({"kinds": [f"k{n}" for n in range(21)]}, "20"),
        ({"classifier_guide": "a\x07b"}, "plain"),
        ({"review_guide": "a‮b"}, "plain"),
        ({"ticket_heading": "two\nlines"}, "one line"),
        ({"ticket_heading": "x" * 81}, "80"),
        ({"route_template": ""}, "1"),
        ({"route_template": "x" * 101}, "100"),
    ],
)
def test_each_rule_names_its_key(raw: dict[str, Any], words: str) -> None:
    (key,) = raw
    problems = _problems(raw)
    assert words in problems[f"slop_policy.{key}"], problems


def test_a_repository_layer_sets_no_words_of_a_prompt() -> None:
    _section()
    problems = _problems(
        {"classifier_guide": "Tell slop apart.", "kinds": ["naming"], "ticket_heading": "Shape"}, layer="repository"
    )
    assert set(problems) == {"slop_policy.classifier_guide", "slop_policy.kinds", "slop_policy.ticket_heading"}
    checked = check(
        CATALOG,
        {"slop_policy": {"enabled": False, "route_template": "quick-fix", "min_score_to_route": 50}},
        stored={},
        layer="repository",
        inherited={},
    )
    assert checked.data["slop_policy"] == {"enabled": False, "route_template": "quick-fix", "min_score_to_route": 50}


def _templates(*, without: str = "") -> dict[str, Any]:
    """The route template check as a write runs it, over the built-in templates and one of the operator's, less
    ``without``."""
    from embry0.settings import write_context

    route_template_checker = getattr(write_context, "route_template_checker", None)
    if route_template_checker is None:
        pytest.fail("a settings write is not checked against the pipeline templates")
    rows = [{"name": name, **seed} for name, seed in BUILTIN_PIPELINE_TEMPLATES.items() if name != without]
    rows.append({"name": "tidy-up", **BUILTIN_PIPELINE_TEMPLATES["quick-fix"], "default_for_kind": None})
    return {"route_templates": route_template_checker(rows)}


@pytest.mark.parametrize(
    ("template", "words"),
    [
        ("nowhere", "no pipeline template named 'nowhere'"),
        ("qa", "no pipeline template named 'qa'"),
        ("research-default", "records its jobs as research jobs"),
        ("kintsugi-trace", "records its jobs as analysis jobs"),
        ("review-only", "has no coding step"),
    ],
)
def test_a_write_names_a_template_that_takes_a_code_job(template: str, words: str) -> None:
    problems = _problems({"route_template": template}, context=_templates())
    assert words in problems["slop_policy.route_template"], problems


@pytest.mark.parametrize("template", ["slop-cleanup", "quick-fix", "full-magnum-opus", "tidy-up"])
def test_a_template_that_takes_a_code_job_is_accepted(template: str) -> None:
    _section()
    checked = check(CATALOG, {"slop_policy": {"route_template": template}}, stored={}, context=_templates())
    assert checked.data["slop_policy"]["route_template"] == template


def test_only_a_template_the_write_changes_is_checked() -> None:
    """A template stored before (and since gone) never blocks a write of another key."""
    _section()
    stored = {"slop_policy": {"route_template": "gone"}}
    checked = check(
        CATALOG,
        {"slop_policy": {"route_template": "gone", "min_score_to_route": 40}},
        stored=stored,
        context=_templates(),
    )
    assert checked.data["slop_policy"] == {"route_template": "gone", "min_score_to_route": 40}
    problems = _problems({"route_template": "gone"}, context=_templates())
    assert "no pipeline template named 'gone'" in problems["slop_policy.route_template"]


def test_offline_a_template_is_checked_for_its_shape_alone() -> None:
    """``embry0 settings check`` has no deployment: a name it cannot know is left to the write."""
    checked = check(Catalog([_section()]), {"slop_policy": {"route_template": "nowhere"}}, stored={})
    assert checked.data["slop_policy"] == {"route_template": "nowhere"}


@pytest.mark.parametrize(
    "write",
    [{"slop_policy": {"enabled": True}}, {"slop_policy": None}, {}],
    ids=["key-taken-out", "section-null", "section-left-out"],
)
def test_taking_route_template_out_checks_the_template_its_readers_then_route_to(write: dict[str, Any]) -> None:
    """The deployment routes to tidy-up and slop-cleanup is gone: a write that leaves the default is refused."""
    _section()
    stored = {"slop_policy": {"route_template": "tidy-up"}}
    try:
        check(CATALOG, write, stored=stored, context=_templates(without="slop-cleanup"))
    except DocumentInvalid as refused:
        named = {".".join(map(str, problem.loc)): problem.msg for problem in refused.problems}
    else:
        pytest.fail(f"{write} left the deployment routing to the default slop-cleanup, which is gone")
    assert "no pipeline template named 'slop-cleanup'" in named.get("slop_policy.route_template", ""), (
        f"the write left the default slop-cleanup, which is gone, unchecked: {named}"
    )


def test_a_layer_that_takes_route_template_out_is_checked_for_what_it_inherits() -> None:
    _section()
    stored = {"slop_policy": {"route_template": "tidy-up"}}
    try:
        check(
            CATALOG,
            {"slop_policy": {"min_score_to_route": 10}},
            stored=stored,
            layer="organization",
            inherited={"slop_policy": {}},
            context=_templates(without="slop-cleanup"),
        )
    except DocumentInvalid as refused:
        problems = [(p.loc, p.type) for p in refused.problems]
    else:
        pytest.fail("the organization's layer took route_template out and now routes to slop-cleanup, which is gone")
    assert problems == [(("slop_policy", "route_template"), "unavailable")], problems
    checked = check(
        CATALOG,
        {"slop_policy": {"min_score_to_route": 10}},
        stored=stored,
        layer="organization",
        inherited={"slop_policy": {"route_template": "quick-fix"}},
        context=_templates(without="slop-cleanup"),
    )
    assert checked.data["slop_policy"] == {"min_score_to_route": 10}, "it inherits quick-fix, which is there"


def test_a_write_that_leaves_the_route_as_it_was_is_taken_while_its_template_is_missing() -> None:
    """slop-cleanup is gone and nothing names another: a write of another key, of this section or another, goes
    through (jobs take their kind's default meanwhile)."""
    _section()
    for write in ({"slop_policy": {"min_score_to_route": 40}}, {"done_when": {"retries": 2}}):
        try:
            checked = check(CATALOG, write, stored={}, context=_templates(without="slop-cleanup"))
        except DocumentInvalid as refused:
            pytest.fail(f"{write} was refused for a template it does not change: {refused}")
        assert checked.data == write


def test_naming_the_default_it_already_routes_to_is_taken_while_that_template_is_missing() -> None:
    """A document that routes to the default writes it out explicitly: its readers' template does not change, so
    the write is not judged by it, as a write of another key is not."""
    _section()
    try:
        checked = check(
            CATALOG,
            {"slop_policy": {"route_template": "slop-cleanup"}},
            stored={},
            context=_templates(without="slop-cleanup"),
        )
    except DocumentInvalid as refused:
        pytest.fail(f"writing out the default its readers already route to was refused: {refused}")
    assert checked.data["slop_policy"] == {"route_template": "slop-cleanup"}
