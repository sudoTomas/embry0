"""A lookup holds a GitHub issue only while it may still take it (#638).

The lookup after a create whose answer was lost claims each candidate issue before it asks the database whether
another embry0 issue holds it, so no other create's lookup takes it in between (#629, #638). A claim it keeps on an
issue it went on to skip, or after the question failed, would keep another create's lookup from its own issue
until this create resolves: that lookup would find nothing and send its create again.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from embry0.execution.github_client import GitHubClient
from embry0.services import github_sync
from embry0.services.github_sync import GitHubSyncService

REPO = "acme/widgets"
BOT = "embry0-bot"
TITLE, BODY = "Fix the idle timer", "It never fires."


def _github() -> GitHubSyncService:
    """One issue, #8, made by the token's user with TITLE and BODY a minute ago."""
    made = (datetime.now(UTC) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    issue = {
        "number": 8,
        "title": TITLE,
        "body": BODY,
        "user": {"login": BOT},
        "created_at": made,
        "updated_at": made,
        "html_url": f"https://github.com/{REPO}/issues/8",
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": BOT})
        if request.url.path == f"/repos/{REPO}/issues":
            return httpx.Response(200, json=[issue])
        raise AssertionError(f"unexpected {request.method} {request.url}")

    async def no_wait(_seconds: float) -> None:
        return None

    client = GitHubClient(
        default_token="t", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=no_wait
    )
    return GitHubSyncService(client=client, sleep=no_wait)


def _answers(answer: Callable[[], bool]) -> Callable[[Any], Awaitable[bool]]:
    async def pushed_by_another(_number: Any) -> bool:
        return answer()

    return pushed_by_another


async def _lookup(
    sync: GitHubSyncService, push: Any, pushed_by_another: Callable[[Any], Awaitable[bool]]
) -> tuple[bool, dict[str, Any] | None]:
    earliest = datetime.now(UTC) - timedelta(minutes=5)
    payload = {"title": TITLE, "body": BODY}
    return await sync._issue_made_since(REPO, payload, earliest, pushed_by_another, sync._claim(REPO, push))


def _two_creates(sync: GitHubSyncService) -> tuple[Any, Any]:
    started = datetime.now(UTC) - timedelta(minutes=2)
    first = github_sync._Push("iss-first", TITLE, BODY, started)
    second = github_sync._Push("iss-second", TITLE, BODY, started)
    sync._pushing[REPO] = [first, second]
    return first, second


def _raise() -> bool:
    raise RuntimeError("the database did not answer")


@pytest.mark.parametrize(
    ("first_hears", "first_reads"),
    [(lambda: True, True), (_raise, False)],
    ids=["linked-elsewhere", "check-failed"],
)
async def test_a_lookup_lets_go_of_an_issue_it_did_not_take(first_hears: Any, first_reads: bool) -> None:
    sync = _github()
    first, second = _two_creates(sync)
    assert await _lookup(sync, first, _answers(first_hears)) == (first_reads, None)
    read, found = await _lookup(sync, second, _answers(lambda: False))
    assert read and found is not None and found["number"] == 8, (
        f"the second create's lookup did not find its own #8 (it answered {read}, {found}): the first lookup still "
        f"holds it ({first.claimed!r})"
    )
