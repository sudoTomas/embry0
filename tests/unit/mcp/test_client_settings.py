"""Embry0Client settings document methods (#162): paths, the raw YAML body, If-Match, and the 422's words."""

from __future__ import annotations

import httpx
import pytest

from embry0.mcp_server.client import Embry0APIError, Embry0Client


def _client(seen: list[httpx.Request], response: httpx.Response | None = None) -> Embry0Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response or httpx.Response(200, json={"ok": True})

    return Embry0Client("http://embry0.test", "test-key", transport=httpx.MockTransport(handler))


async def test_settings_paths_body_and_dry_run() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    text = "# office hours\nasks:\n  hours: '08-17'\n"
    await client.settings_get()
    await client.settings_effective()
    await client.settings_schema()
    await client.settings_set(text, dry_run=True)
    await client.settings_set(text, reason="office hours")
    await client.settings_revisions(limit=5)
    await client.settings_revision(12)
    await client.settings_undo(12)
    assert [(r.method, r.url.path) for r in seen] == [
        ("GET", "/api/v1/config/settings"),
        ("GET", "/api/v1/config/settings/effective"),
        ("GET", "/api/v1/config/settings/schema"),
        ("PUT", "/api/v1/config/settings"),
        ("PUT", "/api/v1/config/settings"),
        ("GET", "/api/v1/config/settings/revisions"),
        ("GET", "/api/v1/config/settings/revisions/12"),
        ("POST", "/api/v1/config/settings/revisions/12/undo"),
    ]
    assert dict(seen[3].url.params) == {"source": "person", "dry_run": "1"}, "a dry run asks the API to store nothing"
    assert dict(seen[4].url.params) == {"source": "person", "reason": "office hours"}, "a real write is not a dry run"
    for put in seen[3:5]:
        assert put.content == text.encode(), "the YAML goes as written, comments kept"
        assert put.headers["Content-Type"] == "application/yaml"
        assert "If-Match" not in put.headers, "without a generation read, the stored document is replaced"
    assert dict(seen[5].url.params) == {"limit": "5"}
    assert seen[7].content == b"", "an undo sends no body"
    assert all(r.headers["X-Requested-With"] == "XMLHttpRequest" for r in seen)


async def test_settings_set_sends_the_generation_it_read_as_if_match() -> None:
    seen: list[httpx.Request] = []
    await _client(seen).settings_set("asks:\n  enabled: true\n", expected_generation=7)
    await _client(seen).settings_set("asks:\n  enabled: true\n", expected_generation=7, dry_run=True)
    assert [r.headers.get("If-Match") for r in seen] == ['"7"', '"7"'], (
        "the generation read must guard the write, and the dry run, against a newer one"
    )


async def test_a_refused_document_reads_one_problem_per_key_without_its_value() -> None:
    detail = [
        {"loc": ["asks", "remind_hours"], "type": "greater_than", "msg": "Input should be greater than 0"},
        {"loc": ["asks", "hours"], "type": "value_error", "msg": "Value error, bad hours", "input": "s3cret-ish"},
        {"loc": [], "type": "yaml_syntax", "msg": "The YAML cannot be read: line 2, column 1: oops"},
    ]
    seen: list[httpx.Request] = []
    client = _client(seen, httpx.Response(422, json={"detail": detail}))
    with pytest.raises(Embry0APIError) as refused:
        await client.settings_set("asks: {}\n")
    assert refused.value.status_code == 422
    assert refused.value.detail == (
        "asks.remind_hours: Input should be greater than 0; asks.hours: bad hours; "
        "The YAML cannot be read: line 2, column 1: oops"
    )
    assert "s3cret-ish" not in str(refused.value), "a value the API echoed in input must not reach the caller"


async def test_a_detail_that_is_not_a_list_of_problems_reads_as_before() -> None:
    for detail, expected in (("plain words", "plain words"), ({"a": 1}, "{'a': 1}"), ([1, 2], "[1, 2]")):
        client = _client([], httpx.Response(409, json={"detail": detail}))
        with pytest.raises(Embry0APIError) as refused:
            await client.settings_get()
        assert refused.value.detail == expected


async def test_settings_name_an_organization_s_or_a_repository_s_layer() -> None:
    """``owner`` or ``repo`` goes in the query (spec 2026-10-29); neither is the deployment's document."""
    seen: list[httpx.Request] = []
    client = _client(seen)
    await client.settings_get(owner="acme")
    await client.settings_effective(repo="acme/widgets")
    await client.settings_set("asks:\n  hours: '08-17'\n", owner="acme", expected_generation=0)
    await client.settings_set("sample: {}\n", repo="acme/widgets", dry_run=True)
    await client.settings_revisions(limit=5, repo="acme/widgets")
    await client.settings_get()
    assert [dict(r.url.params) for r in seen] == [
        {"owner": "acme"},
        {"repo": "acme/widgets"},
        {"source": "person", "owner": "acme"},
        {"source": "person", "repo": "acme/widgets", "dry_run": "1"},
        {"limit": "5", "repo": "acme/widgets"},
        {},
    ], "the layer was not named in the query, so the deployment's document would be read or replaced"
    assert seen[2].headers["If-Match"] == '"0"', "a layer nobody wrote is generation 0"
