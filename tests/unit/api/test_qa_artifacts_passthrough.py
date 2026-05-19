"""Unit tests for the per-sub-task artifact passthrough endpoint (Phase 5B).

The dashboard renders screenshots / console logs / network failures inline by
calling:

  GET /api/v1/qa/runs/{run_id}/apps/{app}/artifacts/{kind}            -> filenames list
  GET /api/v1/qa/runs/{run_id}/apps/{app}/artifacts/{kind}/{filename} -> raw bytes

Both endpoints are under the existing dashboard `auth_deps` and reuse the
`get_qa_minio` dependency. Tests stub MinIO via ``api_client.app.state.qa_minio``
to avoid spinning up a real MinIO — same pattern as ``test_qa_artifacts.py``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
async def api_with_minio(api_client):
    """Wire a stub QA MinIO client into the test app's state."""
    minio = MagicMock()
    minio.list_objects = AsyncMock(return_value=[])
    minio.get_object_bytes = AsyncMock(return_value=b"")
    # Default ``stat_object`` to a small size — the passthrough endpoint
    # consults it before fetching bytes to enforce ``_MAX_ARTIFACT_BYTES``.
    # Tests that need to assert oversized rejection override this.
    minio.stat_object = AsyncMock(return_value={"size": 1024, "etag": "x", "last_modified": None})
    api_client.app.state.qa_minio = minio
    yield api_client


# ─── GET /qa/runs/{run_id}/apps/{app}/artifacts/{kind}/{filename} ──────────


