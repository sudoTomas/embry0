"""Migration 99: org_needs, what embry0 still needs to maintain an owner (ADR 0011)."""

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_99_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert 99 in versions, "migration 99 (org_needs) is missing"
    assert versions == list(range(1, len(versions) + 1))
    description = next(d for v, d, _ in MIGRATIONS if v == 99)
    assert "org_needs" in description
