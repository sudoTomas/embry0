"""Embry0Client host methods (ADR 0013): paths, query, body, and the host id check."""

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


async def test_host_reads_and_probe() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    await client.host_list()
    await client.host_list(source="tailnet", include_removed=True)
    await client.host_get("host-0123456789ab")
    await client.host_probe("host-0123456789ab")
    await client.host_probe("host-0123456789ab", credential_id="cred-000000000009")
    assert [(r.method, r.url.path, dict(r.url.params)) for r in seen] == [
        ("GET", "/api/v1/hosts", {}),
        ("GET", "/api/v1/hosts", {"source": "tailnet", "include_removed": "true"}),
        ("GET", "/api/v1/hosts/host-0123456789ab", {}),
        ("POST", "/api/v1/hosts/host-0123456789ab/probe", {}),
        ("POST", "/api/v1/hosts/host-0123456789ab/probe", {}),
    ]
    assert seen[-2].content == b"", "a probe with no credential named sent a body"
    assert json.loads(seen[-1].content) == {"credential_id": "cred-000000000009"}
    assert seen[-1].headers["X-Requested-With"] == "XMLHttpRequest"


async def test_host_import_tailnet_always_sends_confirm() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    await client.host_import_tailnet()
    await client.host_import_tailnet(credential_id="cred-000000000009")
    assert [(r.method, r.url.path) for r in seen] == [("POST", "/api/v1/hosts/import/tailnet")] * 2
    assert [json.loads(r.content) for r in seen] == [
        {"confirm": True},
        {"confirm": True, "credential_id": "cred-000000000009"},
    ]


@pytest.mark.parametrize("bad", ["", "host-xyz", "../credentials", "host-0123456789ab/probe", "host-0123456789ab\n"])
async def test_a_host_id_that_is_not_one_never_reaches_a_url(bad: str) -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    with pytest.raises(Embry0APIError) as caught:
        await client.host_get(bad)
    assert caught.value.status_code == 422
    with pytest.raises(Embry0APIError):
        await client.host_probe(bad)
    assert seen == []
