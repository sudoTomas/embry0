"""Auth0 SSO tests (RAV-1069): session codec, tier folding, dual auth,
admin gate, /auth endpoints, startup validation."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from embry0.api.app import _check_sso_config
from embry0.api.deps import require_admin, require_auth
from embry0.api.session import (
    SESSION_COOKIE,
    SessionData,
    decode_session,
    decode_state,
    encode_session,
    encode_state,
    fold_tier,
)
from embry0.config import Embry0Config

SECRET = "s" * 40


def _config(**overrides) -> Embry0Config:
    defaults = dict(
        _env_file=None,
        auth_dev_mode=False,
        api_key="master-key",
        auth0_issuer="https://tenant.example.com/",
        auth0_client_id="client-id",
        auth0_client_secret="client-secret",
        session_secret=SECRET,
        sso_public_origin="https://embry0.example.com",
        # #393: no default any more — a full config states its own claim.
        sso_roles_claim="https://example.com/roles",
    )
    defaults.update(overrides)
    with patch.dict(os.environ, {}, clear=True):
        return Embry0Config(**defaults)


def _session_cookie(roles: list[str]) -> str:
    data = SessionData(sub="auth0|u1", email="u@example.com", name="U", roles=roles, tier=fold_tier(roles))
    return encode_session(SECRET, data)


def _app(config: Embry0Config) -> FastAPI:
    from embry0.api.v1 import auth_session

    app = FastAPI()
    app.state.config = config

    @app.get("/guarded", dependencies=[require_auth])
    def guarded():
        return {"ok": True}

    @app.get("/admin-only", dependencies=[require_admin])
    def admin_only():
        return {"ok": True}

    app.include_router(auth_session.router, prefix="/api/v1")
    return app


# ---------------------------------------------------------------------------
# Codec + tier folding
# ---------------------------------------------------------------------------


def test_session_roundtrip_and_tamper():
    data = SessionData(sub="auth0|1", email="a@b.c", name="A", roles=["embry0-admin"], tier="admin")
    token = encode_session(SECRET, data)
    assert decode_session(SECRET, token, max_age=60) == data
    assert decode_session("x" * 40, token, max_age=60) is None
    assert decode_session(SECRET, token + "tamper", max_age=60) is None
    assert decode_session(SECRET, "", max_age=60) is None


def test_fold_tier_default_deny():
    assert fold_tier(["embry0-admin"]) == "admin"
    assert fold_tier(["embry0-operator"]) == "operator"
    assert fold_tier(["embry0-admin", "embry0-operator"]) == "admin"
    assert fold_tier(["Embry0-Operator "]) == "operator"  # case/space-insensitive
    # acme-style roles do NOT grant access — no JIT-REP fallthrough.
    assert fold_tier(["Admin"]) == "none"
    assert fold_tier(["Sales", "AcmeInternal"]) == "none"
    assert fold_tier([]) == "none"


def test_state_cookie_roundtrip():
    payload = {"state": "st", "nonce": "no", "verifier": "ve"}
    token = encode_state(SECRET, payload)
    assert decode_state(SECRET, token) == payload
    assert decode_state(SECRET, token[:-2]) is None


# ---------------------------------------------------------------------------
# Dual auth dependency
# ---------------------------------------------------------------------------


def test_api_key_still_works():
    client = TestClient(_app(_config()))
    assert client.get("/guarded", headers={"Authorization": "Bearer master-key"}).status_code == 200
    assert client.get("/guarded", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_operator_session_passes_guarded():
    client = TestClient(_app(_config()))
    client.cookies.set(SESSION_COOKIE, _session_cookie(["embry0-operator"]))
    assert client.get("/guarded").status_code == 200


def test_tier_none_session_gets_403_not_401():
    client = TestClient(_app(_config()))
    client.cookies.set(SESSION_COOKIE, _session_cookie(["Sales"]))
    resp = client.get("/guarded")
    assert resp.status_code == 403
    assert "sso_role_required" in resp.json()["detail"]


def test_no_credentials_401():
    assert TestClient(_app(_config())).get("/guarded").status_code == 401


def test_session_ignored_when_sso_disabled():
    """A session cookie must be inert on a deploy without SSO configured —
    the cookie can't be validated without SESSION_SECRET."""
    client = TestClient(
        _app(
            _config(
                auth0_issuer="", auth0_client_id="", auth0_client_secret="", session_secret="", sso_public_origin=""
            )
        )
    )
    client.cookies.set(SESSION_COOKIE, _session_cookie(["embry0-admin"]))
    assert client.get("/guarded").status_code == 401


