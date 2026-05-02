from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
async def api_with_minio(api_client):
    minio = MagicMock()
    minio.presign_get = AsyncMock(return_value="http://minio/qa-artifacts/JOB1/1/result.json?signed")
    minio.list_objects = AsyncMock(return_value=["JOB1/1/result.json"])
    api_client.app.state.qa_minio = minio
    yield api_client


async def test_artifact_redirect_to_presigned_url(api_with_minio):
    r = await api_with_minio.get(
        "/api/v1/jobs/JOB1/artifacts/result.json",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"].startswith("http://minio/")


async def test_artifact_unsafe_path_rejected(api_with_minio):
    r = await api_with_minio.get("/api/v1/jobs/JOB1/artifacts/../../etc/passwd")
    assert r.status_code == 404


async def test_artifact_503_when_minio_unconfigured(api_client):
    api_client.app.state.qa_minio = None
    r = await api_client.get("/api/v1/jobs/JOB1/artifacts/result.json")
    assert r.status_code == 503
