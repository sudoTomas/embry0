"""Migration 97 — organizations (ADR 0010)."""

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_97_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert 97 in versions, "migration 97 (organizations) is missing"
    assert versions == list(range(1, len(versions) + 1))


def test_migration_97_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_96.py)."""
    assert MIGRATIONS[-1][0] == 97, "migration 97 must add organizations"
