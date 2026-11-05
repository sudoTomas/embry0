"""The MCP issue, question and merge tools read and act only within the caller's repository allowlist.

The MCP server reaches embry0 through its REST routes with the caller's own
key (``embry0.mcp_server.client``), so its tools hold to the routes' rule:
each tool runs here against the real app over a real database
(:mod:`tests.fixtures.issue_reads`). Through a key whose list covers one
repository, a tool reading the other repository's issue or merge approval
gives exactly what it gives for one that does not exist, a list names none
of it, and the key's own issues read as before. A dispatch bot files an
issue on its repository and follows it; a chat assistant whose propose-scope
key covers many repositories reads its proposals, issues and approvals and
starts an issue an owner said yes to.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from embry0.api.deps import bust_api_key_cache
from embry0.mcp_server import server
from embry0.mcp_server.client import Embry0APIError, Embry0Client
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.issue_reads import (
    APPROVAL,
    CHILD_HERE,
    INPUT,
    INPUT_HERE,
    ISSUE,
    JOB_HERE,
    MISSING,
    MISSING_APPROVAL,
    MISSING_INPUT,
    MIXED,
    MIXED_HIDDEN,
    issue_reads_app,
    shows_uncovered_approval,
    shows_uncovered_issue,
)
from tests.fixtures.job_reads import JOB
from tests.fixtures.repository_writes import COVERED, UNCOVERED, call

pytestmark = pytest.mark.requires_postgres


def _use(monkeypatch: pytest.MonkeyPatch, app: FastAPI, key: str) -> Embry0Client:
    """The MCP server's client, calling ``app`` with ``key``."""
    client = Embry0Client("http://test", api_key=key, transport=ASGITransport(app=app, raise_app_exceptions=False))
    monkeypatch.setattr(server, "_client", client)
    return client


async def _outcome(call_: Awaitable[Any]) -> Any:
    try:
        return "ok", await call_
    except Embry0APIError as exc:
        return "error", exc.status_code, exc.detail


def _quiet(app: FastAPI) -> MagicMock:
    """The executor's starts, recorded and run by no one."""
    executor: MagicMock = app.state.issue_executor
    executor.execute = AsyncMock(return_value="job-started-here")
    executor.schedule_resume = MagicMock()
    executor.cancel_job = AsyncMock()
    return executor


#: Each tool that reads or acts on one item, called on the uncovered repository's or on a missing one: its arguments
#: and the item's id (put back as a placeholder to compare).
ITEM_TOOLS: dict[str, tuple[Callable[[bool], Awaitable[Any]], str, str]] = {
    "issue_get": (
        lambda missing: server.issue_get(MISSING if missing else ISSUE[UNCOVERED]),
        ISSUE[UNCOVERED],
        MISSING,
    ),
    "job_inputs": (lambda missing: server.job_inputs(JOB[UNCOVERED] if not missing else "job-none-gizmo"), "", ""),
    "job_answer": (
        lambda missing: server.job_answer(JOB_HERE, MISSING_INPUT if missing else INPUT_HERE, "yes", confirm=True),
        INPUT_HERE,
        MISSING_INPUT,
    ),
    "merge_approval_get": (
        lambda missing: server.merge_approval_get(MISSING_APPROVAL if missing else APPROVAL[UNCOVERED]),
        APPROVAL[UNCOVERED],
        MISSING_APPROVAL,
    ),
    "merge_decide": (
        lambda missing: server.merge_decide(
            MISSING_APPROVAL if missing else APPROVAL[UNCOVERED], "reject", "ada", "chat-1", confirm=True
        ),
        APPROVAL[UNCOVERED],
        MISSING_APPROVAL,
    ),
}


