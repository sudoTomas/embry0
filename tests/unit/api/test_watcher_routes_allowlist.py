"""The watcher's routes answer a key with a repository allowlist as a deployment watching only the stacks it covers.

A stack the key does not cover, watched or not, answers it exactly as a stack
nobody watches, status, headers and body, with each name put back as a
placeholder: the key cannot tell which stacks exist beyond its list. An
action on such a stack (ticking, changing or removing it) is still refused on
record, logged and audited, whether or not the stack exists; a read is not.
Ticking with no stack named ticks the stacks the key covers, as a deployment
watching only those would. Keys without an allowlist, the master key and
sessions are answered as before.

The app is :mod:`tests.fixtures.repository_writes`' (the real app over a real
database, a watched stack seeded for each repository), with a stand-in
watcher per stack that records its ticks.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.watcher_runs import WatcherRunsRepository
from tests.fixtures.repository_writes import COVERED, MASTER, NOW, OWNER, UNCOVERED, call, repository_writes_app
from tests.fixtures.routes import app_routes

pytestmark = pytest.mark.requires_postgres

#: A stack nobody watches, under the uncovered repository's owner.
MISSING = f"{OWNER[UNCOVERED]}/nothing"
REFUSED = "apikey.repo_refused"


class _Watcher:
    """A stack's watcher that ticks without a log store: it records the tick and answers what it did."""

    def __init__(self, repo: str, ticks: list[str]) -> None:
        self.repo = repo
        self._ticks = ticks

    async def run_once(self, _run_with: Any = None) -> dict[str, Any]:
        self._ticks.append(self.repo)
        return {"action": "no_issue", "detail": f"ticked {self.repo}"}


def _watch(app: FastAPI, stacks: tuple[str, ...]) -> list[str]:
    """Watch ``stacks``, as the orchestrator's lifespan does (one watcher each, the first the back-compat one)."""
    ticks: list[str] = []
    app.state.watcher_services = {repo: _Watcher(repo, ticks) for repo in stacks}
    app.state.watcher_service = next(iter(app.state.watcher_services.values()), None)
    return ticks


def _answer(response: httpx.Response, stack: str) -> tuple[int, str | None, str]:
    """What the caller reads, with the stack's name and its owner put back as placeholders."""
    owner, name = stack.split("/", 1)
    text = response.text.replace(stack, "<stack>").replace(owner, "<owner>").replace(name, "<name>")
    return response.status_code, response.headers.get("content-type"), text


async def _refusals(db: DatabasePool) -> list[tuple[str, str]]:
    rows = await db.fetch(
        "SELECT details->>'repo' AS repo, details->>'action' AS action FROM audit_log WHERE action = $1 ORDER BY id",
        REFUSED,
    )
    return [(str(r["repo"]), str(r["action"])) for r in rows]


async def _seed_ticks(db: DatabasePool) -> None:
    runs = WatcherRunsRepository(db)
    for repo in (COVERED, UNCOVERED):
        await runs.create(NOW - timedelta(minutes=5), NOW, "no_issue", log_lines=3, detail=f"{repo} tick", repo=repo)


#: Every watcher route addressed by a stack, with the body it is called with: a stack the key does not cover must
#: answer as one nobody watches. ``GET /watcher/stacks`` names none and is read below.
ADDRESSED: list[tuple[str, str, Any]] = [
    ("GET", "/api/v1/watcher/stacks/{stack}", None),
    ("GET", "/api/v1/watcher/runs?stack={stack}", None),
    ("POST", "/api/v1/watcher/run?stack={stack}", None),
    ("PUT", "/api/v1/watcher/stacks/{stack}", {"logql": '{job="probe"}'}),
    ("DELETE", "/api/v1/watcher/stacks/{stack}", None),
]
#: The routes that act on a stack, whose refusal is logged and audited.
ACTIONS = {"POST", "PUT", "DELETE"}
#: Watcher routes that name no stack, each read below.
UNADDRESSED = {("GET", "/api/v1/watcher/stacks")}


def test_every_watcher_route_is_called_here() -> None:
    """A new watcher route fails until it is placed: addressed by a stack, or read as a list."""
    from embry0.api.app import create_app
    from embry0.config import Embry0Config

    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    found = {
        (method, route.path)
        for route in app_routes(app)
        if route.is_api_route and route.path.startswith("/api/v1/watcher")
        for method in (route.methods or set()) - {"HEAD"}
    }
    placed = {(m, t.split("?")[0].replace("{stack}", "{owner}/{name}")) for m, t, _ in ADDRESSED} | UNADDRESSED
    assert found == placed, f"watcher routes not placed here: {sorted(found - placed)}; gone: {sorted(placed - found)}"


