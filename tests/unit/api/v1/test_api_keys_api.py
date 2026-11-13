"""/apikeys self-service endpoint tests (RAV-1056 Phase B)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from embry0.api.deps import bust_api_key_cache, bust_user_gate_cache, require_auth
from embry0.api.session import SESSION_COOKIE, SessionData, encode_session
from embry0.config import Embry0Config
from embry0.storage.repositories.api_keys import ApiKeyRow

SECRET = "s" * 40
XRW = {"X-Requested-With": "XMLHttpRequest"}


def _config() -> Embry0Config:
    with patch.dict(os.environ, {}, clear=True):
        return Embry0Config(
            _env_file=None,
            auth_dev_mode=False,
            api_key="master-key",
            auth0_issuer="https://t.example.com/",
            auth0_client_id="cid",
            auth0_client_secret="sec",
            session_secret=SECRET,
            sso_public_origin="https://e.example.com",
            # #393: SSO is on only with a roles claim, so a session-auth fixture states one.
            sso_roles_claim="https://example.com/roles",
        )


def _key_row(**overrides) -> ApiKeyRow:
    base = dict(
        id="key-abc123def456",
        user_id="usr-op",
        name="mine",
        prefix="emb_mine1234",
        scope="operate",
        created_by="op@example.com",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )
    base.update(overrides)
    return ApiKeyRow(**base)


def _app() -> FastAPI:
    from embry0.api.v1 import api_keys

    app = FastAPI()
    app.state.config = _config()
    app.state.db = None

    keys_repo = MagicMock()
    keys_repo.list_all = AsyncMock(return_value=[_key_row(), _key_row(id="key-other", user_id="usr-x", name="theirs")])
    keys_repo.list_for_user = AsyncMock(return_value=[_key_row()])
    keys_repo.create = AsyncMock(return_value=(_key_row(), "emb_plaintext-token"))
    keys_repo.get = AsyncMock(return_value=_key_row())
    keys_repo.revoke = AsyncMock(return_value=_key_row())
    keys_repo.get_by_hash = AsyncMock(return_value=None)
    keys_repo.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys_repo

    users_repo = MagicMock()
    op_user = MagicMock()
    op_user.id = "usr-op"
    op_user.email = "op@example.com"
    gate = MagicMock()
    gate.session_epoch = 1
    gate.disabled = False
    users_repo.get_by_sub = AsyncMock(return_value=op_user)
    # session gate consults get_by_sub too; same mock returns epoch attrs
    op_user.session_epoch = 1
    op_user.disabled = False
    users_repo.get = AsyncMock(return_value=op_user)
    app.state.users_repo = users_repo

    app.include_router(api_keys.router, prefix="/api/v1", dependencies=[require_auth])
    return app


def _cookie(roles: list[str], tier: str, sub: str = "auth0|op") -> str:
    return encode_session(
        SECRET, SessionData(sub=sub, email="op@example.com", name="O", roles=roles, tier=tier, epoch=1)
    )


@pytest.fixture(autouse=True)
def _caches():
    bust_api_key_cache()
    bust_user_gate_cache()
    yield
    bust_api_key_cache()
    bust_user_gate_cache()


def test_operator_sees_only_own_keys():
    app = _app()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-operator"], "operator"))
    body = client.get("/api/v1/apikeys").json()
    assert [k["name"] for k in body["keys"]] == ["mine"]
    app.state.api_keys_repo.list_for_user.assert_awaited_once_with("usr-op")


def test_admin_sees_all_keys():
    app = _app()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-admin"], "admin"))
    body = client.get("/api/v1/apikeys").json()
    assert len(body["keys"]) == 2


def test_operator_mints_capped_scope():
    app = _app()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-operator"], "operator"))
    ok = client.post("/api/v1/apikeys", json={"name": "cli", "scope": "operate"}, headers=XRW)
    assert ok.status_code == 201
    assert ok.json()["key"] == "emb_plaintext-token"
    kwargs = app.state.api_keys_repo.create.await_args.kwargs
    assert kwargs["user_id"] == "usr-op"
    denied = client.post("/api/v1/apikeys", json={"name": "cli", "scope": "admin"}, headers=XRW)
    assert denied.status_code == 403


def test_service_key_requires_admin():
    app = _app()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-operator"], "operator"))
    assert (
        client.post("/api/v1/apikeys", json={"name": "svc", "scope": "read", "service": True}, headers=XRW).status_code
        == 403
    )
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-admin"], "admin"))
    resp = client.post("/api/v1/apikeys", json={"name": "svc", "scope": "operate", "service": True}, headers=XRW)
    assert resp.status_code == 201
    assert app.state.api_keys_repo.create.await_args.kwargs["user_id"] is None


def test_master_key_mints_unowned():
    app = _app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/apikeys",
        json={"name": "hermes", "scope": "operate"},
        headers={"Authorization": "Bearer master-key", **XRW},
    )
    assert resp.status_code == 201
    assert app.state.api_keys_repo.create.await_args.kwargs["user_id"] is None


def test_plaintext_never_listed():
    app = _app()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-admin"], "admin"))
    body = client.get("/api/v1/apikeys").json()
    assert all("key" not in k for k in body["keys"])


def test_revoke_ownership_enforced():
    app = _app()
    client = TestClient(app)
    # operator revoking someone else's key
    app.state.api_keys_repo.get = AsyncMock(return_value=_key_row(user_id="usr-x"))
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-operator"], "operator"))
    assert client.delete("/api/v1/apikeys/key-abc123def456", headers=XRW).status_code == 403
    # owner revokes own
    app.state.api_keys_repo.get = AsyncMock(return_value=_key_row(user_id="usr-op"))
    assert client.delete("/api/v1/apikeys/key-abc123def456", headers=XRW).status_code == 200


# ---------------------------------------------------------------------------
# Repository allowlist
# ---------------------------------------------------------------------------


def test_mint_passes_the_cleaned_allowlist():
    app = _app()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-operator"], "operator"))
    resp = client.post(
        "/api/v1/apikeys",
        json={"name": "bot", "scope": "operate", "allowed_repos": [" acme/widgets", "Acme/Widgets", "acme/docs"]},
        headers=XRW,
    )
    assert resp.status_code == 201, resp.text
    assert app.state.api_keys_repo.create.await_args.kwargs["allowed_repos"] == ["acme/widgets", "acme/docs"], (
        "the list must be trimmed and de-duplicated case-insensitively"
    )


@pytest.mark.parametrize("allowed", [None, []])
def test_mint_without_a_list_is_unrestricted(allowed):
    app = _app()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-operator"], "operator"))
    body = {"name": "cli", "scope": "operate"} | ({} if allowed is None else {"allowed_repos": allowed})
    assert client.post("/api/v1/apikeys", json=body, headers=XRW).status_code == 201
    assert app.state.api_keys_repo.create.await_args.kwargs["allowed_repos"] is None, "empty must mean unrestricted"


@pytest.mark.parametrize("bad", ["widgets", "acme/", "acme/wid gets", "../etc"])
def test_mint_refuses_a_malformed_repository(bad):
    app = _app()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-operator"], "operator"))
    resp = client.post("/api/v1/apikeys", json={"name": "bot", "scope": "operate", "allowed_repos": [bad]}, headers=XRW)
    assert resp.status_code == 422, f"{bad!r} was accepted as a repository"
    assert not app.state.api_keys_repo.create.await_count


def test_list_shows_the_allowlist():
    app = _app()
    app.state.api_keys_repo.list_for_user = AsyncMock(return_value=[_key_row(allowed_repos=("acme/widgets",))])
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-operator"], "operator"))
    keys = client.get("/api/v1/apikeys").json()["keys"]
    assert keys[0]["allowed_repos"] == ["acme/widgets"]


def test_owner_changes_the_allowlist_and_the_cache_is_busted():
    from embry0.api import deps

    app = _app()
    app.state.api_keys_repo.set_allowed_repos = AsyncMock(return_value=_key_row(allowed_repos=("acme/widgets",)))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-operator"], "operator"))
    deps._api_key_cache.put("stale", ("key-abc123def456", "mine", "operate", "usr-op", False, False, None), 1e18)
    resp = client.put(
        "/api/v1/apikeys/key-abc123def456/allowed-repos", json={"allowed_repos": ["acme/widgets"]}, headers=XRW
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["allowed_repos"] == ["acme/widgets"]
    app.state.api_keys_repo.set_allowed_repos.assert_awaited_once_with("key-abc123def456", ["acme/widgets"])
    assert deps._api_key_cache.get("stale", 0.0) is None, (
        "a changed allowlist must apply at once, not after the cache TTL"
    )


def test_an_empty_list_lifts_the_restriction():
    app = _app()
    app.state.api_keys_repo.set_allowed_repos = AsyncMock(return_value=_key_row())
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-operator"], "operator"))
    resp = client.put("/api/v1/apikeys/key-abc123def456/allowed-repos", json={"allowed_repos": []}, headers=XRW)
    assert resp.status_code == 200
    assert resp.json()["allowed_repos"] is None
    app.state.api_keys_repo.set_allowed_repos.assert_awaited_once_with("key-abc123def456", None)


def test_an_operator_cannot_change_another_users_key():
    app = _app()
    app.state.api_keys_repo.get = AsyncMock(return_value=_key_row(user_id="usr-x"))
    app.state.api_keys_repo.set_allowed_repos = AsyncMock()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-operator"], "operator"))
    resp = client.put("/api/v1/apikeys/key-abc123def456/allowed-repos", json={"allowed_repos": []}, headers=XRW)
    assert resp.status_code == 403
    assert not app.state.api_keys_repo.set_allowed_repos.await_count


def test_an_admin_changes_a_service_key():
    app = _app()
    app.state.api_keys_repo.get = AsyncMock(return_value=_key_row(user_id=None))
    app.state.api_keys_repo.set_allowed_repos = AsyncMock(return_value=_key_row(user_id=None))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie(["embry0-admin"], "admin"))
    resp = client.put(
        "/api/v1/apikeys/key-abc123def456/allowed-repos", json={"allowed_repos": ["acme/widgets"]}, headers=XRW
    )
    assert resp.status_code == 200, resp.text


def _restricted_key_app() -> tuple[FastAPI, dict[str, str]]:
    from embry0.storage.repositories.api_keys import hash_token

    token = "emb_" + "r" * 43
    app = _app()
    restricted = _key_row(id="key-restricted01", user_id=None, scope="admin", name="bot", allowed_repos=("acme/a",))
    app.state.api_keys_repo.get_by_hash = AsyncMock(
        side_effect=lambda h: restricted if h == hash_token(token) else None
    )
    app.state.api_keys_repo.set_allowed_repos = AsyncMock()
    return app, {"Authorization": f"Bearer {token}", **XRW}


def test_a_restricted_key_cannot_mint_a_key():
    """Even an admin-scope key: an unrestricted key it minted would undo its own list."""
    app, headers = _restricted_key_app()
    resp = TestClient(app).post("/api/v1/apikeys", json={"name": "x", "scope": "read"}, headers=headers)
    assert resp.status_code == 403, f"a restricted key minted a key: {resp.status_code}"
    assert not app.state.api_keys_repo.create.await_count


def test_a_restricted_key_cannot_lift_its_own_list():
    app, headers = _restricted_key_app()
    resp = TestClient(app).put(
        "/api/v1/apikeys/key-restricted01/allowed-repos", json={"allowed_repos": []}, headers=headers
    )
    assert resp.status_code == 403, f"a restricted key changed an allowlist: {resp.status_code}"
    assert not app.state.api_keys_repo.set_allowed_repos.await_count
