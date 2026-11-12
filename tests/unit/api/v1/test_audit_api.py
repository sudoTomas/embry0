"""/audit global read API tests (RAV-1056 Phase C).

Stub-app style: the real require_auth dependency chain runs (sessions via
signed cookie, keys via mocked api_keys_repo), so row-scoping decisions in
the handler see exactly the request.state the production mount produces.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from embry0.api.deps import bust_api_key_cache, bust_user_gate_cache, require_auth
from embry0.api.session import SESSION_COOKIE, SessionData, encode_session
from embry0.config import Embry0Config
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

SECRET = "s" * 40

ROW = {
    "id": 1,
    "action": "user.login",
    "actor": "op@example.com",
    "details": {"tier": "operator"},
    "issue_id": None,
    "created_at": datetime(2026, 8, 1, tzinfo=UTC),
}


def _config(**overrides) -> Embry0Config:
    kwargs: dict = dict(
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
    kwargs.update(overrides)
    with patch.dict(os.environ, {}, clear=True):
        return Embry0Config(_env_file=None, **kwargs)


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


def _app(config: Embry0Config | None = None) -> FastAPI:
    from embry0.api.v1 import audit

    app = FastAPI()
    app.state.config = config or _config()
    app.state.db = None

    audit_repo = MagicMock()
    audit_repo.list_entries = AsyncMock(return_value=[ROW])
    audit_repo.count_entries = AsyncMock(return_value=1)
    app.state.audit_repo = audit_repo

    keys_repo = MagicMock()
    keys_repo.get_by_hash = AsyncMock(return_value=None)
    keys_repo.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys_repo

    users_repo = MagicMock()
    op_user = MagicMock()
    op_user.id = "usr-op"
    op_user.email = "op@example.com"
    op_user.session_epoch = 1
    op_user.disabled = False
    users_repo.get_by_sub = AsyncMock(return_value=op_user)
    users_repo.get = AsyncMock(return_value=op_user)
    app.state.users_repo = users_repo

    app.include_router(audit.router, prefix="/api/v1", dependencies=[require_auth])
    return app


def _cookie(tier: str, email: str = "op@example.com") -> str:
    return encode_session(
        SECRET,
        SessionData(sub="auth0|op", email=email, name="O", roles=[f"embry0-{tier}"], tier=tier, epoch=1),
    )


def _wire_key(app: FastAPI, token: str, row: ApiKeyRow) -> None:
    app.state.api_keys_repo.get_by_hash = AsyncMock(side_effect=lambda h: row if h == hash_token(token) else None)


@pytest.fixture(autouse=True)
def _caches():
    bust_api_key_cache()
    bust_user_gate_cache()
    yield
    bust_api_key_cache()
    bust_user_gate_cache()


def test_dev_mode_fails_open_to_admin():
    app = _app(_config(auth_dev_mode=True))
    body = TestClient(app).get("/api/v1/audit").json()
    assert body == {
        "entries": [
            {
                "id": 1,
                "action": "user.login",
                "actor": "op@example.com",
                "details": {"tier": "operator"},
                "issue_id": None,
                "created_at": "2026-08-01T00:00:00Z",
            }
        ],
        "total": 1,
    }
    assert app.state.audit_repo.list_entries.call_args.kwargs["actors"] is None


def test_admin_session_sees_all_rows_and_actor_passthrough():
    app = _app()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie("admin"))
    assert client.get("/api/v1/audit").status_code == 200
    assert app.state.audit_repo.list_entries.call_args.kwargs["actors"] is None
    client.get("/api/v1/audit", params={"actor": "someone@x.com"})
    assert app.state.audit_repo.list_entries.call_args.kwargs["actors"] == ["someone@x.com"]


def test_operator_session_scoped_to_own_rows():
    app = _app()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie("operator"))
    assert client.get("/api/v1/audit").status_code == 200
    assert app.state.audit_repo.list_entries.call_args.kwargs["actors"] == ["op@example.com"]


def test_operator_foreign_actor_filter_short_circuits():
    app = _app()
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _cookie("operator"))
    body = client.get("/api/v1/audit", params={"actor": "other@x.com"}).json()
    assert body == {"entries": [], "total": 0}
    app.state.audit_repo.list_entries.assert_not_called()


def test_master_key_is_admin():
    app = _app()
    resp = TestClient(app).get("/api/v1/audit", headers={"Authorization": "Bearer master-key"})
    assert resp.status_code == 200
    assert app.state.audit_repo.list_entries.call_args.kwargs["actors"] is None


def test_owned_key_scopes_to_owner_email():
    app = _app()
    _wire_key(app, "emb_tok1", _key_row())
    resp = TestClient(app).get("/api/v1/audit", headers={"Authorization": "Bearer emb_tok1"})
    assert resp.status_code == 200
    assert app.state.audit_repo.list_entries.call_args.kwargs["actors"] == ["op@example.com"]


def test_service_key_scopes_to_key_name_actor():
    app = _app()
    _wire_key(app, "emb_svc1", _key_row(id="key-svc111111111", user_id=None, name="hermes", scope="operate"))
    resp = TestClient(app).get("/api/v1/audit", headers={"Authorization": "Bearer emb_svc1"})
    assert resp.status_code == 200
    assert app.state.audit_repo.list_entries.call_args.kwargs["actors"] == ["key:hermes"]


def test_read_scope_key_gets_own_rows():
    app = _app()
    _wire_key(app, "emb_read", _key_row(id="key-read11111111", user_id=None, name="probe", scope="read"))
    resp = TestClient(app).get("/api/v1/audit", headers={"Authorization": "Bearer emb_read"})
    assert resp.status_code == 200
    assert app.state.audit_repo.list_entries.call_args.kwargs["actors"] == ["key:probe"]


def test_admin_scope_key_sees_all_rows():
    app = _app()
    _wire_key(app, "emb_adm1", _key_row(id="key-adm111111111", user_id=None, name="ops", scope="admin"))
    resp = TestClient(app).get("/api/v1/audit", headers={"Authorization": "Bearer emb_adm1"})
    assert resp.status_code == 200
    assert app.state.audit_repo.list_entries.call_args.kwargs["actors"] is None


def test_limit_bounds_422():
    app = _app(_config(auth_dev_mode=True))
    client = TestClient(app)
    assert client.get("/api/v1/audit", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/audit", params={"limit": 500}).status_code == 422


def test_naive_since_normalized_to_utc():
    app = _app(_config(auth_dev_mode=True))
    TestClient(app).get("/api/v1/audit", params={"since": "2026-08-01T00:00:00"})
    since = app.state.audit_repo.list_entries.call_args.kwargs["since"]
    assert since.tzinfo is not None


def test_details_redaction_shallow_and_nested():
    app = _app(_config(auth_dev_mode=True))
    app.state.audit_repo.list_entries = AsyncMock(
        return_value=[
            {
                **ROW,
                "details": {
                    "telegram_token": "123:abc",
                    "webhook_notify_secret": "shh",
                    "nested": {"api_key": "k", "plain": "ok"},
                    "plain": "visible",
                },
            }
        ]
    )
    body = TestClient(app).get("/api/v1/audit").json()
    details = body["entries"][0]["details"]
    assert details["telegram_token"] == "[redacted]"
    assert details["webhook_notify_secret"] == "[redacted]"
    assert details["nested"]["api_key"] == "[redacted]"
    assert details["nested"]["plain"] == "ok"
    assert details["plain"] == "visible"


def test_details_redaction_inside_lists_and_of_a_whole_value():
    """A secret-named key's value is hidden whole, whatever it is, and a list is walked like an object."""
    app = _app(_config(auth_dev_mode=True))
    app.state.audit_repo.list_entries = AsyncMock(
        return_value=[
            {
                **ROW,
                "details": {
                    "updates": [{"api_key": "fake-000000000002", "name": "kept"}, "plain"],
                    "token": {"value": "fake-000000000003"},
                    "secrets": ["fake-000000000004"],
                    "nested": {"rows": [[{"password": "fake-000000000005", "n": 1}]]},
                    "counts": [1, 2],
                    "Auth_Token": "fake-000000000008",
                },
            }
        ]
    )
    body = TestClient(app).get("/api/v1/audit").json()
    details = body["entries"][0]["details"]
    assert details["updates"] == [{"api_key": "[redacted]", "name": "kept"}, "plain"], details["updates"]
    assert details["token"] == "[redacted]", details["token"]
    assert details["secrets"] == "[redacted]", details["secrets"]
    assert details["nested"] == {"rows": [[{"password": "[redacted]", "n": 1}]]}, details["nested"]
    assert details["counts"] == [1, 2]
    assert details["Auth_Token"] == "[redacted]", "a key is matched whatever its case"
    assert "fake-0000000000" not in str(details), f"a secret came back: {details}"


def test_details_redaction_walks_a_value_deeper_than_the_recursion_limit():
    """5,000 levels, past the interpreter's recursion limit: the walk keeps its own stack."""
    from embry0.api.v1.audit import _redact

    bottom = {"api_key": "fake-000000000006", "items": [{"token": "fake-000000000007", "n": 1}], "kept": "yes"}
    value: dict = bottom
    for _ in range(5_000):
        value = {"a": value}
    redacted = _redact({"deep": value})
    walked, levels = redacted["deep"], 0
    while "a" in walked:
        walked, levels = walked["a"], levels + 1
    assert levels == 5_000, f"the copy is {levels} levels deep, not 5,000"
    assert walked == {"api_key": "[redacted]", "items": [{"token": "[redacted]", "n": 1}], "kept": "yes"}, walked


def test_response_has_no_trace_id():
    app = _app(_config(auth_dev_mode=True))
    body = TestClient(app).get("/api/v1/audit").json()
    assert "trace_id" not in body["entries"][0]
