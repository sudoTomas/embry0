"""Migration #30 — recast legacy JSON-string-scalar JSONB rows.

Pre-fix bug: the asyncpg JSONB codec calls ``json.dumps()`` once on every
parameter, and several QA repos ALSO called ``json.dumps()`` before passing
the value to a ``$N::jsonb`` parameter. Postgres parsed the resulting
double-encoded string and stored it as a JSONB string scalar
(``jsonb_typeof = 'string'``), which silently broke server-side ``->>`` and
``->`` operators.

Migration #30 recasts any rows still in the broken shape via
``(col #>> '{}')::jsonb``, which extracts the JSON value as text and reparses
as JSONB. Idempotent — guarded by ``WHERE jsonb_typeof(col) = 'string'``.

Tests skip cleanly if PostgreSQL is unavailable.
"""

import json
import os

import pytest

from athanor.storage.database import DatabasePool

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def db_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", "postgresql://athanor:athanor@localhost:5432/athanor_test")


@pytest.fixture
async def fresh_db(db_url: str) -> DatabasePool:
    """A connected pool with a wiped schema. Skips when Postgres is unavailable."""
    import asyncpg

    db = DatabasePool(db_url)
    try:
        await db.connect()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    async with db.pool.acquire() as conn:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    yield db
    await db.close()


async def _apply_migrations_up_to(db: DatabasePool, last_version: int) -> None:
    """Apply un-applied migrations up to (and including) ``last_version``."""
    from athanor.storage.migrations.runner import MIGRATIONS

    async with db.pool.acquire() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS athanor_migrations ("
            "version INTEGER PRIMARY KEY, "
            "description TEXT NOT NULL, "
            "applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        current = await conn.fetchval("SELECT COALESCE(MAX(version), 0) FROM athanor_migrations")
        for version, description, sql in MIGRATIONS:
            if version <= current:
                continue
            if version > last_version:
                break
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO athanor_migrations (version, description) VALUES ($1, $2)",
                    version,
                    description,
                )


async def _seed_double_encoded_qa_app_result(db: DatabasePool, *, job_id: str, app_name: str) -> None:
    """Reproduce the pre-fix double-encoded shape on qa_app_results.

    We bypass the pool-level codec so we can write a raw text value (the
    JSON-string-scalar that the legacy code stored).
    """
    cache_hits_payload = json.dumps(
        {
            "prebaked_image": True,
            "shared_volume": False,
            "turbo_remote_hits": ["a", "b"],
            "turbo_remote_misses": ["c"],
        }
    )
    raw_result_payload = json.dumps({"trace": {"id": "trc-legacy"}})
    boot_phase_payload = json.dumps({"outcome": "passed", "attempts": 1})

    # Seed the parent jobs row (qa_app_results FKs to jobs.job_id).
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task) VALUES ($1, 'org/repo', 'test') ON CONFLICT (job_id) DO NOTHING",
        job_id,
    )

    # Insert with the legacy double-encoded shape: write the JSON-encoded
    # string into a text-cast column, then cast to jsonb. Postgres parses the
    # outer string, leaving a JSONB string scalar.
    async with db.pool.acquire() as conn:
        # Disable the pool's JSONB codec for this connection so we can write
        # the broken shape verbatim. We use to_jsonb(text) which is exactly
        # what double-encoding produces server-side.
        await conn.execute(
            """
            INSERT INTO qa_app_results (
                job_id, app_name, status, duration_ms,
                cache_hits, raw_result, boot_phase
            )
            VALUES (
                $1, $2, 'passed', 100,
                to_jsonb($3::text),
                to_jsonb($4::text),
                to_jsonb($5::text)
            )
            ON CONFLICT (job_id, app_name) DO UPDATE
                SET cache_hits = EXCLUDED.cache_hits,
                    raw_result = EXCLUDED.raw_result,
                    boot_phase = EXCLUDED.boot_phase
            """,
            job_id,
            app_name,
            cache_hits_payload,
            raw_result_payload,
            boot_phase_payload,
        )


