"""#235: every database test starts from the migrations alone, whatever an earlier test left."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repo_setup_proposals import RepoSetupProposalsRepository
from tests import conftest
from tests.fixtures.postgres import (
    MIGRATION_SEEDED_TABLES,
    after_test,
    assert_no_rows_left,
    capture_fresh,
    migrate_from_empty,
    postgres_unavailable,
    reset_to_fresh,
    restore_fresh,
    schema_shape,
    start_clean,
    tables_with_rows,
)

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


@pytest.fixture
async def rows_an_earlier_test_left(db_with_migrations: DatabasePool) -> AsyncIterator[None]:
    """A setup proposal waiting for a person, as a database test leaves it: that fixture cleans up before a
    test, not after."""
    proposals = RepoSetupProposalsRepository(db_with_migrations)
    proposal_id = await proposals.create("acme/left-behind", base_updated_at=None, created_by="dev@example.com")
    await proposals.finish(proposal_id, status="pending", proposed_yaml="version: 2\n")
    yield


async def test_an_api_test_sees_none_of_an_earlier_test_s_rows(rows_an_earlier_test_left, api_client) -> None:
    """The fixture is requested after the rows are written, so it is what has to clear them."""
    resp = await api_client.get("/api/v1/setup/proposals")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"proposals": []}, f"an earlier test's proposal is listed: {resp.json()}"
    left = sorted(set(await tables_with_rows(api_client.app.state.db)) - MIGRATION_SEEDED_TABLES)
    assert left == [], f"an earlier test's rows are still in {left}"


def _database_url() -> str:
    """The database ``db_with_migrations`` uses."""
    return os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")


@pytest.fixture
async def an_earlier_test_s_database() -> AsyncIterator[DatabasePool]:
    """The database as an earlier test has it, before ``db_with_migrations`` is set up for this one."""
    db = DatabasePool(_database_url(), min_size=1, max_size=1)
    try:
        await db.connect()
    except Exception as exc:
        postgres_unavailable(exc)
    try:
        await reset_to_fresh(db, _database_url())
        yield db
    finally:
        await db.close()


@pytest.fixture
async def rows_left_before_the_fixture(an_earlier_test_s_database: DatabasePool) -> AsyncIterator[None]:
    """A setup proposal waiting for a person, left by an earlier test, which the fixture is not told about."""
    proposals = RepoSetupProposalsRepository(an_earlier_test_s_database)
    proposal_id = await proposals.create("acme/left-behind", base_updated_at=None, created_by="dev@example.com")
    await proposals.finish(proposal_id, status="pending", proposed_yaml="version: 2\n")
    yield


async def test_a_database_test_sees_none_of_an_earlier_test_s_rows(
    rows_left_before_the_fixture, db_with_migrations
) -> None:
    """``db_with_migrations`` is set up after the rows are written, so it is what has to clear them."""
    left = sorted(set(await tables_with_rows(db_with_migrations)) - MIGRATION_SEEDED_TABLES)
    assert left == [], f"an earlier test's rows are still in {left}"


#: A change to the schema the digest leaves out: a column's statistics target only steers the planner.
_UNSEEN_CHANGE = "ALTER TABLE repos ALTER COLUMN notes SET STATISTICS 5"
_STATISTICS_TARGET = "SELECT attstattarget FROM pg_attribute WHERE attrelid = 'repos'::regclass AND attname = 'notes'"


@pytest.fixture
async def a_change_the_digest_does_not_see(an_earlier_test_s_database: DatabasePool) -> AsyncIterator[None]:
    db = an_earlier_test_s_database
    shape = await schema_shape(db)
    await db.execute(_UNSEEN_CHANGE)
    assert await schema_shape(db) == shape, f"the digest sees {_UNSEEN_CHANGE}; pick a change it leaves out"
    yield


@pytest.mark.changes_schema
async def test_a_test_that_changes_the_schema_gets_it_migrated_from_empty(
    a_change_the_digest_does_not_see, db_with_migrations
) -> None:
    """A reset keeps what the digest does not see; a test marked changes_schema gets the schema dropped."""
    assert await db_with_migrations.fetchval(_STATISTICS_TARGET) != 5, (
        "a test marked changes_schema got the schema an earlier test changed"
    )


@pytest.mark.changes_schema
async def test_an_api_test_that_changes_the_schema_gets_it_migrated_from_empty(
    a_change_the_digest_does_not_see, api_client
) -> None:
    assert await api_client.app.state.db.fetchval(_STATISTICS_TARGET) != 5, (
        "an API test marked changes_schema got the schema an earlier test changed"
    )


async def test_the_seeded_tables_are_the_ones_the_migrations_fill(db_with_migrations) -> None:
    """The guard's exceptions are exactly what a fresh migration writes: no more, so it misses no leak, and no
    fewer, so it fails no clean start."""
    assert set(await tables_with_rows(db_with_migrations)) == MIGRATION_SEEDED_TABLES


async def test_the_guard_names_a_table_an_earlier_test_left_rows_in(db_with_migrations) -> None:
    await assert_no_rows_left(db_with_migrations)
    await RepoSetupProposalsRepository(db_with_migrations).create(
        "acme/left-behind", base_updated_at=None, created_by="dev@example.com"
    )
    with pytest.raises(pytest.fail.Exception) as exc_info:
        await assert_no_rows_left(db_with_migrations)
    assert "repo_setup_proposals" in str(exc_info.value), str(exc_info.value)


async def test_the_guard_names_a_seeded_table_whose_rows_changed_in_number(db_with_migrations) -> None:
    db = db_with_migrations
    fresh = await capture_fresh(db)
    await assert_no_rows_left(db, fresh.counts)
    await db.execute("DELETE FROM agent_definitions WHERE type = (SELECT min(type) FROM agent_definitions)")
    with pytest.raises(pytest.fail.Exception) as exc_info:
        await assert_no_rows_left(db, fresh.counts)
    assert "agent_definitions" in str(exc_info.value), str(exc_info.value)


async def _seeded_rows(db: DatabasePool) -> dict[str, list[dict[str, object]]]:
    return {
        table: [dict(r) for r in await db.fetch(f'SELECT * FROM public."{table}" ORDER BY 1')]
        for table in sorted(MIGRATION_SEEDED_TABLES)
    }


async def test_a_reset_puts_back_what_the_migrations_left(db_with_migrations) -> None:
    """Without migrating again: every table emptied, the seeded rows as they were, every sequence at its start."""
    db = db_with_migrations
    fresh = await capture_fresh(db)
    seeded = await _seeded_rows(db)
    # What a test leaves: a row in a table the migrations leave empty, rows a foreign key ties together
    # (a binding keeps its credential from being deleted first), a seeded row changed and another
    # deleted, and a sequence moved on.
    await RepoSetupProposalsRepository(db).create(
        "acme/left-behind", base_updated_at=None, created_by="dev@example.com"
    )
    await db.execute(
        "INSERT INTO credentials (id, kind, provider, host, label, secret_ciphertext, masked_preview, created_by)"
        " VALUES ('cred-000000000001', 'forge_token', 'github', 'github.com', 'a', 'cipher-000000000001', '****', 'x')"
    )
    await db.execute(
        "INSERT INTO credential_bindings (scope_kind, scope_value, role, provider, credential_id, created_by)"
        " VALUES ('repo', 'acme/left-behind', 'forge', 'github', 'cred-000000000001', 'x')"
    )
    await db.execute("UPDATE settings_documents SET updated_by = 'someone'")
    await db.execute("DELETE FROM agent_definitions WHERE type = (SELECT min(type) FROM agent_definitions)")
    await db.fetchval("SELECT nextval('audit_log_id_seq')")

    await restore_fresh(db, fresh)

    assert set(await tables_with_rows(db)) == MIGRATION_SEEDED_TABLES
    assert await _seeded_rows(db) == seeded, "the seeded rows were not put back as the migrations wrote them"
    assert await db.fetchval("SELECT nextval('audit_log_id_seq')") == 1, "a sequence kept an earlier test's value"
    assert await db.fetchval("SELECT nextval('credential_bindings_id_seq')") == 1, "a sequence kept its table's rows"
    assert await schema_shape(db) == fresh.shape


@pytest.mark.changes_schema
async def test_tables_whose_keys_form_a_cycle_are_emptied_too(db_with_migrations) -> None:
    """No order deletes them without tripping a key, so they are truncated."""
    db = db_with_migrations
    await db.execute("CREATE TABLE cycle_a (id INT PRIMARY KEY, b_id INT)")
    await db.execute("CREATE TABLE cycle_b (id INT PRIMARY KEY, a_id INT REFERENCES cycle_a (id))")
    await db.execute("ALTER TABLE cycle_a ADD FOREIGN KEY (b_id) REFERENCES cycle_b (id)")
    fresh = await capture_fresh(db)
    assert fresh.delete_order is None
    await db.execute("INSERT INTO cycle_a (id) VALUES (1)")
    await db.execute("INSERT INTO cycle_b (id, a_id) VALUES (1, 1)")
    await db.execute("UPDATE cycle_a SET b_id = 1")
    await RepoSetupProposalsRepository(db).create(
        "acme/left-behind", base_updated_at=None, created_by="dev@example.com"
    )

    await restore_fresh(db, fresh)

    assert set(await tables_with_rows(db)) == MIGRATION_SEEDED_TABLES


@pytest.mark.changes_schema
async def test_two_fresh_migrations_have_one_shape(db_with_migrations) -> None:
    """Otherwise every reset would migrate again."""
    shape = await schema_shape(db_with_migrations)
    await migrate_from_empty(db_with_migrations)
    assert shape and await schema_shape(db_with_migrations) == shape


_SHAPE_KEY = f"test_clean_database-{uuid.uuid4().hex}"


@pytest.mark.changes_schema
async def test_the_test_after_one_that_changes_the_schema_migrates_from_empty(
    db_with_migrations, request: pytest.FixtureRequest
) -> None:
    """Even when the digest does not see what the test changed, the next test does not get it."""
    db = db_with_migrations
    fresh = await reset_to_fresh(db, _SHAPE_KEY)
    await db.execute(_UNSEEN_CHANGE)
    assert await schema_shape(db) == fresh.shape, f"the digest sees {_UNSEEN_CHANGE}; pick a change it leaves out"
    _teardown_hook(request.node)  # what pytest runs after this test
    await reset_to_fresh(db, _SHAPE_KEY)  # what db_with_migrations runs before the next one
    assert await db.fetchval(_STATISTICS_TARGET) != 5, "the test after one marked changes_schema got its schema"


@pytest.mark.changes_schema
async def test_a_marked_test_on_a_connection_of_its_own_makes_the_next_test_migrate() -> None:
    """No database fixture and no requires_postgres: the marker alone, through the teardown hook, keeps
    the change from the next test."""
    on_its_own = _Item("tests/unit/a_test.py::test_on_its_own_connection", marks=("changes_schema",))
    db = DatabasePool(_database_url(), min_size=1, max_size=1)
    try:
        await db.connect()
    except Exception as exc:
        postgres_unavailable(exc)
    try:
        await reset_to_fresh(db, _SHAPE_KEY)
        await db.execute(_UNSEEN_CHANGE)
        _teardown_hook(on_its_own)
        await reset_to_fresh(db, _SHAPE_KEY)
        assert await db.fetchval(_STATISTICS_TARGET) != 5, (
            "the test after a marked test that used a connection of its own got its schema"
        )
    finally:
        await db.close()


def _teardown_hook(item: Any) -> None:
    """Run tests/conftest.py's teardown hook for ``item``, as pytest does once its fixtures are torn down."""
    hook = conftest.pytest_runtest_teardown(item)
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(None)


