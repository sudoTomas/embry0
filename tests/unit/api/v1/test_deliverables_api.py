"""Deliverables API (RAV-603) — list + artifact download resolution."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

REPORT = {
    "id": "del-report1",
    "job_id": "job-1",
    "type": "report",
    "title": "research report",
    "content": "findings",
    "url": None,
    "storage_bucket": None,
    "storage_key": None,
    "media_type": None,
    "size_bytes": None,
    "metadata": {},
}

ARTIFACT = {
    **REPORT,
    "id": "del-art1",
    "type": "artifact",
    "title": "out.csv",
    "content": None,
    "storage_bucket": "job-deliverables",
    "storage_key": "job-1/out.csv",
    "media_type": "text/csv",
    "size_bytes": 4,
}


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    repo = MagicMock()
    repo.list_for_job = AsyncMock(return_value=[REPORT, ARTIFACT])
    repo.get = AsyncMock(return_value=ARTIFACT)
    app.state.deliverables_repo = repo
    minio = MagicMock()
    minio.stat_object = AsyncMock(return_value={"size": 4})
    minio.get_object_bytes = AsyncMock(return_value=b"a,b\n")
    app.state.qa_minio = minio
    return app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_list_deliverables(app):
    async with _client(app) as client:
        resp = await client.get("/api/v1/jobs/job-1/deliverables")
    assert resp.status_code == 200
    data = resp.json()
    assert [d["type"] for d in data] == ["report", "artifact"]
    app.state.deliverables_repo.list_for_job.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
async def test_download_streams_bytes_with_attachment_disposition(app):
    async with _client(app) as client:
        resp = await client.get("/api/v1/jobs/job-1/deliverables/del-art1/download")
    assert resp.status_code == 200
    assert resp.content == b"a,b\n"
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == 'attachment; filename="out.csv"'
    app.state.qa_minio.get_object_bytes.assert_awaited_once_with("job-deliverables", "job-1/out.csv")


@pytest.mark.asyncio
async def test_download_oversized_artifact_413s(app):
    app.state.qa_minio.stat_object = AsyncMock(return_value={"size": 51 * 1024 * 1024})
    async with _client(app) as client:
        resp = await client.get("/api/v1/jobs/job-1/deliverables/del-art1/download")
    assert resp.status_code == 413
    app.state.qa_minio.get_object_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_wrong_job_404s(app):
    async with _client(app) as client:
        resp = await client.get("/api/v1/jobs/job-OTHER/deliverables/del-art1/download")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_non_artifact_400s(app):
    app.state.deliverables_repo.get = AsyncMock(return_value=REPORT)
    async with _client(app) as client:
        resp = await client.get("/api/v1/jobs/job-1/deliverables/del-report1/download")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_download_missing_deliverable_404s(app):
    app.state.deliverables_repo.get = AsyncMock(return_value=None)
    async with _client(app) as client:
        resp = await client.get("/api/v1/jobs/job-1/deliverables/nope/download")
    assert resp.status_code == 404
