"""Migration 127 — the adventurer presets renamed with the original progression theme."""

from __future__ import annotations

import pytest

from embry0.agents.identity import AVATARS
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

RENAMED = {
    "iron-warrior": "silver-warrior",
    "steel-warrior": "gold-warrior",
    "mithril-warrior": "platinum-warrior",
    "adamant-warrior": "diamond-warrior",
    "rune-warrior": "obsidian-warrior",
    "dragon-warrior": "crimson-warrior",
    "mystic": "sorcerer",
    "skiller": "artisan",
}


def test_migration_127_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_126.py)."""
    assert MIGRATIONS[-1][0] == 127, "migration 127 must rename the stored avatars"


def test_every_new_name_is_an_avatar_and_no_old_one_is() -> None:
    sql = next(s for v, _, s in MIGRATIONS if v == 127)
    for old, new in RENAMED.items():
        assert f"WHEN '{old}' THEN '{new}'" in sql, f"migration 127 does not rename {old}"
        assert new in AVATARS and old not in AVATARS, f"{old} -> {new} is not what identity.AVATARS holds"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_stored_avatar_keeps_its_look_under_its_new_name(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    sql = next(s for v, _, s in MIGRATIONS if v == 127)
    for i, old in enumerate([*RENAMED, "golem"]):
        await db.execute(
            "INSERT INTO agent_definitions (type, description, model, avatar) VALUES ($1, 'd', 'm', $2)", f"a{i}", old
        )
    await db.execute(sql)
    rows = {
        r["type"]: r["avatar"]
        for r in await db.fetch("SELECT type, avatar FROM agent_definitions WHERE type LIKE 'a%'")
    }
    expected = {f"a{i}": RENAMED.get(old, old) for i, old in enumerate([*RENAMED, "golem"])}
    assert {k: rows[k] for k in expected} == expected, "each old name is renamed and any other avatar left alone"
