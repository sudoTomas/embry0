"""Unit tests for the context proxy — mediated log queries (RAV-1140)."""

from __future__ import annotations

import json

import httpx
import pytest
from aiohttp.test_utils import TestClient

from embry0.execution.proxy.context_proxy import create_context_proxy_app

_ADMIN = "test-admin-secret-not-real"
_SANDBOX_TOKEN = "sandbox-bearer-token-at-least-32-chars-long"
_ALLOWLIST = ["acme-backend", "acme-frontend"]
_QUOTA = 3


class _FakeUpstreamResponse:
    def __init__(self, status_code: int = 200, content: bytes = b'{"lines": []}'):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": "application/json"}


class _FakeUpstreamClient:
    """Records the forwarded request; returns a canned response or raises."""

    def __init__(self):
        self.calls: list[dict] = []
        self.response = _FakeUpstreamResponse()
        self.raises: Exception | None = None

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        if self.raises is not None:
            raise self.raises
        return self.response

    async def aclose(self):
        pass


def _make_app(**overrides):
    kwargs = {
        "upstream_url": "http://10.0.0.1:8000",
        "orchestrator_api_key": "orch-api-key",
        "admin_token": _ADMIN,
        "container_allowlist": list(_ALLOWLIST),
        "query_quota": _QUOTA,
    }
    kwargs.update(overrides)
    return create_context_proxy_app(**kwargs)


@pytest.fixture
async def ctx(aiohttp_client):
    """(client, fake_upstream) with a stubbed upstream HTTP client."""
    app = _make_app()
    client = await aiohttp_client(app)
    fake = _FakeUpstreamClient()
    app["http_client"] = fake
    return client, fake


async def _enroll(client: TestClient, sandbox_id: str = "s1", token: str = _SANDBOX_TOKEN):
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": sandbox_id, "sandbox_token": token},
        headers={"X-Admin-Token": _ADMIN},
    )
    assert resp.status == 200


def _bearer(token: str = _SANDBOX_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- construction ---


def test_requires_admin_token_on_construct():
    with pytest.raises(ValueError):
        _make_app(admin_token="")


def test_requires_upstream_url_on_construct():
    with pytest.raises(ValueError):
        _make_app(upstream_url="")


def test_requires_nonempty_allowlist_on_construct():
    with pytest.raises(ValueError):
        _make_app(container_allowlist=["", "  "])


def test_requires_positive_quota_on_construct():
    with pytest.raises(ValueError):
        _make_app(query_quota=0)


# --- health / admin ---


async def test_health(ctx):
    client, _ = ctx
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["proxy"] == "context"
    assert data["enrolled"] == 0
    assert data["allowlist_size"] == 2


async def test_enroll_requires_admin(ctx):
    client, _ = ctx
    resp = await client.post("/admin/enroll", json={"sandbox_id": "s1", "sandbox_token": _SANDBOX_TOKEN})
    assert resp.status == 401


async def test_enroll_rejects_short_token(ctx):
    client, _ = ctx
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": "short"},
        headers={"X-Admin-Token": _ADMIN},
    )
    assert resp.status == 400


async def test_unenroll_revokes_bearer(ctx):
    client, fake = ctx
    await _enroll(client)
    resp = await client.delete("/admin/enroll/s1", headers={"X-Admin-Token": _ADMIN})
    assert resp.status == 200
    resp = await client.get("/api/v1/logs/query?container=acme-backend", headers=_bearer())
    assert resp.status == 401
    assert fake.calls == []


# --- query auth ---


async def test_query_without_bearer_is_401(ctx):
    client, fake = ctx
    resp = await client.get("/api/v1/logs/query?container=acme-backend")
    assert resp.status == 401
    assert fake.calls == []


async def test_query_with_unknown_bearer_is_401(ctx):
    client, fake = ctx
    resp = await client.get("/api/v1/logs/query?container=acme-backend", headers=_bearer("nope"))
    assert resp.status == 401
    assert fake.calls == []


# --- request mediation ---


