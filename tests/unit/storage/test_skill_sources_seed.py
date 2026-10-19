"""Builtin skill sources seed (agent identity and skills spec 3.2)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from embry0.storage.repositories.skills import SkillSourcesRepository
from embry0.storage.seeds.skill_sources_builtin import (
    SUPERPOWERS_REF,
    SUPERPOWERS_URL,
    seed_builtin_skill_sources,
)

_TAG_SKILLS = Path(__file__).resolve().parents[2] / "fixtures" / "skills" / "superpowers-v6.4.2-skills.txt"


def test_superpowers_ref_is_a_full_sha() -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", SUPERPOWERS_REF), "SUPERPOWERS_REF must be a pinned commit sha"


def test_seeded_superpowers_skills_exist_in_the_pinned_tag() -> None:
    tag_skills = set(_TAG_SKILLS.read_text().split())
    assert len(tag_skills) > 5, f"{_TAG_SKILLS} looks empty"
    seeded = {
        skill.split(":", 1)[1]
        for seed in BUILTIN_SEED.values()
        for skill in seed["skills"]
        if skill.startswith("superpowers:")
    }
    assert seeded, "BUILTIN_SEED names no superpowers skill; this test would measure nothing"
    missing = sorted(seeded - tag_skills)
    assert not missing, f"BUILTIN_SEED names superpowers skills absent from v6.4.2: {missing}"


@pytest.mark.requires_postgres
async def test_seed_inserts_both_builtin_sources_once(db_with_migrations: Any) -> None:
    repo = SkillSourcesRepository(db_with_migrations)
    await seed_builtin_skill_sources(repo)
    rows = {r["id"]: r for r in await repo.list_all()}
    assert set(rows) == {"superpowers", "local"}
    sp = rows["superpowers"]
    assert (sp["kind"], sp["url"], sp["ref"], sp["is_builtin"]) == ("git", SUPERPOWERS_URL, SUPERPOWERS_REF, True)
    local = rows["local"]
    assert (local["kind"], local["url"], local["ref"], local["is_builtin"]) == ("inline", "", "", True)

    await repo.update("superpowers", enabled=False)
    await seed_builtin_skill_sources(repo)
    after = await repo.list_all()
    assert len(after) == 2
    assert next(r for r in after if r["id"] == "superpowers")["enabled"] is False, "seed must not overwrite"
