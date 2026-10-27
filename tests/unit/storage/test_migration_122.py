"""Migration 122 — the slop-o-meter records how each repository's history was read."""

from __future__ import annotations

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_122_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(122) == 1, "migration 122 (slop-o-meter history version) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 122)
    assert "history_version" in sql and "slop_meter_repos" in sql
