"""Migration 35 — nullable current_stage column on jobs (Console board)."""

from athanor.storage.migrations.runner import MIGRATIONS


def test_migration_35_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert 35 in versions


def test_migration_35_adds_nullable_current_stage() -> None:
    match = [(v, d, sql) for v, d, sql in MIGRATIONS if v == 35]
    assert len(match) == 1
    _, description, sql = match[0]
    assert "current_stage" in description or "current_stage" in sql
    assert "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS current_stage TEXT" in sql
    # Must be nullable — legacy rows stay NULL and the API tolerates that.
    assert "NOT NULL" not in sql
