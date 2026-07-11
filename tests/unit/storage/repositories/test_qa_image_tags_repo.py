import pytest

from embry0.storage.repositories.qa_image_tags import (
    QAImageTagsRepository,
)

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def repo(db_with_migrations):
    return QAImageTagsRepository(db=db_with_migrations)


@pytest.mark.asyncio
async def test_record_tag_then_get_active(repo: QAImageTagsRepository):
    await repo.record(
        repo="org/r1",
        image_tag="org_r1:2026-05-05T06-00",
        lockfile_sha="abc" * 8,
        built_by="cli",
    )
    active = await repo.get_active("org/r1")
    assert active is not None
    assert active.image_tag == "org_r1:2026-05-05T06-00"
    assert active.is_active is True


@pytest.mark.asyncio
async def test_record_same_lockfile_returns_existing(repo: QAImageTagsRepository):
    """Composite uniqueness on (repo, lockfile_sha) — re-recording same hash
    must NOT create a duplicate row but should bump built_at."""
    await repo.record(repo="org/r1", image_tag="t1", lockfile_sha="aaa", built_by="cli")
    await repo.record(repo="org/r1", image_tag="t1", lockfile_sha="aaa", built_by="cli")
    rows = await repo.list_for_repo("org/r1")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_get_active_returns_most_recent(repo: QAImageTagsRepository):
    """Multiple lockfile shas → multiple rows. Active = most recent."""
    await repo.record(repo="org/r1", image_tag="t1", lockfile_sha="aaa", built_by="cli")
    await repo.record(repo="org/r1", image_tag="t2", lockfile_sha="bbb", built_by="cli")
    active = await repo.get_active("org/r1")
    assert active.image_tag == "t2"


@pytest.mark.asyncio
async def test_deactivate_marks_row_inactive(repo: QAImageTagsRepository):
    await repo.record(repo="org/r1", image_tag="t1", lockfile_sha="aaa", built_by="cli")
    await repo.deactivate(repo="org/r1", image_tag="t1")
    active = await repo.get_active("org/r1")
    assert active is None