async def test_allowlist_miss_returns_allowed_names(ctx):
    client, fake = ctx
    await _enroll(client)
    resp = await client.get("/api/v1/logs/query?container=postgres", headers=_bearer())
    assert resp.status == 400
    data = await resp.json()
    assert data["allowed_containers"] == _ALLOWLIST
    assert fake.calls == []


async def test_missing_container_is_400(ctx):
    client, fake = ctx
    await _enroll(client)
    resp = await client.get("/api/v1/logs/query", headers=_bearer())
    assert resp.status == 400
    assert fake.calls == []


async def test_logql_param_is_rejected_not_forwarded(ctx):
    client, fake = ctx
    await _enroll(client)
    resp = await client.get(
        '/api/v1/logs/query?container=acme-backend&logql={container_name=~".%2B"}',
        headers=_bearer(),
    )
    assert resp.status == 400
    data = await resp.json()
    assert "logql" in data["error"]
    assert fake.calls == []


async def test_forward_injects_api_key_and_canonical_params(ctx):
    client, fake = ctx
    await _enroll(client)
    resp = await client.get(
        "/api/v1/logs/query?container=ACME-BACKEND&search=error&since_minutes=30&limit=50",
        headers=_bearer(),
    )
    assert resp.status == 200
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "http://10.0.0.1:8000/api/v1/logs/query"
    assert call["headers"]["Authorization"] == "Bearer orch-api-key"
    # Case-insensitive match forwards the canonical allowlist name.
    assert call["params"] == {
        "container": "acme-backend",
        "search": "error",
        "since_minutes": "30",
        "limit": "50",
    }


async def test_out_of_range_numerics_are_clamped_not_rejected(ctx):
    client, fake = ctx
    await _enroll(client)
    resp = await client.get(
        "/api/v1/logs/query?container=acme-backend&since_minutes=99999&limit=99999",
        headers=_bearer(),
    )
    assert resp.status == 200
    call = fake.calls[0]
    assert call["params"]["since_minutes"] == "240"
    assert call["params"]["limit"] == "200"


async def test_garbage_numerics_fall_back_to_defaults(ctx):
    client, fake = ctx
    await _enroll(client)
    resp = await client.get(
        "/api/v1/logs/query?container=acme-backend&since_minutes=abc&limit=-5",
        headers=_bearer(),
    )
    assert resp.status == 200
    call = fake.calls[0]
    assert call["params"]["since_minutes"] == "60"
    assert call["params"]["limit"] == "1"


async def test_no_sandbox_headers_are_forwarded(ctx):
    client, fake = ctx
    await _enroll(client)
    headers = _bearer() | {"X-Api-Key": "spoof", "Cookie": "session=1"}
    await client.get("/api/v1/logs/query?container=acme-backend", headers=headers)
    call = fake.calls[0]
    assert set(call["headers"].keys()) == {"Authorization"}


async def test_empty_api_key_sends_no_auth_header(aiohttp_client):
    app = _make_app(orchestrator_api_key="")
    client = await aiohttp_client(app)
    fake = _FakeUpstreamClient()
    app["http_client"] = fake
    await _enroll(client)
    await client.get("/api/v1/logs/query?container=acme-backend", headers=_bearer())
    assert "Authorization" not in fake.calls[0]["headers"]


# --- quota ---


async def test_quota_exhaustion_is_429(ctx):
    client, fake = ctx
    await _enroll(client)
    for _ in range(_QUOTA):
        resp = await client.get("/api/v1/logs/query?container=acme-backend", headers=_bearer())
        assert resp.status == 200
    resp = await client.get("/api/v1/logs/query?container=acme-backend", headers=_bearer())
    assert resp.status == 429
    data = await resp.json()
    assert "quota" in data["error"]
    assert len(fake.calls) == _QUOTA


async def test_rejected_queries_do_not_consume_quota(ctx):
    client, fake = ctx
    await _enroll(client)
    for _ in range(_QUOTA + 2):
        resp = await client.get("/api/v1/logs/query?container=not-allowed", headers=_bearer())
        assert resp.status == 400
    resp = await client.get("/api/v1/logs/query?container=acme-backend", headers=_bearer())
    assert resp.status == 200


