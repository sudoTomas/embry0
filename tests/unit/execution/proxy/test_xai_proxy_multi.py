"""Per-credential xAI tokens: each sandbox's bearer is the account it enrolled."""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient

from embry0.execution.proxy.xai_proxy import create_xai_proxy_app

from .upstream_stub import route_upstream

_ADMIN = "test-admin-secret-not-real"
_TOKEN_A = "sandbox-bearer-a-at-least-32-characters"
_TOKEN_B = "sandbox-bearer-b-at-least-32-characters"


@pytest.fixture
async def xai_client(aiohttp_client) -> TestClient:
    return await aiohttp_client(create_xai_proxy_app(admin_token=_ADMIN))


def _capture(app) -> dict:
    captured: dict = {}

    class _FakeStream:
        def __init__(self, **kw):
            captured.update(kw)
            self.status_code = captured.get("_status", 200)
            self.headers = {"content-type": "application/json"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aiter_bytes(self):
            yield b"{}"

    class _FakeClient:
        def stream(self, **kw):
            return _FakeStream(**kw)

        async def aclose(self):
            return None

    route_upstream(app, _FakeClient())
    return captured


async def _enroll(client: TestClient, sandbox_id: str, token: str, credential_id: str | None) -> None:
    body: dict = {"sandbox_id": sandbox_id, "sandbox_token": token}
    if credential_id is not None:
        body["xai_credential_id"] = credential_id
    resp = await client.post("/admin/enroll", json=body, headers={"X-Admin-Token": _ADMIN})
    assert resp.status == 200, await resp.text()


async def _push(client: TestClient, credential_id: str, access_token: str) -> None:
    resp = await client.post(
        "/admin/token",
        json={"credential_id": credential_id, "access_token": access_token, "expires_at": "2030-01-01T00:00:00Z"},
        headers={"X-Admin-Token": _ADMIN},
    )
    assert resp.status == 200, await resp.text()


async def test_two_sandboxes_forward_their_own_bearer(xai_client: TestClient) -> None:
    await _enroll(xai_client, "sb-a", _TOKEN_A, "cred-a")
    await _enroll(xai_client, "sb-b", _TOKEN_B, "cred-b")
    await _push(xai_client, "cred-a", "access-a")
    await _push(xai_client, "cred-b", "access-b")
    captured = _capture(xai_client.app)

    resp_a = await xai_client.post("/v1/messages", headers={"Authorization": f"Bearer {_TOKEN_A}"})
    assert resp_a.status == 200
    assert captured["headers"]["Authorization"] == "Bearer access-a", (
        f"sandbox sb-a must forward cred-a's bearer, got {captured['headers'].get('Authorization')!r}"
    )

    resp_b = await xai_client.post("/v1/messages", headers={"Authorization": f"Bearer {_TOKEN_B}"})
    assert resp_b.status == 200
    assert captured["headers"]["Authorization"] == "Bearer access-b", (
        f"sandbox sb-b must forward cred-b's bearer, got {captured['headers'].get('Authorization')!r}"
    )


async def test_refreshed_token_is_used_without_reenrolling(xai_client: TestClient) -> None:
    await _enroll(xai_client, "sb-a", _TOKEN_A, "cred-a")
    await _enroll(xai_client, "sb-b", _TOKEN_B, "cred-b")
    await _push(xai_client, "cred-a", "access-a")
    await _push(xai_client, "cred-b", "access-b")
    await _push(xai_client, "cred-a", "access-a2")
    captured = _capture(xai_client.app)

    resp_b = await xai_client.post("/v1/messages", headers={"Authorization": f"Bearer {_TOKEN_B}"})
    assert resp_b.status == 200
    assert captured["headers"]["Authorization"] == "Bearer access-b", (
        f"refreshing cred-a must not replace cred-b, got {captured['headers'].get('Authorization')!r}"
    )
    resp_a = await xai_client.post("/v1/messages", headers={"Authorization": f"Bearer {_TOKEN_A}"})
    assert resp_a.status == 200
    assert captured["headers"]["Authorization"] == "Bearer access-a2", (
        f"a refreshed token must apply without re-enrolling, got {captured['headers'].get('Authorization')!r}"
    )


async def test_missing_credential_token_is_503(xai_client: TestClient) -> None:
    await _enroll(xai_client, "sb-a", _TOKEN_A, "cred-a")
    # A global token must not satisfy a sandbox bound to a credential that has none.
    await xai_client.post("/admin/token", json={"access_token": "global-token"}, headers={"X-Admin-Token": _ADMIN})
    resp = await xai_client.post("/v1/messages", headers={"Authorization": f"Bearer {_TOKEN_A}"})
    body = await resp.json()
    assert resp.status == 503, f"a credential with no token must be 503, got {resp.status} {body}"
    assert body.get("error") == "credential_token_missing", (
        f"missing per-credential token must say credential_token_missing, got {body!r}"
    )


async def test_admin_health_counts_upstream_and_resets(xai_client: TestClient) -> None:
    await _enroll(xai_client, "sb-a", _TOKEN_A, "cred-a")
    await _push(xai_client, "cred-a", "access-a")
    captured = _capture(xai_client.app)
    captured["_status"] = 429
    await xai_client.post("/v1/messages", headers={"Authorization": f"Bearer {_TOKEN_A}"})
    captured["_status"] = 401
    await xai_client.post("/v1/messages", headers={"Authorization": f"Bearer {_TOKEN_A}"})

    first = await xai_client.get("/admin/health", headers={"X-Admin-Token": _ADMIN})
    assert first.status == 200, await first.text()
    body = await first.json()
    assert body.get("credentials", {}).get("cred-a") == {"401": 1, "429": 1}, (
        f"/admin/health must count per credential, got {body!r}"
    )
    second = await xai_client.get("/admin/health", headers={"X-Admin-Token": _ADMIN})
    cleared = await second.json()
    assert cleared.get("credentials") == {}, f"/admin/health must reset after a read, got {cleared!r}"


async def test_enroll_without_credential_id_keeps_the_global_token(xai_client: TestClient) -> None:
    """An API-key sandbox enrolls with no xai credential. The file-backed token still forwards."""
    await _enroll(xai_client, "sb-legacy", _TOKEN_A, None)
    pushed = await xai_client.post(
        "/admin/token", json={"access_token": "global-token"}, headers={"X-Admin-Token": _ADMIN}
    )
    assert pushed.status == 200
    captured = _capture(xai_client.app)
    resp = await xai_client.post("/v1/messages", headers={"Authorization": f"Bearer {_TOKEN_A}"})
    assert resp.status == 200
    assert captured["headers"]["Authorization"] == "Bearer global-token"


def _capture_403(app, body: bytes) -> None:
    class _Stream403:
        status_code = 403
        headers = {"content-type": "application/json"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aread(self):
            return body

        async def aiter_bytes(self):
            yield body

    class _Client:
        def stream(self, **kw):
            return _Stream403()

        async def aclose(self):
            return None

    route_upstream(app, _Client())


@pytest.mark.parametrize(
    ("body", "stale"),
    [
        (b'{"error":"The OAuth2 access token could not be validated [WKE=unauthenticated:expired]"}', 1),
        (b'{"code":"personal-team-blocked:spending-limit","error":"Team spending limit reached"}', 0),
    ],
)
async def test_a_stale_token_403_is_counted_and_passed_through(xai_client: TestClient, body, stale) -> None:
    """xAI says the access token is stale only in the 403 body. The proxy counts that per
    account so the orchestrator can force a refresh; the sandbox still gets the body as sent."""
    await _enroll(xai_client, "sb-a", _TOKEN_A, "cred-a")
    await _push(xai_client, "cred-a", "access-a")
    _capture_403(xai_client.app, body)

    resp = await xai_client.post("/v1/messages", headers={"Authorization": f"Bearer {_TOKEN_A}"})
    assert resp.status == 403
    assert await resp.read() == body, "the 403 body must reach the sandbox unchanged"

    health = await (await xai_client.get("/admin/health", headers={"X-Admin-Token": _ADMIN})).json()
    counted = health.get("credentials", {}).get("cred-a", {}).get("stale", 0)
    assert counted == stale, f"stale-token hits for cred-a: expected {stale}, got {health!r}"
