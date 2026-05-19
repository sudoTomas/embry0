import pytest

pytestmark = pytest.mark.requires_postgres


@pytest.mark.asyncio
async def test_qa_image_tags_table_created(db_with_migrations):
    oid = await db_with_migrations.fetchval("SELECT to_regclass('qa_image_tags')")
    assert oid is not None


@pytest.mark.asyncio
async def test_qa_volume_state_table_created(db_with_migrations):
    oid = await db_with_migrations.fetchval("SELECT to_regclass('qa_volume_state')")
    assert oid is not None


@pytest.mark.asyncio
async def test_qa_volume_state_scope_check(db_with_migrations):
    await db_with_migrations.execute(
        "INSERT INTO qa_volume_state (scope, scope_key, volume_name) VALUES ('per-job', 'job-1', 'vol-1')"
    )
    with pytest.raises(Exception):
        await db_with_migrations.execute(
            "INSERT INTO qa_volume_state (scope, scope_key, volume_name) VALUES ('invalid-scope', 'x', 'y')"
        )