async def test_artifact_passthrough_returns_screenshot_bytes(api_with_minio):
    """Seeded PNG → 200, content-type=image/png, body matches the seeded bytes."""
    sub = "RUN1__hub"
    body = b"\x89PNG\r\n\x1a\nfake-bytes"
    api_with_minio.app.state.qa_minio.list_objects = AsyncMock(return_value=[f"{sub}/1/screenshots/boot.png"])
    api_with_minio.app.state.qa_minio.get_object_bytes = AsyncMock(return_value=body)

    r = await api_with_minio.get("/api/v1/qa/runs/RUN1/apps/hub/artifacts/screenshots/boot.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "private, max-age=300"
    assert r.content == body
    # Verify the resolved key targets the latest attempt with the file.
    call = api_with_minio.app.state.qa_minio.get_object_bytes.call_args
    key = call.args[1] if len(call.args) > 1 else call.kwargs["key"]
    assert key == f"{sub}/1/screenshots/boot.png"


async def test_artifact_passthrough_picks_latest_attempt_with_file(api_with_minio):
    """When attempts 1 + 3 both have the file, attempt 3 is served (numeric max).

    Attempt 2 is present in the listing but does NOT have this file — the
    resolver must skip it rather than picking the highest attempt that
    happens to exist.
    """
    sub = "RUN1__hub"
    api_with_minio.app.state.qa_minio.list_objects = AsyncMock(
        return_value=[
            f"{sub}/1/screenshots/boot.png",
            f"{sub}/2/screenshots/other.png",  # different filename; should be skipped
            f"{sub}/3/screenshots/boot.png",
        ]
    )
    api_with_minio.app.state.qa_minio.get_object_bytes = AsyncMock(return_value=b"x")
    r = await api_with_minio.get("/api/v1/qa/runs/RUN1/apps/hub/artifacts/screenshots/boot.png")
    assert r.status_code == 200
    call = api_with_minio.app.state.qa_minio.get_object_bytes.call_args
    key = call.args[1] if len(call.args) > 1 else call.kwargs["key"]
    assert key == f"{sub}/3/screenshots/boot.png"


async def test_artifact_passthrough_returns_404_when_missing(api_with_minio):
    """Filename that no attempt has uploaded → 404."""
    api_with_minio.app.state.qa_minio.list_objects = AsyncMock(
        return_value=["RUN1__hub/1/screenshots/something-else.png"]
    )
    r = await api_with_minio.get("/api/v1/qa/runs/RUN1/apps/hub/artifacts/screenshots/missing.png")
    assert r.status_code == 404
    assert r.json()["detail"] == "artifact not found"


async def test_artifact_passthrough_rejects_path_traversal(api_with_minio):
    """A `..` segment in the filename must never resolve to bytes (400 or 404, never 200)."""
    # FastAPI normalises some traversal forms in path parsing. Test multiple
    # encodings to cover the surface area.
    paths = [
        "/api/v1/qa/runs/RUN1/apps/hub/artifacts/screenshots/..%2F..%2Fetc%2Fpasswd",
        "/api/v1/qa/runs/RUN1/apps/hub/artifacts/screenshots/..",
    ]
    for p in paths:
        r = await api_with_minio.get(p)
        assert r.status_code in (400, 404), f"{p}: {r.status_code} {r.text}"
        assert r.status_code != 200


async def test_artifact_passthrough_rejects_leading_dot_filename(api_with_minio):
    """A filename starting with `.` (e.g. .ssh keys) is rejected with 400."""
    r = await api_with_minio.get("/api/v1/qa/runs/RUN1/apps/hub/artifacts/screenshots/.htaccess")
    assert r.status_code == 400


async def test_artifact_passthrough_rejects_bad_kind(api_with_minio):
    """`kind` outside the allow-list → 400 (NOT 404)."""
    r = await api_with_minio.get("/api/v1/qa/runs/RUN1/apps/hub/artifacts/secret/boot.png")
    assert r.status_code == 400
    assert r.json()["detail"] == "bad artifact kind"


async def test_artifact_passthrough_rejects_bad_app_name(api_with_minio):
    """An app name that starts with `-` is rejected — defense-in-depth.

    The app name is concatenated into the MinIO prefix, so a leading `-` /
    embedded slash / control char must not be honoured.
    """
    # Leading `-` in the app name. Routed via FastAPI as a path param so the
    # value reaches our validator.
    r = await api_with_minio.get("/api/v1/qa/runs/RUN1/apps/-evil/artifacts/screenshots/boot.png")
    assert r.status_code == 400


async def test_artifact_passthrough_picks_correct_media_type(api_with_minio):
    """Each known extension maps to the expected Content-Type."""
    sub = "RUN1__hub"
    cases = [
        ("screenshots", "shot.png", "image/png"),
        ("screenshots", "shot.jpg", "image/jpeg"),
        ("network", "fail.har", "application/json"),
        ("network", "fail.json", "application/json"),
        ("console", "browser.log", "text/plain; charset=utf-8"),
        ("traces", "exploratory.zip", "application/zip"),
    ]
    for kind, fn, expected in cases:
        api_with_minio.app.state.qa_minio.list_objects = AsyncMock(return_value=[f"{sub}/1/{kind}/{fn}"])
        api_with_minio.app.state.qa_minio.get_object_bytes = AsyncMock(return_value=b"x")
        r = await api_with_minio.get(f"/api/v1/qa/runs/RUN1/apps/hub/artifacts/{kind}/{fn}")
        assert r.status_code == 200, f"{kind}/{fn}: {r.text}"
        assert r.headers["content-type"] == expected, f"{kind}/{fn}"


async def test_artifact_passthrough_rejects_oversized_payloads(api_with_minio):
    """A ``stat_object`` size larger than the cap → 413 + bytes never fetched.

    Defense-in-depth against a misconfigured agent uploading a multi-GB HAR
    or trace zip, which would otherwise OOM the orchestrator the first time
    anyone clicks the panel.
    """
    from athanor.api.v1.qa_artifacts import _MAX_ARTIFACT_BYTES

    sub = "RUN1__hub"
    api_with_minio.app.state.qa_minio.list_objects = AsyncMock(return_value=[f"{sub}/1/traces/giant.zip"])
    api_with_minio.app.state.qa_minio.stat_object = AsyncMock(
        return_value={"size": _MAX_ARTIFACT_BYTES + 1, "etag": "x", "last_modified": None}
    )
    # Tripwire: ``get_object_bytes`` must NOT be called when the size check
    # rejects. Fail loudly if it ever is.
    api_with_minio.app.state.qa_minio.get_object_bytes = AsyncMock(
        side_effect=AssertionError("get_object_bytes called despite oversized payload"),
    )

    r = await api_with_minio.get("/api/v1/qa/runs/RUN1/apps/hub/artifacts/traces/giant.zip")
    assert r.status_code == 413
    assert "too large" in r.json()["detail"]
    api_with_minio.app.state.qa_minio.get_object_bytes.assert_not_awaited()


async def test_artifact_passthrough_unknown_extension_falls_back(api_with_minio):
    """Unknown extension defaults to application/octet-stream so the body still downloads."""
    sub = "RUN1__hub"
    api_with_minio.app.state.qa_minio.list_objects = AsyncMock(return_value=[f"{sub}/1/screenshots/strange.bin"])
    api_with_minio.app.state.qa_minio.get_object_bytes = AsyncMock(return_value=b"x")
    r = await api_with_minio.get("/api/v1/qa/runs/RUN1/apps/hub/artifacts/screenshots/strange.bin")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"


# ─── GET /qa/runs/{run_id}/apps/{app}/artifacts/{kind} (listing) ────────────


async def test_list_app_artifacts_returns_filenames_only(api_with_minio):
    """Listing returns just basenames, sorted, on the latest attempt."""
    sub = "RUN1__hub"
    api_with_minio.app.state.qa_minio.list_objects = AsyncMock(
        side_effect=[
            # First call: prefix=<sub>/ (used by _find_latest_attempt_for_kind)
            [
                f"{sub}/1/screenshots/old.png",
                f"{sub}/2/screenshots/a.png",
                f"{sub}/2/screenshots/b.png",
                f"{sub}/2/result.json",
            ],
            # Second call: prefix=<sub>/2/screenshots/ (used by the listing).
            [
                f"{sub}/2/screenshots/a.png",
                f"{sub}/2/screenshots/b.png",
            ],
        ]
    )
    r = await api_with_minio.get("/api/v1/qa/runs/RUN1/apps/hub/artifacts/screenshots")
    assert r.status_code == 200
    assert r.json() == {"filenames": ["a.png", "b.png"]}


async def test_list_app_artifacts_returns_empty_when_none(api_with_minio):
    """No uploads of this kind on any attempt → 200 + empty list (NOT 404)."""
    api_with_minio.app.state.qa_minio.list_objects = AsyncMock(return_value=[])
    r = await api_with_minio.get("/api/v1/qa/runs/RUN1/apps/hub/artifacts/console")
    assert r.status_code == 200
    assert r.json() == {"filenames": []}


async def test_list_app_artifacts_rejects_bad_kind(api_with_minio):
    """`kind` outside the allow-list → 400."""
    r = await api_with_minio.get("/api/v1/qa/runs/RUN1/apps/hub/artifacts/secret")
    assert r.status_code == 400


async def test_list_app_artifacts_503_when_minio_unconfigured(api_client):
    """No QA MinIO wired into app.state → 503 (matches existing endpoints)."""
    api_client.app.state.qa_minio = None
    r = await api_client.get("/api/v1/qa/runs/RUN1/apps/hub/artifacts/screenshots")
    assert r.status_code == 503