class _Item:
    """What the reset reads of a test item: its id, its markers and the fixtures it uses."""

    def __init__(self, nodeid: str, *, marks: tuple[str, ...] = (), fixturenames: tuple[str, ...] = ()) -> None:
        self.nodeid = nodeid
        self.fixturenames = list(fixturenames)
        self._marks = marks

    def get_closest_marker(self, name: str) -> object | None:
        return getattr(pytest.mark, name) if name in self._marks else None


_LEFT_A_TABLE = "CREATE TABLE left_behind (id INT)"
_LEFT_BEHIND = "SELECT to_regclass('left_behind') IS NOT NULL"


async def _given_then_changed(url: str) -> None:
    """A database test's database, as start_clean gives it, and the table the test leaves in it."""
    db = DatabasePool(url, min_size=1, max_size=1)
    try:
        await db.connect()
    except Exception as exc:
        postgres_unavailable(exc)
    try:
        await start_clean(db, url, _Item("tests/unit/a_test.py::test_before"))
        await db.execute(_LEFT_A_TABLE)
    finally:
        await db.close()


async def _next_test_s_database(url: str) -> bool:
    """Whether the next test, given its database by start_clean, still finds the table."""
    db = DatabasePool(url, min_size=1, max_size=1)
    await db.connect()
    try:
        await start_clean(db, url, _Item("tests/unit/a_test.py::test_after", fixturenames=("db_with_migrations",)))
        return bool(await db.fetchval(_LEFT_BEHIND))
    finally:
        await db.close()


