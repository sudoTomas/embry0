"""The issues webhook and the sync's create of the same issue (#622).

GitHub sends ``issues.opened`` as it makes the issue the sync's create asked for. When that delivery is handled
before the create's answer is, the webhook found no embry0 issue for the number and made one; the create's link
then failed on the tracker reference, the pushed embry0 issue stayed unlinked, and a trigger label started a
triage for the second one. The webhook now links the embry0 issue being pushed, matched as the create's own lookup
matches (#613, #620): the token's own issue, with the same title and body, created within the window of the push.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from embry0.execution.github_client import GitHubClient
from embry0.services.github_sync import GitHubSyncService
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.issues import IssuesRepository
from embry0.trackers import TriggerContext

pytestmark = pytest.mark.requires_postgres

REPO = "acme/widgets"
BOT = "embry0-bot"  # the login the sync's token creates as
TITLE, BODY = "Fix the idle timer", "It never fires."


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class _Executor:
    """Records each triage the webhook starts."""

    def __init__(self) -> None:
        self.started: list[str] = []

    async def dispatch_refusal(self, *, actor: str | None = None) -> None:
        return None

    async def execute(self, issue_id: str, **_: Any) -> str:
        self.started.append(issue_id)
        return "job-1"


def _delivery(made: dict[str, Any], action: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"action": action, "issue": made, "repository": {"full_name": REPO}}
    if action == "labeled" and made["labels"]:
        payload["label"] = made["labels"][0]
    return payload


def _github(
    issues_repo: IssuesRepository,
    executor: _Executor,
    *,
    answer: str = "201",
    labels: tuple[str, ...] = (),
    also: dict[str, Any] | None = None,
    together: asyncio.Barrier | None = None,
    events: tuple[str, ...] = ("opened",),
) -> tuple[GitHubSyncService, list[dict[str, Any]], list[dict[str, Any]]]:
    """GitHub, whose create makes the issue and delivers its ``issues.opened`` webhook before it answers: 201, or
    ``answer`` "lost" (the answer never arrives). ``also``: another issue's webhook, delivered first. ``together``:
    each create waits there, after making its issue, so the webhooks come while every create is under way.
    ``events``: the deliveries GitHub sends for the issue it made, in order (``labeled`` for each label it was made
    with, which can come first)."""
    made: list[dict[str, Any]] = []
    webhooks: list[dict[str, Any]] = []
    holder: list[GitHubSyncService] = []

    async def deliver(issue: dict[str, Any], action: str = "opened") -> None:
        ctx = TriggerContext(
            issues_repo=issues_repo,
            issue_executor=executor,
            trigger_labels={"embry0"},
            event_type="issues",
            action=action,
        )
        webhooks.append(await holder[0].handle_webhook_event(_delivery(issue, action), ctx))

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/user":
            return httpx.Response(200, json={"login": BOT})
        if request.method == "GET" and request.url.path == f"/repos/{REPO}/issues":
            return httpx.Response(200, json=list(reversed(made)))
        if request.method == "POST" and request.url.path == f"/repos/{REPO}/issues":
            sent = json.loads(request.content)
            number = 7 + len(made)
            issue = {
                "number": number,
                "title": sent["title"],
                "body": sent["body"],
                "user": {"login": BOT},
                "labels": [{"name": name} for name in (sent.get("labels") or labels)],
                "state": "open",
                "created_at": _stamp(datetime.now(UTC)),
                "updated_at": _stamp(datetime.now(UTC)),
                "html_url": f"https://github.com/{REPO}/issues/{number}",
            }
            made.append(issue)
            if together is not None:
                await together.wait()
            if also is not None:
                await deliver(also)
            for action in events:
                await deliver(issue, action)
            if answer == "lost":
                raise httpx.ReadTimeout("the answer was lost", request=request)
            return httpx.Response(201, json=issue)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    async def no_wait(_seconds: float) -> None:
        return None

    client = GitHubClient(
        default_token="t", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=no_wait
    )
    sync = GitHubSyncService(client=client, sleep=no_wait)
    holder.append(sync)
    return sync, made, webhooks


async def _pushed(db: DatabasePool, labels: list[str] | None = None) -> tuple[IssuesRepository, str]:
    """An embry0 issue to push; the create sends its ``labels``, and GitHub makes the issue with them."""
    repo = IssuesRepository(db)
    return repo, await repo.create(
        TITLE, BODY, labels=labels, repo=REPO, github_sync_enabled=True, created_by="dev@example.com"
    )


@pytest.mark.parametrize("answer", ["201", "lost"])
async def test_the_webhook_for_an_issue_being_pushed_links_the_issue_that_pushed_it(
    db_with_migrations: DatabasePool, answer: str
) -> None:
    issues, pushed = await _pushed(db_with_migrations)
    sync, made, webhooks = _github(issues, _Executor(), answer=answer)
    try:
        data = await sync.push_create(pushed, issues)
    except Exception as exc:  # noqa: BLE001 - what the create raised is the finding
        pytest.fail(f"the create's link failed after the webhook came first: {type(exc).__name__}: {exc}")
    assert data is not None and data["number"] == 7, data
    linked = await issues.get_by_tracker_ref("github", REPO, "7")
    assert linked is not None and linked["id"] == pushed, (
        f"the GitHub issue is linked to {linked and (linked['id'], linked['created_by'])}, not the embry0 issue that "
        f"pushed it ({pushed}); the webhook answered {webhooks}"
    )
    rows = await db_with_migrations.fetch("SELECT id, created_by FROM issues ORDER BY created_at")
    assert [r["id"] for r in rows] == [pushed], f"the webhook made a second embry0 issue: {[dict(r) for r in rows]}"


async def test_a_trigger_label_on_an_issue_being_pushed_starts_no_second_triage(
    db_with_migrations: DatabasePool,
) -> None:
    issues, pushed = await _pushed(db_with_migrations, labels=["embry0"])
    executor = _Executor()
    sync, _made, webhooks = _github(issues, executor)
    try:
        await sync.push_create(pushed, issues)
    except Exception:  # noqa: BLE001, S110 - the failed link is the test above's finding; this one reads the triage
        pass
    assert executor.started == [], f"the webhook started a triage for a second embry0 issue: {executor.started}"
    assert webhooks and webhooks[0].get("issue_id") == pushed, webhooks


@pytest.mark.parametrize(
    "another",
    [
        {"user": {"login": "a-person"}, "created_at": "now"},
        {"user": {"login": BOT}, "created_at": "seven-minutes-ago"},
        {"user": {"login": BOT}, "created_at": "now", "title": "Another finding"},
    ],
    ids=["a-persons-issue", "the-tokens-older-issue", "the-tokens-other-issue"],
)
async def test_another_issue_with_the_same_words_is_not_the_push(
    db_with_migrations: DatabasePool, another: dict[str, Any]
) -> None:
    """Only the token's own issue, created within the window of the push, is the push: a person's issue with the same
    words, opened while the push is under way, is tracked as any other, and so is the token's older one."""
    issues, pushed = await _pushed(db_with_migrations)
    when = datetime.now(UTC) - (timedelta(minutes=7) if another["created_at"] == "seven-minutes-ago" else timedelta(0))
    other = {
        "number": 3,
        "title": another.get("title", TITLE),
        "body": BODY,
        "user": another["user"],
        "labels": [],
        "state": "open",
        "created_at": _stamp(when),
        "updated_at": _stamp(datetime.now(UTC)),
        "html_url": f"https://github.com/{REPO}/issues/3",
    }
    sync, _made, _webhooks = _github(issues, _Executor(), also=other)
    await sync.push_create(pushed, issues)
    mirrored = await issues.get_by_tracker_ref("github", REPO, "3")
    assert mirrored is not None and mirrored["id"] != pushed and mirrored["created_by"] == "webhook", mirrored
    linked = await issues.get_by_tracker_ref("github", REPO, "7")
    assert linked is not None and linked["id"] == pushed, linked


