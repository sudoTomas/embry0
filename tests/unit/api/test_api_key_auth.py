"""Multi-key bearer auth tests (RAV-1056 Phase B): principal resolution,
scope ladder, owner gating, admin gate, actor attribution."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embry0.api.deps import bust_api_key_cache, bust_user_gate_cache, request_actor, require_admin, require_auth
from embry0.config import Embry0Config
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

SECRET = "s" * 40


def _config() -> Embry0Config:
    with patch.dict(os.environ, {}, clear=True):
        return Embry0Config(_env_file=None, auth_dev_mode=False, api_key="master-key")


def _key_row(**overrides) -> ApiKeyRow:
    base = dict(
        id="key-abc123def456",
        user_id=None,
        name="test-key",
        prefix="emb_test1234",
        scope="operate",
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )
    base.update(overrides)
    return ApiKeyRow(**base)


def _app(key_rows: dict[str, ApiKeyRow], users: dict[str, object] | None = None) -> FastAPI:
    app = FastAPI()
    app.state.config = _config()
    keys_repo = MagicMock()
    keys_repo.get_by_hash = AsyncMock(side_effect=lambda h: key_rows.get(h))
    keys_repo.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys_repo
    users_repo = MagicMock()
    users_repo.get = AsyncMock(side_effect=lambda uid: (users or {}).get(uid))
    app.state.users_repo = users_repo

    @app.get("/guarded", dependencies=[require_auth])
    def guarded():
        return {"ok": True}

    @app.post("/guarded-post", dependencies=[require_auth])
    def guarded_post(request: Request):
        return {"actor": request_actor(request)}

    @app.get("/admin-only", dependencies=[require_admin])
    def admin_only():
        return {"ok": True}

    return app


@pytest.fixture(autouse=True)
def _clear_caches():
    bust_api_key_cache()
    bust_user_gate_cache()
    yield
    bust_api_key_cache()
    bust_user_gate_cache()


TOKEN = "emb_" + "t" * 43


def test_master_key_still_admin():
    client = TestClient(_app({}))
    assert client.get("/guarded", headers={"Authorization": "Bearer master-key"}).status_code == 200
    assert client.get("/admin-only", headers={"Authorization": "Bearer master-key"}).status_code == 200


def test_second_valid_key_authenticates():
    app = _app({hash_token(TOKEN): _key_row(scope="operate")})
    client = TestClient(app)
    assert client.get("/guarded", headers={"Authorization": "Bearer " + TOKEN}).status_code == 200


def test_unknown_key_401():
    client = TestClient(_app({}))
    assert client.get("/guarded", headers={"Authorization": "Bearer " + TOKEN}).status_code == 401


def test_revoked_key_401():
    app = _app({hash_token(TOKEN): _key_row(revoked_at=datetime.now(UTC))})
    client = TestClient(app)
    assert client.get("/guarded", headers={"Authorization": "Bearer " + TOKEN}).status_code == 401


def test_expired_key_401():
    app = _app({hash_token(TOKEN): _key_row(expires_at=datetime.now(UTC) - timedelta(days=1))})
    client = TestClient(app)
    assert client.get("/guarded", headers={"Authorization": "Bearer " + TOKEN}).status_code == 401


def test_unexpired_key_passes():
    app = _app({hash_token(TOKEN): _key_row(expires_at=datetime.now(UTC) + timedelta(days=1))})
    client = TestClient(app)
    assert client.get("/guarded", headers={"Authorization": "Bearer " + TOKEN}).status_code == 200


def test_owner_disabled_401():
    owner = MagicMock()
    owner.disabled = True
    owner.email = "o@example.com"
    app = _app({hash_token(TOKEN): _key_row(user_id="usr-1")}, users={"usr-1": owner})
    client = TestClient(app)
    assert client.get("/guarded", headers={"Authorization": "Bearer " + TOKEN}).status_code == 401


def test_read_scope_blocks_mutations():
    app = _app({hash_token(TOKEN): _key_row(scope="read")})
    client = TestClient(app)
    assert client.get("/guarded", headers={"Authorization": "Bearer " + TOKEN}).status_code == 200
    resp = client.post(
        "/guarded-post",
        headers={"Authorization": "Bearer " + TOKEN, "X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 403


def test_operate_key_rejected_on_admin_route():
    app = _app({hash_token(TOKEN): _key_row(scope="operate")})
    client = TestClient(app)
    assert client.get("/admin-only", headers={"Authorization": "Bearer " + TOKEN}).status_code == 403


def test_admin_scope_key_passes_admin_route():
    app = _app({hash_token(TOKEN): _key_row(scope="admin")})
    client = TestClient(app)
    assert client.get("/admin-only", headers={"Authorization": "Bearer " + TOKEN}).status_code == 200


def test_actor_prefers_owner_email_then_key_name():
    owner = MagicMock()
    owner.disabled = False
    owner.email = "owner@example.com"
    owned = "emb_" + "a" * 43
    unowned = "emb_" + "b" * 43
    app = _app(
        {
            hash_token(owned): _key_row(id="key-owned", user_id="usr-1", name="laptop"),
            hash_token(unowned): _key_row(id="key-svc", user_id=None, name="hermes"),
        },
        users={"usr-1": owner},
    )
    client = TestClient(app)
    r1 = client.post(
        "/guarded-post", headers={"Authorization": "Bearer " + owned, "X-Requested-With": "XMLHttpRequest"}
    )
    assert r1.json()["actor"] == "owner@example.com"
    r2 = client.post(
        "/guarded-post", headers={"Authorization": "Bearer " + unowned, "X-Requested-With": "XMLHttpRequest"}
    )
    assert r2.json()["actor"] == "key:hermes"


def test_master_key_actor_names_the_key_not_the_client_address():
    """Behind the dashboard proxy every master-key request arrives from the
    proxy's address, so an IP actor named no one: about one audit row in five,
    API key creation and revocation among them."""
    client = TestClient(_app({}))
    resp = client.post(
        "/guarded-post", headers={"Authorization": "Bearer master-key", "X-Requested-With": "XMLHttpRequest"}
    )
    assert resp.status_code == 200
    assert resp.json()["actor"] == "embry0:master-key", (
        f"a master-key request was audited as {resp.json()['actor']!r}, not under the key"
    )


def test_a_request_with_no_identity_is_anonymous_not_an_address():
    """Only AUTH_DEV_MODE lets a request reach a handler with no identity."""
    app = FastAPI()
    with patch.dict(os.environ, {}, clear=True):
        app.state.config = Embry0Config(_env_file=None, auth_dev_mode=True)

    @app.post("/open-post", dependencies=[require_auth])
    def open_post(request: Request):
        return {"actor": request_actor(request)}

    actor = TestClient(app).post("/open-post", headers={"X-Requested-With": "XMLHttpRequest"}).json()["actor"]
    assert actor == "embry0:anonymous", f"an unidentified request was audited as {actor!r}"


def test_revocation_takes_effect_after_cache_bust():
    row = _key_row(scope="operate")
    key_rows = {hash_token(TOKEN): row}
    app = _app(key_rows)
    client = TestClient(app)
    assert client.get("/guarded", headers={"Authorization": "Bearer " + TOKEN}).status_code == 200
    key_rows[hash_token(TOKEN)] = _key_row(revoked_at=datetime.now(UTC))
    # cached → still passes until bust (documented 30s window)
    assert client.get("/guarded", headers={"Authorization": "Bearer " + TOKEN}).status_code == 200
    bust_api_key_cache()
    assert client.get("/guarded", headers={"Authorization": "Bearer " + TOKEN}).status_code == 401