async def test_reenroll_resets_quota(ctx):
    client, fake = ctx
    await _enroll(client)
    for _ in range(_QUOTA):
        await client.get("/api/v1/logs/query?container=acme-backend", headers=_bearer())
    await _enroll(client)  # same sandbox_id, fresh enrollment
    resp = await client.get("/api/v1/logs/query?container=acme-backend", headers=_bearer())
    assert resp.status == 200


# --- upstream errors ---


async def test_upstream_connect_error_is_502(ctx):
    client, fake = ctx
    await _enroll(client)
    fake.raises = httpx.ConnectError("boom")
    resp = await client.get("/api/v1/logs/query?container=acme-backend", headers=_bearer())
    assert resp.status == 502


async def test_upstream_timeout_is_504(ctx):
    client, fake = ctx
    await _enroll(client)
    fake.raises = httpx.ReadTimeout("slow")
    resp = await client.get("/api/v1/logs/query?container=acme-backend", headers=_bearer())
    assert resp.status == 504


async def test_upstream_error_status_passes_through(ctx):
    client, fake = ctx
    await _enroll(client)
    fake.response = _FakeUpstreamResponse(status_code=502, content=b'{"detail": "Loki query failed"}')
    resp = await client.get("/api/v1/logs/query?container=acme-backend", headers=_bearer())
    assert resp.status == 502
    assert "Loki" in await resp.text()


# --- SH-P4.4: per-sandbox container scope + redacted results ---


async def _enroll_scoped(client: TestClient, containers, sandbox_id: str = "s1"):
    return await client.post(
        "/admin/enroll",
        json={"sandbox_id": sandbox_id, "sandbox_token": _SANDBOX_TOKEN, "allowed_containers": containers},
        headers={"X-Admin-Token": _ADMIN},
    )


async def test_scoped_enrollment_reports_its_scope(ctx):
    client, _ = ctx
    resp = await _enroll_scoped(client, ["Acme-Backend"])
    assert resp.status == 200
    assert await resp.json() == {"status": "enrolled", "allowed_containers": ["acme-backend"]}


async def test_unscoped_enrollment_reports_no_scope(ctx):
    """The watcher's analysis enrollment is unchanged: the full allowlist."""
    client, fake = ctx
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": _SANDBOX_TOKEN},
        headers={"X-Admin-Token": _ADMIN},
    )
    assert await resp.json() == {"status": "enrolled"}
    ok = await client.get("/api/v1/logs/query", params={"container": "acme-frontend"}, headers=_bearer())
    assert ok.status == 200


async def test_scoped_sandbox_cannot_query_outside_its_scope(ctx):
    client, fake = ctx
    await _enroll_scoped(client, ["acme-backend"])
    resp = await client.get("/api/v1/logs/query", params={"container": "acme-frontend"}, headers=_bearer())
    assert resp.status == 400
    data = await resp.json()
    # The miss names only what THIS sandbox may query, not the global list.
    assert data["allowed_containers"] == ["acme-backend"]
    assert fake.calls == []

    ok = await client.get("/api/v1/logs/query", params={"container": "acme-backend"}, headers=_bearer())
    assert ok.status == 200
    assert fake.calls[0]["params"]["container"] == "acme-backend"


async def test_scope_can_never_widen_the_global_allowlist(ctx):
    client, _ = ctx
    resp = await _enroll_scoped(client, ["acme-backend", "embry0-postgres"])
    assert (await resp.json())["allowed_containers"] == ["acme-backend"]


@pytest.mark.parametrize("bad", [[], ["embry0-postgres"], "acme-backend", [1], None])
async def test_a_scope_that_grants_nothing_refuses_the_enrollment(ctx, bad):
    """Fail closed: an empty/invalid scope must not fall back to the full allowlist."""
    client, _ = ctx
    resp = await _enroll_scoped(client, bad)
    assert resp.status == 400
    denied = await client.get("/api/v1/logs/query", params={"container": "acme-backend"}, headers=_bearer())
    assert denied.status == 401


