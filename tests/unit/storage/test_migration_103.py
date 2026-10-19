"""Migration 103 — repo_config_revisions, every change to a repo's test setup and preferences (ADR 0019)."""

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_103_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(103) == 1, "migration 103 (repo_config_revisions) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))
    description = next(d for v, d, _ in MIGRATIONS if v == 103)
    assert "repo_config_revisions" in description


@pytest.mark.requires_postgres
async def test_a_revision_defaults_to_a_person_with_no_reason(db_with_migrations: DatabasePool) -> None:
    await db_with_migrations.execute(
        "INSERT INTO repo_config_revisions (repo, surface, after) VALUES ('acme/widgets', 'qa_config', $1::jsonb)",
        {"yaml_text": "version: 2\n"},
    )
    row = await db_with_migrations.fetchrow(
        "SELECT before, after, actor, source, reason, created_at FROM repo_config_revisions"
    )
    assert row is not None
    assert (row["before"], row["after"], row["actor"], row["source"], row["reason"]) == (
        None,
        {"yaml_text": "version: 2\n"},
        "",
        "person",
        "",
    )
    assert row["created_at"] is not None


@pytest.mark.requires_postgres
@pytest.mark.parametrize(("column", "value"), [("surface", "env"), ("source", "mcp")])
async def test_the_checks_refuse_an_unknown_surface_or_source(
    db_with_migrations: DatabasePool, column: str, value: str
) -> None:
    row = {"surface": "qa_config", "source": "person", column: value}
    with pytest.raises(asyncpg.CheckViolationError):
        await db_with_migrations.execute(
            "INSERT INTO repo_config_revisions (repo, surface, source) VALUES ('acme/widgets', $1, $2)",
            row["surface"],
            row["source"],
        )


@pytest.mark.requires_postgres
async def test_history_outlives_the_repository(db_with_migrations: DatabasePool) -> None:
    """No foreign key to repos: removing a repository keeps what was changed on it."""
    await db_with_migrations.execute("INSERT INTO repos (repo, forge) VALUES ('acme/widgets', 'github')")
    await db_with_migrations.execute(
        "INSERT INTO repo_config_revisions (repo, surface) VALUES ('acme/widgets', 'preferences')"
    )
    await db_with_migrations.execute("DELETE FROM repos WHERE repo = 'acme/widgets'")
    assert await db_with_migrations.fetchval("SELECT count(*) FROM repo_config_revisions") == 1
