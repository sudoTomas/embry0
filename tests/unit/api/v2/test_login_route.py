"""POST /v2/auth/dashboard/login route tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

_VALID_KEY = "test-api-key-32-characters-minimum-x"


def _client_with_key(api_key: str = _VALID_KEY) -> TestClient:
    from athanor.api.app import create_app

    app = create_app()
    # The TestClient takes the app verbatim; we set state.config attributes
    # to mimic what the lifespan would normally populate.
    class _Cfg:
        pass
    cfg = _Cfg()
    cfg.api_key = api_key
    cfg.auth_dev_mode = False
    cfg.webhook_dev_mode = False
    app.state.config = cfg
    return TestClient(app)


def test_login_with_valid_key_returns_token_and_sets_cookie():
    client = _client_with_key()
    resp = client.post("/v2/auth/dashboard/login", json={"api_key": _VALID_KEY}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    assert "expires_at" in body
    # Cookie set
    assert "dashboard_session" in resp.cookies


def test_login_with_invalid_key_returns_401():
    client = _client_with_key()
    resp = client.post("/v2/auth/dashboard/login", json={"api_key": "wrong-key-also-32-chars-long-ok"}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 401


def test_login_with_short_key_validation_fails_at_pydantic():
    client = _client_with_key()
    resp = client.post("/v2/auth/dashboard/login", json={"api_key": "short"}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 422


def test_login_in_dev_mode_accepts_any_key():
    """When auth_dev_mode is True, login accepts any api_key — but still
    requires it pass the Pydantic min_length to avoid empty submissions."""
    from athanor.api.app import create_app

    class _Cfg:
        pass
    cfg = _Cfg()
    cfg.api_key = "real-prod-key-not-used-in-dev-mode"
    cfg.auth_dev_mode = True
    cfg.webhook_dev_mode = False
    app = create_app()
    app.state.config = cfg

    client = TestClient(app)
    resp = client.post("/v2/auth/dashboard/login", json={"api_key": "any-32-char-key-works-in-dev-mode-x"}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200
