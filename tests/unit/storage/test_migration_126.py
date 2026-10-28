"""Migration 126 — asks, and each decision's code."""

from __future__ import annotations

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_126_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(126) == 1, "migration 126 (asks) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 126)
    assert "CREATE TABLE IF NOT EXISTS asks" in sql and "CREATE TABLE IF NOT EXISTS decision_codes" in sql
    assert "code        TEXT NOT NULL UNIQUE" in sql, "a code belongs to one decision, ever"
    assert "uq_asks_live" in sql, "one live ask per decision and person"
