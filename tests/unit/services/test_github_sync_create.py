"""Issue sync's create after an unknown outcome (#613).

Since #611 the create is sent again only when GitHub did not act on it. A create GitHub made but answered 502,
or whose answer was lost, left the GitHub issue unlinked: the embry0 issue recorded no number, and a later sync
made a second. The sync now reads the newest issues once for the one it made, and sends the create again only
when it is not there.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from embry0.execution.github_client import GitHubClient
from embry0.services.github_sync import GitHubSyncService

ISSUE = {"id": "iss-1", "repo": "acme/widgets", "title": "Fix the idle timer", "body": "It never fires.", "labels": []}


def _github(first: str, *, unreadable: bool = False) -> tuple[GitHubSyncService, list[dict[str, Any]], list[str]]:
    """GitHub with one repository. The first create meets ``first``: "made-502" (made, answered 502), "made-lost"
    (made, its answer lost), "not-made-502" (refused before it was made, answered 502), or "refused" (422)."""
    issues: list[dict[str, Any]] = [
        {"number": 7, "title": "an older one", "body": "", "html_url": "https://github.com/acme/widgets/issues/7"}
    ]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "GET" and request.url.path == "/repos/acme/widgets/issues":
            if unreadable:
                return httpx.Response(500)
            return httpx.Response(200, json=list(reversed(issues)))
        if request.method == "POST" and request.url.path == "/repos/acme/widgets/issues":
            posts = calls.count("POST")
            if first == "refused" and posts == 1:
                return httpx.Response(422, json={"message": "Validation Failed"})
            if first == "not-made-502" and posts == 1:
                return httpx.Response(502)
            body = json.loads(request.content)
            made = {
                "number": 8 + posts,
                "title": body["title"],
                "body": body["body"],
                "html_url": f"https://github.com/acme/widgets/issues/{8 + posts}",
            }
            issues.append(made)
            if posts == 1 and first == "made-502":
                return httpx.Response(502)
            if posts == 1 and first == "made-lost":
                raise httpx.ReadTimeout("the answer was lost", request=request)
            return httpx.Response(201, json=made)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    async def _sleep(_seconds: float) -> None:
        return None

    client = GitHubClient(
        default_token="t", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=_sleep
    )
    return GitHubSyncService(client=client), issues, calls


def _repo() -> Any:
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=dict(ISSUE))
    return repo


@pytest.mark.parametrize("first", ["made-502", "made-lost"])
async def test_an_issue_github_made_behind_a_failed_answer_is_found_and_linked(first: str) -> None:
    sync, issues, calls = _github(first)
    repo = _repo()
    data = await sync.push_create("iss-1", repo)
    assert data is not None and data["number"] == 9, data
    assert len(issues) == 2 and calls.count("POST") == 1, f"a second issue was made: {issues}"
    linked = repo.update.await_args.kwargs
    assert (linked["tracker_key"], linked["tracker_url"]) == ("9", "https://github.com/acme/widgets/issues/9")


async def test_an_issue_github_did_not_make_is_created_once_more() -> None:
    sync, issues, calls = _github("not-made-502")
    repo = _repo()
    data = await sync.push_create("iss-1", repo)
    assert data is not None and calls.count("POST") == 2 and len(issues) == 2, (calls, issues)
    assert repo.update.await_args.kwargs["tracker_key"] == str(data["number"])


async def test_a_refused_create_raises_and_looks_nothing_up() -> None:
    sync, issues, calls = _github("refused")
    with pytest.raises(httpx.HTTPStatusError):
        await sync.push_create("iss-1", _repo())
    assert calls == ["POST"], calls


@pytest.mark.parametrize(("first", "raised"), [("made-502", httpx.HTTPStatusError), ("made-lost", httpx.ReadTimeout)])
async def test_an_outcome_the_issues_cannot_tell_is_not_sent_again(first: str, raised: type[Exception]) -> None:
    """The newest issues cannot be read: whether GitHub made the issue stays unknown, and a second create could make
    a second issue. The failure is raised instead."""
    sync, issues, calls = _github(first, unreadable=True)
    with pytest.raises(raised):
        await sync.push_create("iss-1", _repo())
    assert calls.count("POST") == 1 and len(issues) == 2, (calls, issues)
