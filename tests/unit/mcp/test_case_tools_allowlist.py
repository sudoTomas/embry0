"""The MCP case tools read and act only within the caller's repository allowlist.

The MCP server reaches embry0 through its REST routes with the caller's own
key (``embry0.mcp_server.client``), so its tools hold to the routes' rule:
each tool runs here against the real app over a real database
(:mod:`tests.fixtures.case_reads`). Through a key whose list covers one
repository, ``case_get``, ``case_accept`` and ``case_close`` on another
repository's case, or on one filed on an owner or on no repository, give
exactly what they give for a case that does not exist and change nothing;
``case_list`` lists and counts the key's own cases alone; and a case the key
reads names nothing of another repository. A chat bot with such a key reads
what waits for a decision in its repository, and accepts and closes its own
findings.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from embry0.api.deps import bust_api_key_cache
from embry0.mcp_server import server
from embry0.mcp_server.client import Embry0APIError, Embry0Client
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.case_reads import (
    CASE,
    CROSSING,
    HIDDEN_CASES,
    NO_REPOSITORY,
    OWNER_CASE,
    STARTED,
    UNNAMED_MERGE,
    case_reads_app,
    missing_like,
    shows_crossing,
    shows_hidden_case,
)
from tests.fixtures.repository_writes import COVERED, OWNER, UNCOVERED

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


#: Each tool that reads or acts on one case, given the case's id.
ITEM_TOOLS: dict[str, Callable[[str], Awaitable[Any]]] = {
    "case_get": server.case_get,
    "case_accept": lambda case_id: server.case_accept(case_id, "ada", confirm=True),
    "case_close": lambda case_id: server.case_close(case_id, "ada", "abandoned", confirm=True),
}


async def _cases_state(db: DatabasePool) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in await db.fetch("SELECT id, stage, updated_at FROM healing_cases ORDER BY id")]


@pytest.mark.parametrize("hidden", HIDDEN_CASES)
@pytest.mark.parametrize("tool", list(ITEM_TOOLS))
async def test_a_tool_gives_a_case_the_key_does_not_read_as_one_that_does_not_exist(
    tool: str, hidden: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    run, missing_id = ITEM_TOOLS[tool], missing_like(hidden)
    async with case_reads_app(db, monkeypatch) as (app, keys, _values):
        before = await _cases_state(db)
        limited = _use(monkeypatch, app, keys.limited)
        uncovered = await _outcome(run(hidden))
        missing = await _outcome(run(missing_id))
        await limited.aclose()
        after = await _cases_state(db)
        everyone_client = _use(monkeypatch, app, keys.open)
        everyone = await _outcome(run(hidden))
        await everyone_client.aclose()
    shown = repr(uncovered).replace(hidden, "<case>")
    expected = repr(missing).replace(missing_id, "<case>")
    reached = repr(everyone).replace(hidden, "<case>")
    assert shown == expected, f"{tool} {hidden}: the limited key gets the case as a missing one: {shown}"
    assert uncovered[:2] == ("error", 404), uncovered
    assert after == before, f"{tool} {hidden}: the limited key's call changed nothing"
    assert reached != expected, f"{tool} {hidden}: a key without a list reaches the case, so this measures something"


async def test_the_listing_tool_lists_only_the_key_s_cases(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        client = _use(monkeypatch, app, keys.limited)
        listed = await server.case_list(limit=200)
        waiting = await server.case_list(awaiting_decision=True)
        needs = await server.case_list(origin="org")
        other = await server.case_list(repo=UNCOVERED)
        nobody = await server.case_list(repo="nobody/nobody")
        crossing = await server.case_get(CROSSING)
        await client.aclose()
        everyone_client = _use(monkeypatch, app, keys.open)
        everyone = await server.case_list(limit=200)
        everyone_crossing = await server.case_get(CROSSING)
        await everyone_client.aclose()
    own = sorted([CASE[COVERED], CROSSING, UNNAMED_MERGE])
    assert sorted(c["id"] for c in listed["cases"]) == own and listed["total"] == len(own), (
        f"the limited key lists its own cases alone: {listed}"
    )
    assert [c["id"] for c in waiting["cases"]] == [CASE[COVERED]] and waiting["total"] == 1, waiting
    assert needs["cases"] == [] and needs["total"] == 0, f"no case filed on an owner: {needs}"
    assert other == nobody and other["total"] == 0, (
        f"another repository's cases list as a repository's with none: {other}"
    )
    assert not shows_crossing(json.dumps(crossing)), f"the key's case names nothing of {UNCOVERED}: {crossing}"
    assert not shows_hidden_case(json.dumps(listed)), listed
    assert {*HIDDEN_CASES, CROSSING} <= {c["id"] for c in everyone["cases"]}, "a key without a list lists every case"
    assert shows_crossing(json.dumps(everyone_crossing)), "a key without a list reads the crossing case whole"


async def test_a_chat_bot_reads_and_answers_its_own_findings(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operate key with a list does what a chat bot does with findings: reads what waits for a decision, reads one,
    accepts its own (the retry its issue waits for) and closes another; another repository's, an owner's and one of
    no repository are not there for it."""
    db = db_with_migrations
    async with case_reads_app(db, monkeypatch) as (app, _keys, _values):
        _row, bot = await ApiKeysRepository(db).create(
            name="chat-bot", scope="operate", user_id=None, created_by="tests", allowed_repos=[COVERED]
        )
        bust_api_key_cache()
        client = _use(monkeypatch, app, bot)
        waiting = await server.case_list(awaiting_decision=True)
        read = await server.case_get(CASE[COVERED])
        accepted = await server.case_accept(CASE[COVERED], "ada", confirm=True)
        closed = await server.case_close(CROSSING, "ada", "out_of_band", confirm=True)
        elsewhere = {
            case_id: await _outcome(server.case_accept(case_id, "ada", confirm=True))
            for case_id in (CASE[UNCOVERED], OWNER_CASE[OWNER[COVERED]], NO_REPOSITORY)
        }
        await client.aclose()
    assert [c["id"] for c in waiting["cases"]] == [CASE[COVERED]], waiting
    assert read["id"] == CASE[COVERED] and read["repo"] == COVERED, read
    assert accepted["job_id"] == STARTED and accepted["status"] == "dispatched", accepted
    assert closed["stage"] == "closed", closed
    assert {case_id: answer[:2] for case_id, answer in elsewhere.items()} == {
        case_id: ("error", 404) for case_id in elsewhere
    }, elsewhere
