"""Migration 99: org_needs, what embry0 still needs to maintain an owner (ADR 0011)."""

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_99_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert 99 in versions, "migration 99 (org_needs) is missing"
    assert versions == list(range(1, len(versions) + 1))
    description = next(d for v, d, _ in MIGRATIONS if v == 99)
    assert "org_needs" in description


def test_migration_99_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_97.py)."""
    assert MIGRATIONS[-1][0] == 99, "migration 99 must add org_needs"
