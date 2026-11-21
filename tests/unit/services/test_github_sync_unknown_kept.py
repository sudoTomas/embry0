"""A create whose outcome stays unknown is remembered for a while (#629).

A create answered 5xx, or whose answer was lost, is looked up (#613, #620). When that lookup cannot read GitHub
(the token's user, say), the outcome stays unknown and the create raises. Its record of a create under way (#622) was
dropped then: a late ``issues.opened`` webhook for the issue GitHub did make found nothing, made an embry0 issue that
mirrors it, and the next push of the pushed embry0 issue took that mirror for another issue's and created a second
GitHub issue. The record is now kept, marked unknown, for a while: a late webhook links the pushed issue, and the
next push looks for what the first create made before it creates. Two creates with the same words whose answers
were both lost each link an issue of their own.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

import embry0.services.github_sync as github_sync
from embry0.execution.github_client import GitHubClient
from embry0.services.github_sync import GitHubSyncService
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.issues import IssuesRepository
from embry0.trackers import TriggerContext

pytestmark = pytest.mark.requires_postgres

REPO = "acme/widgets"
BOT = "embry0-bot"
TITLE, BODY = "Fix the idle timer", "It never fires."


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class _GitHub:
    """One repository. Each create makes an issue and answers as ``answer`` says ("201", or "lost": the answer never
    arrives), or "502-unmade" makes none and answers 502; ``user_ok`` False makes ``GET /user`` answer 500. ``together``: each create waits there after making
    its issue, so every create has made its issue before any answer is lost."""

    def __init__(self) -> None:
        self.issues: list[dict[str, Any]] = []
        self.answer = "201"
        self.user_ok = True
        self.posts = 0
        self.together: asyncio.Barrier | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/user":
            return httpx.Response(200, json={"login": BOT}) if self.user_ok else httpx.Response(500)
        if request.method == "GET" and request.url.path == f"/repos/{REPO}/issues":
            return httpx.Response(200, json=list(reversed(self.issues)))
        if request.method == "POST" and request.url.path == f"/repos/{REPO}/issues":
            self.posts += 1
            if self.answer == "502-unmade":
                return httpx.Response(502, json={"message": "Bad Gateway"})
            sent = json.loads(request.content)
            number = 7 + len(self.issues)
            issue = {
                "number": number,
                "title": sent["title"],
                "body": sent["body"],
                "user": {"login": BOT},
                "labels": [],
                "state": "open",
                "created_at": _stamp(datetime.now(UTC)),
                "updated_at": _stamp(datetime.now(UTC)),
                "html_url": f"https://github.com/{REPO}/issues/{number}",
            }
            self.issues.append(issue)
            if self.together is not None:
                await self.together.wait()
            if self.answer == "lost":
                raise httpx.ReadTimeout("the answer was lost", request=request)
            return httpx.Response(201, json=issue)
        raise AssertionError(f"unexpected {request.method} {request.url}")


def _sync(gh: _GitHub) -> GitHubSyncService:
    async def no_wait(_seconds: float) -> None:
        return None

    client = GitHubClient(
        default_token="t", http_client=httpx.AsyncClient(transport=httpx.MockTransport(gh.handler)), sleep=no_wait
    )
    return GitHubSyncService(client=client, sleep=no_wait)


async def _webhook(sync: GitHubSyncService, issues: IssuesRepository, issue: dict[str, Any]) -> dict[str, Any]:
    ctx = TriggerContext(
        issues_repo=issues, issue_executor=None, trigger_labels=set(), event_type="issues", action="opened"
    )
    return await sync.handle_webhook_event({"action": "opened", "issue": issue, "repository": {"full_name": REPO}}, ctx)


async def _unknown_create(
    db: DatabasePool, answer: str = "lost"
) -> tuple[_GitHub, GitHubSyncService, IssuesRepository, str]:
    """A create whose outcome stayed unknown, its lookup unable to read the token's user: GitHub made it (#7) and the
    answer was lost, or, with ``answer`` "502-unmade", it made nothing and answered 502."""
    issues = IssuesRepository(db)
    pushed = await issues.create(TITLE, BODY, repo=REPO, github_sync_enabled=True, created_by="dev@example.com")
    gh = _GitHub()
    sync = _sync(gh)
    gh.answer, gh.user_ok = answer, False
    with pytest.raises(httpx.ReadTimeout if answer == "lost" else httpx.HTTPStatusError):
        await sync.push_create(pushed, issues)
    gh.answer, gh.user_ok = "201", True
    return gh, sync, issues, pushed


async def _rows(db: DatabasePool) -> list[dict[str, Any]]:
    return [dict(r) for r in await db.fetch("SELECT id, created_by, tracker_key FROM issues ORDER BY created_at")]


async def test_a_late_webhook_links_a_create_whose_outcome_stayed_unknown(db_with_migrations: DatabasePool) -> None:
    gh, sync, issues, pushed = await _unknown_create(db_with_migrations)
    answered = await _webhook(sync, issues, gh.issues[0])
    rows = await _rows(db_with_migrations)
    assert rows == [{"id": pushed, "created_by": "dev@example.com", "tracker_key": "7"}], (
        f"the late webhook did not link the pushed issue: {rows}; it answered {answered}"
    )


async def test_the_next_push_finds_what_a_create_whose_outcome_stayed_unknown_made(
    db_with_migrations: DatabasePool,
) -> None:
    gh, sync, issues, pushed = await _unknown_create(db_with_migrations)
    data = await sync.push_create(pushed, issues)
    assert gh.posts == 1, f"the next push created a second GitHub issue: {gh.posts} creates, {gh.issues}"
    assert data is not None and data["number"] == 7, data
    rows = await _rows(db_with_migrations)
    assert rows == [{"id": pushed, "created_by": "dev@example.com", "tracker_key": "7"}], rows


async def test_the_next_push_after_a_late_webhook_creates_nothing_more(db_with_migrations: DatabasePool) -> None:
    """The webhook linked the pushed issue: a sync of it is an update, and the record is gone with the link."""
    gh, sync, issues, pushed = await _unknown_create(db_with_migrations)
    await _webhook(sync, issues, gh.issues[0])
    assert sync._pushing == {}, f"a create the webhook linked is still recorded: {sync._pushing}"


@pytest.mark.parametrize("then", ["push", "webhook"])
async def test_a_record_kept_past_its_time_is_forgotten(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, then: str
) -> None:
    """Kept for a while only: past it, a push creates and a webhook tracks the issue, as before."""
    gh, sync, issues, pushed = await _unknown_create(db_with_migrations)
    kept = getattr(github_sync, "UNKNOWN_OUTCOME_KEPT", timedelta(0))
    later = datetime.now(UTC) + kept + timedelta(seconds=1)
    monkeypatch.setattr(github_sync, "_now", lambda: later, raising=False)
    if then == "push":
        await sync.push_create(pushed, issues)
        assert gh.posts == 2, f"a forgotten record still held the push back: {gh.posts} creates"
    else:
        await _webhook(sync, issues, gh.issues[0])
        rows = await _rows(db_with_migrations)
        assert [r["created_by"] for r in rows] == ["dev@example.com", "webhook"], rows
    assert sync._pushing == {}, f"a record past its time is still kept: {sync._pushing}"


async def test_two_creates_with_the_same_words_whose_answers_were_lost_each_link_their_own(
    db_with_migrations: DatabasePool,
) -> None:
    """Both lookups read the same newest issue at once: one claims it, and the other takes the next."""
    issues = IssuesRepository(db_with_migrations)
    first = await issues.create(TITLE, BODY, repo=REPO, github_sync_enabled=True, created_by="dev@example.com")
    second = await issues.create(TITLE, BODY, repo=REPO, github_sync_enabled=True, created_by="dev@example.com")
    gh = _GitHub()
    gh.answer, gh.together = "lost", asyncio.Barrier(2)
    sync = _sync(gh)
    results = await asyncio.gather(
        sync.push_create(first, issues), sync.push_create(second, issues), return_exceptions=True
    )
    failed = [r for r in results if isinstance(r, BaseException)]
    assert not failed, f"a create failed: {[f'{type(r).__name__}: {r}' for r in failed]}"
    assert gh.posts == 2, f"a create was sent again: {gh.posts} creates"
    linked = {r["id"]: r["tracker_key"] for r in await _rows(db_with_migrations)}
    assert set(linked) == {first, second} and sorted(linked.values()) == ["7", "8"], linked


class _HeldAtTheCheck:
    """The first lookup to ask the database about issue #8 holds there, its answer read, until the other create has
    finished: its link written and its record, with its claim, forgotten. That is the gap between a lookup's check
    and its claim when the check comes first (#638)."""

    def __init__(self, issues: IssuesRepository) -> None:
        self._read = issues.get_by_tracker_ref
        self.first: asyncio.Task[Any] | None = None
        self.other_done = asyncio.Event()

    async def get_by_tracker_ref(self, tracker: str, repo: str | None, key: str) -> dict[str, Any] | None:
        row = await self._read(tracker, repo, key)
        if key == "8" and self.first is None:
            self.first = asyncio.current_task()
            await self.other_done.wait()
        return row

    def finished(self, task: asyncio.Task[Any]) -> None:
        if task is not self.first:
            self.other_done.set()


async def test_a_lookup_held_between_its_check_and_its_claim_takes_no_issue_another_linked(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#638: one lookup asked the database about #8 and heard nothing; before it claimed #8, the other create's lookup
    took #8, linked it and resolved, releasing its claim. The first then claimed #8 too, and its link failed."""
    issues = IssuesRepository(db_with_migrations)
    first = await issues.create(TITLE, BODY, repo=REPO, github_sync_enabled=True, created_by="dev@example.com")
    second = await issues.create(TITLE, BODY, repo=REPO, github_sync_enabled=True, created_by="dev@example.com")
    held = _HeldAtTheCheck(issues)
    monkeypatch.setattr(issues, "get_by_tracker_ref", held.get_by_tracker_ref)
    gh = _GitHub()
    gh.answer, gh.together = "lost", asyncio.Barrier(2)
    sync = _sync(gh)
    tasks = [asyncio.create_task(sync.push_create(issue_id, issues)) for issue_id in (first, second)]
    for task in tasks:
        task.add_done_callback(held.finished)
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=30)
    assert held.first is not None, "no lookup asked the database about #8: the gap was never opened"
    failed = [r for r in results if isinstance(r, BaseException)]
    assert not failed, f"a create failed: {[f'{type(r).__name__}: {r}' for r in failed]}"
    linked = {r["id"]: r["tracker_key"] for r in await _rows(db_with_migrations)}
    assert set(linked) == {first, second} and sorted(linked.values()) == ["7", "8"], (
        f"each create must link the issue it made: {linked}"
    )


async def test_the_next_push_creates_nothing_while_github_still_cannot_say(db_with_migrations: DatabasePool) -> None:
    """The earlier create may have made the issue, and GitHub still cannot be read to say: nothing is created."""
    gh, sync, issues, pushed = await _unknown_create(db_with_migrations)
    gh.user_ok = False
    raised: Exception | None = None
    try:
        await sync.push_create(pushed, issues)
    except Exception as exc:  # noqa: BLE001 - its type is checked below, after what matters more
        raised = exc
    assert gh.posts == 1, f"a create was sent while the earlier one's outcome was unknown: {gh.posts} creates"
    assert type(raised).__name__ == "CreateOutcomeUnknown", f"the push answered {raised!r}"
    assert sync._pushing, "the record of the earlier create was dropped"


async def test_the_next_push_creates_once_when_the_earlier_create_made_nothing(
    db_with_migrations: DatabasePool,
) -> None:
    gh, sync, issues, pushed = await _unknown_create(db_with_migrations, answer="502-unmade")
    data = await sync.push_create(pushed, issues)
    assert gh.posts == 2 and data is not None and data["number"] == 7, (gh.posts, data)
    rows = await _rows(db_with_migrations)
    assert rows == [{"id": pushed, "created_by": "dev@example.com", "tracker_key": "7"}], rows
    assert sync._pushing == {}, f"the record of the earlier create is still kept: {sync._pushing}"


def _later_issue(number: int, when: datetime) -> dict[str, Any]:
    """The token's issue with the same words, made at ``when``: by another deployment or a script with the token."""
    return {
        "number": number,
        "title": TITLE,
        "body": BODY,
        "user": {"login": BOT},
        "labels": [],
        "state": "open",
        "created_at": _stamp(when),
        "updated_at": _stamp(when),
        "html_url": f"https://github.com/{REPO}/issues/{number}",
    }


async def test_a_kept_record_does_not_take_an_issue_made_well_after_its_outcome_became_unknown(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#630 review: the token's issue with the same words, made eight minutes later, is not what the create made,
    though the record is kept ten minutes: an issue it made was made before its outcome became unknown."""
    gh, sync, issues, pushed = await _unknown_create(db_with_migrations, answer="502-unmade")
    later = datetime.now(UTC) + timedelta(minutes=8)
    monkeypatch.setattr(github_sync, "_now", lambda: later, raising=False)
    answered = await _webhook(sync, issues, _later_issue(20, later))
    tracked = await issues.get_by_tracker_ref("github", REPO, "20")
    assert tracked is not None and tracked["id"] != pushed, (
        f"an issue made eight minutes after the outcome became unknown was linked to the kept record: {tracked}, "
        f"{answered}"
    )


async def test_the_next_push_does_not_take_an_issue_made_well_after_its_outcome_became_unknown(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    gh, sync, issues, pushed = await _unknown_create(db_with_migrations, answer="502-unmade")
    later = datetime.now(UTC) + timedelta(minutes=8)
    gh.issues.append(_later_issue(20, later))
    monkeypatch.setattr(github_sync, "_now", lambda: later, raising=False)
    data = await sync.push_create(pushed, issues)
    assert data is not None and data["number"] != 20, f"the next push took an issue made eight minutes later: {data}"
    assert gh.posts == 2, f"the next push did not create: {gh.posts} creates"


async def test_the_next_push_says_what_to_do_while_github_still_cannot_say(db_with_migrations: DatabasePool) -> None:
    """#630 review: the 502 the sync route answers carries this, so it says what to do."""
    gh, sync, issues, pushed = await _unknown_create(db_with_migrations)
    gh.user_ok = False
    with pytest.raises(Exception) as raised:
        await sync.push_create(pushed, issues)
    assert "try again in a few minutes" in str(raised.value) and "10 minutes" in str(raised.value), raised.value


async def test_records_past_their_time_are_forgotten_on_any_repositorys_event(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#630 review: not only on an event of their own repository."""
    gh, sync, issues, pushed = await _unknown_create(db_with_migrations)
    later = datetime.now(UTC) + timedelta(minutes=11)
    monkeypatch.setattr(github_sync, "_now", lambda: later, raising=False)
    other = _later_issue(30, later)
    ctx = TriggerContext(
        issues_repo=issues, issue_executor=None, trigger_labels=set(), event_type="issues", action="opened"
    )
    await sync.handle_webhook_event(
        {"action": "opened", "issue": other, "repository": {"full_name": "acme/other"}}, ctx
    )
    assert sync._pushing == {}, f"a record past its time in another repository is still kept: {sync._pushing}"
