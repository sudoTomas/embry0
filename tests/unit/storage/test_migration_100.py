"""Migration 100 — hosts, repo_hosts and the tailnet_api_key credential kind (ADR 0013)."""

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_100_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(100) == 1, "migration 100 (hosts, repo_hosts, tailnet_api_key) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))


def test_migration_100_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_99.py)."""
    assert MIGRATIONS[-1][0] == 100, "migration 100 must add hosts, repo_hosts and the tailnet_api_key kind"
