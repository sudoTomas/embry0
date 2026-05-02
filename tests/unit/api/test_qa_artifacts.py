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


async def test_latest_screenshot_returns_most_recent(api_with_minio):
    api_with_minio.app.state.qa_minio.list_objects = AsyncMock(return_value=[
        "JOB1/1/screenshots/boot/2026-04-30T12:00:00.png",
        "JOB1/1/screenshots/exploratory/2026-04-30T12:05:30-login.png",
        "JOB1/1/screenshots/exploratory/2026-04-30T12:08:11-portfolio.png",
        "JOB1/1/result.json",
    ])
    # Extract the ISO timestamp embedded in the filename (e.g. `2026-04-30T12:00:00`)
    # and use it as the mock `last_modified`. The filename may have an optional
    # `-<slug>` suffix before `.png`, so we take the first 19 chars of the basename.
    api_with_minio.app.state.qa_minio.stat_object = AsyncMock(side_effect=lambda b, k: {
        "size": 1, "etag": "x",
        "last_modified": __import__("datetime").datetime.fromisoformat(
            k.split("/")[-1][:19] + "+00:00"
        )
    })
    api_with_minio.app.state.qa_minio.presign_get = AsyncMock(return_value="http://minio/latest")

    r = await api_with_minio.get(
        "/api/v1/jobs/JOB1/artifacts/screenshots/latest",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "http://minio/latest" in r.headers["location"]


async def test_latest_screenshot_404_when_no_screenshots(api_with_minio):
    api_with_minio.app.state.qa_minio.list_objects = AsyncMock(return_value=[
        "JOB1/1/result.json",  # no screenshots
    ])
    r = await api_with_minio.get("/api/v1/jobs/JOB1/artifacts/screenshots/latest")
    assert r.status_code == 404


async def test_latest_screenshot_spans_multiple_attempts(api_with_minio):
    # Screenshots exist under both attempt 1 and attempt 2. The route uses
    # prefix=f"{job_id}/" (not scoped to a single attempt), so it should pick
    # the most recent screenshot across ALL attempts — here, the one in
    # attempt 2 with the latest mtime.
    import datetime as _dt

    api_with_minio.app.state.qa_minio.list_objects = AsyncMock(return_value=[
        "JOB1/1/screenshots/boot/2026-04-30T12:00:00.png",
        "JOB1/1/screenshots/exploratory/2026-04-30T12:05:00-old.png",
        "JOB1/2/screenshots/boot/2026-04-30T13:00:00.png",
        "JOB1/2/screenshots/exploratory/2026-04-30T13:10:00-newest.png",
        "JOB1/2/result.json",
    ])
    mtimes = {
        "JOB1/1/screenshots/boot/2026-04-30T12:00:00.png":
            _dt.datetime.fromisoformat("2026-04-30T12:00:00+00:00"),
        "JOB1/1/screenshots/exploratory/2026-04-30T12:05:00-old.png":
            _dt.datetime.fromisoformat("2026-04-30T12:05:00+00:00"),
        "JOB1/2/screenshots/boot/2026-04-30T13:00:00.png":
            _dt.datetime.fromisoformat("2026-04-30T13:00:00+00:00"),
        "JOB1/2/screenshots/exploratory/2026-04-30T13:10:00-newest.png":
            _dt.datetime.fromisoformat("2026-04-30T13:10:00+00:00"),
    }
    api_with_minio.app.state.qa_minio.stat_object = AsyncMock(
        side_effect=lambda b, k: {"size": 1, "etag": "x", "last_modified": mtimes[k]}
    )
    api_with_minio.app.state.qa_minio.presign_get = AsyncMock(
        return_value="http://minio/latest-across-attempts"
    )

    r = await api_with_minio.get(
        "/api/v1/jobs/JOB1/artifacts/screenshots/latest",
        follow_redirects=False,
    )
    assert r.status_code == 302
    # Verify the route picked the screenshot from attempt 2 (the most recent).
    call_args = api_with_minio.app.state.qa_minio.presign_get.call_args
    key = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["key"]
    assert key == "JOB1/2/screenshots/exploratory/2026-04-30T13:10:00-newest.png"
