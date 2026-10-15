"""GitHubClient calls that post a pull request review (#30)."""

from __future__ import annotations

import json

import httpx
import pytest

from embry0.execution.github_client import GitHubClient

SHA = "a" * 40


@pytest.fixture(autouse=True)
def _clean_owner_tokens(monkeypatch):
    import os

    for key in [k for k in os.environ if k.startswith("GITHUB_TOKEN__")]:
        monkeypatch.delenv(key)


def _client(handler) -> GitHubClient:
    async def _sleep(_seconds: float) -> None:
        return None

    return GitHubClient(
        default_token="tok", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=_sleep
    )


async def test_a_review_is_posted_as_a_comment_review() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(method=request.method, path=request.url.path, body=json.loads(request.content))
        return httpx.Response(200, json={"id": 1})

    comments = [{"path": "a.py", "line": 1, "side": "RIGHT", "body": "x"}]
    result = await _client(handler).create_pull_review(
        "acme/widgets", 7, commit_id=SHA, body="summary", comments=comments
    )

    assert result.ok and result.status_code == 200
    assert seen == {
        "method": "POST",
        "path": "/repos/acme/widgets/pulls/7/reviews",
        "body": {"commit_id": SHA, "body": "summary", "event": "COMMENT", "comments": comments},
    }, "a review must be event COMMENT: embry0 never approves or requests changes"


async def test_without_a_commit_id_the_key_is_left_out() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(body=json.loads(request.content))
        return httpx.Response(200, json={})

    await _client(handler).create_pull_review("acme/widgets", 7, commit_id=None, body="b", comments=[])
    assert "commit_id" not in seen["body"]


async def test_a_refusal_carries_the_forges_own_words() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "Unprocessable Entity", "errors": ["Line could not be resolved"]})

    result = await _client(handler).create_pull_review("acme/widgets", 7, commit_id=SHA, body="b", comments=[])
    assert not result.ok and result.status_code == 422
    assert "Line could not be resolved" in result.detail


async def test_pull_file_patches_follow_every_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"filename": "b.py", "patch": "@@ -1 +1 @@\n+b"}])
        nxt = "https://api.github.com/repos/acme/widgets/pulls/7/files?page=2"
        return httpx.Response(
            200, json=[{"filename": "a.py", "patch": "@@ -1 +1 @@\n+a"}], headers={"Link": f'<{nxt}>; rel="next"'}
        )

    files = await _client(handler).list_pull_file_patches("acme/widgets", 7)
    assert [f["filename"] for f in files] == ["a.py", "b.py"]


async def test_pull_file_patches_raise_on_an_error_page() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await _client(lambda _r: httpx.Response(404, json={"message": "Not Found"})).list_pull_file_patches(
            "acme/widgets", 7
        )