async def test_scope_is_dropped_on_unenroll_and_replaced_on_reenroll(ctx):
    client, _ = ctx
    await _enroll_scoped(client, ["acme-backend"])
    await client.delete("/admin/enroll/s1", headers={"X-Admin-Token": _ADMIN})
    await _enroll(client)  # same id, now unscoped
    ok = await client.get("/api/v1/logs/query", params={"container": "acme-frontend"}, headers=_bearer())
    assert ok.status == 200


async def test_scoped_results_are_redacted(ctx):
    client, fake = ctx
    fake.response = _FakeUpstreamResponse(
        content=(
            b'{"query": "q", "count": 1, "lines": [{"ts": "1", "container": "acme-backend", '
            b'"line": "401 for jane@example.com Authorization: Bearer abcdef123456"}]}'
        )
    )
    await _enroll_scoped(client, ["acme-backend"])
    resp = await client.get("/api/v1/logs/query", params={"container": "acme-backend"}, headers=_bearer())
    data = await resp.json()
    line = data["lines"][0]["line"]
    assert "jane@example.com" not in line and "abcdef123456" not in line
    assert line.startswith("401 for <email>")
    assert data["count"] == 1 and data["lines"][0]["container"] == "acme-backend"


async def test_scoped_unparseable_upstream_body_is_withheld(ctx):
    """A 200 body the proxy cannot redact is not passed through raw."""
    client, fake = ctx
    fake.response = _FakeUpstreamResponse(content=b"secret=abc not json")
    await _enroll_scoped(client, ["acme-backend"])
    resp = await client.get("/api/v1/logs/query", params={"container": "acme-backend"}, headers=_bearer())
    assert resp.status == 502
    assert "secret=abc" not in await resp.text()


async def test_unscoped_results_are_redacted_too(ctx):
    """#294: the watcher's analysis agent (an unscoped enrollment) hands what
    it reads to a model provider exactly as the developer does."""
    client, fake = ctx
    from tests.fixtures.redaction_corpus import CORPUS

    lines = [{"ts": str(i), "container": "acme-backend", "line": line} for i, (line, _) in enumerate(CORPUS)]
    fake.response = _FakeUpstreamResponse(content=json.dumps({"count": len(lines), "lines": lines}).encode())
    await _enroll(client)
    resp = await client.get("/api/v1/logs/query", params={"container": "acme-backend"}, headers=_bearer())
    text = await resp.text()
    assert resp.status == 200
    leaked = [secret for _, secret in CORPUS if secret in text]
    assert leaked == [], f"an unscoped (watcher) drill-down returned raw {leaked}"
    assert "<email>" in text


async def test_unscoped_unparseable_upstream_body_is_withheld(ctx):
    client, fake = ctx
    fake.response = _FakeUpstreamResponse(content=b"secret=abc not json")
    await _enroll(client)
    resp = await client.get("/api/v1/logs/query", params={"container": "acme-backend"}, headers=_bearer())
    assert resp.status == 502
    assert "secret=abc" not in await resp.text()


async def test_configured_payload_fields_are_redacted_in_results(ctx):
    from embry0.safety.redaction import register_redacted_fields

    client, fake = ctx
    fake.response = _FakeUpstreamResponse(content=b'{"lines": [{"line": "lead phone=+15551234 created"}]}')
    register_redacted_fields("phone")
    try:
        await _enroll(client)
        resp = await client.get("/api/v1/logs/query", params={"container": "acme-backend"}, headers=_bearer())
        line = (await resp.json())["lines"][0]["line"]
    finally:
        register_redacted_fields([])
    assert "+15551234" not in line, line


def test_the_per_line_ceiling_is_a_ceiling() -> None:
    """Pinned to a concrete value: the test below sizes its input from the
    constant, so on its own it would pass for any ceiling at all."""
    from embry0.execution.proxy.context_proxy import MAX_REDACTED_LINE_CHARS

    assert MAX_REDACTED_LINE_CHARS == 2000


