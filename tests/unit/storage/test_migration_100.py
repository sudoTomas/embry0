"""Migration 100 — hosts, repo_hosts and the tailnet_api_key credential kind (ADR 0013)."""

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_100_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(100) == 1, "migration 100 (hosts, repo_hosts, tailnet_api_key) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))
