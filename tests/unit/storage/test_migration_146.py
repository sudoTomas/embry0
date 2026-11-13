"""Migration 146 — where each repository's Slop-O-Meter event read stopped, and each credential's turn (#396).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embry0.services.slop_meter.events import ReaderPlace
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations
from embry0.storage.repositories.slop_meter import SlopMeterRepository

#: This migration's number.
VERSION = 146

REPO = "acme/widgets"
AT = datetime(2026, 11, 2, 9, tzinfo=UTC)


def test_the_reader_place_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_145.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must add slop_meter_repos.reader_place"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


@pytest.mark.requires_postgres
async def test_a_repository_read_before_has_no_place_and_a_place_and_a_turn_are_kept(
    db_with_migrations: DatabasePool,
) -> None:
    """Upgrading keeps every repository's state, with no place, so its next read starts at the first listing; a
    place and a credential's turn stored after it read back as written."""
    db = db_with_migrations
    await db.execute("ALTER TABLE slop_meter_repos DROP COLUMN reader_place")
    await db.execute("DROP TABLE slop_meter_turns")
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    await db.execute("INSERT INTO slop_meter_repos (repo, comments_since) VALUES ($1, $2)", REPO, AT)
    await run_migrations(db)

    store = SlopMeterRepository(db)
    before = await store.state(REPO)
    assert before["comments_since"] == AT and before["reader_place"] is None, (
        f"a repository read before the migration keeps its cursors and has no place: {before}"
    )
    place = ReaderPlace(stage=3, pulls=((AT, 7),), found=2, pulls_left=49, logins=("ana",), failures=1)
    await store.set_state(REPO, reader_place=place.to_json())
    assert ReaderPlace.from_json((await store.state(REPO))["reader_place"]) == place, "a place reads back as stored"
    assert await store.turns() == {}
    await store.set_turns({"cred-0001": 3})
    await store.set_turns({"cred-0001": 4, "cred-0002": 0})
    assert await store.turns() == {"cred-0001": 4, "cred-0002": 0}, "each credential keeps its last turn"