@pytest.mark.asyncio
async def test_migration_30_recasts_legacy_double_encoded_rows(fresh_db: DatabasePool):
    # Apply through migration 29 (pre-fix world) and seed the broken shape.
    await _apply_migrations_up_to(fresh_db, 29)
    await _seed_double_encoded_qa_app_result(fresh_db, job_id="legacy-1", app_name="hub")

    # Confirm the seed produced the broken shape.
    pre = await fresh_db.fetchrow(
        "SELECT jsonb_typeof(cache_hits) AS ch, jsonb_typeof(raw_result) AS rr, "
        "jsonb_typeof(boot_phase) AS bp "
        "FROM qa_app_results WHERE job_id = 'legacy-1'"
    )
    assert pre["ch"] == "string"
    assert pre["rr"] == "string"
    assert pre["bp"] == "string"

    # Apply migration 30 — the recast.
    await _apply_migrations_up_to(fresh_db, 30)

    post = await fresh_db.fetchrow(
        """
        SELECT
            jsonb_typeof(cache_hits)              AS ch,
            jsonb_typeof(raw_result)              AS rr,
            jsonb_typeof(boot_phase)              AS bp,
            cache_hits ->> 'prebaked_image'       AS prebaked,
            jsonb_array_length(cache_hits -> 'turbo_remote_hits') AS hits,
            raw_result -> 'trace' ->> 'id'        AS trace_id,
            boot_phase ->> 'outcome'              AS boot_outcome
        FROM qa_app_results
        WHERE job_id = 'legacy-1'
        """
    )
    # All three columns are now real JSONB objects.
    assert post["ch"] == "object"
    assert post["rr"] == "object"
    assert post["bp"] == "object"
    # Server-side -> / ->> operators now see the inner fields.
    assert post["prebaked"] == "true"
    assert post["hits"] == 2
    assert post["trace_id"] == "trc-legacy"
    assert post["boot_outcome"] == "passed"


@pytest.mark.asyncio
async def test_migration_30_is_idempotent(fresh_db: DatabasePool):
    """Re-running migration 30's SQL on already-fixed rows is a no-op."""
    await _apply_migrations_up_to(fresh_db, 29)
    await _seed_double_encoded_qa_app_result(fresh_db, job_id="legacy-2", app_name="hub")
    await _apply_migrations_up_to(fresh_db, 30)

    # Run the same UPDATE statements directly — should affect zero rows.
    result1 = await fresh_db.execute(
        "UPDATE qa_app_results SET cache_hits = (cache_hits #>> '{}')::jsonb WHERE jsonb_typeof(cache_hits) = 'string'"
    )
    result2 = await fresh_db.execute(
        "UPDATE qa_app_results SET raw_result = (raw_result #>> '{}')::jsonb WHERE jsonb_typeof(raw_result) = 'string'"
    )
    result3 = await fresh_db.execute(
        "UPDATE qa_app_results "
        "SET boot_phase = (boot_phase #>> '{}')::jsonb "
        "WHERE boot_phase IS NOT NULL AND jsonb_typeof(boot_phase) = 'string'"
    )
    # asyncpg execute returns "UPDATE <count>" on success.
    assert result1.endswith(" 0")
    assert result2.endswith(" 0")
    assert result3.endswith(" 0")


@pytest.mark.asyncio
async def test_migration_30_skips_already_correct_rows(fresh_db: DatabasePool):
    """Rows already shaped as objects must NOT be touched (no-op)."""
    await _apply_migrations_up_to(fresh_db, 29)

    # Seed a correctly-shaped row via the codec.
    await fresh_db.execute(
        "INSERT INTO jobs (job_id, repo, task) VALUES ($1, 'org/repo', 'test')",
        "good-1",
    )
    await fresh_db.execute(
        """
        INSERT INTO qa_app_results (
            job_id, app_name, status, duration_ms, cache_hits
        )
        VALUES ($1, 'hub', 'passed', 100, $2::jsonb)
        """,
        "good-1",
        {"prebaked_image": True, "shared_volume": False, "turbo_remote_hits": [], "turbo_remote_misses": []},
    )

    pre = await fresh_db.fetchrow("SELECT cache_hits FROM qa_app_results WHERE job_id = 'good-1'")

    await _apply_migrations_up_to(fresh_db, 30)

    post = await fresh_db.fetchrow(
        "SELECT jsonb_typeof(cache_hits) AS ch_type, cache_hits FROM qa_app_results WHERE job_id = 'good-1'"
    )
    assert post["ch_type"] == "object"
    assert post["cache_hits"] == pre["cache_hits"]
