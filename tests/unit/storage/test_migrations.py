import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations


@pytest.mark.asyncio
async def test_migrations_list_is_ordered():
    """Migration versions are sequential starting from 1."""
    versions = [m[0] for m in MIGRATIONS]
    assert versions == list(range(1, len(MIGRATIONS) + 1))


def test_every_migration_test_gets_a_schema_migrated_from_empty(request: pytest.FixtureRequest) -> None:
    """tests/conftest.py marks every test of a test_migration* module changes_schema: db_with_migrations
    drops its schema and migrates it, and the next test's too, because a migration test may leave the
    schema in a state the reset's digest does not see (tests/fixtures/postgres.py). Every such test this
    run collected is checked, this one included."""
    collected = [item for item in request.session.items if item.path.name.startswith("test_migration")]
    assert request.node in collected
    unmarked = [item.nodeid for item in collected if item.get_closest_marker("changes_schema") is None]
    assert not unmarked, f"tests of a test_migration* module that would get a reset database: {unmarked[:10]}"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_run_migrations_creates_tables():
    """Running migrations creates all expected tables."""
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)

    # Verify core tables exist
    tables = await db.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    table_names = {row["tablename"] for row in tables}
    assert "embry0_migrations" in table_names
    assert "jobs" in table_names
    assert "traces" in table_names
    assert "pipeline_templates" in table_names
    assert "sandbox_profiles" in table_names
    assert "context_config" in table_names
    assert "budget_config" in table_names
    assert "audit_log" in table_names
    assert "job_logs" in table_names

    await db.close()


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_run_migrations_is_idempotent():
    """Running migrations twice does not error."""
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    await run_migrations(db)  # Second run should be no-op

    version = await db.fetchval("SELECT MAX(version) FROM embry0_migrations")
    assert version == len(MIGRATIONS)

    await db.close()


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_run_migrations_backfills_out_of_order_gap(db_with_migrations):
    """A migration missing from the ledger below the current max must still run.

    Regression for a bug where run_migrations() compared each version only
    against MAX(applied): once a higher version had been applied, a
    branch-merge landing a lower-numbered migration behind it would be
    silently skipped forever, with no error.
    """
    # Simulate exactly that: erase migration 5's ledger row while every
    # higher version stays applied (max is untouched).
    await db_with_migrations.execute("DELETE FROM embry0_migrations WHERE version = 5")

    await run_migrations(db_with_migrations)

    versions = {row["version"] for row in await db_with_migrations.fetch("SELECT version FROM embry0_migrations")}
    assert versions == set(range(1, len(MIGRATIONS) + 1))


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_migration_18_adds_sandbox_profile_qa_columns(db_with_migrations):
    """Migration 18 must add the QA agent foundation columns to sandbox_profiles."""
    cols = await db_with_migrations.fetch(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_name = 'sandbox_profiles' "
        "ORDER BY column_name"
    )
    names = {c["column_name"] for c in cols}
    expected_new = {
        "description",
        "dind_enabled",
        "idle_timeout_seconds",
        "extra_networks",
        "env_defaults",
        "is_builtin",
    }
    assert expected_new.issubset(names), f"Missing columns: {expected_new - names}"

    by_name = {c["column_name"]: c for c in cols}
    assert by_name["dind_enabled"]["data_type"] == "boolean"
    assert by_name["idle_timeout_seconds"]["data_type"] == "integer"
    assert by_name["extra_networks"]["data_type"] == "jsonb"
    assert by_name["env_defaults"]["data_type"] == "jsonb"
    assert by_name["is_builtin"]["data_type"] == "boolean"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_migration_19_adds_scope_to_env_tables(db_with_migrations):
    """Migration 19 adds scope='app'|'qa' (CHECK-enforced) to env tables."""
    for table in ("global_environment", "repo_environment"):
        cols = await db_with_migrations.fetch(
            "SELECT column_name, data_type, column_default FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = 'scope'",
            table,
        )
        assert len(cols) == 1, f"{table}.scope missing"
        assert cols[0]["data_type"] == "text"

        # CHECK constraint enforces app|qa
        constraints = await db_with_migrations.fetch(
            "SELECT conname FROM pg_constraint c "
            "JOIN pg_class t ON c.conrelid = t.oid WHERE t.relname = $1 AND contype = 'c'",
            table,
        )
        constraint_defs = []
        for c in constraints:
            cdef = await db_with_migrations.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = $1",
                c["conname"],
            )
            constraint_defs.append(cdef or "")
        assert any("scope" in cdef for cdef in constraint_defs), f"{table} missing scope CHECK"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_migration_20_adds_mcp_servers_column(db_with_migrations):
    cols = await db_with_migrations.fetch(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'agent_definitions' AND column_name = 'mcp_servers'"
    )
    assert len(cols) == 1
    assert cols[0]["data_type"] == "jsonb"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_migration_23_creates_agent_sessions(db_with_migrations):
    """Migration 23 creates agent_sessions table for per-(job, agent) conversation state."""
    cols = await db_with_migrations.fetch(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='agent_sessions' ORDER BY ordinal_position"
    )
    names = {c["column_name"]: c["data_type"] for c in cols}
    assert names["id"] == "text"
    assert names["job_id"] == "text"
    assert names["agent_type"] == "text"
    assert names["mode"] == "text"
    assert names["session_id"] == "text"
    assert names["messages"] == "jsonb"
    assert names["session_blob"] == "bytea"
    assert "last_turn_at" in names
    assert "created_at" in names

    # Unique (job_id, agent_type) constraint
    constraints = await db_with_migrations.fetch(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'agent_sessions'::regclass"
    )
    cnames = {c["conname"] for c in constraints}
    assert any("job_id_agent_type" in n or "agent_sessions_job_id" in n for n in cnames)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_migration_21_adds_notification_channels(db_with_migrations):
    """Migration 21 adds issues.notification_channels JSONB DEFAULT '["dashboard"]'."""
    cols = await db_with_migrations.fetch(
        "SELECT column_name, data_type, column_default, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_name='issues' AND column_name='notification_channels'"
    )
    assert len(cols) == 1
    assert cols[0]["data_type"] == "jsonb"
    assert cols[0]["is_nullable"] == "NO"
    # default text in postgres looks like: '["dashboard"]'::jsonb
    assert "dashboard" in (cols[0]["column_default"] or "")

    # Existing rows get the default applied
    iid = "iss-test-21"
    await db_with_migrations.execute(
        "INSERT INTO issues (id, repo, title, body, status) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING",
        iid,
        "test/test",
        "x",
        "x",
        "open",
    )
    try:
        row = await db_with_migrations.fetchrow("SELECT notification_channels FROM issues WHERE id = $1", iid)
        # JSONB is decoded to a Python list by the asyncpg codec.
        assert row["notification_channels"] == ["dashboard"]
    finally:
        await db_with_migrations.execute("DELETE FROM issues WHERE id = $1", iid)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_migration_84_allows_abandoned_as_a_resolution_path(db_with_migrations):
    """#410: a case accepted, attempted and given up had no honest path, so it
    could not be closed at all and sat at `accepted` forever."""
    predicate = await db_with_migrations.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'healing_cases'::regclass AND conname = 'healing_cases_resolution_path_check'"
    )
    assert predicate is not None, "the resolution_path CHECK is gone, not extended"
    for path in ("embry0", "out_of_band", "cancelled", "non_code", "self_resolved", "abandoned"):
        assert f"'{path}'" in predicate, path

    await db_with_migrations.execute(
        "INSERT INTO healing_cases (id, repo, origin, title, stage, resolution_path) "
        "VALUES ('hc-m84', 'o/r', 'watcher', 't', 'closed', 'abandoned')"
    )
    assert await db_with_migrations.fetchval("SELECT resolution_path FROM healing_cases WHERE id = 'hc-m84'") == (
        "abandoned"
    )
    with pytest.raises(Exception, match="resolution_path"):
        await db_with_migrations.execute(
            "INSERT INTO healing_cases (id, repo, origin, title, stage, resolution_path) "
            "VALUES ('hc-m84b', 'o/r', 'watcher', 't', 'closed', 'because_i_said_so')"
        )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_migration_85_releases_slots_a_human_already_accepted(db_with_migrations):
    """Rows accepted before #415 keep released_at NULL. The migration stamps
    those and leaves a draft that is still awaiting a human alone."""
    from datetime import UTC, datetime, timedelta

    from embry0.storage.repositories.watcher_runs import WatcherRunsRepository

    found = [(desc, sql) for version, desc, sql in MIGRATIONS if version == 85]
    assert found, "migration 85 must backfill released_at for drafts already accepted (#415)"
    _, sql = found[0]

    db = db_with_migrations
    runs = WatcherRunsRepository(db)
    now = datetime.now(UTC)
    accepted_at = now - timedelta(hours=3)
    accepted_run = await runs.create(
        window_start=now, window_end=now, action="proposed", tracker="linear", tracker_key="ENG-85A", repo="o/r"
    )
    waiting_run = await runs.create(
        window_start=now, window_end=now, action="proposed", tracker="linear", tracker_key="ENG-85W", repo="o/r"
    )
    kept = now - timedelta(days=1)
    already_run = await runs.create(
        window_start=now, window_end=now, action="proposed", tracker="linear", tracker_key="ENG-85K", repo="o/r"
    )
    await db.execute("UPDATE watcher_runs SET released_at = $2 WHERE id = $1", already_run, kept)
    await db.execute(
        """
        INSERT INTO healing_cases (id, repo, origin, title, stage, watcher_run_id, tracker, tracker_key, accepted_at)
        VALUES
          ('hc-85a', 'o/r', 'watcher', 'accepted', 'verified', $1, 'linear', 'ENG-85A', $4),
          ('hc-85w', 'o/r', 'watcher', 'waiting', 'tracked', $2, 'linear', 'ENG-85W', NULL),
          ('hc-85k', 'o/r', 'watcher', 'kept', 'accepted', $3, 'linear', 'ENG-85K', $4)
        """,
        accepted_run,
        waiting_run,
        already_run,
        accepted_at,
    )

    await db.execute(sql)

    released = await db.fetchval("SELECT released_at FROM watcher_runs WHERE id = $1", accepted_run)
    waiting = await db.fetchval("SELECT released_at FROM watcher_runs WHERE id = $1", waiting_run)
    untouched = await db.fetchval("SELECT released_at FROM watcher_runs WHERE id = $1", already_run)
    assert released == accepted_at, "an already-accepted draft's slot must be stamped with its accepted_at (#415)"
    assert waiting is None, "a draft still awaiting a human must keep its slot"
    assert untouched == kept, "a slot released earlier must keep that timestamp"
