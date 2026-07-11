"""AgentSessionsRepository: upsert + get round-trip; delete; per-job cleanup."""

import pytest

from embry0.storage.database import DatabasePool

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def db(db_with_migrations: DatabasePool) -> DatabasePool:
    return db_with_migrations


async def _seed_job(db: DatabasePool, job_id: str) -> None:
    """Insert a minimal jobs row so agent_sessions FK is satisfied."""
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task) VALUES ($1, $2, $3) ON CONFLICT (job_id) DO NOTHING",
        job_id,
        "test/repo",
        "test task",
    )


@pytest.mark.asyncio
async def test_upsert_and_get(db):
    from embry0.storage.repositories.agent_sessions import AgentSessionsRepository

    await _seed_job(db, "job-test-as1")
    repo = AgentSessionsRepository(db)
    await repo.upsert(
        job_id="job-test-as1",
        agent_type="developer",
        mode="anthropic_api",
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hi back"}],
    )
    row = await repo.get(job_id="job-test-as1", agent_type="developer")
    assert row is not None
    assert row["mode"] == "anthropic_api"
    assert row["messages"] == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hi back"}]
    assert row["session_id"] is None
    assert row["session_blob"] is None


@pytest.mark.asyncio
async def test_upsert_overwrites_same_job_agent(db):
    from embry0.storage.repositories.agent_sessions import AgentSessionsRepository

    await _seed_job(db, "job-test-as2")
    repo = AgentSessionsRepository(db)
    await repo.upsert(job_id="job-test-as2", agent_type="developer", mode="claude_max", session_id="sess-1")
    await repo.upsert(job_id="job-test-as2", agent_type="developer", mode="claude_max", session_id="sess-2")
    row = await repo.get(job_id="job-test-as2", agent_type="developer")
    assert row["session_id"] == "sess-2"


@pytest.mark.asyncio
async def test_delete_for_job_cascades_to_all_agents(db):
    from embry0.storage.repositories.agent_sessions import AgentSessionsRepository

    await _seed_job(db, "job-test-as3")
    repo = AgentSessionsRepository(db)
    await repo.upsert(job_id="job-test-as3", agent_type="developer", mode="claude_max", session_id="s")
    await repo.upsert(job_id="job-test-as3", agent_type="review", mode="claude_max", session_id="s")
    await repo.delete_for_job("job-test-as3")
    assert await repo.get("job-test-as3", "developer") is None
    assert await repo.get("job-test-as3", "review") is None
