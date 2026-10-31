"""The ``background`` section of the settings document (#165): which repositories each background feature reads.

One flat switch per feature, each settable by an organization's layer and a
repository's layer. Read through the catalog, so each test fails with its own
message on a tree that has no background section.
"""

from __future__ import annotations

from typing import Any

from embry0.settings.document import DocumentInvalid, check, load_yaml
from embry0.settings.sections import CATALOG


def _stored(document: dict[str, Any], layer: str = "deployment") -> Any:
    """What a write of ``document`` stores, or each of its problems as ``(loc, type)``."""
    try:
        return check(CATALOG, document, stored={}, layer=layer, inherited={}).data
    except DocumentInvalid as exc:
        return [(problem.loc, problem.type) for problem in exc.problems]


def test_the_section_holds_one_flat_switch_per_feature_settable_by_an_organization_and_a_repository() -> None:
    section = CATALOG.get("background")
    assert section is not None, "the settings document has a background section: which repositories each reads"
    assert section.fields == ("leads", "scout", "slop_meter", "prune_after_days"), (
        f"one flat key per feature, and the meter's grace period: {section.fields}"
    )
    assert section.org_keys == section.repo_keys == frozenset({"leads", "scout", "slop_meter"}), (
        "an organization's layer and a repository's layer may each set every feature's key, and nothing else"
    )
    defaults = section.defaults()
    assert (defaults.leads, defaults.scout, defaults.slop_meter) == (True, False, False), (
        "on a new deployment the scout and the meter, which clone repositories and may spend, read nothing until "
        "enrolled; leads, off by their own switch, read every repository someone answers for"
    )


def test_a_repository_layer_sets_one_feature_and_a_switch_is_a_boolean() -> None:
    assert CATALOG.get("background") is not None, "the settings document has a background section"
    document = load_yaml("background:\n  slop_meter: false\n")
    assert _stored(document, layer="repository") == {"background": {"slop_meter": False}}, (
        "a repository's layer may take it out of one feature and say nothing of the others"
    )
    assert _stored({"background": {"scout": "yes"}}, layer="organization") == [
        (("background", "scout"), "bool_type")
    ], "a switch is true or false, never text that looks like one"
    assert _stored({"background": {"watcher": True}}, layer="organization") == [
        (("background", "watcher"), "extra_forbidden")
    ], "the watcher and the context audit keep their own opt-ins, so the section has no key for them"


def test_the_grace_period_is_the_deployment_s_a_week_by_default_and_bounded() -> None:
    section = CATALOG.get("background")
    assert section is not None, "the settings document has a background section"
    assert getattr(section.defaults(), "prune_after_days", None) == 7, (
        "the meter waits a week after a repository leaves its scope before it removes what it kept"
    )
    assert _stored({"background": {"prune_after_days": 30}}) == {"background": {"prune_after_days": 30}}
    for days in (0, 366):
        problems = _stored({"background": {"prune_after_days": days}})
        assert [loc for loc, _ in problems] == [("background", "prune_after_days")], (
            f"{days} days is refused: at least one, at most a year"
        )
    assert _stored({"background": {"prune_after_days": 1}}, layer="organization") == [
        (("background", "prune_after_days"), "not_overridable")
    ], "an organization cannot shorten it: it bounds how fast a misclick anywhere costs data"
