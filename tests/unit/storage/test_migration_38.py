"""Migration 38 — per-run token usage columns on traces (EMB-35)."""

from embry0.storage.migrations.runner import MIGRATIONS

TOKEN_COLUMNS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)


def test_migration_38_present_and_ordered() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert 38 in versions
    assert versions == sorted(versions), "MIGRATIONS must stay version-ordered"
    assert versions[-1] == 38, "38 is the latest migration"


def test_migration_38_adds_four_token_columns_with_default_zero() -> None:
    match = [(v, d, sql) for v, d, sql in MIGRATIONS if v == 38]
    assert len(match) == 1
    _, description, sql = match[0]
    assert "EMB-35" in description
    for col in TOKEN_COLUMNS:
        assert f"ALTER TABLE traces ADD COLUMN IF NOT EXISTS {col} BIGINT NOT NULL DEFAULT 0" in sql, (
            f"missing column DDL for {col}"
        )
