"""Issue sync's create after an unknown outcome (#613).

Since #611 the create is sent again only when GitHub did not act on it. A create GitHub made but answered 502,
or whose answer was lost, left the GitHub issue unlinked: the embry0 issue recorded no number, and a later sync
made a second. The sync now reads the token's user and the newest issues once for the one it made, and sends the
create again only when it is not there. Only the token's own issue, created within a few minutes of the create,
counts: a person's issue with the same words, or an older one of the token's edited since, is not this create.
Since #620 the window follows GitHub's clock when its failed answer carried one, the issues are not listed when
the token's user cannot be read, and an issue another embry0 issue pushed is not this create either.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
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


_DEFAULT_EARLIER = ("bot-two-days-ago-edited-now", "person-now")


def _github(
    first: str,
    *,
    unreadable: str = "",
    stored_as: str = "sent",
    earlier: tuple[str, ...] = _DEFAULT_EARLIER,
    older: int = 0,
    skew: timedelta = timedelta(0),
    links: dict[int, dict[str, Any]] | None = None,
    made_is_adopted_by: str = "",
    database_clock: timedelta = timedelta(0),
) -> tuple[GitHubSyncService, list[dict[str, Any]], list[str], list[float]]:
    """GitHub with one repository. The first create meets ``first``: "made-502" (made, answered 502), "made-lost"
    (made, its answer lost), "not-made-502" (refused before it was made, answered 502), or "refused" (422).
    ``earlier``: issues with the same title and body the repository already holds: "bot-two-days-ago-edited-now"
    (the token's own, edited now, so GitHub's ``since``, which reads the update time, lists it), "person-now" (a
    person's), "bot-a-minute-ago" and "bot-seven-minutes-ago" (the token's own). ``older``: that many other issues,
    two days old and edited now, come before them. ``skew``: how far GitHub's clock runs ahead of this host's,
    which every timestamp and every answer's ``Date`` header follow. ``stored_as`` "crlf-padded": GitHub stores the
    body with CRLF line ends and trailing spaces. ``unreadable``: "issues" or "user" cannot be read. The list is
    GitHub's: by creation, newest first unless asked otherwise, ``per_page`` at a time (30 unless asked), and only
    the issues updated since ``since``. ``made_is_adopted_by``: as the first create makes its issue, the issues
    webhook makes an embry0 issue from it, with this ``created_by``, in ``links`` (number → embry0 issue), at the
    database's time, which runs ``database_clock`` ahead of this host's."""
    now = datetime.now(UTC) + skew
    issues: list[dict[str, Any]] = []
    calls: list[str] = []
    slept: list[float] = []

    def answer(status: int, **kwargs: Any) -> httpx.Response:
        stamp = format_datetime(datetime.now(UTC) + skew, usegmt=True)
        return httpx.Response(status, headers={"Date": stamp}, **kwargs)

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

    for n in range(older):
        issues.append(issue(f"An older issue {n}", "", "a-person", now - timedelta(days=2), now))
    if "bot-two-days-ago-edited-now" in earlier:
        issues.append(issue(ISSUE["title"], ISSUE["body"], BOT, now - timedelta(days=2), now))
    if "bot-seven-minutes-ago" in earlier:
        issues.append(issue(ISSUE["title"], ISSUE["body"], BOT, now - timedelta(minutes=7)))
    if "bot-a-minute-ago" in earlier:
        issues.append(issue(ISSUE["title"], ISSUE["body"], BOT, now - timedelta(minutes=1)))
    if "person-now" in earlier:
        issues.append(issue(ISSUE["title"], ISSUE["body"], "a-person", now))

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path.rsplit('/', 1)[-1]}")
        if request.method == "GET" and request.url.path == "/user":
            return answer(500) if unreadable == "user" else answer(200, json={"login": BOT})
        if request.method == "GET" and request.url.path == "/repos/acme/widgets/issues":
            if unreadable == "issues":
                return answer(500)
            params = request.url.params
            assert params.get("sort", "created") == "created", f"only a sort by creation is modelled: {params}"
            since = params.get("since") or ""
            listed = sorted(
                (i for i in issues if i["updated_at"] >= since),
                key=lambda i: (i["created_at"], i["number"]),
                reverse=params.get("direction", "desc") == "desc",
            )
            return answer(200, json=listed[: int(params.get("per_page") or 30)])
        if request.method == "POST" and request.url.path == "/repos/acme/widgets/issues":
            posts = sum(1 for c in calls if c.startswith("POST"))
            if first == "refused" and posts == 1:
                return answer(422, json={"message": "Validation Failed"})
            if first == "not-made-502" and posts == 1:
                return answer(502)
            sent = json.loads(request.content)
            body = sent["body"].replace("\n", "  \r\n") if stored_as == "crlf-padded" else sent["body"]
            made = issue(sent["title"], body, BOT, datetime.now(UTC) + skew)
            issues.append(made)
            if posts == 1 and made_is_adopted_by and links is not None:
                links[made["number"]] = {
                    "id": "iss-adopted",
                    "created_by": made_is_adopted_by,
                    "created_at": datetime.now(UTC) + database_clock,
                }
            if posts == 1 and first == "made-502":
                return answer(502)
            if posts == 1 and first == "made-lost":
                raise httpx.ReadTimeout("the answer was lost", request=request)
            return answer(201, json=made)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    client = GitHubClient(
        default_token="t", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=_sleep
    )
    return GitHubSyncService(client=client, sleep=_sleep), issues, calls, slept


def _repo(links: dict[int, dict[str, Any]] | None = None, *, unreadable: bool = False) -> Any:
    """The issues repository: ``links`` maps a GitHub issue number to the embry0 issue it is linked to."""
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=dict(ISSUE))

    async def get_by_tracker_ref(tracker: str, repo_name: str | None, key: str) -> dict[str, Any] | None:
        assert (tracker, repo_name) == ("github", ISSUE["repo"]), (tracker, repo_name)
        if unreadable:
            raise ConnectionError("the database is gone")
        return (links or {}).get(int(key))

    repo.get_by_tracker_ref = get_by_tracker_ref
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
        ("links", "made-502", httpx.HTTPStatusError),
    ],
)
async def test_an_outcome_the_issues_cannot_tell_is_not_sent_again(
    unreadable: str, first: str, raised: type[Exception]
) -> None:
    """The token's user, the newest issues or the embry0 issues they are linked to cannot be read: whether GitHub
    made the issue stays unknown, and a second create could make a second issue. The failure is raised instead.
    Without the user there is no login to match, and the issues are not listed (#620)."""
    sync, issues, calls, _ = _github(first, unreadable="" if unreadable == "links" else unreadable)
    with pytest.raises(raised):
        await sync.push_create("iss-1", _repo(unreadable=unreadable == "links"))
    assert calls.count("POST issues") == 1 and len(_creates(issues)) == 1, (calls, issues)
    if unreadable == "user":
        assert "GET issues" not in calls, f"the issues were listed without a login to match: {calls}"


async def test_an_issue_github_made_is_found_among_many_issues_edited_in_the_window() -> None:
    """#620: 150 older issues were edited in the window (a relabel, say), so GitHub's ``since`` lists them all.
    The issues are read newest first by creation, so the one GitHub just made is on the first page of 100."""
    sync, issues, calls, _ = _github("made-502", older=150)
    data = await sync.push_create("iss-1", _repo())
    assert calls.count("POST issues") == 1 and len(_creates(issues)) == 1, calls
    assert data is not None and data["number"] == _creates(issues)[0]["number"]


async def test_an_issue_github_made_is_found_when_this_hosts_clock_runs_ahead() -> None:
    """#620: this host's clock runs ten minutes ahead of GitHub's. By the local clock the issue GitHub made was
    created before the window; by the 502's Date header, GitHub's clock, it was created inside it, and is found."""
    sync, issues, calls, _ = _github("made-502", skew=-timedelta(minutes=10))
    data = await sync.push_create("iss-1", _repo())
    assert calls.count("POST issues") == 1, f"a second issue was made: {calls}"
    assert data is not None and data["number"] == _creates(issues)[0]["number"]


async def test_an_earlier_issue_is_not_taken_for_this_create_when_this_hosts_clock_runs_behind() -> None:
    """#620: this host's clock runs ten minutes behind GitHub's, and the token created the same issue seven minutes
    before, by GitHub's clock. By the local clock it falls inside the window; by the 502's Date header it is an
    earlier issue, and the create GitHub did not make is sent once more."""
    sync, issues, calls, _ = _github("not-made-502", skew=timedelta(minutes=10), earlier=("bot-seven-minutes-ago",))
    data = await sync.push_create("iss-1", _repo())
    assert calls.count("POST issues") == 2, f"the earlier issue was taken for this create: {calls}"
    assert data is not None and data["number"] == max(i["number"] for i in issues)


# -- an issue already linked to an embry0 issue (#620) ---------------------------------------------------------


@pytest.mark.parametrize("made", ["a-minute-before", "just-now"])
async def test_an_issue_another_embry0_issue_pushed_is_not_taken_for_this_one(made: str) -> None:
    """Two embry0 issues with the same title and body, pushed by the same token a minute apart: the first one's
    GitHub issue is linked to it. The second's create is answered 502 before GitHub made it; the first one's issue
    matches every other test, but it is another embry0 issue's, so the create is sent once more. That holds when
    the other embry0 issue was made just now, too: only the webhook's rows mirror an issue."""
    when = datetime.now(UTC) - (timedelta(minutes=1) if made == "a-minute-before" else timedelta(0))
    links = {7: {"id": "iss-0", "created_by": "user", "created_at": when}}
    sync, issues, calls, _ = _github("not-made-502", earlier=("bot-a-minute-ago",), links=links)
    repo = _repo(links)
    data = await sync.push_create("iss-1", repo)
    assert calls.count("POST issues") == 2, f"another embry0 issue's GitHub issue was taken: {calls}"
    assert data is not None and data["number"] != 7
    assert repo.update.await_args.kwargs["tracker_key"] == str(data["number"])


async def test_an_issue_already_linked_to_this_embry0_issue_is_taken() -> None:
    """Two syncs of one embry0 issue raced: the first linked the GitHub issue it made, and the second's create is
    answered 502 before GitHub made it. The issue it finds is this embry0 issue's own, and is not made again."""
    links = {7: {"id": "iss-1", "created_by": "user"}}
    sync, _, calls, _ = _github("not-made-502", earlier=("bot-a-minute-ago",), links=links)
    data = await sync.push_create("iss-1", _repo(links))
    assert calls.count("POST issues") == 1 and data is not None and data["number"] == 7, calls


@pytest.mark.parametrize("created_by", ["webhook", "webhook-label"])
@pytest.mark.parametrize("database_clock", [timedelta(0), -timedelta(seconds=10)], ids=["same-clock", "db-10s-behind"])
async def test_an_issue_the_webhook_made_an_embry0_issue_from_is_still_this_create(
    created_by: str, database_clock: timedelta
) -> None:
    """GitHub made the issue and answered 502, and its issues webhook made an embry0 issue from it before the
    lookup ran (#622). That row mirrors the issue this create made, made after the create started, give or take
    the database's clock: it is not another embry0 issue's push, and sending the create again would make a second
    GitHub issue."""
    links: dict[int, dict[str, Any]] = {}
    sync, issues, calls, _ = _github(
        "made-502", links=links, made_is_adopted_by=created_by, database_clock=database_clock
    )
    data = await sync.push_create("iss-1", _repo(links))
    assert calls.count("POST issues") == 1, f"a second issue was made: {calls}"
    assert data is not None and data["number"] == _creates(issues)[0]["number"] and data["number"] in links


@pytest.mark.parametrize(
    ("created_by", "made"),
    [("webhook", "a-minute-before"), ("webhook-label", "a-minute-before"), ("webhook", "unknown")],
)
async def test_the_webhooks_mirror_of_an_earlier_push_is_another_embry0_issues(created_by: str, made: str) -> None:
    """Another embry0 issue with the same title and body was pushed a minute before; GitHub made #7 for it, and the
    issues webhook mirrored #7 first (#622). This create is answered 502 before GitHub made it. The mirror was made
    before this create started, so it is not this create's: the create is sent once more. A mirror whose creation
    time cannot be read is not taken for this create's either."""
    when = datetime.now(UTC) - timedelta(minutes=1) if made == "a-minute-before" else None
    links: dict[int, dict[str, Any]] = {7: {"id": "iss-0-mirror", "created_by": created_by, "created_at": when}}
    sync, _, calls, _ = _github("not-made-502", earlier=("bot-a-minute-ago",), links=links)
    repo = _repo(links)
    data = await sync.push_create("iss-1", repo)
    assert calls.count("POST issues") == 2, f"the mirror of another embry0 issue's push was taken: {calls}"
    assert data is not None and data["number"] != 7
    assert repo.update.await_args.kwargs["tracker_key"] == str(data["number"])
