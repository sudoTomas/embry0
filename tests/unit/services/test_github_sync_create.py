"""Issue sync's create after an unknown outcome (#613).

Since #611 the create is sent again only when GitHub did not act on it. A create GitHub made but answered 502,
or whose answer was lost, left the GitHub issue unlinked: the embry0 issue recorded no number, and a later sync
made a second. The sync now reads the token's user and the newest issues once for the one it made, and sends the
create again only when it is not there. Only the token's own issue, created within a few minutes of the create,
counts: a person's issue with the same words, or an older one of the token's edited since, is not this create.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from embry0.execution.github_client import GitHubClient
from embry0.services.github_sync import GitHubSyncService

ISSUE = {
    "id": "iss-1",
    "repo": "acme/widgets",
    "title": "Fix the idle timer",
    "body": "It never fires.\nAt all.",
    "labels": [],
}
BOT = "embry0-bot"  # the login the sync's token creates as


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _github(
    first: str, *, unreadable: str = "", stored_as: str = "sent"
) -> tuple[GitHubSyncService, list[dict[str, Any]], list[str], list[float]]:
    """GitHub with one repository. The first create meets ``first``: "made-502" (made, answered 502), "made-lost"
    (made, its answer lost), "not-made-502" (refused before it was made, answered 502), or "refused" (422). The
    repository already holds the same title and body twice: the token's own issue from two days ago, edited now
    (so GitHub's ``since``, which reads the update time, lists it), and a person's from now. ``stored_as``
    "crlf-padded": GitHub stores the body with CRLF line ends and trailing spaces. ``unreadable``: "issues" or
    "user" cannot be read. The list honours ``since`` by update time."""
    now = datetime.now(UTC)
    issues: list[dict[str, Any]] = []
    calls: list[str] = []
    slept: list[float] = []

    def issue(title: str, body: str, login: str, created: datetime, updated: datetime | None = None) -> dict[str, Any]:
        number = len(issues) + 7
        return {
            "number": number,
            "title": title,
            "body": body,
            "user": {"login": login},
            "created_at": _stamp(created),
            "updated_at": _stamp(updated or created),
            "html_url": f"https://github.com/acme/widgets/issues/{number}",
        }

    issues.append(issue(ISSUE["title"], ISSUE["body"], BOT, now - timedelta(days=2), now))
    issues.append(issue(ISSUE["title"], ISSUE["body"], "a-person", now))

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path.rsplit('/', 1)[-1]}")
        if request.method == "GET" and request.url.path == "/user":
            return httpx.Response(500) if unreadable == "user" else httpx.Response(200, json={"login": BOT})
        if request.method == "GET" and request.url.path == "/repos/acme/widgets/issues":
            if unreadable == "issues":
                return httpx.Response(500)
            since = request.url.params.get("since") or ""
            return httpx.Response(200, json=[i for i in reversed(issues) if i["updated_at"] >= since])
        if request.method == "POST" and request.url.path == "/repos/acme/widgets/issues":
            posts = sum(1 for c in calls if c.startswith("POST"))
            if first == "refused" and posts == 1:
                return httpx.Response(422, json={"message": "Validation Failed"})
            if first == "not-made-502" and posts == 1:
                return httpx.Response(502)
            sent = json.loads(request.content)
            body = sent["body"].replace("\n", "  \r\n") if stored_as == "crlf-padded" else sent["body"]
            made = issue(sent["title"], body, BOT, datetime.now(UTC))
            issues.append(made)
            if posts == 1 and first == "made-502":
                return httpx.Response(502)
            if posts == 1 and first == "made-lost":
                raise httpx.ReadTimeout("the answer was lost", request=request)
            return httpx.Response(201, json=made)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    client = GitHubClient(
        default_token="t", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=_sleep
    )
    return GitHubSyncService(client=client, sleep=_sleep), issues, calls, slept


def _repo() -> Any:
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=dict(ISSUE))
    return repo


def _creates(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The issues the token created in the last hour: the creates of this test."""
    recent = _stamp(datetime.now(UTC) - timedelta(hours=1))
    return [i for i in issues if i["user"]["login"] == BOT and i["created_at"] >= recent]


@pytest.mark.parametrize(
    ("first", "stored_as"), [("made-502", "sent"), ("made-lost", "sent"), ("made-502", "crlf-padded")]
)
async def test_an_issue_github_made_behind_a_failed_answer_is_found_and_linked(first: str, stored_as: str) -> None:
    sync, issues, calls, slept = _github(first, stored_as=stored_as)
    repo = _repo()
    data = await sync.push_create("iss-1", repo)
    made = _creates(issues)
    assert len(made) == 1 and data is not None and data["number"] == made[0]["number"], (data, made)
    assert calls.count("POST issues") == 1, f"a second issue was made: {calls}"
    assert 1.5 in slept, "the lookup does not wait for the issue to show"
    linked = repo.update.await_args.kwargs
    assert linked["tracker_key"] == str(made[0]["number"])


async def test_an_issue_github_did_not_make_is_created_once_more() -> None:
    """Neither the token's issue from two days ago (edited now) nor a person's issue with the same words is this
    create: it was not made, so it is sent once more."""
    sync, issues, calls, _ = _github("not-made-502")
    repo = _repo()
    data = await sync.push_create("iss-1", repo)
    assert calls.count("POST issues") == 2 and len(_creates(issues)) == 1, (calls, issues)
    assert data is not None and repo.update.await_args.kwargs["tracker_key"] == str(data["number"])


async def test_a_refused_create_raises_and_looks_nothing_up() -> None:
    sync, _, calls, _ = _github("refused")
    with pytest.raises(httpx.HTTPStatusError):
        await sync.push_create("iss-1", _repo())
    assert calls == ["POST issues"], calls


@pytest.mark.parametrize(
    ("unreadable", "first", "raised"),
    [
        ("issues", "made-502", httpx.HTTPStatusError),
        ("issues", "made-lost", httpx.ReadTimeout),
        ("user", "made-502", httpx.HTTPStatusError),
    ],
)
async def test_an_outcome_the_issues_cannot_tell_is_not_sent_again(
    unreadable: str, first: str, raised: type[Exception]
) -> None:
    """The token's user or the newest issues cannot be read: whether GitHub made the issue stays unknown, and a
    second create could make a second issue. The failure is raised instead."""
    sync, issues, calls, _ = _github(first, unreadable=unreadable)
    with pytest.raises(raised):
        await sync.push_create("iss-1", _repo())
    assert calls.count("POST issues") == 1 and len(_creates(issues)) == 1, (calls, issues)
