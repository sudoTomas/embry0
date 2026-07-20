"""Migration 39 — nullable issue_inputs.issue_id for issue-less jobs (EMB-43)."""

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_39_present_and_ordered() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert 39 in versions
    assert versions == sorted(versions)
    assert versions[-1] == 39


def test_migration_39_drops_not_null() -> None:
    match = [(v, d, sql) for v, d, sql in MIGRATIONS if v == 39]
    assert len(match) == 1
    _, description, sql = match[0]
    assert "EMB-43" in description
    assert "ALTER TABLE issue_inputs ALTER COLUMN issue_id DROP NOT NULL" in sql
