"""GitLabClient calls the pull request reviewer needs (#30)."""

from __future__ import annotations

import json

import httpx
import pytest

from embry0.execution.gitlab_client import GitLabClient


@pytest.fixture(autouse=True)
def _clean_namespace_tokens(monkeypatch):
    import os

    for key in [k for k in os.environ if k.startswith("GITLAB_TOKEN__")]:
        monkeypatch.delenv(key)


def _client(handler) -> GitLabClient:
    async def _sleep(_seconds: float) -> None:
        return None

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitLabClient(
        default_token="glpat-abc", gitlab_url="https://gitlab.example.com", http_client=http, sleep=_sleep
    )


async def test_member_access_level_reads_the_inherited_membership() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"id": 42, "access_level": 30})

    level = await _client(handler).get_member_access_level("acme/products/web", 42)

    assert level == 30
    assert seen == ["/api/v4/projects/acme%2Fproducts%2Fweb/members/all/42"], "inherited members live under /all"


async def test_a_non_member_has_no_access_level() -> None:
    client = _client(lambda _r: httpx.Response(404, json={"message": "404 Not found"}))
    assert await client.get_member_access_level("acme/products/web", 42) is None


async def test_get_merge_request_returns_the_record_or_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.raw_path.decode().endswith("/merge_requests/5"):
            return httpx.Response(200, content=json.dumps({"iid": 5, "sha": "c" * 40}))
        return httpx.Response(404)

    client = _client(handler)
    assert (await client.get_merge_request("acme/products/web", 5)) == {"iid": 5, "sha": "c" * 40}
    assert await client.get_merge_request("acme/products/web", 6) is None
