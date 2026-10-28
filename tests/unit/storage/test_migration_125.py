"""Migration 125 — a person's GitHub login, and hand-offs."""

from __future__ import annotations

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_125_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(125) == 1, "migration 125 (hand-offs) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 125)
    assert "ADD COLUMN IF NOT EXISTS github_login" in sql and "CREATE TABLE IF NOT EXISTS handoffs" in sql
    assert "uq_handoffs_one_open" in sql, "a decision has at most one open hand-off"


def test_migration_125_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_124.py)."""
    assert MIGRATIONS[-1][0] == 125, "migration 125 must add users.github_login and handoffs"
