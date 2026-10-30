"""#235: every API test starts from the migrations alone, whatever an earlier test left."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repo_setup_proposals import RepoSetupProposalsRepository
from tests.fixtures.postgres import (
    MIGRATION_SEEDED_TABLES,
    assert_no_rows_left,
    capture_fresh,
    migrate_from_empty,
    reset_to_fresh,
    restore_fresh,
    schema_shape,
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


async def test_two_fresh_migrations_have_one_shape(db_with_migrations) -> None:
    """Otherwise every reset would migrate again."""
    shape = await schema_shape(db_with_migrations)
    await migrate_from_empty(db_with_migrations)
    assert shape and await schema_shape(db_with_migrations) == shape


_SHAPE_KEY = f"test_clean_database-{uuid.uuid4().hex}"


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
