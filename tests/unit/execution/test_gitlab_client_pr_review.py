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


async def test_a_discussion_is_posted_with_its_text_position() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(path=request.url.raw_path.decode(), body=json.loads(request.content))
        return httpx.Response(201, json={"id": "d1"})

    position = {
        "position_type": "text",
        "base_sha": "b" * 40,
        "start_sha": "b" * 40,
        "head_sha": "c" * 40,
        "old_path": "a.py",
        "new_path": "a.py",
        "new_line": 3,
    }
    result = await _client(handler).create_mr_discussion(
        "acme/products/web", 5, body="**Important** — x", position=position
    )

    assert result.ok
    assert seen == {
        "path": "/api/v4/projects/acme%2Fproducts%2Fweb/merge_requests/5/discussions",
        "body": {"body": "**Important** — x", "position": position},
    }


async def test_a_refused_position_carries_the_forges_message() -> None:
    client = _client(
        lambda _r: httpx.Response(400, json={"message": '400 Bad request - Note {:line_code=>["can\'t be blank"]}'})
    )
    result = await client.create_mr_discussion("acme/products/web", 5, body="x", position={})
    assert not result.ok and result.status_code == 400 and "line_code" in result.detail


async def test_a_note_returns_what_the_forge_answered_and_the_bool_api_still_works() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.decode() == "/api/v4/projects/acme%2Fproducts%2Fweb/merge_requests/5/notes"
        return httpx.Response(201, json={"id": 1})

    client = _client(handler)
    assert (await client.post_mr_note("acme/products/web", 5, "summary")).ok
    assert await client.create_mr_note("acme/products/web", 5, "summary") is True


async def test_diffs_come_from_the_diffs_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.decode().startswith("/api/v4/projects/acme%2Fproducts%2Fweb/merge_requests/5/diffs")
        return httpx.Response(200, json=[{"new_path": "a.py", "old_path": "a.py", "diff": "@@ -1 +1 @@\n+a"}])

    assert [d["new_path"] for d in await _client(handler).list_merge_request_diffs("acme/products/web", 5)] == ["a.py"]


async def test_an_older_gitlab_without_the_diffs_endpoint_falls_back_to_changes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        if "/diffs" in path:
            return httpx.Response(404, json={"error": "404 Not Found"})
        assert path.endswith("/merge_requests/5/changes")
        return httpx.Response(
            200, json={"changes": [{"new_path": "b.py", "old_path": "b.py", "diff": "@@ -1 +1 @@\n+b"}]}
        )

    assert [d["new_path"] for d in await _client(handler).list_merge_request_diffs("acme/products/web", 5)] == ["b.py"]


@pytest.mark.parametrize("write", ["discussion", "note"])
async def test_a_review_write_the_forge_answered_502_is_not_sent_again(write) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(502, text="Bad Gateway") if len(calls) == 1 else httpx.Response(201, json={})

    client = _client(handler)
    if write == "discussion":
        result = await client.create_mr_discussion("acme/products/web", 5, body="x", position={})
    else:
        result = await client.post_mr_note("acme/products/web", 5, "summary")
    assert calls == ["POST"], "a 502 may follow an accepted write: re-sending it would post it twice"
    assert result.status_code == 502


@pytest.mark.parametrize("write", ["discussion", "note"])
async def test_a_review_write_whose_answer_was_lost_is_not_sent_again(write) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            raise httpx.ReadTimeout("timed out")
        return httpx.Response(201, json={})

    client = _client(handler)
    with pytest.raises(httpx.ReadTimeout):
        if write == "discussion":
            await client.create_mr_discussion("acme/products/web", 5, body="x", position={})
        else:
            await client.post_mr_note("acme/products/web", 5, "summary")
    assert calls == ["POST"], "a lost answer may follow an accepted write: re-sending it would post it twice"


async def test_other_gitlab_posts_still_retry_a_502() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(502) if len(calls) == 1 else httpx.Response(201, json={})

    assert await _client(handler).create_issue_note("acme/products/web", 5, "b") is True
    assert len(calls) == 2, "the default retry policy must be unchanged"


async def test_the_existing_mr_note_api_keeps_retrying_a_502() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(502) if len(calls) == 1 else httpx.Response(201, json={})

    assert await _client(handler).create_mr_note("acme/products/web", 5, "b") is True
    assert len(calls) == 2, "create_mr_note's callers keep the retry policy they had"
