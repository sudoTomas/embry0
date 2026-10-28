"""WatcherStacksRepository against a real database (#378, migration 83)."""

from __future__ import annotations

import pytest

from embry0.services.watcher_stacks import WatcherStack
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS
from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

VERSION = 83

STACK = WatcherStack(
    repo="acme/backend",
    logql='{compose_project="acme"}',
    tracker="linear",
    tracker_target={"team_id": "team-1", "project_id": "proj-1"},
)
SELF = WatcherStack(
    repo="example-org/embry0",
    logql='{compose_project="embry0"} | json | level=~"error|critical"',
    tracker="github",
    settings={"interval_seconds": 7200},
)


def test_the_tracker_vocabulary_is_closed() -> None:
    sql = next(sql for v, _, sql in MIGRATIONS if v == VERSION)
    assert "CHECK (tracker IN ('linear', 'github', 'gitlab'))" in sql
    assert "repo           TEXT PRIMARY KEY" in sql, "one row per stack, keyed like every scoped table"


async def test_a_stack_round_trips(db_with_migrations: DatabasePool) -> None:
    repo = WatcherStacksRepository(db_with_migrations)
    stored = await repo.upsert(SELF)
    assert stored == SELF
    assert await repo.get("example-org/embry0") == SELF
    assert await repo.get("nobody/nothing") is None


async def test_upsert_replaces_rather_than_duplicates(db_with_migrations: DatabasePool) -> None:
    repo = WatcherStacksRepository(db_with_migrations)
    await repo.upsert(STACK)
    changed = await repo.upsert(WatcherStack(repo=STACK.repo, logql='{compose_project="other"}'))
    assert changed.logql == '{compose_project="other"}'
    assert await repo.count() == 1


async def test_unknown_setting_keys_are_dropped_on_write(db_with_migrations: DatabasePool) -> None:
    """Stored, they would read as configuration that silently does nothing."""
    repo = WatcherStacksRepository(db_with_migrations)
    stored = await repo.upsert(
        WatcherStack(repo="o/r", logql="{x=1}", settings={"interval_seconds": 60, "typo_seconds": 5})
    )
    assert stored.settings == {"interval_seconds": 60}


async def test_a_stacks_own_signature_masks_are_kept_on_write(db_with_migrations: DatabasePool) -> None:
    """#172: a setting with no global default is still a setting, not a typo to drop."""
    masks = [{"pattern": r"\brec_[a-z0-9]{7}\b", "replacement": "<record>"}]
    repo = WatcherStacksRepository(db_with_migrations)
    await repo.upsert(WatcherStack(repo="o/r", logql="{x=1}", settings={"signature_masks": masks}))
    stored = await repo.get("o/r")
    assert stored is not None
    assert stored.settings == {"signature_masks": masks}, f"the masks were dropped on write: {stored.settings}"


async def test_a_stack_needs_a_repo_a_selector_and_a_known_tracker(db_with_migrations: DatabasePool) -> None:
    repo = WatcherStacksRepository(db_with_migrations)
    for bad in (
        WatcherStack(repo="  ", logql="{x=1}"),
        WatcherStack(repo="o/r", logql="   "),
        WatcherStack(repo="o/r", logql="{x=1}", tracker="jira"),
    ):
        with pytest.raises(ValueError):
            await repo.upsert(bad)
    assert await repo.count() == 0


async def test_listing_can_skip_the_disabled(db_with_migrations: DatabasePool) -> None:
    repo = WatcherStacksRepository(db_with_migrations)
    await repo.upsert(STACK)
    await repo.upsert(SELF)
    await repo.set_enabled(SELF.repo, False)

    assert [s.repo for s in await repo.list_all()] == [STACK.repo, SELF.repo]
    assert [s.repo for s in await repo.list_all(enabled_only=True)] == [STACK.repo]


async def test_deleting_a_stack_leaves_what_it_found(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    repo = WatcherStacksRepository(db)
    await repo.upsert(STACK)
    await db.execute(
        "INSERT INTO watcher_runs (id, window_start, window_end, log_lines, action, repo) "
        "VALUES ('wr-1', NOW(), NOW(), 5, 'proposed', $1)",
        STACK.repo,
    )

    assert await repo.delete(STACK.repo) is True
    assert await repo.delete(STACK.repo) is False
    assert await db.fetchval("SELECT count(*) FROM watcher_runs WHERE repo = $1", STACK.repo) == 1
