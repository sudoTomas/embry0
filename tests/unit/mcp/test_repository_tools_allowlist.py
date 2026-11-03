"""The MCP repository and organization tools read only within the caller's repository allowlist.

The MCP server reaches embry0 through its REST routes with the caller's own
key (``embry0.mcp_server.client``), so its tools hold to the routes' rule:
each tool runs here against the real app over a real database
(:mod:`tests.fixtures.repository_reads`). Through a key whose list covers
``acme/web``, a tool reading ``globex/gizmos``, or its sibling ``acme/api``,
gives exactly what it gives for a repository nobody registered; a list names
neither; the key's own repository reads as before. A dispatch bot, an operate
key with a list, reads its own repository and starts work on it, and a chat
key covering many repositories reads what it reads every day.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from embry0.api.deps import bust_api_key_cache
from embry0.mcp_server import server
from embry0.mcp_server.client import Embry0APIError, Embry0Client
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.repository_reads import (
    HOST,
    MARK,
    MISSING,
    MISSING_OWNER,
    OWNER_MARK,
    OWNERS,
    SIBLING,
    repository_reads_app,
    shows_hidden,
)
from tests.fixtures.repository_writes import COVERED, UNCOVERED, call

pytestmark = pytest.mark.requires_postgres

HIDDEN = (UNCOVERED, SIBLING)


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


#: Each MCP tool that reads one repository, or its owner, called on a repository's slug.
REPO_TOOLS: dict[str, Callable[[str], Awaitable[Any]]] = {
    "repo_get": lambda repo: server.repo_get(repo),
    "qa_config_get": lambda repo: server.qa_config_get(repo),
    "env_list": lambda repo: server.env_list(repo=repo),
    "credential_resolve": lambda repo: server.credential_resolve(repo),
    "settings_get(repo)": lambda repo: server.settings_get(repo=repo),
    "settings_effective(repo)": lambda repo: server.settings_effective(repo=repo),
    "settings_revisions(repo)": lambda repo: server.settings_revisions(repo=repo),
    "org_get": lambda repo: server.org_get(OWNERS[repo] if repo in OWNERS else repo.split("/", 1)[0]),
    "org_needs": lambda repo: server.org_needs(OWNERS[repo] if repo in OWNERS else repo.split("/", 1)[0]),
    "context_audit_status": lambda repo: server.context_audit_status(repo.split("/", 1)[0]),
    "standard_get": lambda repo: server.standard_get(repo.split("/", 1)[0], "design"),
    "settings_get(owner)": lambda repo: server.settings_get(owner=repo.split("/", 1)[0]),
}


def _missing(repo: str) -> str:
    """The repository nobody registered that stands for ``repo``: under an owner nobody registered when the limited
    key covers nothing under ``repo``'s owner."""
    return MISSING[repo]


def _placed(outcome: Any, repo: str, missing: bool) -> str:
    text = repr(outcome)
    if missing:
        text = text.replace(MISSING[repo], "<repo>")
        if repo == UNCOVERED:
            text = text.replace(MISSING_OWNER[OWNERS[repo]], "<owner>")
        return text
    text = text.replace(repo, "<repo>")
    return text.replace(OWNERS[repo], "<owner>") if repo == UNCOVERED else text


