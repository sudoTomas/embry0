import asyncio
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
    import datetime as _dt

    # Helper: build the {key, last_modified, size} dict shape returned by the
    # new list_objects_with_meta wrapper. The screenshot timestamp is embedded
    # in the filename (first 19 chars of the basename, ISO 8601).
    def _meta(key: str) -> dict:
        return {
            "key": key, "size": 1,
            "last_modified": _dt.datetime.fromisoformat(
                key.split("/")[-1][:19] + "+00:00"
            ),
        }
    api_with_minio.app.state.qa_minio.list_objects_with_meta = AsyncMock(return_value=[
        _meta("JOB1/1/screenshots/boot/2026-04-30T12:00:00.png"),
        _meta("JOB1/1/screenshots/exploratory/2026-04-30T12:05:30-login.png"),
        _meta("JOB1/1/screenshots/exploratory/2026-04-30T12:08:11-portfolio.png"),
        # result.json has no parseable timestamp; use a sentinel mtime so the
        # filter (only .png in /screenshots/) is what excludes it, not a parse error.
        {"key": "JOB1/1/result.json", "size": 1,
         "last_modified": _dt.datetime.fromisoformat("2026-04-30T12:00:00+00:00")},
    ])
    api_with_minio.app.state.qa_minio.presign_get = AsyncMock(return_value="http://minio/latest")

    r = await api_with_minio.get(
        "/api/v1/jobs/JOB1/artifacts/screenshots/latest",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "http://minio/latest" in r.headers["location"]


async def test_latest_screenshot_404_when_no_screenshots(api_with_minio):
    import datetime as _dt
    api_with_minio.app.state.qa_minio.list_objects_with_meta = AsyncMock(return_value=[
        {"key": "JOB1/1/result.json", "size": 1,
         "last_modified": _dt.datetime.fromisoformat("2026-04-30T12:00:00+00:00")},
    ])
    r = await api_with_minio.get("/api/v1/jobs/JOB1/artifacts/screenshots/latest")
    assert r.status_code == 404


async def test_latest_screenshot_spans_multiple_attempts(api_with_minio):
    # Screenshots exist under both attempt 1 and attempt 2. The route uses
    # prefix=f"{job_id}/" (not scoped to a single attempt), so it should pick
    # the most recent screenshot across ALL attempts — here, the one in
    # attempt 2 with the latest mtime.
    import datetime as _dt

    api_with_minio.app.state.qa_minio.list_objects_with_meta = AsyncMock(return_value=[
        {"key": "JOB1/1/screenshots/boot/2026-04-30T12:00:00.png", "size": 1,
         "last_modified": _dt.datetime.fromisoformat("2026-04-30T12:00:00+00:00")},
        {"key": "JOB1/1/screenshots/exploratory/2026-04-30T12:05:00-old.png", "size": 1,
         "last_modified": _dt.datetime.fromisoformat("2026-04-30T12:05:00+00:00")},
        {"key": "JOB1/2/screenshots/boot/2026-04-30T13:00:00.png", "size": 1,
         "last_modified": _dt.datetime.fromisoformat("2026-04-30T13:00:00+00:00")},
        {"key": "JOB1/2/screenshots/exploratory/2026-04-30T13:10:00-newest.png", "size": 1,
         "last_modified": _dt.datetime.fromisoformat("2026-04-30T13:10:00+00:00")},
        {"key": "JOB1/2/result.json", "size": 1,
         "last_modified": _dt.datetime.fromisoformat("2026-04-30T13:00:00+00:00")},
    ])
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


async def test_log_stream_returns_sse_content_type(api_with_minio):
    """SSE endpoint with follow=false redirects to the static artifact."""
    # `Depends(get_docker)` is resolved before the handler runs, even on the
    # follow=false path, so we have to wire a docker stub. We don't actually
    # touch it in this test — the static fallback only uses qa_minio.
    api_with_minio.app.state.docker = MagicMock()
    api_with_minio.app.state.docker._build_base_cmd = lambda: ["docker"]

    r = await api_with_minio.get(
        "/api/v1/jobs/JOB1/artifacts/logs/gateway?follow=false",
        follow_redirects=False,
    )
    # follow=false routes through `_redirect_to_artifact`, which uses the
    # api_with_minio fixture's `list_objects` (returns ["JOB1/1/result.json"])
    # → resolves attempt 1 → presigns `JOB1/1/logs/gateway.log` → 302.
    assert r.status_code == 302
    assert r.headers["location"].startswith("http://minio/")


async def test_log_stream_unsafe_service_rejected(api_with_minio):
    api_with_minio.app.state.docker = MagicMock()
    api_with_minio.app.state.docker._build_base_cmd = lambda: ["docker"]
    r = await api_with_minio.get("/api/v1/jobs/JOB1/artifacts/logs/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code == 404


async def test_latest_screenshot_route_not_swallowed_by_catchall(api_with_minio):
    # Regression test: the catch-all `{path:path}` route in `get_artifact`
    # MUST NOT match `screenshots/latest`. If a future maintainer reorders
    # the routes (placing the catch-all first), this test fails because the
    # catch-all would resolve the latest attempt and presign for the literal
    # key `<job_id>/<attempt>/screenshots/latest` — which is NOT a real
    # screenshot path. We assert the resolved key actually points at a .png
    # under `screenshots/`.
    import datetime as _dt
    api_with_minio.app.state.qa_minio.list_objects_with_meta = AsyncMock(return_value=[
        {"key": "JOB1/1/screenshots/boot/2026-04-30T12:00:00.png", "size": 1,
         "last_modified": _dt.datetime.fromisoformat("2026-04-30T12:00:00+00:00")},
    ])
    # `_resolve_latest_attempt` (used by the catch-all) reads `list_objects`,
    # so wire that too in case the catch-all incorrectly handles the request.
    api_with_minio.app.state.qa_minio.list_objects = AsyncMock(
        return_value=["JOB1/1/screenshots/boot/2026-04-30T12:00:00.png"]
    )
    api_with_minio.app.state.qa_minio.presign_get = AsyncMock(return_value="http://minio/x")

    r = await api_with_minio.get(
        "/api/v1/jobs/JOB1/artifacts/screenshots/latest",
        follow_redirects=False,
    )
    assert r.status_code == 302
    call_args = api_with_minio.app.state.qa_minio.presign_get.call_args
    key = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["key"]
    # The resolved key must be a real screenshot under JOB1/<attempt>/screenshots/,
    # NOT the literal `JOB1/<attempt>/screenshots/latest` that the catch-all
    # would have produced.
    assert key.startswith("JOB1/1/screenshots/"), (
        f"catch-all swallowed screenshots/latest -- got key={key!r}"
    )
    assert key.endswith(".png"), f"resolved key is not a screenshot: {key!r}"
    assert not key.endswith("/latest"), (
        f"catch-all matched `screenshots/latest` literally: {key!r}"
    )


async def test_log_stream_rejects_flag_like_service(api_with_minio):
    """A service name that starts with `-` must not reach the docker argv.

    Without the `[A-Za-z0-9]` lead-anchor on `_SAFE_SERVICE` (and the `--`
    separator in the cmd), a request for `/logs/--no-color` would inject a
    flag into `docker compose ... logs ...`. The combined defence is: the
    regex rejects leading `-`, AND the cmd places `--` before the service
    so even a future regex regression can't smuggle a flag through.
    """
    api_with_minio.app.state.docker = MagicMock()
    api_with_minio.app.state.docker._build_base_cmd = lambda: ["docker"]
    r = await api_with_minio.get(
        "/api/v1/jobs/JOB1/artifacts/logs/--no-color?follow=true",
        follow_redirects=False,
    )
    assert r.status_code == 404


async def test_log_stream_streams_lines_and_terminates_on_disconnect(
    api_with_minio, monkeypatch
):
    """SSE framing emits `data: <line>\\n\\n` per stdout line, and closing the
    response triggers the generator's `finally` to terminate the subprocess.
    """
    api_with_minio.app.state.docker = MagicMock()
    api_with_minio.app.state.docker._build_base_cmd = lambda: ["docker"]

    class _FakeStdout:
        """Minimal StreamReader stand-in for the test.

        Implements `readline()` (used by the generator) — returns each
        configured line in order and an empty bytes object when drained,
        which the generator treats as EOF.
        """

        def __init__(self, lines: list[bytes]) -> None:
            self._lines = list(lines)

        async def readline(self) -> bytes:
            if self._lines:
                return self._lines.pop(0)
            return b""

    fake_proc = MagicMock()
    fake_proc.stdout = _FakeStdout([b"hello\n", b"world\n"])
    fake_proc.returncode = None
    fake_proc.terminate = MagicMock()
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock(return_value=0)

    async def _fake_create(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)

    # Stream the response body to completion. httpx's AsyncClient resolves
    # the StreamingResponse by consuming it fully, then the response is
    # closed — which fires the generator's `finally` (and our terminate).
    r = await api_with_minio.get(
        "/api/v1/jobs/JOB1/artifacts/logs/gateway?follow=true",
        follow_redirects=False,
    )
    assert r.status_code == 200
    body = r.text
    assert "data: hello\n\n" in body
    assert "data: world\n\n" in body
    # Generator's finally must terminate the subprocess on disconnect/EOF.
    assert fake_proc.terminate.called
