"""effective_skills and the catalog check (spec 3.3), pinned to the shared fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from embry0.agents.skills import UnknownSkillError, check_skill_names, closest_names, effective_skills, normalize_delta

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "skills" / "effective-skills.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_effective_skills_matches_fixture(case: dict) -> None:
    assert effective_skills(case["base"], *case["deltas"]) == case["expected"], case["name"]


def test_normalize_delta_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="skills delta"):
        normalize_delta({"add": "a:x"})
    with pytest.raises(ValueError, match="skills delta"):
        normalize_delta(42)


def test_check_skill_names_lists_unknown_with_suggestions() -> None:
    known = {"superpowers:writing-plans", "superpowers:brainstorming", "local:house-style"}
    with pytest.raises(UnknownSkillError) as exc:
        check_skill_names(["superpowers:writing-plan", "local:house-style"], known)
    assert exc.value.unknown == ["superpowers:writing-plan"]
    assert exc.value.suggestions["superpowers:writing-plan"][0] == "superpowers:writing-plans"
    assert "superpowers:writing-plans" in str(exc.value)


def test_closest_names_is_bounded_and_ranked() -> None:
    assert len(closest_names("x", ["a", "b", "c", "d"], n=2)) == 2
    assert closest_names("writing-plan", ["writing-plans", "brainstorming", "debugging"], n=1) == ["writing-plans"]
