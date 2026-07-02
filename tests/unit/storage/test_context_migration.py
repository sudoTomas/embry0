import json
import pytest

pytestmark = pytest.mark.requires_postgres


@pytest.mark.asyncio
async def test_repo_is_nullable_after_migration(db_with_migrations):
    # A row with NULL repo must be insertable (was NOT NULL before INT-599).
    await db_with_migrations.execute(
        "INSERT INTO jobs (job_id, task) VALUES ('job-nullrepo', 'research')"
    )
    row = await db_with_migrations.fetchrow(
        "SELECT repo, context FROM jobs WHERE job_id = 'job-nullrepo'"
    )
    assert row["repo"] is None


@pytest.mark.asyncio
async def test_context_column_exists(db_with_migrations):
    exists = await db_with_migrations.fetchval(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='jobs' AND column_name='context'"
    )
    assert exists == 1
