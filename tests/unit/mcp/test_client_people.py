"""The MCP client's people calls (#179 pull request 3): the routes they reach and the version a change is guarded by."""

from __future__ import annotations

import json
from typing import Any

import httpx

from embry0.mcp_server.client import Embry0Client


def _client(seen: list[tuple[str, str, Any, str | None]]) -> Embry0Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append(
            (
                request.method,
                request.url.raw_path.decode(),
                body,
                request.headers.get("if-match"),
            )
        )
        return httpx.Response(200, json={"ok": True})

    return Embry0Client("http://embry0.test", "test-key", transport=httpx.MockTransport(handler))


async def test_the_people_calls_reach_their_routes_and_a_change_names_the_version_it_read() -> None:
    seen: list[tuple[str, str, Any, str | None]] = []
    client = _client(seen)
    assert hasattr(client, "person_change"), "the MCP client has no people calls"
    await client.people_list(include_disabled=True)
    await client.person_get("per-a/b")
    await client.person_add({"name": "Ada"})
    await client.person_change("per-1", {"hours": None}, expected_updated_at="2026-11-25T09:00:00+00:00")
    await client.person_change("per-1", {"hours": "09-17"})
    assert seen == [
        ("GET", "/api/v1/people?include_disabled=true", None, None),
        ("GET", "/api/v1/people/per-a%2Fb", None, None),
        ("POST", "/api/v1/people", {"name": "Ada"}, None),
        ("PATCH", "/api/v1/people/per-1", {"hours": None}, '"2026-11-25T09:00:00+00:00"'),
        ("PATCH", "/api/v1/people/per-1", {"hours": "09-17"}, None),
    ]