async def test_two_creates_with_the_same_words_each_link_the_issue_they_made(db_with_migrations: DatabasePool) -> None:
    """While two creates that sent the same words are under way, an issue's webhook cannot tell which made it: it
    makes no embry0 issue and links none, and each create links the one its answer names."""
    issues = IssuesRepository(db_with_migrations)
    first = await issues.create(TITLE, BODY, repo=REPO, github_sync_enabled=True, created_by="dev@example.com")
    second = await issues.create(TITLE, BODY, repo=REPO, github_sync_enabled=True, created_by="dev@example.com")
    sync, _made, webhooks = _github(issues, _Executor(), together=asyncio.Barrier(2))
    answers = await asyncio.gather(sync.push_create(first, issues), sync.push_create(second, issues))
    assert all(w.get("status") == "ignored" for w in webhooks) and len(webhooks) == 2, (
        f"a webhook linked an issue two creates under way could have made: {webhooks}"
    )
    linked = {
        str(r["id"]): str(r["tracker_key"])
        for r in await db_with_migrations.fetch("SELECT id, tracker_key FROM issues ORDER BY created_at")
    }
    assert linked == {first: str(answers[0]["number"]), second: str(answers[1]["number"])}, linked


@pytest.mark.parametrize(
    "events", [("labeled", "opened"), ("opened", "labeled")], ids=["labeled-first", "opened-first"]
)
async def test_a_label_delivered_before_the_create_is_answered_adopts_no_second_issue(
    db_with_migrations: DatabasePool, events: tuple[str, ...]
) -> None:
    """GitHub sends ``labeled`` for each label an issue is made with, and the webhook adopts an untracked issue that
    gets a trigger label, starting a triage: for an issue being pushed that was a second embry0 issue, and a second
    job when the pushed one was auto-triaged. It is the pushed issue's, linked, with no triage started."""
    issues, pushed = await _pushed(db_with_migrations, labels=["embry0"])
    executor = _Executor()
    sync, _made, webhooks = _github(issues, executor, events=events)
    try:
        await sync.push_create(pushed, issues)
    except Exception:  # noqa: BLE001, S110 - the failed link is not this test's finding; the second issue is
        pass
    rows = await db_with_migrations.fetch("SELECT id, created_by FROM issues ORDER BY created_at")
    assert [r["id"] for r in rows] == [pushed], f"the webhook adopted a second embry0 issue: {[dict(r) for r in rows]}"
    assert executor.started == [], f"the webhook started a triage: {executor.started}; it answered {webhooks}"
    linked = await issues.get_by_tracker_ref("github", REPO, "7")
    assert linked is not None and linked["id"] == pushed, linked