async def test_a_stack_the_key_does_not_cover_answers_as_one_nobody_watches(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        app.state.watcher_runs_repo = WatcherRunsRepository(db)
        await _seed_ticks(db)
        ticks = _watch(app, (COVERED, UNCOVERED))
        differ: dict[str, Any] = {}
        unaudited: dict[str, Any] = {}
        for method, template, body in ADDRESSED:
            before = await _refusals(db)
            answers = []
            for stack in (UNCOVERED, MISSING):
                response = await call(app, method, template.format(stack=stack), keys.limited, body)
                answers.append(_answer(response, stack))
            if answers[0] != answers[1]:
                differ[f"{method} {template}"] = answers
            added = (await _refusals(db))[len(before) :]
            if [repo for repo, _action in added] != ([UNCOVERED, MISSING] if method in ACTIONS else []):
                unaudited[f"{method} {template}"] = added
        assert not differ, f"the limited key reads a stack it does not cover apart from a missing one: {differ}"
        assert not unaudited, f"each action, and no read, on a stack the key does not cover is audited: {unaudited}"
        assert ticks == [], f"a stack the key does not cover ticked: {ticks}"
        listed = await call(app, "GET", "/api/v1/watcher/stacks", keys.limited)
        assert listed.status_code == 200 and UNCOVERED not in listed.text, f"the list names it: {listed.text}"
        stacks = [s["repo"] for s in listed.json()["stacks"]]
        assert stacks == [COVERED], f"the limited key lists its own stack alone: {stacks}"
        # What they answer: a missing stack's 404, and for an action the refusal it is given anywhere.
        ran = await call(app, "POST", f"/api/v1/watcher/run?stack={UNCOVERED}", keys.limited)
        assert (ran.status_code, ran.json()) == (404, {"detail": f"{UNCOVERED} is not a watched stack"}), ran.text


async def test_a_key_without_a_list_ticks_any_stack_and_the_key_its_own(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        ticks = _watch(app, (COVERED, UNCOVERED))
        for token, stack in ((keys.open, UNCOVERED), (MASTER, UNCOVERED), (keys.limited, COVERED)):
            ran = await call(app, "POST", f"/api/v1/watcher/run?stack={stack}", token)
            assert ran.status_code == 200 and ran.json()["detail"] == f"ticked {stack}", ran.text
        assert ticks == [UNCOVERED, UNCOVERED, COVERED], ticks
        assert await _refusals(db_with_migrations) == [], "nothing was refused"


async def test_an_unnamed_tick_ticks_the_stacks_the_key_covers_as_a_deployment_watching_only_those(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        _watch(app, (COVERED,))
        alone = await call(app, "POST", "/api/v1/watcher/run", keys.open)
        assert alone.status_code == 200, alone.text
        ticks = _watch(app, (COVERED, UNCOVERED))
        limited = await call(app, "POST", "/api/v1/watcher/run", keys.limited)
        assert _answer(limited, UNCOVERED) == _answer(alone, UNCOVERED), (
            f"the limited key's unnamed tick answers as a deployment watching only {COVERED}: "
            f"{limited.status_code} {limited.text} against {alone.text}"
        )
        assert ticks == [COVERED], f"the limited key's unnamed tick ticked: {ticks}"
        every = await call(app, "POST", "/api/v1/watcher/run", keys.open)
        assert [s["stack"] for s in every.json()["stacks"]] == sorted((COVERED, UNCOVERED)), every.text
        assert await _refusals(db_with_migrations) == [], "nothing was refused"


async def test_a_key_covering_no_watched_stack_is_answered_as_a_deployment_watching_none(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        calls = ("/api/v1/watcher/run", f"/api/v1/watcher/run?stack={COVERED}", f"/api/v1/watcher/run?stack={MISSING}")
        _watch(app, ())
        none = [await call(app, "POST", url, keys.open) for url in calls]
        assert {r.status_code for r in none} == {503}, [r.text for r in none]
        ticks = _watch(app, (UNCOVERED,))
        urls = (*calls, f"/api/v1/watcher/run?stack={UNCOVERED}")
        limited = [await call(app, "POST", url, keys.limited) for url in urls]
        expected = [*none, none[-1]]
        shown = [(r.status_code, r.headers.get("content-type"), r.content) for r in limited]
        assert shown == [(r.status_code, r.headers.get("content-type"), r.content) for r in expected], (
            f"a key covering no watched stack is answered as a deployment watching none: {shown}"
        )
        assert ticks == [], f"the only watched stack ticked for a key that does not cover it: {ticks}"
        assert await _refusals(db) == [(MISSING, "run the watcher"), (UNCOVERED, "run the watcher")], (
            "the stacks it does not cover are refused on record, and its own and the unnamed tick are not"
        )


async def test_the_key_reads_back_none_of_its_refusals(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        _watch(app, (COVERED, UNCOVERED))
        await call(app, "POST", f"/api/v1/watcher/run?stack={UNCOVERED}", keys.limited)
        assert await _refusals(db_with_migrations) == [(UNCOVERED, "run the watcher")]
        audit = await call(app, "GET", "/api/v1/audit?limit=200", keys.limited)
        assert audit.status_code == 200 and UNCOVERED not in audit.text, f"the key reads its refusal: {audit.text}"
        admin = await call(app, "GET", f"/api/v1/audit?action={REFUSED}", keys.open)
        assert UNCOVERED in admin.text, f"an admin reads the refusal: {admin.text}"
