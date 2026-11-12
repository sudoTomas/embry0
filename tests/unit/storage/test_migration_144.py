"""Migration 144 — ``slop_meter_scores.config_version``, the settings a Slop-O-Meter score was computed from (#169).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from embry0.services.slop_meter.score import Meter, Week
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations
from embry0.storage.repositories.slop_meter import SlopMeterRepository

#: This migration's number.
VERSION = 144

AT = datetime(2026, 10, 26, 6, tzinfo=UTC)
REPO = "acme/widgets"


def test_the_score_version_migration_is_listed_once() -> None:
    """ "Is the last entry" passed to tests/unit/storage/test_migration_145.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


async def _column(db: DatabasePool) -> dict[str, str] | None:
    row = await db.fetchrow(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'slop_meter_scores' AND column_name = 'config_version'"
    )
    return dict(row) if row is not None else None


@pytest.mark.requires_postgres
async def test_a_score_from_before_names_no_version_and_a_new_one_records_its_own(
    db_with_migrations: DatabasePool,
) -> None:
    """Upgrading keeps every stored score, with an empty version that no digest equals, so the next run
    records one; a score stored after it records the version it was given."""
    db = db_with_migrations
    assert await _column(db) is not None, "slop_meter_scores has no config_version column after migrating"
    await db.execute("ALTER TABLE slop_meter_scores DROP COLUMN config_version")
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    await db.execute(
        "INSERT INTO slop_meter_scores (repo, area, score, level, name, provisional, commits, computed_at) "
        "VALUES ($1, '', 0.4, 3, 'Lasagna', false, 12, $2)",
        REPO,
        AT,
    )
    await run_migrations(db)

    column = await _column(db)
    assert column is not None and (column["data_type"], column["is_nullable"]) == ("text", "NO"), column
    assert column["column_default"] == "''::text", f"a row written without one gets none: {column['column_default']}"
    store = SlopMeterRepository(db)
    [before] = await store.scores(REPO)
    assert (before["name"], before["config_version"]) == ("Lasagna", ""), (
        f"the score stored before the migration is kept, naming no version: {before}"
    )

    week = Week(date(2026, 10, 19), 100.0, 80.0, 20.0, 900.0, 0.2)
    await store.store(REPO, {"": Meter(0.2, 2, "Spaghetti Bolognese", False, [week], 13)}, AT, config_version="v-1")
    [after] = await store.scores(REPO)
    assert (after["name"], after["config_version"]) == ("Spaghetti Bolognese", "v-1"), (
        f"a score records the configuration version it was computed with: {after}"
    )
