"""Migration 106 — specialist agent identities for rows still at the defaults.

Migration 101 wrote the nine core agents' identity; the eight read-only
specialists were seeded later and a deployment that seeded them before
this release shows them as slug / golem / slate. 106 writes their identity
the way 101 did, guarded by ``display_name = ''`` so an operator's edit
survives. The VALUES rows and ``BUILTIN_IDENTITY`` are two copies of one
table; a test here reads the SQL so the two cannot drift.
"""

from __future__ import annotations

import re

import pytest

from embry0.agents.identity import BUILTIN_IDENTITY
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations

SPECIALISTS = ("kintsugi", "gardener", "interrogator", "why", "how", "blast-radius", "comment-sicko", "design-auditor")
FIELDS = ("display_name", "avatar", "color", "tagline")

# One VALUES row: five single-quoted SQL strings, a doubled quote being a literal one.
_VALUES_ROW = re.compile(r"\(\s*'([\w-]+)',\s*'((?:[^']|'')*)',\s*'([\w-]+)',\s*'([\w-]+)',\s*'((?:[^']|'')*)'\s*\)")


def _sql_for(version: int) -> str:
    match = [sql for v, _, sql in MIGRATIONS if v == version]
    assert len(match) == 1, f"migration {version} must exist exactly once"
    return match[0]


def _specialists_identity() -> dict[str, dict[str, str]]:
    return {agent_type: BUILTIN_IDENTITY[agent_type] for agent_type in SPECIALISTS}


def test_migration_106_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(106) == 1, "migration 106 (specialist agent identities) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))
    description = next(d for v, d, _ in MIGRATIONS if v == 106)
    assert "specialist" in description


def test_migration_106_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_105.py)."""
    assert MIGRATIONS[-1][0] == 106, "migration 106 must write the specialist identities"


def test_migration_106_writes_the_identity_that_identity_py_carries() -> None:
    """The migration's VALUES rows equal BUILTIN_IDENTITY for the eight specialists, field by field.

    Read from the SQL text, so it holds without a database; the Postgres
    tests below check the same values land in the rows.
    """
    rows = _VALUES_ROW.findall(_sql_for(106))
    assert len(rows) == len(SPECIALISTS), f"expected one VALUES row per specialist, parsed {len(rows)}"
    written = {
        agent_type: {
            "display_name": display_name.replace("''", "'"),
            "avatar": avatar,
            "color": color,
            "tagline": tagline.replace("''", "'"),
        }
        for agent_type, display_name, avatar, color, tagline in rows
    }
    assert written == _specialists_identity(), "migration 106 and BUILTIN_IDENTITY disagree"


async def _specialists_at_the_defaults(db: DatabasePool) -> None:
    """The rows a seeder inserted before identity existed: name, avatar, color and tagline at the column defaults."""
    for agent_type in SPECIALISTS:
        await db.execute(
            "INSERT INTO agent_definitions (type, description, model, is_builtin) VALUES ($1, $2, $3, TRUE)",
            agent_type,
            f"{agent_type} specialist",
            "claude-sonnet-5",
        )


async def _identity_rows(db: DatabasePool) -> dict[str, dict[str, str]]:
    rows = await db.fetch(
        "SELECT type, display_name, avatar, color, tagline FROM agent_definitions WHERE type = ANY($1)",
        list(SPECIALISTS),
    )
    return {r["type"]: {f: r[f] for f in FIELDS} for r in rows}


@pytest.mark.requires_postgres
async def test_a_fresh_schema_records_106(db_with_migrations: DatabasePool) -> None:
    assert await db_with_migrations.fetchval("SELECT 1 FROM embry0_migrations WHERE version = 106") == 1


@pytest.mark.requires_postgres
async def test_it_writes_the_specialists_identity_on_a_schema_at_105(db_with_migrations: DatabasePool) -> None:
    """A deployment at 105 whose specialists sit at the defaults gets their identity on its next start."""
    db = db_with_migrations
    await db.execute("DELETE FROM embry0_migrations WHERE version = 106")
    assert await db.fetchval("SELECT 1 FROM embry0_migrations WHERE version = 106") is None
    await _specialists_at_the_defaults(db)
    assert all(row["display_name"] == "" for row in (await _identity_rows(db)).values())

    await run_migrations(db)

    assert await _identity_rows(db) == _specialists_identity(), "migration 106 left specialists at the defaults"
    assert await db.fetchval("SELECT 1 FROM embry0_migrations WHERE version = 106") == 1


@pytest.mark.requires_postgres
async def test_it_leaves_an_edited_specialist_alone(db_with_migrations: DatabasePool) -> None:
    """The guard is display_name = '': a specialist an operator already named keeps the whole edit."""
    db = db_with_migrations
    await db.execute("DELETE FROM embry0_migrations WHERE version = 106")
    await _specialists_at_the_defaults(db)
    await db.execute("UPDATE agent_definitions SET display_name = 'Renamed', color = 'sky' WHERE type = 'kintsugi'")

    await run_migrations(db)

    rows = await _identity_rows(db)
    assert rows["kintsugi"] == {"display_name": "Renamed", "avatar": "golem", "color": "sky", "tagline": ""}
    assert rows["gardener"] == BUILTIN_IDENTITY["gardener"], "the other rows at the defaults are still written"


@pytest.mark.requires_postgres
async def test_migration_106_is_idempotent(db_with_migrations: DatabasePool) -> None:
    """Running the SQL again on a migrated schema changes nothing: the guard excludes every written row."""
    db = db_with_migrations
    await _specialists_at_the_defaults(db)
    await db.execute(_sql_for(106))
    before = await _identity_rows(db)
    await db.execute(_sql_for(106))
    assert await _identity_rows(db) == before == _specialists_identity()
    assert await db.fetchval("SELECT count(*) FROM embry0_migrations WHERE version = 106") == 1
