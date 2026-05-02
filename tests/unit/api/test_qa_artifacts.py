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


async def test_artifact_url_encoded_traversal_rejected(api_with_minio):
    # URL-encoded `..` segment must be rejected after FastAPI decoding.
    r = await api_with_minio.get("/api/v1/jobs/JOB1/artifacts/foo%2F..%2Fbar")
    assert r.status_code == 404


async def test_artifact_no_attempts_returns_404(api_client):
    minio = MagicMock()
    minio.presign_get = AsyncMock(return_value="http://minio/should-not-be-called")
    minio.list_objects = AsyncMock(return_value=[])
    api_client.app.state.qa_minio = minio

    r = await api_client.get("/api/v1/jobs/JOB1/artifacts/result.json")
    assert r.status_code == 404
    assert r.json()["detail"] == "no attempts found"


async def test_artifact_malformed_keys_returns_404(api_client):
    # Keys whose attempt segment is missing or non-numeric should be ignored
    # by _resolve_latest_attempt → no valid attempts → 404.
    minio = MagicMock()
    minio.presign_get = AsyncMock(return_value="http://minio/should-not-be-called")
    minio.list_objects = AsyncMock(return_value=["JOB1/", "JOB1/notanumber/x", "JOB1/3a/x"])
    api_client.app.state.qa_minio = minio

    r = await api_client.get("/api/v1/jobs/JOB1/artifacts/result.json")
    assert r.status_code == 404
    assert r.json()["detail"] == "no attempts found"


async def test_artifact_numeric_attempt_sort(api_client):
    # With attempts 1, 2, 10 present, the resolver must pick 10 (numeric max),
    # not "2" (lexical max).
    minio = MagicMock()
    minio.presign_get = AsyncMock(return_value="http://minio/qa-artifacts/JOB1/10/x?signed")
    minio.list_objects = AsyncMock(return_value=["JOB1/1/x", "JOB1/2/x", "JOB1/10/x"])
    api_client.app.state.qa_minio = minio

    r = await api_client.get("/api/v1/jobs/JOB1/artifacts/x", follow_redirects=False)
    assert r.status_code == 302
    # Verify the resolver picked attempt 10 by inspecting the key passed to presign_get.
    call_args = minio.presign_get.call_args
    # presign_get(bucket, key, expires_seconds=...)
    key = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["key"]
    assert "/10/" in key
