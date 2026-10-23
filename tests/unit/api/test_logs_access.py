"""Who may read container logs, and what they get back.

``GET /logs/query`` and ``GET /logs/containers`` read production logs, which
carry personal data. They need operator tier: an operate or admin key, an SSO
session, or the master key. The one read-scope key they admit is the
context-proxy's own service key, which forwards allowlisted queries for
enrolled sandboxes. Every line comes back through the log-text redactor,
whoever asks.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.safety.redaction import register_redacted_fields
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

LOKI = "http://loki.test:3100"
PERSONAL_LINE = 'order saved customer_name="Jane Roe" phone=+1-555-0100 email=jane@example.com'


def _key(key_id: str, name: str, scope: str, *, owner: str | None = None) -> ApiKeyRow:
    return ApiKeyRow(
        id=key_id,
        user_id=owner,
        name=name,
        prefix="emb_xxxxxxxx",
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )


TOKENS = {
    "read": ("emb_" + "r" * 43, _key("key-read00000001", "dashboard-bot", "read")),
    "merge": ("emb_" + "m" * 43, _key("key-merge0000001", "chat-relay", "merge")),
    "operate": ("emb_" + "o" * 43, _key("key-operate00001", "ops-bot", "operate")),
    "admin": ("emb_" + "a" * 43, _key("key-admin0000001", "admin-bot", "admin")),
    # The key app.py rotates at boot for the context-proxy: unowned, read scope.
    "context-proxy": ("emb_" + "c" * 43, _key("key-ctxproxy0001", "svc-context-proxy", "read")),
    # The same name on a key a person owns is just a read key.
    "owned-lookalike": (
        "emb_" + "l" * 43,
        _key("key-lookalike001", "svc-context-proxy", "read", owner="usr-000000000001"),
    ),
}
KEYS = {hash_token(token): row for token, row in TOKENS.values()}

ROUTES = ["/api/v1/logs/query?container=backend", "/api/v1/logs/containers"]


def _loki(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/label/container_name/values"):
        return httpx.Response(200, json={"data": ["app-backend"]})
    return httpx.Response(
        200,
        json={
            "data": {
                "result": [{"stream": {"container_name": "app-backend"}, "values": [["1", PERSONAL_LINE]]}],
            }
        },
    )


@pytest.fixture
def app() -> Any:
    from embry0.api.app import create_app
    from embry0.api.v1 import logs as logs_module

    @asynccontextmanager
    async def _noop_lifespan(app: Any) -> Any:
        yield

    bust_api_key_cache()
    logs_module._CONTAINERS_CACHE.update(at=0.0, values=[])
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(
            _env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key", watcher_loki_url=LOKI
        )
    app = create_app(config, lifespan_override=_noop_lifespan)
    app.state.config = config
    app.state.db = None
    app.state.users_repo = None
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    yield app
    bust_api_key_cache()
    logs_module._CONTAINERS_CACHE.update(at=0.0, values=[])


def _client_with(handler: Any) -> Any:
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    return _factory


def _get(app: Any, path: str, token: str, loki: Any = _loki) -> httpx.Response:
    with patch("embry0.api.v1.logs.httpx.AsyncClient", _client_with(loki)):
        return TestClient(app, raise_server_exceptions=False).get(path, headers={"Authorization": f"Bearer {token}"})


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("caller", ["read", "merge", "owned-lookalike"])
def test_a_read_or_merge_key_cannot_read_container_logs(app: Any, caller: str, path: str) -> None:
    response = _get(app, path, TOKENS[caller][0])
    assert response.status_code == 403, (
        f"a {caller} key read {path}: {response.status_code} {response.text}; log lines carry personal data"
    )
    assert "Jane Roe" not in response.text


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("caller", ["operate", "admin", "context-proxy", "master"])
def test_operator_tier_and_the_context_proxy_key_read_container_logs(app: Any, caller: str, path: str) -> None:
    token = "master-key" if caller == "master" else TOKENS[caller][0]
    response = _get(app, path, token)
    assert response.status_code == 200, f"a {caller} caller was refused {path}: {response.status_code} {response.text}"


def test_every_returned_line_is_redacted(app: Any) -> None:
    register_redacted_fields("customer_name,phone")
    try:
        response = _get(app, ROUTES[0], TOKENS["operate"][0])
    finally:
        register_redacted_fields([])
    assert response.status_code == 200, response.text
    line = response.json()["lines"][0]["line"]
    for personal in ("Jane Roe", "555-0100", "jane@example.com"):
        assert personal not in line, f"{personal!r} came back unredacted: {line!r}"
    assert line.startswith("order saved customer_name="), f"the redactor removed more than the values: {line!r}"


def test_a_secret_in_a_line_is_redacted_and_a_long_line_is_cut(app: Any) -> None:
    secret_line = "auth failed Authorization: Bearer ghp_" + "Z" * 36
    long_line = "x " * 20_000

    def _loki_lines(request: httpx.Request) -> httpx.Response:
        values = [["2", secret_line], ["1", long_line]]
        return httpx.Response(200, json={"data": {"result": [{"stream": {"container_name": "c"}, "values": values}]}})

    response = _get(app, ROUTES[0], TOKENS["operate"][0], loki=_loki_lines)
    assert response.status_code == 200, response.text
    lines = [entry["line"] for entry in response.json()["lines"]]
    assert "ghp_" + "Z" * 36 not in lines[0], f"a forge token came back: {lines[0]!r}"
    assert len(lines[1]) < len(long_line.strip()), "a 40 KB line came back whole"
    assert lines[1].endswith("[line truncated]"), f"a cut line is not marked: {lines[1][-40:]!r}"
