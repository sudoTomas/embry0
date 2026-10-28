"""Embry0Client progression content pack methods (#176): paths, the raw YAML body, and the dry run."""

from __future__ import annotations

import httpx

from embry0.mcp_server.client import Embry0Client


def _client(seen: list[httpx.Request]) -> Embry0Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    return Embry0Client("http://embry0.test", "test-key", transport=httpx.MockTransport(handler))


async def test_progression_pack_paths_body_and_dry_run() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    text = "# my pack\nversion: 1\n"
    await client.progression_pack_get()
    await client.progression_pack_set(text, dry_run=True)
    await client.progression_pack_set(text)
    await client.progression_pack_reset()
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/progression/pack"),
        ("PUT", "/api/v1/progression/pack"),
        ("PUT", "/api/v1/progression/pack"),
        ("POST", "/api/v1/progression/pack/reset"),
    ]
    assert dict(seen[1].url.params) == {"source": "person", "dry_run": "1"}, "a dry run asks the API to store nothing"
    assert dict(seen[2].url.params) == {"source": "person"}, "a real write is not a dry run"
    for put in seen[1:3]:
        assert put.content == text.encode(), "the YAML goes as written, comments kept"
        assert put.headers["Content-Type"] == "application/yaml"
    assert seen[3].content == b"", "a reset sends no body"
    assert "If-Match" not in seen[2].headers, "without a version read, any active version is replaced"


async def test_progression_pack_set_sends_the_version_it_read_as_if_match() -> None:
    seen: list[httpx.Request] = []
    await _client(seen).progression_pack_set("version: 1\n", expected_version=7)
    assert seen[0].headers.get("If-Match") == '"7"', "the version read must guard the write against a newer one"
    assert all(r.headers["X-Requested-With"] == "XMLHttpRequest" for r in seen)
