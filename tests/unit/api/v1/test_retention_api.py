"""GET /admin/retention/preview (ADR 0017): admin only, a dry run of both sweeps.

Stub-app style, as test_audit_api.py: the real require_auth and require_admin
chain runs (sessions via signed cookie, keys via a mocked api_keys_repo).
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from embry0.api.deps import bust_api_key_cache, bust_user_gate_cache, require_auth
from embry0.api.session import SESSION_COOKIE, SessionData, encode_session
from embry0.cache.qa_image_retention import QAImageRetentionResult
from embry0.config import Embry0Config
from embry0.services.job_log_retention import JobLogRetentionResult
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

SECRET = "s" * 40
PATH = "/api/v1/admin/retention/preview"


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
            sso_roles_claim="https://example.com/roles",
        )


class FakeSweep:
    def __init__(self, result: Any, configured: int | None) -> None:
        self.result = result
        self.retention_days = configured
        self.calls: list[dict[str, Any]] = []

    async def run_once(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.result


def _app() -> FastAPI:
    from embry0.api.v1 import retention

    app = FastAPI()
    app.state.config = _config()
    app.state.db = None
    app.state.job_log_retention = FakeSweep(
        JobLogRetentionResult(retention_days=30, jobs=2, events=5, bytes=900, agent_sessions=1, agent_session_bytes=50),
        configured=None,
    )
    app.state.qa_image_retention = FakeSweep(QAImageRetentionResult(retention_days=14), configured=14)
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(return_value=None)
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    user = MagicMock(id="usr-op", email="op@example.com", session_epoch=1, disabled=False)
    users = MagicMock()
    users.get_by_sub = AsyncMock(return_value=user)
    users.get = AsyncMock(return_value=user)
    app.state.users_repo = users
    app.include_router(retention.router, prefix="/api/v1", dependencies=[require_auth])
    return app


def _key(app: FastAPI, token: str, scope: str) -> None:
    row = ApiKeyRow(
        id=f"key-{scope:0<12}",
        user_id=None,
        name=scope,
        prefix=token[:12],
        scope=scope,
        created_by="op@example.com",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )
    app.state.api_keys_repo.get_by_hash = AsyncMock(side_effect=lambda h: row if h == hash_token(token) else None)


def _cookie(tier: str) -> str:
    return encode_session(
        SECRET,
        SessionData(sub="auth0|op", email="op@example.com", name="O", roles=[f"embry0-{tier}"], tier=tier, epoch=1),
    )


@pytest.fixture(autouse=True)
def _caches():
    bust_api_key_cache()
    bust_user_gate_cache()
    yield
    bust_api_key_cache()
    bust_user_gate_cache()


@pytest.mark.parametrize("scope", ["operate", "read", "merge"])
def test_a_key_below_admin_is_refused(scope: str) -> None:
    app = _app()
    _key(app, "emb_tok12345678", scope)

    resp = TestClient(app).get(PATH, headers={"Authorization": "Bearer emb_tok12345678"})

    assert resp.status_code == 403, f"a {scope}-scope key reached the retention preview"
    assert app.state.job_log_retention.calls == [], "the dry run ran for a refused caller"


def test_an_operator_session_is_refused() -> None:
    client = TestClient(_app())
    client.cookies.set(SESSION_COOKIE, _cookie("operator"))
    assert client.get(PATH).status_code == 403


def test_no_credential_is_refused() -> None:
    assert TestClient(_app()).get(PATH).status_code == 401


def test_an_admin_gets_both_dry_runs() -> None:
    app = _app()

    resp = TestClient(app).get(PATH, headers={"Authorization": "Bearer master-key"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_logs"] == {
        "configured_days": None,
        "retention_days": 30,
        "jobs": 2,
        "events": 5,
        "bytes": 900,
        "agent_sessions": 1,
        "agent_session_bytes": 50,
    }
    assert body["qa_images"]["configured_days"] == 14 and body["qa_images"]["images"] == 0
    assert app.state.job_log_retention.calls == [{"dry_run": True, "retention_days": None}]
    assert app.state.qa_image_retention.calls == [{"dry_run": True, "retention_days": None}]


def test_the_query_previews_a_value_before_it_is_set() -> None:
    app = _app()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie("admin"))

    assert client.get(PATH, params={"job_log_days": 30, "qa_image_days": 7}).status_code == 200
    assert app.state.job_log_retention.calls == [{"dry_run": True, "retention_days": 30}]
    assert app.state.qa_image_retention.calls == [{"dry_run": True, "retention_days": 7}]
    assert client.get(PATH, params={"job_log_days": 0}).status_code == 422


def test_the_served_route_is_admin_tier() -> None:
    """The app mounts it behind require_auth, and the route adds require_admin."""
    from embry0.api.app import create_app
    from embry0.api.deps import _admin_dependency
    from tests.fixtures.routes import app_routes

    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    routes = [r for r in app_routes(app) if r.path == PATH]
    assert routes, f"the app does not serve {PATH}"
    calls = {d.call for r in routes if r.dependant is not None for d in r.dependant.dependencies}
    assert _admin_dependency in calls, f"{PATH} is served without the admin gate"