_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_the_orchestrator_is_configured_as_one_process() -> None:
    """The creates under way are kept in memory: the orchestrator image runs one uvicorn worker, and no compose file
    asks for more (``--workers``, or ``WEB_CONCURRENCY``, which ``--workers`` reads)."""
    dockerfile = (_REPO_ROOT / "infra" / "Dockerfile.orchestrator").read_text()
    command = dockerfile[dockerfile.index('CMD ["uvicorn"') :]
    assert "--workers" not in command, f"the orchestrator image runs several workers: {command[:300]}"
    for compose in sorted((_REPO_ROOT / "infra").glob("docker-compose*.yml")):
        text = compose.read_text()
        assert "--workers" not in text and "WEB_CONCURRENCY" not in text, f"{compose.name} asks for several workers"


@pytest.mark.parametrize(("value", "warned"), [(None, False), ("1", False), ("2", True), ("4", True)])
def test_several_workers_are_warned_of_at_startup(
    monkeypatch: pytest.MonkeyPatch, value: str | None, warned: bool
) -> None:
    """An operator's WEB_CONCURRENCY in the root .env would start several workers: the sync says what that breaks."""
    if value is None:
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("WEB_CONCURRENCY", value)
    with capture_logs() as logs:
        GitHubSyncService(github_token="t")
    events = [entry["event"] for entry in logs]
    assert ("github_sync_several_workers" in events) is warned, f"WEB_CONCURRENCY={value}: {events}"
