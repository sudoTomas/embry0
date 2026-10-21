"""The dashboard form of POST /merge-approvals/{id}/decide is admin-tier (spec 3.8).

The relay's form (decision, decided_by, chat_id) keeps working for the merge
key the chat client holds; the dashboard's form (decision alone) is for an
admin, and the route names the actor from who authenticated.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

pytestmark = pytest.mark.asyncio

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
        app.state.config = Embry0Config(_env_file=None, auth_dev_mode=False, api_key="master-key")
    rows = {hash_token(token): _key_row(token, scope) for token, scope in KEYS.items()}
    app.state.api_keys_repo = MagicMock()
    app.state.api_keys_repo.get_by_hash = AsyncMock(side_effect=lambda h: rows.get(h))
    app.state.api_keys_repo.touch_last_used = AsyncMock()
    app.state.users_repo = MagicMock()
    service = MagicMock()
    service.mode = "dry_run"
    service.get = AsyncMock(return_value={"id": "mrg-1", "repo": "acme/widgets", "status": "pending"})
    service.decide = AsyncMock(side_effect=lambda *_a, **kw: {**RESULT, "decided_by": kw["decided_by"]})
    app.state.merge_approvals = service
    app.state.db = None  # emit_audit without a database only logs
    bust_api_key_cache()
    yield app
    bust_api_key_cache()


async def _decide(app, token: str, body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/api/v1/merge-approvals/mrg-1/decide",
            json=body,
            headers={"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"},
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


@pytest.mark.parametrize("token, actor", [("emb_admin", "key:admin-key"), ("master-key", None)])
async def test_an_admin_key_decides_as_itself(app, token, actor):
    resp = await _decide(app, token, {"decision": "approve"})
    assert resp.status_code == 200, resp.text
    kwargs = app.state.merge_approvals.decide.await_args.kwargs
    assert kwargs["via"] == "dashboard"
    assert kwargs["chat_id"] == "dashboard"
    if actor is not None:
        assert kwargs["decided_by"] == actor, "the actor is who authenticated, not a name the client sent"
    assert kwargs["decided_by"], "the master key still names an actor"
    assert resp.json()["decided_by"] == kwargs["decided_by"]


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
