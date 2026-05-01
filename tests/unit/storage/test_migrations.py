import pytest

from athanor.storage.database import DatabasePool
from athanor.storage.migrations.runner import MIGRATIONS, run_migrations


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

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://athanor:athanor@localhost:5432/athanor_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)

    # Verify core tables exist
    tables = await db.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    table_names = {row["tablename"] for row in tables}
    assert "athanor_migrations" in table_names
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

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://athanor:athanor@localhost:5432/athanor_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    await run_migrations(db)  # Second run should be no-op

    version = await db.fetchval("SELECT MAX(version) FROM athanor_migrations")
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
