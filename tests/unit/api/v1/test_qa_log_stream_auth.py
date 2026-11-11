"""Auth of the QA live log stream: GET /api/v1/jobs/{job_id}/artifacts/logs/{service}.

The dashboard tails it with an EventSource, which cannot send an
Authorization header (#634). Like the QA run events stream, the route accepts
a short-lived token scoped to the job, minted by an authenticated POST, so
the API key never rides the URL. The Bearer header keeps working for
non-browser clients.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

_KEY = "test-api-key-32-characters-minimum-x"
_AUTH = {"Authorization": f"Bearer {_KEY}", "X-Requested-With": "XMLHttpRequest"}


def _printed(lines: list[bytes]) -> asyncio.StreamReader:
    """The stdout of a process that printed ``lines`` and ended."""
    stdout = asyncio.StreamReader()
    stdout.feed_data(b"".join(lines))
    stdout.feed_eof()
    return stdout


def _make_client() -> TestClient:
    from embry0.api.app import create_app

    app = create_app()

    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.api_key = _KEY
    cfg.auth_dev_mode = False
    cfg.webhook_dev_mode = False
    app.state.config = cfg
    app.state.qa_minio = MagicMock()

    async def _log_stream(project: str, service: str, tail: int) -> MagicMock:
        proc = MagicMock()
        proc.stdout = _printed([f"{project}/{service} up\n".encode()])
        proc.returncode = 0
        proc.wait = AsyncMock(return_value=0)
        return proc

    runtime = MagicMock()
    runtime.compose_log_stream = _log_stream
    app.state.runtime = runtime
    return TestClient(app)


def _stream(client: TestClient, job_id: str, **params: str):
    return client.get(f"/api/v1/jobs/{job_id}/artifacts/logs/gateway", params={"follow": "true", **params})


def _mint(client: TestClient, job_id: str) -> str:
    resp = client.post(f"/api/v1/jobs/{job_id}/qa/log-stream-token", headers=_AUTH)
    assert resp.status_code == 200, f"minting a log-stream token failed: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["expires_in"] > 0
    return str(body["token"])


def test_log_stream_refuses_a_request_without_credentials() -> None:
    resp = _stream(_make_client(), "JOB1")
    assert resp.status_code == 401, f"an unauthenticated log stream must be refused, got {resp.status_code}"


def test_log_stream_accepts_a_job_scoped_token() -> None:
    client = _make_client()
    token = _mint(client, "JOB1")
    assert _KEY not in token, "the credential in the URL must never be the API key"

    resp = _stream(client, "JOB1", token=token)

    assert resp.status_code == 200, f"the EventSource's token was refused: {resp.status_code} {resp.text}"
    assert "data: qa_JOB1/gateway up\n\n" in resp.text


def test_log_stream_token_is_bound_to_its_job() -> None:
    client = _make_client()
    token = _mint(client, "JOB-A")
    resp = _stream(client, "JOB-B", token=token)
    assert resp.status_code == 401, "a token minted for one job must not open another job's logs"


def test_log_stream_refuses_the_api_key_in_the_query() -> None:
    resp = _stream(_make_client(), "JOB1", token=_KEY)
    assert resp.status_code == 401, "the master key must never be accepted from a URL"


def test_log_stream_refuses_a_run_events_token() -> None:
    """Scopes do not cross: a token for the run events stream is not a log token."""
    client = _make_client()
    minted = client.post("/api/v1/qa/runs/JOB1/events-token", headers=_AUTH)
    assert minted.status_code == 200
    resp = _stream(client, "JOB1", token=minted.json()["token"])
    assert resp.status_code == 401, "an events token must not open the log stream"


def test_log_stream_still_accepts_the_bearer_header() -> None:
    resp = _make_client().get("/api/v1/jobs/JOB1/artifacts/logs/gateway", params={"follow": "true"}, headers=_AUTH)
    assert resp.status_code == 200, f"Bearer-header clients must keep working, got {resp.status_code}"


def test_log_stream_token_route_requires_auth() -> None:
    resp = _make_client().post("/api/v1/jobs/JOB1/qa/log-stream-token", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 401, f"minting a token must need credentials, got {resp.status_code}"


def test_other_artifact_routes_still_refuse_a_log_token() -> None:
    """The token opens the log route only, not the rest of the artifacts router."""
    client = _make_client()
    token = _mint(client, "JOB1")
    resp = client.get("/api/v1/jobs/JOB1/qa/attempts", params={"token": token})
    assert resp.status_code == 401, "a log-stream token must not authenticate other artifact routes"
