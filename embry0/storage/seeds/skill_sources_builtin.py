"""Builtin skill sources (spec 3.2): insert-if-missing, never overwriting."""

from __future__ import annotations

import structlog

from embry0.storage.repositories.skills import SkillSourcesRepository

logger = structlog.get_logger(__name__)

SUPERPOWERS_URL = "https://github.com/obra/superpowers.git"
#: The commit of release tag v6.4.2 (the 6.4 line is where the seeded skill
#: names, e.g. superpowers:writing-plans, are known to exist; v7.0.0 exists
#: and is a separate evaluation). Bumping it is a deliberate one-line change
#: reviewed like any dependency bump; never a branch name.
SUPERPOWERS_REF = "8ca22dba9a94f28898bbce59f2537ff4d87c747d"

BUILTIN_SOURCES: tuple[dict[str, str], ...] = (
    {"source_id": "superpowers", "kind": "git", "url": SUPERPOWERS_URL, "ref": SUPERPOWERS_REF},
    {"source_id": "local", "kind": "inline", "url": "", "ref": ""},
)


async def seed_builtin_skill_sources(repo: SkillSourcesRepository) -> None:
    for spec in BUILTIN_SOURCES:
        if await repo.get(spec["source_id"]) is not None:
            continue
        await repo.create(
            source_id=spec["source_id"], kind=spec["kind"], url=spec["url"], ref=spec["ref"], is_builtin=True
        )
        logger.info("builtin_skill_source_seeded", source_id=spec["source_id"])
