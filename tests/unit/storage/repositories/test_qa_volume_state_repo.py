import pytest

from embry0.storage.repositories.qa_volume_state import (
    QAVolumeStateRepository,
)

pytestmark = pytest.mark.requires_postgres


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
        scope="per-repo",
        scope_key="org/r1",
        volume_name="qa-vol-org-r1",
        last_warmed_sha="aaa",
    )
    await repo.upsert(
        scope="per-repo",
        scope_key="org/r1",
        volume_name="qa-vol-org-r1",
        last_warmed_sha="bbb",
    )
    state = await repo.get(scope="per-repo", scope_key="org/r1")
    assert state.last_warmed_sha == "bbb"


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown(repo: QAVolumeStateRepository):
    state = await repo.get(scope="per-job", scope_key="ghost")
    assert state is None


@pytest.mark.asyncio
async def test_upsert_preserves_last_warmed_sha_when_none_passed(repo: QAVolumeStateRepository):
    """Passing last_warmed_sha=None on a subsequent upsert must NOT clobber
    a previously-recorded SHA. Volume idempotency depends on this.
    Regression test for ultrareview bug_009."""
    # First write: real SHA from the warmer
    await repo.upsert(
        scope="per-repo",
        scope_key="org/r1",
        volume_name="embry0-qa-vol-org-r1",
        last_warmed_sha="abc123",
    )

    # Second write: ensure() pre-existing-volume path passes None
    await repo.upsert(
        scope="per-repo",
        scope_key="org/r1",
        volume_name="embry0-qa-vol-org-r1",
        last_warmed_sha=None,
    )

    # The SHA should be preserved, not nulled out
    state = await repo.get(scope="per-repo", scope_key="org/r1")
    assert state is not None
    assert state.last_warmed_sha == "abc123"


@pytest.mark.asyncio
async def test_reset_warmth_clears_a_recorded_sha(repo: QAVolumeStateRepository):
    """A freshly created (empty) volume must clear any stale warmth, or the
    warmer skips against the old SHA and a sub-task mounts an empty tree."""
    import inspect

    assert "reset_warmth" in inspect.signature(QAVolumeStateRepository.upsert).parameters, (
        "QAVolumeStateRepository.upsert() takes no reset_warmth: nothing clears the warmth of a new volume"
    )
    await repo.upsert(
        scope="per-repo",
        scope_key="org/r1",
        volume_name="embry0-qa-vol-per-repo-x",
        last_warmed_sha="abc123",
    )
    # ensure()'s create path re-records with reset_warmth=True and no SHA.
    await repo.upsert(
        scope="per-repo",
        scope_key="org/r1",
        volume_name="embry0-qa-vol-per-repo-x",
        last_warmed_sha=None,
        reset_warmth=True,
    )
    state = await repo.get(scope="per-repo", scope_key="org/r1")
    assert state is not None
    assert state.last_warmed_sha is None, (
        "reset_warmth=True must clear the recorded SHA so a recreated volume is re-warmed"
    )


@pytest.mark.asyncio
async def test_delete_by_volume_name_removes_only_that_row(repo: QAVolumeStateRepository):
    """#328: the row of a removed volume is deleted; others stay."""
    await repo.upsert(scope="per-job", scope_key="job-1", volume_name="embry0-qa-vol-job-1", last_warmed_sha=None)
    await repo.upsert(scope="per-job", scope_key="job-10", volume_name="embry0-qa-vol-job-10", last_warmed_sha=None)
    await repo.delete_by_volume_name("embry0-qa-vol-job-1")
    assert await repo.get(scope="per-job", scope_key="job-1") is None
    assert await repo.get(scope="per-job", scope_key="job-10") is not None
