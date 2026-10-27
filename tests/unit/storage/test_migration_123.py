"""Migration 123 — the scout loop's runs, and its lead kinds."""

from __future__ import annotations

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_123_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(123) == 1, "migration 123 (scout) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 123)
    assert "CREATE TABLE IF NOT EXISTS scout_runs" in sql
    for check in ("leads_kind_check", "proposal_decisions_kind_check"):
        assert f"ADD CONSTRAINT {check}" in sql and "'crack', 'weed'" in sql


def test_migration_123_keeps_every_lead_kind_the_code_knows() -> None:
    from embry0.services.leads import LEAD_KINDS

    sql = next(s for v, _, s in MIGRATIONS if v == 123)
    for kind in LEAD_KINDS:
        assert f"'{kind}'" in sql, f"migration 123 would refuse stored leads of kind {kind!r}"


def test_migration_123_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_122.py)."""
    assert MIGRATIONS[-1][0] == 123, "migration 123 must add scout_runs and the crack and weed lead kinds"
