"""Migration 124 — knowledge maps: the uploads, the marks on their parts, and what was sent."""

from __future__ import annotations

from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_124_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(124) == 1, "migration 124 (knowledge maps) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 124)
    for table in ("knowledge_maps", "knowledge_map_marks", "knowledge_map_sends"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "CHECK (verdict IN ('bloat', 'keep', 'unsure'))" in sql
    assert "CHECK (kind IN ('remove', 'fix', 'analysis'))" in sql


def test_migration_124_matches_what_the_code_sends_and_marks() -> None:
    from embry0.services.knowledge_maps import SEND_KINDS, VERDICTS

    sql = next(s for v, _, s in MIGRATIONS if v == 124)
    for value in (*SEND_KINDS, *VERDICTS):
        assert f"'{value}'" in sql, f"migration 124 would refuse {value!r}"


def test_migration_124_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_123.py)."""
    assert MIGRATIONS[-1][0] == 124, "migration 124 must add the knowledge map tables"
