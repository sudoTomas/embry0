import pytest

pytestmark = pytest.mark.requires_postgres


@pytest.mark.asyncio
async def test_qa_image_tags_table_created(pg_pool):
    async with pg_pool.acquire() as conn:
        oid = await conn.fetchval("SELECT to_regclass('qa_image_tags')")
        assert oid is not None


@pytest.mark.asyncio
async def test_qa_volume_state_table_created(pg_pool):
    async with pg_pool.acquire() as conn:
        oid = await conn.fetchval("SELECT to_regclass('qa_volume_state')")
        assert oid is not None


@pytest.mark.asyncio
async def test_qa_volume_state_scope_check(pg_pool):
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO qa_volume_state (scope, scope_key, volume_name) "
            "VALUES ('per-job', 'job-1', 'vol-1')"
        )
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO qa_volume_state (scope, scope_key, volume_name) "
                "VALUES ('invalid-scope', 'x', 'y')"
            )