@pytest.mark.parametrize("tool", list(ITEM_TOOLS))
async def test_a_tool_gives_another_repository_s_item_as_one_that_does_not_exist(
    tool: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    run, item, missing_item = ITEM_TOOLS[tool]
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        _quiet(app)
        # The relay's chat, so a decision reaches the approval it names: an unconfigured chat refuses every decision
        # before it looks the approval up, the missing one's included.
        monkeypatch.setattr(app.state.config, "merge_approval_chat_id", "chat-1")
        limited = _use(monkeypatch, app, keys.limited)
        uncovered = await _outcome(run(False))
        missing = await _outcome(run(True))
        await limited.aclose()
        unchanged = await db.fetchrow(
            "SELECT (SELECT status FROM issue_inputs WHERE id = $1) AS question, "
            "(SELECT status FROM merge_approvals WHERE id = $2) AS approval",
            INPUT_HERE,
            APPROVAL[UNCOVERED],
        )
        everyone_client = _use(monkeypatch, app, keys.open)
        everyone = await _outcome(run(False))
        await everyone_client.aclose()
    shown = repr(uncovered).replace(item, "<item>") if item else repr(uncovered)
    expected = repr(missing).replace(missing_item, "<item>") if missing_item else repr(missing)
    reached = repr(everyone).replace(item, "<item>") if item else repr(everyone)
    assert shown == expected, f"{tool}: the limited key gets the other repository's item as a missing one: {shown}"
    assert not (shows_uncovered_issue(shown) or shows_uncovered_approval(shown)), f"{tool}: {shown}"
    assert dict(unchanged or {}) == {"question": "pending", "approval": "pending"}, f"{tool} acted: {unchanged}"
    assert reached != expected, f"{tool}: a key without a list reaches the item, so this measures something: {reached}"


async def test_the_tools_read_the_other_repository_s_items_to_a_key_without_a_list(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the comparisons above hide is there: a key without a list reads each item, so they measure something."""
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        client = _use(monkeypatch, app, keys.open)
        read = {
            "issue_get": await server.issue_get(ISSUE[UNCOVERED]),
            "job_inputs": await server.job_inputs(JOB_HERE),
            "merge_approval_get": await server.merge_approval_get(APPROVAL[UNCOVERED]),
            "merge_approval_list": await server.merge_approval_list(),
            "issue_list": await server.issue_list(),
        }
        await client.aclose()
    quiet = [
        tool
        for tool, body in read.items()
        if not (shows_uncovered_issue(repr(body)) or shows_uncovered_approval(repr(body)))
    ]
    assert quiet == [], f"tools that show a key without a list nothing of {UNCOVERED}: {quiet}"


async def test_the_listing_tools_name_no_issue_or_approval_of_another_repository(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        client = _use(monkeypatch, app, keys.limited)
        listed = await server.issue_list()
        other = await server.issue_list(repo=UNCOVERED)
        nobody = await server.issue_list(repo="nobody/nobody")
        mixed = await server.issue_get(MIXED)
        child = await server.issue_get(CHILD_HERE)
        job = await server.job_get(JOB_HERE)
        questions = await server.job_inputs(JOB_HERE)
        approvals = await server.merge_approval_list()
        await client.aclose()
    shown = {
        name: body
        for name, body in {
            "issue_list": listed,
            "issue_get": mixed,
            "issue_get(child)": child,
            "job_get": job,
            "merge_approval_list": approvals,
        }.items()
        if shows_uncovered_issue(repr(body)) or shows_uncovered_approval(repr(body))
    }
    assert shown == {}, f"the tools that name an issue, its work or an approval of {UNCOVERED}: {shown}"
    assert sorted(i["id"] for i in listed["issues"]) == sorted([ISSUE[COVERED], MIXED]) and listed["total"] == 2, listed
    assert other == nobody and other["total"] == 0, (
        f"another repository's issues list as a repository's with none: {other}"
    )
    assert (mixed["children"], mixed["jobs"], mixed["children_count"], mixed["jobs_count"]) == ([], [], 0, 0), mixed
    assert job.get("issue_id") is None and questions == [], f"the key's job names no hidden issue: {job} {questions}"
    assert [a["id"] for a in approvals["items"]] == [APPROVAL[COVERED]], approvals


async def _api_key(db: DatabasePool, name: str, scope: str, allowed: list[str]) -> str:
    _row, token = await ApiKeysRepository(db).create(
        name=name, scope=scope, user_id=None, created_by="tests", allowed_repos=allowed
    )
    bust_api_key_cache()
    return token


async def test_the_dispatch_bot_files_and_follows_its_own_issues(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operate key with a list files an issue on its repository, reads it and its questions back, answers its
    own question, lists its own issues, and is refused another repository's."""
    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, _keys, _values):
        bot = await _api_key(db, "dispatch-bot", "operate", [COVERED])
        executor = _quiet(app)
        client = _use(monkeypatch, app, bot)
        filed = await server.issue_create("Fix the header", repo=COVERED)
        read = await server.issue_get(filed["id"])
        listed = await server.issue_list()
        own = await server.issue_get(ISSUE[COVERED])
        own_questions = await server.job_inputs(JOB[COVERED])
        answered = await server.job_answer(JOB[COVERED], INPUT[COVERED], "the first one", confirm=True)
        elsewhere = await _outcome(server.issue_create("Fix theirs", repo=UNCOVERED))
        await client.aclose()
    assert filed["repo"] == COVERED and filed["job_id"] == "job-started-here", f"the bot files and starts it: {filed}"
    assert executor.execute.await_count == 1, "one job was started"
    assert read["id"] == filed["id"] and read["children"] == [], read
    assert sorted(i["id"] for i in listed["issues"]) == sorted([filed["id"], ISSUE[COVERED], MIXED]), (
        f"the bot lists its own repository's issues, the one it filed included, and no other: {listed}"
    )
    assert listed["total"] == 3, listed
    assert own["id"] == ISSUE[COVERED] and own["jobs"] and own["jobs"][0]["job_id"] == JOB[COVERED], own
    assert [q["id"] for q in own_questions] == [INPUT[COVERED]], own_questions
    assert answered["status"] == "answered", answered
    assert elsewhere[0] == "error" and elsewhere[1] == 403, f"the bot files nothing on {UNCOVERED}: {elsewhere}"


#: The chat key covers this many repositories, as a proposals chat with many repositories does.
CHAT_REPOS = 43


async def test_a_chat_assistant_with_a_key_covering_many_repositories_reads_and_starts_its_own_issues(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A propose-scope key whose list names 43 repositories, the covered one among them, does what a chat assistant
    does every day: reads the proposals and the asks, reads the issue a proposal offers, the issues, their questions
    and the merge approvals, and starts the issue an owner said yes to. It reads all of its repositories' work and
    none of another's."""
    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        whole = [f"initech/service-{n:02d}" for n in range(CHAT_REPOS - 1)]
        for n, repo in enumerate(whole):
            await db.execute("INSERT INTO repos (repo, active) VALUES ($1, true)", repo)
            await db.execute(
                "INSERT INTO issues (id, title, repo, status) VALUES ($1, 'Theirs too', $2, 'open')",
                f"iss-initech-{n:02d}",
                repo,
            )
        for fresh, repo in (("iss-fresh-widget", COVERED), ("iss-fresh-gizmo", UNCOVERED)):
            await db.execute(
                "INSERT INTO issues (id, title, repo, status) VALUES ($1, 'Fresh', $2, 'open')", fresh, repo
            )
        covered = sorted([*whole, COVERED])
        assert len(covered) == CHAT_REPOS
        chat = await _api_key(db, "proposals-chat", "propose", covered)
        executor = _quiet(app)
        client = _use(monkeypatch, app, chat)
        proposals = await server.proposal_list()
        offered = await server.proposal_get("issue:iss-fresh-widget")
        issue = await server.issue_get("iss-fresh-widget")
        issues = await server.issue_list(limit=200)
        own = await server.issue_get(ISSUE[COVERED])
        approvals = await server.merge_approval_list()
        started = await server.proposal_accept("issue:iss-fresh-widget", by="ada", confirm=True)
        refused = await _outcome(server.proposal_accept("issue:iss-fresh-gizmo", by="ada", confirm=True))
        await client.aclose()
        asks = {path: await call(app, "GET", path, chat) for path in ("/api/v1/asks", "/api/v1/asks/due?peek=true")}
        everyone = await call(app, "GET", "/api/v1/issues?limit=200&parent_issue_id=any", keys.open)
    reads = {"proposals": proposals, "offered": offered, "issue": issue, "issues": issues, "own": own}
    reads |= {"approvals": approvals, **{path: r.json() for path, r in asks.items()}}
    assert all(r.status_code == 200 for r in asks.values()), {p: r.text[:200] for p, r in asks.items()}
    shown = {
        name: repr(body)[:300]
        for name, body in reads.items()
        if shows_uncovered_issue(repr(body)) or shows_uncovered_approval(repr(body)) or UNCOVERED in repr(body)
    }
    assert shown == {}, f"the chat key reads nothing of {UNCOVERED}: {shown}"
    expected = sorted(
        i["id"] for i in everyone.json()["issues"] if i["repo"] in covered and i["parent_issue_id"] is None
    )
    assert sorted(i["id"] for i in issues["issues"]) == expected and issues["total"] == len(expected), (
        f"the chat key lists every issue of its {CHAT_REPOS} repositories and no other: {issues['total']}"
    )
    assert "issue:iss-fresh-widget" in [p["id"] for p in proposals["proposals"]], proposals
    assert issue["id"] == "iss-fresh-widget" and own["id"] == ISSUE[COVERED], (issue, own)
    assert started["job_id"] == "job-started-here" and executor.execute.await_count == 1, started
    assert refused[0] == "error" and refused[1] == 404, f"another repository's proposal is not there: {refused}"
    assert MIXED_HIDDEN not in repr(issues), issues