@pytest.mark.changes_schema
async def test_a_database_test_that_changes_the_schema_unmarked_fails() -> None:
    """The test itself fails, by name, once its fixtures are torn down; the next test gets a fresh schema."""
    url = _database_url()
    await _given_then_changed(url)
    culprit = _Item("tests/unit/a_test.py::test_leaves_a_table", fixturenames=("db_with_migrations",))
    with pytest.raises(pytest.fail.Exception) as exc_info:
        # pytest calls the hook outside any event loop; the check runs one of its own.
        await asyncio.to_thread(after_test, culprit)
    message = str(exc_info.value)
    assert "tests/unit/a_test.py::test_leaves_a_table changed the schema" in message, message
    assert "@pytest.mark.changes_schema" in message, message
    assert not await _next_test_s_database(url), "the next test got the table an unmarked test left"


@pytest.mark.changes_schema
async def test_a_schema_change_no_check_saw_fails_the_next_database_test_naming_the_one_before() -> None:
    """A test that neither uses a database fixture nor is marked requires_postgres is not checked after it,
    so the next database test finds the change: it fails, naming the test before it, on a fresh schema."""
    url = _database_url()
    await _given_then_changed(url)
    after_test(_Item("tests/unit/a_test.py::test_on_its_own_connection"))
    db = DatabasePool(url, min_size=1, max_size=1)
    await db.connect()
    try:
        with pytest.raises(pytest.fail.Exception) as exc_info:
            await start_clean(db, url, _Item("tests/unit/a_test.py::test_after"))
        message = str(exc_info.value)
        assert "tests/unit/a_test.py::test_on_its_own_connection changed the schema" in message, message
        assert not await db.fetchval(_LEFT_BEHIND), "the test that found the change kept it"
    finally:
        await db.close()