@pytest.mark.parametrize("repo", HIDDEN)
@pytest.mark.parametrize("tool", list(REPO_TOOLS))
async def test_a_repository_tool_gives_another_repository_as_one_nobody_registered(
    tool: str, repo: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        limited = _use(monkeypatch, app, keys.limited)
        hidden = await _outcome(REPO_TOOLS[tool](repo))
        missing = await _outcome(REPO_TOOLS[tool](_missing(repo)))
        own = await _outcome(REPO_TOOLS[tool](COVERED))
        await limited.aclose()
        everyone_client = _use(monkeypatch, app, keys.open)
        everyone = await _outcome(REPO_TOOLS[tool](repo))
        await everyone_client.aclose()
    owner_wide = tool.startswith(("org_", "context_audit", "standard_get", "settings_get(owner)"))
    if owner_wide and repo == SIBLING:
        # acme, which the key covers in part: what belongs to it as a whole reads as an owner nobody registered, as
        # its own repository's owner does too.
        assert hidden == own and hidden[0] == "error" and hidden[1] == 404, f"{tool}: {hidden} {own}"
        return
    assert _placed(hidden, repo, missing=False) == _placed(missing, repo, missing=True), (
        f"{tool}: the limited key gets {repo} as a repository nobody registered: {hidden} against {missing}"
    )
    assert not shows_hidden(repr(hidden).replace(repo, "")), f"{tool}: it shows nothing of {repo}: {hidden}"
    assert everyone[0] == "ok", f"{tool}: a key without a list reads {repo}: {everyone}"
    if not owner_wide:
        assert own[0] == "ok", f"{tool}: the limited key reads its own repository: {own}"


async def test_the_listing_tools_name_no_repository_or_owner_the_key_does_not_cover(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """repo_list, org_list, credential_list, host_list and host_get name only what the key covers; to a key without
    a list they name the hidden repositories and owners, so the check measures something."""
    tools: dict[str, Callable[[], Awaitable[Any]]] = {
        "repo_list": lambda: server.repo_list(include_inactive=True),
        "org_list": lambda: server.org_list(include_removed=True),
        "credential_list": lambda: server.credential_list(),
        "host_list": lambda: server.host_list(include_removed=True),
        "host_get": lambda: server.host_get(HOST),
    }
    async with repository_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        limited_client = _use(monkeypatch, app, keys.limited)
        limited = {name: await tool() for name, tool in tools.items()}
        await limited_client.aclose()
        everyone_client = _use(monkeypatch, app, keys.open)
        everyone = {name: await tool() for name, tool in tools.items()}
        await everyone_client.aclose()
    shown = {name: body for name, body in limited.items() if shows_hidden(repr(body))}
    assert shown == {}, f"the listing tools name to the limited key what it does not cover: {shown}"
    quiet = [name for name, body in everyone.items() if not shows_hidden(repr(body))]
    assert quiet == [], f"the listing tools name the hidden ones to a key without a list: {quiet}"
    assert [r["repo"] for r in limited["repo_list"]] == [COVERED], limited["repo_list"]
    assert limited["org_list"]["organizations"] == [], limited["org_list"]
    assert [link["repo"] for link in limited["host_get"]["repos"]] == [COVERED], limited["host_get"]
    assert any(OWNER_MARK["globex"] in repr(o) for o in everyone["org_list"]["organizations"]), everyone["org_list"]


async def _api_key(db: DatabasePool, name: str, scope: str, allowed: list[str]) -> str:
    _row, token = await ApiKeysRepository(db).create(
        name=name, scope=scope, user_id=None, created_by="tests", allowed_repos=allowed
    )
    bust_api_key_cache()
    return token


async def test_the_dispatch_bot_reads_its_own_repository_and_starts_work_on_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operate key with a list, the shape a dispatch bot runs with, reads its own repository (its view, test
    setup, variables, credentials and checklist), lists it alone, and starts a job on it, by repo and by git
    context; it reads another repository as one nobody registered."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, _keys, _values):
        bot = await _api_key(db, "dispatch-bot", "operate", [COVERED])
        submitted = MagicMock()
        monkeypatch.setattr(app.state.issue_executor, "submit_job", submitted)
        client = _use(monkeypatch, app, bot)
        view = await server.repo_get(COVERED)
        setup = await server.qa_config_get(COVERED)
        variables = await server.env_list(repo=COVERED)
        listed = await server.repo_list()
        resolved = await server.credential_resolve(COVERED)
        started = [
            (await server.job_create(task="Fix the header", repo=COVERED))["job_id"],
            (await server.job_create(task="Fix the footer", context={"type": "git", "repo": COVERED}))["job_id"],
        ]
        other = await _outcome(server.repo_get(UNCOVERED))
        nobody = await _outcome(server.repo_get(MISSING[UNCOVERED]))
        await client.aclose()
        readiness = await call(app, "GET", f"/api/v1/repos/{COVERED}/-/readiness", bot)
    assert view["repo"] == COVERED and view["notes"] == MARK[COVERED], f"the bot reads its repository: {view}"
    assert MARK[COVERED] in setup["yaml_text"], f"the bot reads its test setup: {setup}"
    assert {v["key"] for v in variables["variables"]} >= {"API_URL", "API_TOKEN"}, f"and its variables: {variables}"
    assert [r["repo"] for r in listed] == [COVERED], f"the bot lists its own repository and no other: {listed}"
    assert resolved["label"] == f"{MARK[COVERED]}-forge", f"the bot resolves its repository's credential: {resolved}"
    assert readiness.status_code == 200, f"the bot reads its repository's checklist: {readiness.text[:300]}"
    assert submitted.call_count == 2 and all(started), f"the bot starts work on its repository: {started}"
    assert repr(other).replace(UNCOVERED, "<r>") == repr(nobody).replace(MISSING[UNCOVERED], "<r>"), (
        f"the bot reads another repository as one nobody registered: {other} against {nobody}"
    )


#: The chat key covers this many repositories, as the deployment's proposals chat does.
CHAT_REPOS = 43


async def test_a_chat_key_covering_many_repositories_reads_what_it_reads_every_day(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chat key whose list names 43 repositories across two owners, every repository registered under the one and
    some of the other's, reads the repositories, the decisions, the proposals, the asks and their feed, the
    hand-offs and the leads of exactly those, and the organization it covers as a whole, as a key without a list
    reads them narrowed to its list."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        whole = [f"initech/service-{n:02d}" for n in range(CHAT_REPOS - 2)]
        for repo in whole:
            await db.execute("INSERT INTO repos (repo, active) VALUES ($1, true)", repo)
        await db.execute("INSERT INTO organizations (owner, forge) VALUES ('initech', 'github')")
        covered = sorted([*whole, COVERED, SIBLING])
        assert len(covered) == CHAT_REPOS
        chat = await _api_key(db, "proposals-chat", "operate", covered)
        reads = {
            path: (await call(app, "GET", path, chat), await call(app, "GET", path, keys.open))
            for path in (
                "/api/v1/repos",
                "/api/v1/orgs",
                "/api/v1/orgs/initech",
                "/api/v1/decisions",
                "/api/v1/proposals",
                "/api/v1/asks",
                "/api/v1/asks/due?peek=true",
                "/api/v1/handoffs",
                "/api/v1/leads",
                "/api/v1/setup/proposals",
                "/api/v1/maps",
            )
        }
    failed = {path: (mine.status_code, mine.text[:200]) for path, (mine, _) in reads.items() if mine.status_code != 200}
    assert failed == {}, f"the chat key reads what it reads every day: {failed}"
    listed = sorted(r["repo"] for r in reads["/api/v1/repos"][0].json())
    assert listed == covered, f"the chat key lists its {CHAT_REPOS} repositories and no other: {listed}"
    assert sorted(o["owner"] for o in reads["/api/v1/orgs"][0].json()["organizations"]) == ["acme", "initech"], (
        "the chat key covers acme and initech as a whole: every repository registered under each is on its list"
    )
    shown = {path: mine.text[:200] for path, (mine, _) in reads.items() if UNCOVERED in mine.text}
    assert shown == {}, f"the chat key reads nothing of {UNCOVERED}: {shown}"
    everyone_repos = sorted(r["repo"] for r in reads["/api/v1/repos"][1].json() if r["repo"] in covered)
    assert everyone_repos == covered, "a key without a list reads the same repositories, and more"


@pytest.mark.parametrize("repo", HIDDEN)
async def test_settings_undo_gives_a_change_of_another_repository_s_layer_as_one_that_does_not_exist(
    repo: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``settings_undo`` reaches ``POST /config/settings/revisions/{id}/undo`` with the caller's key: a change of
    another repository's layer gives exactly what a change id that names nothing gives, naming no repository."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        hidden_id = int(
            await db.fetchval(
                "SELECT min(id) FROM config_revisions WHERE surface = 'settings' AND scope = $1", f"repository:{repo}"
            )
        )
        missing_id = int(await db.fetchval("SELECT max(id) FROM config_revisions")) + 1000
        client = _use(monkeypatch, app, keys.limited)
        hidden = await _outcome(server.settings_undo(hidden_id, confirm=True))
        missing = await _outcome(server.settings_undo(missing_id, confirm=True))
        await client.aclose()
    assert hidden == missing and hidden[0] == "error", (
        f"settings_undo gives {repo}'s change as a missing one: {hidden} against {missing}"
    )
    assert repo not in repr(hidden), f"it names no repository: {hidden}"
