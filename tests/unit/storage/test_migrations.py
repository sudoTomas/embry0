import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations


@pytest.mark.asyncio
async def test_migrations_list_is_ordered():
    """Migration versions are sequential starting from 1."""
    versions = [m[0] for m in MIGRATIONS]
    assert versions == list(range(1, len(MIGRATIONS) + 1))


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