@pytest.mark.changes_schema
@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE repos ADD COLUMN left_behind TEXT",
        "ALTER TABLE jobs DISABLE TRIGGER trg_jobs_updated_at",
        "DROP INDEX idx_jobs_repo",
        "DELETE FROM embry0_migrations WHERE version = (SELECT max(version) FROM embry0_migrations)",
    ],
)
async def test_a_schema_an_earlier_test_changed_is_migrated_again(db_with_migrations, change: str) -> None:
    """A migration test may leave the schema in any state; emptying its tables would keep that state."""
    db = db_with_migrations
    # One key for every case: only the first migrates to remember, so each case costs one migration.
    fresh = await reset_to_fresh(db, _SHAPE_KEY)
    await db.execute(change)
    assert await schema_shape(db) != fresh.shape, f"the shape misses: {change}"
    await reset_to_fresh(db, _SHAPE_KEY)
    assert await schema_shape(db) == fresh.shape, f"the reset kept: {change}"


class _RolledBack(Exception):
    """Ends a transaction that made a change only to see whether the digest notices it."""


@pytest.mark.parametrize(
    "change",
    [
        "COMMENT ON TABLE repos IS 'left behind'",
        "ALTER TABLE repos DROP COLUMN notes; ALTER TABLE repos ADD COLUMN notes TEXT NOT NULL DEFAULT ''",
        "CREATE TYPE left_behind AS (id INT)",
        "CREATE DOMAIN left_behind AS TEXT",
        "ALTER TABLE repos ENABLE ROW LEVEL SECURITY",
        "CREATE POLICY left_behind ON repos USING (true)",
        "CREATE RULE left_behind AS ON INSERT TO repos DO ALSO NOTIFY left_behind",
        "ALTER TABLE repos SET (fillfactor = 50)",
        "GRANT SELECT ON repos TO PUBLIC",
        # Every trigger of a table, the foreign-key triggers too: the table's keys go unenforced.
        "ALTER TABLE credential_uses DISABLE TRIGGER ALL",
        "ALTER SEQUENCE config_revisions_id_seq MINVALUE -5",
        "ALTER SEQUENCE config_revisions_id_seq MAXVALUE 2",
        "ALTER SEQUENCE config_revisions_id_seq CACHE 5",
        "ALTER SEQUENCE config_revisions_id_seq CYCLE",
        'ALTER TABLE repos ALTER COLUMN notes TYPE text COLLATE "C"',
        "GRANT SELECT (notes) ON repos TO PUBLIC",
        "GRANT USAGE ON SCHEMA public TO PUBLIC",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO PUBLIC",
        "CREATE AGGREGATE left_behind (int) (SFUNC = int4pl, STYPE = int)",
        "CREATE FUNCTION left_behind() RETURNS bigint LANGUAGE internal WINDOW AS 'window_row_number'",
    ],
)
async def test_the_digest_sees_each_kind_of_schema_change(db_with_migrations, change: str) -> None:
    """Most database tests are reset, not migrated: whatever kind of change one leaves, the next migrates again.

    Each change is rolled back, so this test leaves the schema as it found it.
    """
    db = db_with_migrations
    shape = await schema_shape(db)
    changed = ""
    with pytest.raises(_RolledBack):
        async with db.transaction() as conn:
            await conn.execute(change)
            changed = await schema_shape(conn)
            raise _RolledBack
    assert changed != shape, f"the shape misses: {change}"
    assert await schema_shape(db) == shape, f"not rolled back: {change}"
