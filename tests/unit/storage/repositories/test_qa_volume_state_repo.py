import pytest
pytestmark = pytest.mark.requires_postgres

from athanor.storage.repositories.qa_volume_state import (
    QAVolumeStateRepository,
    VolumeState,
)


@pytest.fixture
async def repo(db_with_migrations):
    return QAVolumeStateRepository(db=db_with_migrations)


@pytest.mark.asyncio
async def test_upsert_then_get(repo: QAVolumeStateRepository):
    await repo.upsert(
        scope="per-repo",
        scope_key="org/r1",
        volume_name="qa-vol-org-r1",
        last_warmed_sha="abc",
    )
    state = await repo.get(scope="per-repo", scope_key="org/r1")
    assert state is not None
    assert state.volume_name == "qa-vol-org-r1"
    assert state.last_warmed_sha == "abc"


@pytest.mark.asyncio
async def test_upsert_replaces_on_conflict(repo: QAVolumeStateRepository):
    await repo.upsert(
        scope="per-repo", scope_key="org/r1",
        volume_name="qa-vol-org-r1", last_warmed_sha="aaa",
    )
    await repo.upsert(
        scope="per-repo", scope_key="org/r1",
        volume_name="qa-vol-org-r1", last_warmed_sha="bbb",
    )
    state = await repo.get(scope="per-repo", scope_key="org/r1")
    assert state.last_warmed_sha == "bbb"


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown(repo: QAVolumeStateRepository):
    state = await repo.get(scope="per-job", scope_key="ghost")
    assert state is None
