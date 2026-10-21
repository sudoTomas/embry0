"""The dashboard form of POST /merge-approvals/{id}/decide is admin-tier (spec 3.8).

The relay's form (decision, decided_by, chat_id) keeps working for the merge
key the chat client holds; the dashboard's form (decision alone) is for an
admin, and the route names the actor from who authenticated. Sessions are
posed the way tests/unit/api/v1/test_audit_api.py poses them: a signed cookie
and a users row that passes the session gate.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.deps import bust_api_key_cache, bust_user_gate_cache
from embry0.api.session import SESSION_COOKIE, SessionData, encode_session
from embry0.config import Embry0Config
from embry0.orchestration import dispatch_actors
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

pytestmark = pytest.mark.asyncio

SECRET = "s" * 40
KEYS = {"emb_read": "read", "emb_merge": "merge", "emb_operate": "operate", "emb_admin": "admin"}
RESULT = {
    "approval_id": "mrg-1",
    "outcome": "would_merge",
    "status": "approved",
    "dry_run": True,
    "detail": None,
    "repo": "acme/widgets",
    "pr_number": 42,
    "pr_url": "https://github.com/acme/widgets/pull/42",
    "head_sha": "a" * 40,
    "merge_sha": None,
    "decided_by": "x",
}


def _key_row(token: str, scope: str) -> ApiKeyRow:
    return ApiKeyRow(
        id=f"key-{scope}-000000",
        user_id=None,
        name=f"{scope}-key",
        prefix=token[:12],
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )


@pytest.fixture
def app():
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    with patch.dict(os.environ, {}, clear=True):
        app.state.config = Embry0Config(
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
    rows = {hash_token(token): _key_row(token, scope) for token, scope in KEYS.items()}
    app.state.api_keys_repo = MagicMock()
    app.state.api_keys_repo.get_by_hash = AsyncMock(side_effect=lambda h: rows.get(h))
    app.state.api_keys_repo.touch_last_used = AsyncMock()
    # The users-table session gate: an enabled user whose epoch matches the cookie's.
    user = MagicMock()
    user.id = "usr-ops"
    user.email = "ops@example.test"
    user.session_epoch = 1
    user.disabled = False
    app.state.users_repo = MagicMock()
    app.state.users_repo.get_by_sub = AsyncMock(return_value=user)
    app.state.users_repo.get = AsyncMock(return_value=user)
    service = MagicMock()
    service.mode = "dry_run"
    service.get = AsyncMock(return_value={"id": "mrg-1", "repo": "acme/widgets", "status": "pending"})
    service.decide = AsyncMock(side_effect=lambda *_a, **kw: {**RESULT, "decided_by": kw["decided_by"]})
    app.state.merge_approvals = service
    app.state.db = None  # emit_audit without a database only logs
    bust_api_key_cache()
    bust_user_gate_cache()
    yield app
    bust_api_key_cache()
    bust_user_gate_cache()


def _cookie(tier: str, email: str = "ops@example.test") -> str:
    return encode_session(
        SECRET,
        SessionData(sub="auth0|ops", email=email, name="O", roles=[f"embry0-{tier}"], tier=tier, epoch=1),
    )


async def _decide(app, token: str, body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/api/v1/merge-approvals/mrg-1/decide",
            json=body,
            headers={"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"},
        )


async def _decide_as_session(app, tier: str, body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(SESSION_COOKIE, _cookie(tier))
        return await client.post(
            "/api/v1/merge-approvals/mrg-1/decide", json=body, headers={"X-Requested-With": "XMLHttpRequest"}
        )


# A read key never reaches the route: the router's own auth refuses every
# mutation to it. A merge or operate key gets in and is stopped by the admin gate.
@pytest.mark.parametrize(
    "token, refused_for", [("emb_read", "mutations"), ("emb_merge", "admin"), ("emb_operate", "admin")]
)
async def test_a_key_below_admin_cannot_decide_from_the_dashboard(app, token, refused_for):
    resp = await _decide(app, token, {"decision": "approve"})
    assert resp.status_code == 403, f"a {KEYS[token]} key decided from the dashboard: {resp.status_code} {resp.text}"
    assert refused_for in resp.json()["detail"], resp.text
    app.state.merge_approvals.decide.assert_not_awaited()


@pytest.mark.parametrize("token, actor", [("emb_admin", "key:admin-key"), ("master-key", dispatch_actors.MASTER_KEY)])
async def test_an_admin_key_decides_as_itself(app, token, actor):
    resp = await _decide(app, token, {"decision": "approve"})
    assert resp.status_code == 200, resp.text
    kwargs = app.state.merge_approvals.decide.await_args.kwargs
    assert kwargs["via"] == "dashboard"
    assert kwargs["chat_id"] == "dashboard"
    assert kwargs["decided_by"] == actor, "the actor is who authenticated, not a name the client sent"
    assert resp.json()["decided_by"] == kwargs["decided_by"]


async def test_an_admin_session_decides_as_its_email(app):
    resp = await _decide_as_session(app, "admin", {"decision": "approve"})
    assert resp.status_code == 200, resp.text
    kwargs = app.state.merge_approvals.decide.await_args.kwargs
    assert (kwargs["via"], kwargs["decided_by"]) == ("dashboard", "ops@example.test"), (
        "an admin session's decision is attributed to the session's email"
    )
    assert resp.json()["decided_by"] == "ops@example.test"


async def test_an_operator_session_cannot_decide_from_the_dashboard(app):
    resp = await _decide_as_session(app, "operator", {"decision": "approve"})
    assert resp.status_code == 403, f"an operator session decided from the dashboard: {resp.status_code} {resp.text}"
    assert resp.json()["detail"] == "embry0-admin role required"
    app.state.merge_approvals.decide.assert_not_awaited()


async def test_the_relay_form_still_works_for_the_merge_key(app):
    resp = await _decide(
        app, "emb_merge", {"decision": "approve", "decided_by": "Alex", "chat_id": "19:chat@thread.v2"}
    )
    assert resp.status_code == 200, resp.text
    kwargs = app.state.merge_approvals.decide.await_args.kwargs
    assert (kwargs["via"], kwargs["decided_by"], kwargs["chat_id"]) == ("chat", "Alex", "19:chat@thread.v2")


async def test_no_credentials_is_401(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # The CSRF marker alone: without it the middleware answers 403 before auth runs.
        resp = await client.post(
            "/api/v1/merge-approvals/mrg-1/decide",
            json={"decision": "approve"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 401, resp.text