async def test_scoped_results_cap_each_line_before_redacting(ctx):
    """One pathological line must not make every other sandbox wait: the
    handler is shared, and redaction cost follows input length."""
    from embry0.execution.proxy.context_proxy import MAX_REDACTED_LINE_CHARS

    giant = "z" * (MAX_REDACTED_LINE_CHARS + 5_000)
    fake_body = ('{"count": 1, "lines": [{"ts": "1", "container": "acme-backend", "line": "' + giant + '"}]}').encode()
    client, fake = ctx
    fake.response = _FakeUpstreamResponse(content=fake_body)
    await _enroll_scoped(client, ["acme-backend"])
    resp = await client.get("/api/v1/logs/query", params={"container": "acme-backend"}, headers=_bearer())
    line = (await resp.json())["lines"][0]["line"]
    assert line.endswith("… [line truncated]")
    assert len(line) <= MAX_REDACTED_LINE_CHARS + 32


# --- glob entries for containers named per release (blue/green slots) ---

_PATTERNED = ["acme-backend*", "acme-frontend"]


@pytest.fixture
async def pctx(aiohttp_client):
    """(client, fake_upstream) over an allowlist with a pattern entry."""
    app = _make_app(container_allowlist=list(_PATTERNED))
    client = await aiohttp_client(app)
    fake = _FakeUpstreamClient()
    app["http_client"] = fake
    return client, fake


@pytest.mark.parametrize("name", ["acme-backend-blue", "acme-backend-b-green", "acme-backend-leads-blue"])
async def test_a_pattern_admits_a_slot_name_and_forwards_it_as_given(pctx, name):
    client, fake = pctx
    await _enroll(client)
    resp = await client.get("/api/v1/logs/query", params={"container": name}, headers=_bearer())
    assert resp.status == 200
    assert fake.calls[0]["params"]["container"] == name


@pytest.mark.parametrize("name", ["acme-backend|.+", "acme-backend.*", "acme-backend*", "acme-backend-(a|b)"])
async def test_a_pattern_never_lets_regex_syntax_reach_the_selector(pctx, name):
    """The upstream selector embeds the name in a regex unescaped: through a
    pattern only a plain container name may pass, and a miss costs no quota."""
    client, fake = pctx
    await _enroll(client)
    resp = await client.get("/api/v1/logs/query", params={"container": name}, headers=_bearer())
    assert resp.status == 400
    data = await resp.json()
    assert data["allowed_containers"] == _PATTERNED
    assert fake.calls == []
    assert client.server.app["query_count_by_id"].get("s1", 0) == 0


async def test_exact_entries_next_to_a_pattern_are_unchanged(pctx):
    client, fake = pctx
    await _enroll(client)
    assert (await client.get("/api/v1/logs/query?container=ACME-FRONTEND", headers=_bearer())).status == 200
    assert fake.calls[0]["params"]["container"] == "acme-frontend"
    # An exact entry is not a prefix.
    assert (await client.get("/api/v1/logs/query?container=acme-frontend-old", headers=_bearer())).status == 400


async def test_a_scope_holds_the_slot_names_asked_for_never_the_pattern(pctx):
    client, fake = pctx
    resp = await _enroll_scoped(client, ["acme-backend-green", "acme-backend*", "embry0-postgres"])
    assert resp.status == 200
    assert (await resp.json())["allowed_containers"] == ["acme-backend-green"]

    sibling = await client.get("/api/v1/logs/query", params={"container": "acme-backend-blue"}, headers=_bearer())
    assert sibling.status == 400
    assert (await sibling.json())["allowed_containers"] == ["acme-backend-green"]
    ok = await client.get("/api/v1/logs/query", params={"container": "ACME-BACKEND-GREEN"}, headers=_bearer())
    assert ok.status == 200
    assert fake.calls[0]["params"]["container"] == "acme-backend-green"


async def test_a_scope_of_only_the_pattern_grants_nothing(pctx):
    client, _ = pctx
    resp = await _enroll_scoped(client, ["acme-backend*"])
    assert resp.status == 400


async def test_health_counts_entries_not_names(pctx):
    client, _ = pctx
    assert (await (await client.get("/health")).json())["allowlist_size"] == 2
