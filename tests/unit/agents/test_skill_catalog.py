"""Classifying the skill names a write adds (spec 3.3): available, unavailable, repository, unknown."""

from __future__ import annotations

import pytest

from embry0.agents.skill_catalog import check_skill_write, classify_skill_names
from embry0.agents.skills import UnknownSkillError

_CATALOG = [
    {"name": "superpowers:brainstorming", "available": True},
    {"name": "team:house-style", "available": False},
]
_SOURCES = [
    {"id": "superpowers", "plugin_name": "superpowers", "status": "ready", "enabled": True},
    {"id": "team", "plugin_name": "team", "status": "ready", "enabled": False},
    {"id": "fresh", "plugin_name": "", "status": "pending", "enabled": True},
    {"id": "broken-src", "plugin_name": "broken", "status": "error", "enabled": True},
]


def test_classify_sorts_each_name_into_one_bucket() -> None:
    got = classify_skill_names(
        [
            "superpowers:brainstorming",
            "team:house-style",
            "fresh:anything",
            "broken:thing",
            "broken-src:thing",
            "commit-standard",
            "superpowers:brainstormin",
            "nobody:knows",
        ],
        _CATALOG,
        _SOURCES,
    )
    assert got == {
        "available": ["superpowers:brainstorming"],
        "unavailable": ["team:house-style", "fresh:anything", "broken:thing", "broken-src:thing"],
        "repository": ["commit-standard"],
        "unknown": ["superpowers:brainstormin", "nobody:knows"],
    }


def test_a_ready_source_that_does_not_list_the_skill_does_not_vouch_for_it() -> None:
    assert classify_skill_names(["superpowers:nope"], _CATALOG, _SOURCES)["unknown"] == ["superpowers:nope"]


def test_classify_deduplicates() -> None:
    got = classify_skill_names(["commit-standard", "commit-standard"], [], [])
    assert got["repository"] == ["commit-standard"]


def test_check_skill_write_warns_about_unavailable_names() -> None:
    warnings = check_skill_write(["superpowers:brainstorming", "fresh:x", "commit-standard"], _CATALOG, _SOURCES)
    assert len(warnings) == 1, warnings
    assert warnings[0].startswith("fresh:x"), warnings


def test_check_skill_write_refuses_unknown_names_with_suggestions() -> None:
    with pytest.raises(UnknownSkillError) as exc:
        check_skill_write(["superpowers:brainstormin", "fresh:x"], _CATALOG, _SOURCES)
    assert exc.value.unknown == ["superpowers:brainstormin"]
    assert exc.value.suggestions["superpowers:brainstormin"][0] == "superpowers:brainstorming"