def test_admin_gate():
    client = TestClient(_app(_config()))
    # API key = master credential.
    assert client.get("/admin-only", headers={"Authorization": "Bearer master-key"}).status_code == 200
    # Operator session: authenticated but not admin.
    client.cookies.set(SESSION_COOKIE, _session_cookie(["embry0-operator"]))
    resp = client.get("/admin-only")
    assert resp.status_code == 403
    assert "embry0-admin" in resp.json()["detail"]
    # Admin session passes.
    client.cookies.set(SESSION_COOKIE, _session_cookie(["embry0-admin"]))
    assert client.get("/admin-only").status_code == 200


def test_expired_session_is_401(monkeypatch):
    config = _config()
    monkeypatch.setattr(config, "sso_session_max_age_seconds", -1)
    client = TestClient(_app(config))
    client.cookies.set(SESSION_COOKIE, _session_cookie(["embry0-admin"]))
    assert client.get("/guarded").status_code == 401


# ---------------------------------------------------------------------------
# /auth endpoints
# ---------------------------------------------------------------------------


def test_check_reports_sso_and_session():
    client = TestClient(_app(_config()))
    body = client.get("/api/v1/auth/check").json()
    assert body == {
        "sso_enabled": True,
        "authenticated": False,
        "tier": None,
        "api_key_login": True,
        "key_tier": None,
    }
    client.cookies.set(SESSION_COOKIE, _session_cookie(["embry0-operator"]))
    body = client.get("/api/v1/auth/check").json()
    assert body["authenticated"] is True and body["tier"] == "operator"


def test_check_when_sso_off():
    config = _config(
        auth0_issuer="", auth0_client_id="", auth0_client_secret="", session_secret="", sso_public_origin=""
    )
    body = TestClient(_app(config)).get("/api/v1/auth/check").json()
    assert body["sso_enabled"] is False


def test_me_requires_session():
    client = TestClient(_app(_config()))
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.set(SESSION_COOKIE, _session_cookie(["embry0-admin"]))
    body = client.get("/api/v1/auth/me").json()
    assert body["tier"] == "admin" and body["access"] is True


def test_me_tier_none_still_shows_identity():
    """The access-denied screen needs the identity even without a role."""
    client = TestClient(_app(_config()))
    client.cookies.set(SESSION_COOKIE, _session_cookie(["Sales"]))
    body = client.get("/api/v1/auth/me").json()
    assert body["access"] is False and body["email"] == "u@example.com"


def test_login_redirects_to_authorize():
    app = _app(_config())
    app.state.oidc_metadata = {
        "authorization_endpoint": "https://tenant.example.com/authorize",
        "token_endpoint": "https://tenant.example.com/oauth/token",
        "jwks_uri": "https://tenant.example.com/.well-known/jwks.json",
    }
    client = TestClient(app)
    resp = client.get("/api/v1/auth/login", follow_redirects=False)
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("https://tenant.example.com/authorize?")
    assert "code_challenge_method=S256" in loc
    assert "redirect_uri=https%3A%2F%2Fembry0.example.com%2Fapi%2Fv1%2Fauth%2Fcallback" in loc
    assert "embry0_oidc_state" in resp.headers.get("set-cookie", "")


def test_login_404_when_sso_off():
    config = _config(
        auth0_issuer="", auth0_client_id="", auth0_client_secret="", session_secret="", sso_public_origin=""
    )
    assert TestClient(_app(config)).get("/api/v1/auth/login", follow_redirects=False).status_code == 404


def test_callback_rejects_bad_state():
    app = _app(_config())
    app.state.oidc_metadata = {"token_endpoint": "https://tenant.example.com/oauth/token"}
    client = TestClient(app)
    resp = client.get("/api/v1/auth/callback?code=x&state=forged", follow_redirects=False)
    assert resp.status_code == 400


def test_logout_clears_cookie_and_returns_url():
    client = TestClient(_app(_config()))
    client.cookies.set(SESSION_COOKIE, _session_cookie(["embry0-admin"]))
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 200
    assert "v2/logout" in resp.json()["logoutUrl"]
    assert 'embry0_session="";' in resp.headers.get("set-cookie", "") or "embry0_session=;" in resp.headers.get(
        "set-cookie", ""
    )


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------


