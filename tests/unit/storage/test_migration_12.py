"""Migration 12 — execution_mode + auth_mode nullable columns."""

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_12_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert 12 in versions


def test_migration_12_alters_both_tables() -> None:
    match = [(v, d, sql) for v, d, sql in MIGRATIONS if v == 12]
    assert len(match) == 1
    _, description, sql = match[0]
    assert "repo_preferences" in sql
    assert "agent_definitions" in sql
    assert "execution_mode" in sql
    assert "auth_mode" in sql
    # Must be nullable (no NOT NULL)
    assert "execution_mode TEXT" in sql or "execution_mode  TEXT" in sql
    assert "NOT NULL" not in sql.replace("NOT NULL DEFAULT", "")
