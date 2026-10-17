"""Migrations 97 and 98 — organizations, org_repo_facts and org_discovery_runs (ADR 0010)."""

from embry0.storage.migrations.runner import MIGRATIONS


def test_migrations_97_and_98_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert 97 in versions, "migration 97 (organizations) is missing"
    assert 98 in versions, "migration 98 (org_repo_facts, org_discovery_runs) is missing"
    assert versions == list(range(1, len(versions) + 1))


def test_migration_98_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_96.py)."""
    assert MIGRATIONS[-1][0] == 98, "migration 98 must add org_repo_facts and org_discovery_runs"
