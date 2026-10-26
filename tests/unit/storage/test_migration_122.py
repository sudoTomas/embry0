"""Migration 122 — the slop-o-meter records how each repository's history was read."""

from __future__ import annotations

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_122_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(122) == 1, "migration 122 (slop-o-meter history version) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 122)
    assert "history_version" in sql and "slop_meter_repos" in sql


def test_migration_122_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_121.py)."""
    assert MIGRATIONS[-1][0] == 122, "migration 122 must add slop_meter_repos.history_version"
