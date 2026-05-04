"""Tests for POST /api/v1/internal/qa/presign."""

from __future__ import annotations

import pytest


class _FakeMinio:
    async def presign_put(self, bucket, key, expires_seconds):
        return f"http://fake/{bucket}/{key}?put"

    async def presign_get(self, bucket, key, expires_seconds):
        return f"http://fake/{bucket}/{key}?get"


@pytest.fixture
async def api_with_qa(api_client):
    """Wires fake MinIO + a registered token into the test app."""
    from athanor.execution.qa.token_registry import SandboxTokenRegistry

    api_client.app.state.qa_minio = _FakeMinio()
    reg = SandboxTokenRegistry()
    reg.register("test-token-aaaaaaaaaaaaaaa", job_id="JOB1", attempt_n=1)
    api_client.app.state.qa_token_registry = reg
    yield api_client


async def test_presign_returns_batch_of_urls(api_with_qa):
    r = await api_with_qa.post(
        "/api/v1/internal/qa/presign",
        json={
            "sandbox_token": "test-token-aaaaaaaaaaaaaaa",
            "paths": ["result.json", "screenshots/a.png"],
            "expires_seconds": 600,
            "direction": "put",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["bucket"] == "qa-artifacts"
    assert body["prefix"] == "JOB1/1/"
    assert {u["path"] for u in body["urls"]} == {"result.json", "screenshots/a.png"}
    assert all(u["url"].startswith("http://fake/qa-artifacts/JOB1/1/") for u in body["urls"])


async def test_presign_rejects_unknown_token(api_with_qa):
    r = await api_with_qa.post(
        "/api/v1/internal/qa/presign",
        json={
            "sandbox_token": "no-such-token-xxxxxxxxxxx",
            "paths": ["x"],
        },
    )
    assert r.status_code == 401


async def test_presign_rejects_path_traversal(api_with_qa):
    r = await api_with_qa.post(
        "/api/v1/internal/qa/presign",
        json={
            "sandbox_token": "test-token-aaaaaaaaaaaaaaa",
            "paths": ["../../escape.png"],
        },
    )
    assert r.status_code == 422


async def test_presign_503_when_minio_unconfigured(api_client):
    api_client.app.state.qa_minio = None
    api_client.app.state.qa_minio_sandbox = None
    r = await api_client.post(
        "/api/v1/internal/qa/presign",
        json={"sandbox_token": "x" * 16, "paths": ["x"]},
    )
    assert r.status_code == 503


async def test_presign_uses_sandbox_facing_minio_client(api_client):
    """Phase 1.5: when both qa_minio and qa_minio_sandbox are set, the
    SANDBOX-facing client mints URLs (so the URL hostname matches what the
    sandbox can reach). qa_minio is the internal client used for bucket admin.
    """
    from athanor.execution.qa.token_registry import SandboxTokenRegistry

    class _SandboxClient:
        async def presign_put(self, bucket, key, expires_seconds):
            return f"http://minio-proxy:9100/{bucket}/{key}?put"

        async def presign_get(self, bucket, key, expires_seconds):
            return f"http://minio-proxy:9100/{bucket}/{key}?get"

    class _InternalClient:
        async def presign_put(self, bucket, key, expires_seconds):
            return f"http://minio:9000/{bucket}/{key}?put"

        async def presign_get(self, bucket, key, expires_seconds):
            return f"http://minio:9000/{bucket}/{key}?get"

    api_client.app.state.qa_minio = _InternalClient()
    api_client.app.state.qa_minio_sandbox = _SandboxClient()
    reg = SandboxTokenRegistry()
    reg.register("phase15-token-aaaaaaaaaa", job_id="P15", attempt_n=1)
    api_client.app.state.qa_token_registry = reg

    r = await api_client.post(
        "/api/v1/internal/qa/presign",
        json={"sandbox_token": "phase15-token-aaaaaaaaaa", "paths": ["x.json"]},
    )
    assert r.status_code == 200
    body = r.json()
    # The minted URL must use the SANDBOX endpoint, not the internal one.
    assert body["urls"][0]["url"].startswith("http://minio-proxy:9100/"), body["urls"]


async def test_presign_does_not_require_csrf_header(api_with_qa):
    """Sandboxes call without X-Requested-With; the internal endpoint must work without it."""
    import httpx
    from httpx import ASGITransport

    async with httpx.AsyncClient(transport=ASGITransport(app=api_with_qa.app), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/internal/qa/presign",
            json={"sandbox_token": "test-token-aaaaaaaaaaaaaaa", "paths": ["x.json"]},
        )
    # 200 = full success (token + path valid). 401 = token unknown (CSRF passed but auth failed).
    # 422 = validation. NOT 403 (which would indicate CSRF blocked the request).
    assert r.status_code in (200, 401, 422)
    assert r.status_code != 403