def test_sso_config_all_unset_ok():
    # #393 made the roles claim one of the all-or-nothing fields, so "unset"
    # has to include it — and must still mean SSO off, never an error.
    off = _config(
        auth0_issuer="",
        auth0_client_id="",
        auth0_client_secret="",
        session_secret="",
        sso_public_origin="",
        sso_roles_claim="",
    )
    _check_sso_config(off)
    assert off.sso_enabled is False


def test_sso_config_full_ok():
    _check_sso_config(_config())


def test_sso_config_partial_refuses():
    with pytest.raises(RuntimeError, match="Partial SSO configuration"):
        _check_sso_config(_config(session_secret=""))


def test_sso_config_without_a_roles_claim_refuses():
    # #393: the claim used to default to one deployment's domain. Empty is now
    # a partial config and refuses startup, because the alternative is every
    # user authenticating with zero roles and being refused by default-deny,
    # with nothing in the log naming the claim as the reason.
    with pytest.raises(RuntimeError, match="SSO_ROLES_CLAIM"):
        _check_sso_config(_config(sso_roles_claim=""))
    assert _config(sso_roles_claim="").sso_enabled is False, "a claimless config is not 'SSO on'"


def test_sso_config_short_secret_refuses():
    with pytest.raises(RuntimeError, match="32 characters"):
        _check_sso_config(_config(session_secret="short"))


def test_sso_config_http_issuer_refuses():
    with pytest.raises(RuntimeError, match="https"):
        _check_sso_config(_config(auth0_issuer="http://tenant.example.com/"))


# ---------------------------------------------------------------------------
# Public origin = SSO-only login form (api_key_login flag)
# ---------------------------------------------------------------------------


def test_check_api_key_login_false_on_public_origin():
    """Requests arriving via the public hostname must not offer the key
    form (owner decision) — bearer-header auth itself is unaffected."""
    client = TestClient(_app(_config()))
    body = client.get("/api/v1/auth/check", headers={"host": "embry0.example.com"}).json()
    assert body["api_key_login"] is False


def test_check_api_key_login_true_on_lan():
    client = TestClient(_app(_config()))
    body = client.get("/api/v1/auth/check", headers={"host": "192.168.1.10:8200"}).json()
    assert body["api_key_login"] is True


def test_check_api_key_login_true_when_sso_off():
    config = _config(
        auth0_issuer="", auth0_client_id="", auth0_client_secret="", session_secret="", sso_public_origin=""
    )
    body = TestClient(_app(config)).get("/api/v1/auth/check").json()
    assert body["api_key_login"] is True


