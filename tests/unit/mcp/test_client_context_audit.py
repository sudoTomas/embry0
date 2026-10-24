"""Embry0Client organization settings and context audit methods: paths, bodies, and the owner and repo checks."""

from __future__ import annotations

import json

import httpx
import pytest

from embry0.mcp_server.client import Embry0APIError, Embry0Client


def _client(seen: list[httpx.Request]) -> Embry0Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    return Embry0Client("http://embry0.test", "test-key", transport=httpx.MockTransport(handler))


async def test_paths_and_bodies() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    await client.org_update("acme", settings={"context_audit_days": 7, "context_audit_budget_usd": None})
    await client.org_update("acme", display_name="Acme")
    await client.context_audit_status("acme")
    await client.context_audit_status("acme", refresh=True)
    await client.context_audit_run("acme")
    await client.context_audit_run("acme", limit=2)
    await client.context_audit_run("acme", repo="acme/widgets", force=True)
    assert [(r.method, r.url.path) for r in seen] == [
        ("PATCH", "/api/v1/orgs/acme"),
        ("PATCH", "/api/v1/orgs/acme"),
        ("GET", "/api/v1/orgs/acme/context-audit"),
        ("GET", "/api/v1/orgs/acme/context-audit"),
        ("POST", "/api/v1/orgs/acme/context-audit/run"),
        ("POST", "/api/v1/orgs/acme/context-audit/run"),
        ("POST", "/api/v1/orgs/acme/context-audit/run"),
    ]
    bodies = [json.loads(r.content) if r.content else None for r in seen]
    assert bodies == [
        # null is kept: it is how a setting is turned off or handed back to the default.
        {"settings": {"context_audit_days": 7, "context_audit_budget_usd": None}},
        {"display_name": "Acme"},
        None,
        None,
        {"force": False},
        {"force": False, "limit": 2},
        {"force": True, "repo": "acme/widgets"},
    ]
    assert [r.url.query for r in seen[2:4]] == [b"", b"refresh=true"], "refresh reads the forge again only when asked"
    assert all(r.headers["X-Requested-With"] == "XMLHttpRequest" for r in seen)
    assert all(r.headers["Authorization"] == "Bearer test-key" for r in seen)


@pytest.mark.parametrize("owner", ["acme/widgets", "../credentials", "acme\n", ""])
async def test_a_bad_owner_never_reaches_a_url(owner: str) -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    for call in (
        client.org_update(owner, settings={"context_audit_days": 7}),
        client.context_audit_status(owner),
        client.context_audit_run(owner),
    ):
        with pytest.raises(Embry0APIError) as caught:
            await call
        assert caught.value.status_code == 422
    assert seen == []


@pytest.mark.parametrize("repo", ["widgets", "acme/../x", "acme/widgets\n", "/acme/widgets"])
async def test_a_bad_repo_never_reaches_the_api(repo: str) -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(Embry0APIError) as caught:
        await _client(seen).context_audit_run("acme", repo=repo, force=True)
    assert caught.value.status_code == 422
    assert seen == []
