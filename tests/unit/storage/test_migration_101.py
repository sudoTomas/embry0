"""Migration 101 — agent identity columns, skill_sources and inline_skills (agent identity and skills spec 3.1, 3.2)."""

from __future__ import annotations

import pytest

from embry0.storage.migrations.runner import MIGRATIONS, run_migrations


def test_migration_101_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(101) == 1, (
        "migration 101 (agent identity, skill_sources, inline_skills) must exist exactly once"
    )
    assert versions == list(range(1, len(versions) + 1))


@pytest.mark.requires_postgres
async def test_migration_101_adds_identity_columns_and_tables(db_with_migrations) -> None:
    cols = {
        r["column_name"]: r
        for r in await db_with_migrations.fetch(
            "SELECT column_name, column_default, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'agent_definitions'"
        )
    }
    for name in ("display_name", "avatar", "color", "tagline"):
        assert name in cols, f"agent_definitions.{name} missing after migration 101"
        assert cols[name]["is_nullable"] == "NO"
    assert "'golem'" in cols["avatar"]["column_default"]
    assert "'slate'" in cols["color"]["column_default"]
    tables = {
        r["table_name"]
        for r in await db_with_migrations.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
    }
    assert {"skill_sources", "inline_skills"} <= tables


@pytest.mark.requires_postgres
async def test_migration_101_writes_builtin_identities(db_with_migrations) -> None:
    """Every builtin row the migrations pre-populate carries BUILTIN_IDENTITY, all four fields.

    Rows a seeder inserts at startup (absent before seeding) are skipped; the
    seeders write identity on create, pinned in test_agent_definitions.py.
    """
    from embry0.agents.identity import BUILTIN_IDENTITY

    fields = ("display_name", "avatar", "color", "tagline")
    rows = {
        r["type"]: r
        for r in await db_with_migrations.fetch(
            "SELECT type, display_name, avatar, color, tagline FROM agent_definitions WHERE type = ANY($1)",
            list(BUILTIN_IDENTITY),
        )
    }
    assert "developer" in rows, f"the migrations pre-populate no developer row: {sorted(rows)}"
    mismatched = {
        agent_type: {f: rows[agent_type][f] for f in fields}
        for agent_type, identity in BUILTIN_IDENTITY.items()
        if agent_type in rows and any(rows[agent_type][f] != identity[f] for f in fields)
    }
    assert mismatched == {}, f"migration 101 wrote identities that differ from BUILTIN_IDENTITY: {mismatched}"


@pytest.mark.requires_postgres
async def test_migration_101_is_idempotent(db_with_migrations) -> None:
    await run_migrations(db_with_migrations)  # second run: every statement is IF NOT EXISTS / guarded
    n = await db_with_migrations.fetchval("SELECT count(*) FROM embry0_migrations WHERE version = 101")
    assert n == 1
