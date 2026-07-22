"""Unit tests for the xAI transparent-forward proxy (EMB-45)."""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient

from embry0.execution.proxy.xai_proxy import create_xai_proxy_app

_ADMIN = "test-admin-secret-not-real"
_SANDBOX_TOKEN = "sandbox-bearer-token-at-least-32-chars-long"


@pytest.fixture
async def xai_client(aiohttp_client) -> TestClient:
    return await aiohttp_client(create_xai_proxy_app(admin_token=_ADMIN))


async def test_requires_admin_token_on_construct():
    with pytest.raises(ValueError):
        create_xai_proxy_app(admin_token="")


async def test_health(xai_client: TestClient):
    resp = await xai_client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    assert data["proxy"] == "xai"
    assert data["provisioned"] is False


async def test_enroll_requires_admin(xai_client: TestClient):
    resp = await xai_client.post("/admin/enroll", json={"sandbox_id": "s1", "sandbox_token": _SANDBOX_TOKEN})
    assert resp.status == 401


async def test_set_token_requires_admin(xai_client: TestClient):
    resp = await xai_client.post("/admin/token", json={"access_token": "at-1"})
    assert resp.status == 401


async def test_set_token_rejects_empty(xai_client: TestClient):
    resp = await xai_client.post("/admin/token", json={"access_token": ""}, headers={"X-Admin-Token": _ADMIN})
    assert resp.status == 400


async def test_forward_without_bearer_is_401(xai_client: TestClient):
    resp = await xai_client.post("/v1/messages", json={"model": "grok-4.5"})
    assert resp.status == 401


async def test_forward_with_bad_bearer_is_401(xai_client: TestClient):
    resp = await xai_client.post("/v1/messages", json={"model": "grok-4.5"}, headers={"Authorization": "Bearer nope"})
    assert resp.status == 401


async def test_enrolled_but_unprovisioned_is_503(xai_client: TestClient):
    enroll = await xai_client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": _SANDBOX_TOKEN},
        headers={"X-Admin-Token": _ADMIN},
    )
    assert enroll.status == 200
    # Enrolled bearer valid, but no access token pushed yet → 503, not a forward attempt.
    resp = await xai_client.post(
        "/v1/messages",
        json={"model": "grok-4.5"},
        headers={"Authorization": f"Bearer {_SANDBOX_TOKEN}"},
    )
    assert resp.status == 503


async def test_full_provision_then_forward_attempt(xai_client: TestClient):
    """Enroll + push token → a valid bearer reaches the forward path (502 offline / not 401/503)."""
    await xai_client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": _SANDBOX_TOKEN},
        headers={"X-Admin-Token": _ADMIN},
    )
    tok = await xai_client.post("/admin/token", json={"access_token": "at-1"}, headers={"X-Admin-Token": _ADMIN})
    assert tok.status == 200
    health = await (await xai_client.get("/health")).json()
    assert health["provisioned"] is True
    resp = await xai_client.post(
        "/v1/messages",
        json={"model": "grok-4.5"},
        headers={"Authorization": f"Bearer {_SANDBOX_TOKEN}"},
    )
    # Provisioning gate passed: never 503. (Offline → 502; online → an upstream status.
    # Upstream may itself return 401 for the fake token, so 401 is not asserted against.)
    assert resp.status != 503


async def test_unenroll_revokes_bearer(xai_client: TestClient):
    await xai_client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": _SANDBOX_TOKEN},
        headers={"X-Admin-Token": _ADMIN},
    )
    await xai_client.post("/admin/token", json={"access_token": "at-1"}, headers={"X-Admin-Token": _ADMIN})
    await xai_client.delete("/admin/enroll/s1", headers={"X-Admin-Token": _ADMIN})
    resp = await xai_client.post(
        "/v1/messages",
        json={"model": "grok-4.5"},
        headers={"Authorization": f"Bearer {_SANDBOX_TOKEN}"},
    )
    assert resp.status == 401


# ---------------------------------------------------------------------------
# EMB-46: outbound tool-schema normalization — xAI rejects input_schema
# without a `required` array, and the Claude CLI's builtin schemas omit it.
# ---------------------------------------------------------------------------


def test_normalize_adds_required_when_absent_or_null():
    import json

    from embry0.execution.proxy.xai_proxy import _normalize_tool_schemas

    body = json.dumps(
        {
            "model": "grok-4.5",
            "tools": [
                {"name": "a", "input_schema": {"type": "object", "properties": {}}},
                {"name": "b", "input_schema": {"type": "object", "required": None}},
                {"name": "c", "input_schema": {"type": "object", "required": ["x"]}},
                {"type": "web_search_20250305", "name": "web_search"},
                "not-a-dict",
            ],
            "messages": [],
        }
    ).encode()
    out = json.loads(_normalize_tool_schemas(body))
    assert out["tools"][0]["input_schema"]["required"] == []
    assert out["tools"][1]["input_schema"]["required"] == []
    assert out["tools"][2]["input_schema"]["required"] == ["x"]
    assert "input_schema" not in out["tools"][3]  # server tools untouched
    assert out["messages"] == []


def test_normalize_returns_same_object_when_clean():
    import json

    from embry0.execution.proxy.xai_proxy import _normalize_tool_schemas

    body = json.dumps(
        {"model": "grok-4.5", "tools": [{"name": "a", "input_schema": {"type": "object", "required": []}}]}
    ).encode()
    assert _normalize_tool_schemas(body) is body


def test_normalize_passes_through_non_json_and_toolless_bodies():
    from embry0.execution.proxy.xai_proxy import _normalize_tool_schemas

    for body in (b"\x00\x01binary", b'{"model": "grok-4.5", "messages": []}', b'["list"]'):
        assert _normalize_tool_schemas(body) is body
