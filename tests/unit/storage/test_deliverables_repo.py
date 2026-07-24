"""DeliverablesRepository (RAV-603) — parameter shape + type validation."""

import pytest

from embry0.storage.repositories.deliverables import DELIVERABLE_TYPES, DeliverablesRepository


class _FakeDb:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    async def execute(self, sql, *params):
        self.executed.append((sql, params))

    async def fetch(self, sql, *params):
        self.executed.append((sql, params))
        return []

    async def fetchrow(self, sql, *params):
        self.executed.append((sql, params))
        return None


@pytest.mark.asyncio
async def test_create_rejects_unknown_type():
    repo = DeliverablesRepository(_FakeDb())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown deliverable type"):
        await repo.create(job_id="job-1", type="screenshot")


@pytest.mark.asyncio
async def test_create_inserts_all_fields():
    db = _FakeDb()
    repo = DeliverablesRepository(db)  # type: ignore[arg-type]
    deliverable_id = await repo.create(
        job_id="job-1",
        type="artifact",
        title="out.csv",
        storage_bucket="job-deliverables",
        storage_key="job-1/out.csv",
        media_type="text/csv",
        size_bytes=4,
    )
    assert deliverable_id.startswith("del-")
    sql, params = db.executed[0]
    assert "INSERT INTO deliverables" in sql
    # (id, job_id, type, title, content, url, bucket, key, media, size, metadata)
    assert params[1:] == (
        "job-1",
        "artifact",
        "out.csv",
        None,
        None,
        "job-deliverables",
        "job-1/out.csv",
        "text/csv",
        4,
        {},
    )


def test_type_vocabulary_matches_ticket():
    assert DELIVERABLE_TYPES == {"pr", "report", "artifact", "message"}


@pytest.mark.asyncio
async def test_list_for_job_orders_by_creation():
    db = _FakeDb()
    repo = DeliverablesRepository(db)  # type: ignore[arg-type]
    assert await repo.list_for_job("job-1") == []
    sql, params = db.executed[0]
    assert "ORDER BY created_at" in sql
    assert params == ("job-1",)
