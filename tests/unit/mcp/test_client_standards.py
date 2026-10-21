"""Embry0Client standards methods (ADR 0014): paths, the body, and the owner and kind checks."""

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


async def test_standard_paths_and_body() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    await client.standard_get("acme", "design")
    await client.standard_set("acme", "design", schema={"roles": {}}, values={}, prose="One accent.")
    await client.standard_audit("acme", "design", "acme/sub/widgets")
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/standards/acme/design"),
        ("PUT", "/api/v1/standards/acme/design"),
        ("POST", "/api/v1/standards/acme/design/audit/acme/sub/widgets"),
    ]
    assert json.loads(seen[1].content) == {
        "schema": {"roles": {}},
        "values": {},
        "prose": "One accent.",
        "references": [],
    }
    assert seen[2].content == b"", "an audit sends no body"
    assert all(r.headers["X-Requested-With"] == "XMLHttpRequest" for r in seen)


@pytest.mark.parametrize(
    ("owner", "kind", "repo"),
    [
        ("acme/widgets", "design", "acme/widgets"),
        ("../credentials", "design", "acme/widgets"),
        ("acme", "Design", "acme/widgets"),
        ("acme", "design/../x", "acme/widgets"),
        ("acme", "design", "widgets"),
        ("acme\n", "design", "acme/widgets"),
    ],
)
async def test_a_bad_owner_kind_or_repo_never_reaches_a_url(owner: str, kind: str, repo: str) -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    with pytest.raises(Embry0APIError) as caught:
        await client.standard_audit(owner, kind, repo)
    assert caught.value.status_code == 422
    assert seen == []
