"""Migration 138 — ``agent_definitions.runs_in_jobs``, and the prompt each brief was written with (#166).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

#: This migration's number.
VERSION = 138


def test_the_brief_writer_migration_is_listed_once() -> None:
    """The "last entry" assertion passed to tests/unit/storage/test_migration_139.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


@pytest.mark.requires_postgres
async def test_every_definition_runs_in_jobs_until_the_seeder_says_otherwise(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    column = await db.fetchrow(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'agent_definitions' AND column_name = 'runs_in_jobs'"
    )
    assert column is not None, f"migration {VERSION} adds agent_definitions.runs_in_jobs"
    assert (column["data_type"], column["is_nullable"], column["column_default"]) == ("boolean", "NO", "true"), (
        "every definition that existed before runs in jobs"
    )
    rows = await db.fetch("SELECT type, runs_in_jobs FROM agent_definitions")
    assert rows and all(r["runs_in_jobs"] for r in rows), "the migrated rows all run in jobs"


@pytest.mark.requires_postgres
async def test_a_brief_written_before_the_column_has_no_prompt_hash(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    for table in ("proposal_briefs", "job_briefs"):
        column = await db.fetchrow(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = 'prompt_hash'",
            table,
        )
        assert column is not None, f"migration {VERSION} adds {table}.prompt_hash"
        assert (column["data_type"], column["is_nullable"], column["column_default"]) == ("text", "YES", None), (
            f"{table}: a brief written before the column has none (NULL), the prompt sent before #166"
        )
