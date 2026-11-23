"""The forge's own open time for a PR (#278 part 2, review of #340/#342)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from embry0.services.healing.forge import GitHubPrTimes, parse_forge_time

URL = "https://github.com/acme/widgets/pull/42"
OPENED = datetime(2026, 9, 20, 8, 26, tzinfo=UTC)

pytestmark = pytest.mark.asyncio


def _client(status: int, body: object = None) -> MagicMock:
    request = httpx.Request("GET", "https://api.github.test/pulls")
    return MagicMock(get=AsyncMock(return_value=httpx.Response(status, json=body, request=request)))


async def test_the_pr_is_read_once_and_its_open_time_returned():
    client = _client(200, {"created_at": "2026-09-20T08:26:00Z", "number": 42})
    times = GitHubPrTimes(client)
    assert await times.opened_at(URL) == OPENED
    assert await times.opened_at(URL) == OPENED
    client.get.assert_awaited_once_with("/repos/acme/widgets/pulls/42", repo="acme/widgets")


@pytest.mark.parametrize("status", [404, 403, 500])
async def test_a_forge_that_cannot_answer_yields_no_time(status):
    assert await GitHubPrTimes(_client(status, {"message": "x"})).opened_at(URL) is None


async def test_a_forge_error_yields_no_time_never_an_exception():
    times = GitHubPrTimes(MagicMock(get=AsyncMock(side_effect=httpx.ConnectError("down"))))
    assert await times.opened_at(URL) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/acme/widgets/-/merge_requests/42",  # not GitHub: no guessing
        "https://ghe.example.test/acme/widgets/pull/42",  # GitHub, on a host this client does not read
        "https://github.com/acme/widgets/pull/abc",
        "https://github.com/acme/widgets/pulls/42",
        "https://evil.test/github.com/acme/widgets/pull/42",
        "",
    ],
)
async def test_only_a_github_pr_url_is_asked_about(url):
    client = _client(200, {"created_at": "2026-09-20T08:26:00Z"})
    assert await GitHubPrTimes(client).opened_at(url) is None
    client.get.assert_not_awaited()


@pytest.mark.parametrize("body", [{}, {"created_at": None}, {"created_at": "not a time"}, {"created_at": ""}, None])
async def test_a_body_without_a_readable_time_yields_none(body):
    assert await GitHubPrTimes(_client(200, body)).opened_at(URL) is None


async def test_forge_times_are_always_aware():
    assert parse_forge_time("2026-09-20T08:26:00Z") == OPENED
    assert parse_forge_time("2026-09-20T08:26:00+00:00") == OPENED
    # A forge that omits the zone is read as UTC rather than refused: the
    # ledger would reject a naive datetime outright.
    assert parse_forge_time("2026-09-20T08:26:00").tzinfo is UTC
    assert parse_forge_time(None) is None and parse_forge_time(17) is None