def test_api_key_bearer_still_works_via_public_host_header():
    """The flag gates the FORM only — bearer requests through the public
    origin keep working (MCP-anywhere requirement)."""
    client = TestClient(_app(_config()))
    resp = client.get(
        "/guarded",
        headers={"host": "embry0.example.com", "Authorization": "Bearer master-key"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /auth/check reports the Bearer key's tier (#101)
# ---------------------------------------------------------------------------


def _key_row(scope: str):
    from embry0.storage.repositories.api_keys import ApiKeyRow

    return ApiKeyRow(
        id="key-0001",
        user_id=None,
        name="dashboard",
        prefix="emb_dash0001",
        scope=scope,
        created_by="op@example.com",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )


def _app_with_key(scope: str, token: str = "emb_db-token") -> FastAPI:
    """An app whose keys repo knows exactly one DB key, of ``scope``."""
    from unittest.mock import AsyncMock, MagicMock

    from embry0.storage.repositories.api_keys import hash_token

    app = _app(_config())
    keys_repo = MagicMock()
    keys_repo.get_by_hash = AsyncMock(side_effect=lambda h: _key_row(scope) if h == hash_token(token) else None)
    keys_repo.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys_repo
    return app


@pytest.fixture(autouse=True)
def _clear_api_key_cache():
    from embry0.api.deps import bust_api_key_cache

    bust_api_key_cache()
    yield
    bust_api_key_cache()


def test_check_key_tier_master_key_is_admin():
    client = TestClient(_app(_config()))
    body = client.get("/api/v1/auth/check", headers={"Authorization": "Bearer master-key"}).json()
    assert body["key_tier"] == "admin", body


def test_check_key_tier_operate_scope_is_operator():
    client = TestClient(_app_with_key("operate"))
    body = client.get("/api/v1/auth/check", headers={"Authorization": "Bearer emb_db-token"}).json()
    assert body["key_tier"] == "operator", body


def test_check_key_tier_read_scope_is_read():
    client = TestClient(_app_with_key("read"))
    body = client.get("/api/v1/auth/check", headers={"Authorization": "Bearer emb_db-token"}).json()
    assert body["key_tier"] == "read", body


def test_check_key_tier_unknown_token_is_null_not_401():
    """The probe stays unauthenticated: a bad key answers 200 with no tier."""
    client = TestClient(_app_with_key("operate"))
    resp = client.get("/api/v1/auth/check", headers={"Authorization": "Bearer not-a-key"})
    assert resp.status_code == 200
    assert resp.json()["key_tier"] is None, resp.json()


def test_check_key_tier_absent_without_bearer_header():
    client = TestClient(_app(_config()))
    body = client.get("/api/v1/auth/check").json()
    assert "key_tier" in body, "the field is always present"
    assert body["key_tier"] is None


def test_check_reports_session_tier_and_key_tier_side_by_side():
    """A session cookie and a key header are two answers, not one."""
    client = TestClient(_app(_config()))
    client.cookies.set(SESSION_COOKIE, _session_cookie(["embry0-operator"]))
    body = client.get("/api/v1/auth/check", headers={"Authorization": "Bearer master-key"}).json()
    assert body["authenticated"] is True
    assert body["tier"] == "operator"
    assert body["key_tier"] == "admin"


# ---------------------------------------------------------------------------
# Users-table session gate (RAV-1056 Phase A)
# ---------------------------------------------------------------------------


def _session_cookie_with_epoch(roles: list[str], epoch: int) -> str:
    data = SessionData(sub="auth0|u1", email="u@example.com", name="U", roles=roles, tier=fold_tier(roles), epoch=epoch)
    return encode_session(SECRET, data)


def _users_repo_stub(epoch: int = 1, disabled: bool = False):
    from unittest.mock import AsyncMock, MagicMock

    row = MagicMock()
    row.session_epoch = epoch
    row.disabled = disabled
    row.auth0_sub = "auth0|u1"
    repo = MagicMock()
    repo.get_by_sub = AsyncMock(return_value=row)
    return repo


@pytest.fixture(autouse=True)
def _clear_user_gate_cache():
    from embry0.api.deps import bust_user_gate_cache

    bust_user_gate_cache()
    yield
    bust_user_gate_cache()


def test_gate_passes_with_matching_epoch():
    app = _app(_config())
    app.state.users_repo = _users_repo_stub(epoch=2)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _session_cookie_with_epoch(["embry0-operator"], epoch=2))
    assert client.get("/guarded").status_code == 200


def test_gate_rejects_stale_epoch():
    """Force-logout: cookie minted under epoch 1, DB bumped to 2 -> 401."""
    app = _app(_config())
    app.state.users_repo = _users_repo_stub(epoch=2)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _session_cookie_with_epoch(["embry0-operator"], epoch=1))
    assert client.get("/guarded").status_code == 401


def test_gate_rejects_disabled_user():
    app = _app(_config())
    app.state.users_repo = _users_repo_stub(epoch=1, disabled=True)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _session_cookie_with_epoch(["embry0-admin"], epoch=1))
    assert client.get("/guarded").status_code == 401


def test_gate_rejects_unknown_sub():
    from unittest.mock import AsyncMock, MagicMock

    app = _app(_config())
    repo = MagicMock()
    repo.get_by_sub = AsyncMock(return_value=None)
    app.state.users_repo = repo
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _session_cookie_with_epoch(["embry0-admin"], epoch=1))
    assert client.get("/guarded").status_code == 401


def test_gate_fails_open_without_users_repo():
    """Pre-Phase-A boots and unit-test stub apps: no repo -> cookie alone
    authenticates (the pre-existing behavior, pinned)."""
    client = TestClient(_app(_config()))
    client.cookies.set(SESSION_COOKIE, _session_cookie_with_epoch(["embry0-operator"], epoch=0))
    assert client.get("/guarded").status_code == 200


def test_gate_api_key_unaffected_by_disabled_users():
    app = _app(_config())
    app.state.users_repo = _users_repo_stub(disabled=True)
    client = TestClient(app)
    assert client.get("/guarded", headers={"Authorization": "Bearer master-key"}).status_code == 200


def test_pre_phase_a_cookie_decodes_with_epoch_zero():
    """Cookies minted before the epoch field exist decode (epoch=0) and fail
    the gate against any real row (epoch >= 1) — one forced re-login."""
    legacy = SessionData(sub="auth0|u1", email="u@x.c", name="U", roles=["embry0-admin"], tier="admin")
    assert legacy.epoch == 0
    token = encode_session(SECRET, legacy)
    back = decode_session(SECRET, token, max_age=60)
    assert back is not None and back.epoch == 0
