"""Every GET route reads, to a key with a repository allowlist, only the repositories it covers.

On the real app over a real database (:mod:`tests.fixtures.repository_reads`),
the limited key covers ``acme/web``; ``globex/gizmos`` is another owner's
repository, and ``acme/api`` a repository of the covered repository's owner
that the key does not cover, so the key covers ``acme`` only in part. Each
holds data of every kind the repository reads serve, and each owner what an
owner's reads serve. Every GET route the app serves under ``/api/v1`` is
read through the limited key and through an admin key without a list, twice:
with each parameter naming ``globex/gizmos`` and its owner, and with each
naming ``acme/api`` (its owner, ``acme``, stays). :data:`QUERIES` adds, for a
route that names a repository or an owner in its query, the queries that
name them.

- A read whose address names the hidden repository (or, for ``globex``, its
  owner) answers the limited key byte for byte as the same read naming a
  repository (an owner) nobody registered: status, headers and body, each
  name put back as a placeholder. Unless :data:`MISSING_DIFFERS` says why,
  that answer is the one a key without a list gets for the missing one too.
- Any other read shows the limited key nothing of a repository it does not
  cover nor of an owner it does not cover as a whole: no slug, nothing
  stored for one (:func:`tests.fixtures.repository_reads.shows_hidden`).
- The routes that show it to the key without a list are exactly
  :data:`READ_ROUTES` and :data:`ELSEWHERE`. A new route that serves a
  repository's data fails until it applies the rule and is listed with how,
  or is placed in :data:`ELSEWHERE` with the stage that narrows it; a listed
  route that no longer serves it fails until it is taken out.
- A route answering nothing but 422 to the walk takes its subject in the
  query: it is in :data:`QUERIES`, or :data:`NOT_A_REPOSITORY_QUERY` says
  why not, so no route is passed over for want of a query.

:data:`NO_ANSWER_HERE` lists the routes that cannot answer in this app; any
other route that does not answer fails the walk.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Request

from embry0.settings.scope import Scope
from embry0.storage.database import DatabasePool
from tests.fixtures.job_reads import APP, JOB, deliverable_id
from tests.fixtures.repository_reads import (
    HOST,
    MARK,
    MISSING,
    MISSING_OWNER,
    OWNER_MARK,
    OWNERS,
    SIBLING,
    repository_reads_app,
    same_answer,
    shows_hidden,
)
from tests.fixtures.repository_writes import COVERED, UNCOVERED, call
from tests.fixtures.routes import app_routes

pytestmark = pytest.mark.requires_postgres

_UNREGISTERED = "another repository reads as one nobody registered"
_OLD_PATH = "the old path of the same handler, which answers the same"
_OWNER_WHOLE = "an owner the key does not cover as a whole reads as one nobody registered"

#: Every GET route that serves a repository's or an owner's data, and how it applies a key's allowlist.
READ_ROUTES: dict[str, str] = {
    # A repository: a repository the key does not cover answers as one nobody registered, nothing stored for it.
    "/api/v1/repos": "lists the repositories the key covers, narrowed in the query",
    "/api/v1/repos/{full_repo:repo}": f"{_UNREGISTERED} (404)",
    "/api/v1/repos/{full_repo:repo}/-": "the same handler",
    "/api/v1/repos/{full_repo:repo}/-/": "the same handler",
    "/api/v1/repos/{full_repo:repo}/-/areas": f"{_UNREGISTERED} (404), no longer 403",
    "/api/v1/repos/{full_repo:repo}/-/credentials": f"{_UNREGISTERED}: nothing bound, nothing resolved",
    "/api/v1/repos/{full_repo:repo}/-/environment": f"{_UNREGISTERED}: no variables",
    "/api/v1/repos/{full_repo:repo}/-/environment/resolve": f"{_UNREGISTERED}: the global variables alone",
    "/api/v1/repos/{full_repo:repo}/-/environment/{key}/reveal": f"{_UNREGISTERED}: no such variable (404)",
    "/api/v1/repos/{full_repo:repo}/-/history": f"{_UNREGISTERED}: no changes",
    "/api/v1/repos/{full_repo:repo}/-/preferences": f"{_UNREGISTERED}: none (null)",
    "/api/v1/repos/{full_repo:repo}/-/qa-config": f"{_UNREGISTERED}: no test setup (404)",
    "/api/v1/repos/{full_repo:repo}/-/readiness": f"{_UNREGISTERED} (404): no setup state, job or pull request",
    "/api/v1/repos/{full_repo:repo}/-/setup/cost": f"{_UNREGISTERED}: no account resolved to pay",
    "/api/v1/repos/{full_repo:repo}/-/setup/proposal": f"{_UNREGISTERED}: no proposal (404), no job",
    "/api/v1/repos/{full_repo:repo}/-/slop-meter": f"{_UNREGISTERED} (404), no longer 403",
    **{
        f"/api/v1/repos/{{full_repo:repo}}/{tail}": _OLD_PATH
        for tail in (
            "areas",
            "credentials",
            "environment",
            "environment/resolve",
            "environment/{key}/reveal",
            "history",
            "preferences",
            "qa-config",
            "readiness",
            "setup/cost",
            "setup/proposal",
            "slop-meter",
        )
    },
    "/api/v1/setup/proposals": "lists the waiting proposals of the repositories the key covers, narrowed in the query",
    "/api/v1/config/context/repos": "lists the agent contexts of the repositories the key covers, narrowed in the query",
    "/api/v1/config/context/repos/{repo:path}": f"{_UNREGISTERED}: the empty context",
    "/api/v1/watcher/stacks": "lists the stacks of the repositories the key covers, narrowed in the query",
    "/api/v1/watcher/stacks/{owner}/{name}": f"{_UNREGISTERED}: no stack (404)",
    "/api/v1/standards/{owner}/{kind}/repos/{repo:path}": f"{_UNREGISTERED} (404)",
    "/api/v1/hosts": "names only the repositories the key covers among each host's",
    "/api/v1/hosts/{host_id}": "names only the repositories the key covers among the host's",
    "/api/v1/credentials/bindings": (
        "a repository the key does not cover, or an owner it does not cover as a whole, has no bindings"
    ),
    "/api/v1/credentials/resolve": "refuses another repository (403) whatever is stored, as GET /run-with/keys does",
    "/api/v1/config/settings": f"a repository's layer: {_UNREGISTERED}; an organization's: {_OWNER_WHOLE} (404)",
    "/api/v1/config/settings/effective": "a layer the key may not read is one nobody wrote",
    "/api/v1/config/settings/layers": "lists the layers the key may read",
    "/api/v1/config/settings/revisions": "a layer the key may not read has no changes",
    "/api/v1/config/settings/revisions/{revision_id}": "a change of a layer the key may not read is not there (404)",
    # An owner: what belongs to it as a whole, to a key that covers it as a whole; its repositories, those covered.
    "/api/v1/orgs": "lists the owners the key covers as a whole",
    "/api/v1/orgs/{owner}": f"{_OWNER_WHOLE} (404); the probe's sample repository only when covered",
    "/api/v1/orgs/{owner}/repos": f"{_OWNER_WHOLE} (404); the facts of the repositories the key covers",
    "/api/v1/orgs/{owner}/runs": f"{_OWNER_WHOLE} (404)",
    "/api/v1/orgs/{owner}/webhook": f"{_OWNER_WHOLE} (404), before the forge is asked",
    "/api/v1/orgs/{owner}/needs": f"{_OWNER_WHOLE} (404)",
    "/api/v1/orgs/{owner}/context-audit": f"{_OWNER_WHOLE} (404); surveys only the repositories the key covers",
    "/api/v1/standards/{owner}": "the owner's standards to a key covering it as a whole; the covered repositories",
    "/api/v1/standards/{owner}/{kind}": f"{_OWNER_WHOLE}: no standard (404)",
    "/api/v1/standards/{owner}/{kind}/versions": f"{_OWNER_WHOLE}: no versions",
    "/api/v1/maps": "lists a map of a repository the key covers, or of none to a key covering its owner as a whole",
    "/api/v1/maps/{slug}": "reads any other map as one that does not exist (404)",
    "/api/v1/maps/{slug}/marks": "reads any other map as one that does not exist (404)",
    "/api/v1/maps/{slug}/parts/{node_id:path}": "reads any other map as one that does not exist (404)",
    "/api/v1/maps/{slug}/work": "reads any other map as one that does not exist (404); sends narrowed since #318",
    # Earlier stages.
    "/api/v1/credentials": "lists the key's own credentials, since #308 and #309",
    "/api/v1/run-with/keys": "refuses another repository (403), since #309",
    "/api/v1/decisions": "lists the decisions of the repositories the key covers, since #297",
    "/api/v1/decisions/{decision_id:path}": "reads another repository's decision as a missing one, since #297",
    "/api/v1/proposals": "lists the proposals of the repositories the key covers, since #297",
    "/api/v1/leads": "lists the leads of the repositories the key covers, and refuses another's ?repo=, since #297",
    "/api/v1/world": "shows the repositories the key covers, since #297",
    "/api/v1/world/characters/{agent_type}": "lists the quests of the repositories the key covers, since #297",
    "/api/v1/world/quests/{job_id}": "reads another repository's job as a missing one, since #297",
    "/api/v1/progression": "names only the repositories the key covers, since #297",
    "/api/v1/jobs": "lists the jobs of the repositories the key covers, since #318",
    "/api/v1/merge-approvals": "lists the approvals of the repositories the key covers, since #318",
    "/api/v1/merge-approvals/{approval_id}": "reads another repository's approval as a missing one, since #318",
    "/api/v1/watcher/runs": "lists the ticks of the stacks the key covers, since #318",
    "/api/v1/qa/repos": "lists the QA runs of the repositories the key covers, since #318",
    **{
        path: "reads another repository's job, or its QA runs, as missing, since #318"
        for path in (
            "/api/v1/jobs/{job_id}",
            "/api/v1/jobs/{job_id}/agents/{agent_type}/session",
            "/api/v1/jobs/{job_id}/deliverables",
            "/api/v1/jobs/{job_id}/deliverables/{deliverable_id}/download",
            "/api/v1/jobs/{job_id}/logs/events",
            "/api/v1/jobs/{job_id}/qa/attempts/{attempt_n}/artifacts/{path:path}",
            "/api/v1/jobs/{job_id}/qa/attempts/{attempt_n}/result",
            "/api/v1/jobs/{job_id}/qa/screenshots/latest",
            "/api/v1/jobs/{job_id}/qa/services",
            "/api/v1/jobs/{job_id}/verification",
            "/api/v1/qa/repos/{repo:path}/apps/{app}/history",
            "/api/v1/qa/repos/{repo:path}/cache/analytics",
            "/api/v1/qa/repos/{repo:path}/flake",
            "/api/v1/qa/repos/{repo:path}/runs",
            "/api/v1/qa/runs/{run_id}",
            "/api/v1/qa/runs/{run_id}/affected_set",
            "/api/v1/qa/runs/{run_id}/apps/{app}",
        )
    },
}

#: GET routes that show another repository's data, each narrowed in a stage of its own, and which.
ELSEWHERE: dict[str, str] = {
    "/api/v1/issues": "the issue reads",
    "/api/v1/issues/{issue_id}": "the issue reads",
    "/api/v1/cases": "the case reads",
    "/api/v1/cases/{case_id}": "the case reads",
    "/api/v1/traces": "the trace reads",
    "/api/v1/stats": "the stats reads",
    "/api/v1/audit": "the audit reads",
}

#: Each route that names a repository or an owner in its query, and the queries that name them: ``{repo}`` and
#: ``{owner}`` are filled as the path parameters are.
QUERIES: dict[str, tuple[str, ...]] = {
    "/api/v1/credentials/bindings": (
        "?scope_kind=repo&scope_value={repo}",
        "?scope_kind=owner&scope_value=github:{owner}",
        "?scope_kind=global",
    ),
    "/api/v1/credentials/resolve": ("?repo={repo}", "?repo={repo}&role=llm&provider=anthropic"),
    "/api/v1/config/settings": ("?repo={repo}", "?owner={owner}"),
    "/api/v1/config/settings/effective": ("?repo={repo}", "?owner={owner}"),
    "/api/v1/config/settings/revisions": ("?repo={repo}", "?owner={owner}"),
    "/api/v1/run-with/keys": ("?repo={repo}",),
    "/api/v1/jobs": ("?repo={repo}",),
    "/api/v1/leads": ("?repo={repo}",),
    "/api/v1/decisions": ("?repo={repo}",),
    "/api/v1/watcher/runs": ("?stack={repo}",),
    "/api/v1/repos": ("?include_inactive=true",),
    "/api/v1/orgs": ("?include_removed=true",),
    "/api/v1/hosts": ("?include_removed=true",),
    "/api/v1/maps/{slug}": ("?owner={owner}",),
    "/api/v1/maps/{slug}/marks": ("?owner={owner}",),
    "/api/v1/maps/{slug}/work": ("?owner={owner}",),
    "/api/v1/maps/{slug}/parts/{node_id:path}": ("?owner={owner}",),
}

#: GET routes that answer nothing but 422 here for want of something other than a query naming a repository, and why.
NOT_A_REPOSITORY_QUERY: dict[str, str] = {
    "/api/v1/asks/of": "its query names a person; what it lists is narrowed to what the key covers, as the feed is",
    "/api/v1/asks/recap": "its query names a time; what it counts is narrowed to what the key covers, as the feed is",
    "/api/v1/orgs/{owner}/webhook": (
        "asks the forge with the organization's credential, which no owner here has; the walk still compares its "
        "answer to an owner the key does not cover as a whole with the missing owner's (404)"
    ),
}

#: Reads whose answer for a missing repository or owner differs between the limited key and a key without a list,
#: and why: the limited key's is the same for every repository it does not cover, registered or not.
MISSING_DIFFERS: dict[str, str] = {
    "/api/v1/repos/{full_repo:repo}/-/credentials": (
        "a key without a list sees what a repository nobody bound would fall back to, its owner's and the "
        "deployment's credentials; nothing is resolved for a repository the key does not cover"
    ),
    "/api/v1/repos/{full_repo:repo}/credentials": "the old path of the same handler",
    "/api/v1/config/settings/effective": (
        "a repository nobody registered under an owner the key covers in part reads that organization's layer, "
        "which the key may not read, as one nobody wrote"
    ),
    "/api/v1/credentials/resolve": "refused (403) whatever is stored, as GET /run-with/keys",
    "/api/v1/run-with/keys": "refused (403) whatever is stored, since #309",
    "/api/v1/leads": "?repo= another repository is refused (403) whatever is stored, since #297",
}

#: GET routes that stream until the client leaves, read elsewhere.
STREAMS: dict[str, str] = {
    "/api/v1/qa/runs/{run_id}/events": "a QA run's events (server-sent events), test_job_streams_allowlist.py",
}

#: GET routes that cannot answer in this app, and why; any other that does not answer fails the walk.
NO_ANSWER_HERE: dict[str, str] = {
    path: "reads a repository's environment template from the forge, whose client retries while no network "
    "answers; test_repository_reads.py reads it for a repository the key does not cover"
    for path in (
        "/api/v1/repos/{full_repo:repo}/-/environment/detect",
        "/api/v1/repos/{full_repo:repo}/environment/detect",
    )
}

_PARAMETER = re.compile(r"\{(\w+)(?::\w+)?\}")
#: Long enough for any route here to answer.
_SECONDS = 10.0


@dataclass(frozen=True)
class Scenario:
    """A repository the limited key does not cover, the parameters that name it, and their missing counterparts."""

    repo: str
    #: Whether its owner is one the limited key does not cover at all: then an owner's address names it too.
    owner_hidden: bool
    values: Mapping[str, list[str]] = field(default_factory=dict)

    @property
    def owner(self) -> str:
        return OWNERS[self.repo]

    def names(self, missing: bool = False) -> dict[str, str]:
        """The names its addresses carry, each to its placeholder; with ``missing``, their missing counterparts'."""
        repo = MISSING[self.repo] if missing else self.repo
        out = {repo: "<repo>"}
        if self.owner_hidden:
            out[MISSING_OWNER[self.owner] if missing else self.owner] = "<owner>"
        return out

    def missing_url(self, url: str) -> str:
        """``url`` naming the missing repository (and owner) instead."""
        out = url.replace(self.repo, MISSING[self.repo])
        if self.owner_hidden:
            out = re.sub(rf"(?<![\w-]){re.escape(self.owner)}(?![\w-])", MISSING_OWNER[self.owner], out)
        return out

    def addresses(self, url: str) -> bool:
        """Whether ``url`` names its repository, or its owner when that owner is hidden as a whole."""
        if self.repo in url:
            return True
        return self.owner_hidden and re.search(rf"(?<![\w-]){re.escape(self.owner)}(?![\w-])", url) is not None


def scenario_values(
    values: Mapping[str, Mapping[str, str]], repo: str, revisions: Mapping[str, str]
) -> dict[str, list[str]]:
    """Each path parameter's values naming ``repo``'s items, or its owner's; the job routes' as the job inventory's."""
    owner = OWNERS[repo]
    return {
        **{name: [value] for name, value in values[UNCOVERED].items()},
        "full_repo": [repo],
        "repo": [repo],
        "owner": [owner],
        "name": [repo.split("/", 1)[1]],
        "slug": [f"map-{MARK[repo]}", f"map-{OWNER_MARK[owner]}"],
        "host_id": [HOST],
        "revision_id": [revisions[repo], revisions[owner]],
        "key": ["API_TOKEN"],
        "kind": ["design"],
        "node_id": ["n1"],
        "job_id": [JOB[UNCOVERED]],
        "run_id": [JOB[UNCOVERED]],
        "decision_id": [f"done:{JOB[UNCOVERED]}"],
        "agent_type": ["developer"],
        "deliverable_id": [deliverable_id(UNCOVERED)],
        "attempt_n": ["1"],
        "path": ["result.json"],
        "service": [APP],
        "app": [APP],
        "filename": ["1-home.png"],
        "version": ["1"],
    }


def urls(path: str, scenario: Scenario) -> list[str]:
    """``path`` with each value of each parameter (``x`` for one that names nothing here), and its queries."""
    out = [path]
    for name in _PARAMETER.findall(path):
        out = [
            _PARAMETER.sub(lambda m, v=value: v if m.group(1) == name else m.group(0), url, count=1)
            for url in out
            for value in scenario.values.get(name, ["x"])
        ]
    queries = [q.format(repo=scenario.repo, owner=scenario.owner) for q in QUERIES.get(path, ())]
    return [*out, *(f"{url}{query}" for url in out for query in queries)]


def get_routes(app: FastAPI) -> list[str]:
    return sorted(
        {
            r.path
            for r in app_routes(app)
            if r.is_api_route and r.path.startswith("/api/v1/") and r.methods and "GET" in r.methods
        }
        - set(STREAMS)
        - set(NO_ANSWER_HERE)
    )


async def _read(app: FastAPI, url: str, token: str) -> httpx.Response:
    try:
        return await asyncio.wait_for(call(app, "GET", url, token), _SECONDS)
    except TimeoutError:
        pytest.fail(f"GET {url} did not answer in {_SECONDS}s: it measures nothing; list it in NO_ANSWER_HERE with why")


def _shows(response: httpx.Response) -> bool:
    return shows_hidden(response.text) or shows_hidden(str(response.headers))


@dataclass
class Walk:
    #: Each route showing a hidden repository's or owner's data, with the first URL that did and its status.
    shown: dict[str, tuple[str, int]] = field(default_factory=dict)
    #: Each addressed read whose missing answer differs between the two keys, with both answers.
    differs: dict[str, tuple[str, Any]] = field(default_factory=dict)
    #: Each route answering nothing but 422 to every read of the walk.
    unprocessable: set[str] = field(default_factory=set)


async def walk(app: FastAPI, scenarios: list[Scenario], token: str, open_token: str) -> Walk:
    """Every GET route read through ``token`` for each scenario: what showed what it may not read."""
    out = Walk()
    for path in get_routes(app):
        statuses: set[int] = set()
        for scenario in scenarios:
            for url in urls(path, scenario):
                response = await _read(app, url, token)
                statuses.add(response.status_code)
                if scenario.addresses(url):
                    missing = await _read(app, scenario.missing_url(url), token)
                    shows = same_answer(response, scenario.names(), missing, scenario.names(missing=True)) is not None
                    if not shows and token != open_token:
                        everyone = await _read(app, scenario.missing_url(url), open_token)
                        differ = same_answer(
                            missing, scenario.names(missing=True), everyone, scenario.names(missing=True)
                        )
                        if differ is not None:
                            out.differs.setdefault(path, (url, differ))
                else:
                    shows = _shows(response)
                if shows:
                    out.shown.setdefault(path, (url, response.status_code))
        if statuses == {422}:
            out.unprocessable.add(path)
    return out


async def _revisions(db: DatabasePool) -> dict[str, str]:
    """The id of the settings revision that wrote each repository's and owner's layer."""
    rows = await db.fetch("SELECT id, scope FROM config_revisions WHERE surface = 'settings' ORDER BY id")
    out: dict[str, str] = {}
    for row in rows:
        layer = Scope.parse(str(row["scope"]))
        if layer.name:
            out.setdefault(layer.name, str(row["id"]))
    return out


async def _scenarios(app: FastAPI, values: Mapping[str, Mapping[str, str]]) -> list[Scenario]:
    revisions = await _revisions(app.state.db)
    return [
        Scenario(UNCOVERED, owner_hidden=True, values=scenario_values(values, UNCOVERED, revisions)),
        Scenario(SIBLING, owner_hidden=False, values=scenario_values(values, SIBLING, revisions)),
    ]


async def test_the_routes_that_serve_a_repository_show_a_key_only_the_repositories_it_covers(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_reads_app(db_with_migrations, monkeypatch) as (app, keys, values):
        served = {r.path for r in app_routes(app) if r.methods and "GET" in r.methods}
        scenarios = await _scenarios(app, values)
        limited = await walk(app, scenarios, keys.limited, keys.open)
        everyone = await walk(app, scenarios, keys.open, keys.open)
    stale = sorted((set(STREAMS) | set(NO_ANSWER_HERE) | set(QUERIES) | set(NOT_A_REPOSITORY_QUERY)) - served)
    assert stale == [], f"listed here but not served: {stale}"
    shown = {path: seen for path, seen in limited.shown.items() if path not in ELSEWHERE}
    assert shown == {}, (
        f"routes that showed a key covering only {COVERED} another repository, or an owner it does not cover as a "
        f"whole: {shown}. A read of a repository the key does not cover answers as one nobody registered "
        "(embry0.api.repository_reads), and a list narrows its query to the repositories the key covers"
    )
    unexplained = {path: seen for path, seen in limited.differs.items() if path not in MISSING_DIFFERS}
    assert unexplained == {}, (
        f"reads whose answer to the limited key for a missing repository is not a key's without a list: {unexplained}"
    )
    unplaced = sorted(set(everyone.shown) - set(READ_ROUTES) - set(ELSEWHERE))
    assert unplaced == [], (
        f"routes that serve a repository's or an owner's data and are not listed: {unplaced}. Apply the key's "
        "allowlist and list each in READ_ROUTES with how, or place it in ELSEWHERE with the stage that narrows it"
    )
    quiet = sorted((set(READ_ROUTES) | set(ELSEWHERE)) - set(everyone.shown))
    assert quiet == [], (
        f"listed routes that did not show a hidden repository's data to a key without an allowlist: {quiet}: the "
        "inventory no longer measures them; seed what they read in tests/fixtures/repository_reads.py, or take them out"
    )
    unqueried = sorted(everyone.unprocessable - set(QUERIES) - set(NOT_A_REPOSITORY_QUERY))
    assert unqueried == [], (
        f"routes that answer nothing but 422 and are not read with a query: {unqueried}. List the "
        "queries that name a repository or an owner in QUERIES, or say why none does in NOT_A_REPOSITORY_QUERY"
    )


async def test_the_inventory_finds_a_new_route_that_serves_a_repository_without_the_rule(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not quiet by construction: a route serving a repository's registration as it is stored is found and reported,
    as is a list of every repository."""
    async with repository_reads_app(db_with_migrations, monkeypatch) as (app, keys, values):
        extra = APIRouter()

        @extra.get("/repos/{full_repo:repo}/-/raw-for-a-test")
        async def raw(full_repo: str, request: Request) -> dict[str, Any]:
            return dict(await request.app.state.repos_repo.get(full_repo) or {})

        @extra.get("/every-repository-for-a-test")
        async def every(request: Request) -> list[dict[str, Any]]:
            return [dict(row) for row in await request.app.state.repos_repo.list_all()]

        app.include_router(extra, prefix="/api/v1")
        found = await walk(app, await _scenarios(app, values), keys.limited, keys.open)
    assert {path for path in found.shown if path not in ELSEWHERE} == {
        "/api/v1/repos/{full_repo:repo}/-/raw-for-a-test",
        "/api/v1/every-repository-for-a-test",
    }, f"the walk reports both new routes and no route in READ_ROUTES: {found.shown}"


#: The routes addressed by a repository that the limited key reads, for its own repository, as a key without a list.
OWN = sorted(
    path
    for path in READ_ROUTES
    if "{full_repo:repo}" in path or path.endswith("{repo:path}") or path.endswith("{owner}/{name}")
)


async def test_a_key_reads_its_own_repository_as_a_key_without_an_allowlist_does(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule takes nothing from the key's own repository: each read addressed by a repository, and each query
    naming one, answers the limited key on the repository it covers byte for byte as a key without a list."""
    async with repository_reads_app(db_with_migrations, monkeypatch) as (app, keys, values):
        own = Scenario(
            COVERED, owner_hidden=False, values=scenario_values(values, COVERED, await _revisions(app.state.db))
        )
        differ: dict[str, Any] = {}
        read = 0
        for path in [*OWN, *(p for p in QUERIES if "{repo}" in " ".join(QUERIES[p]))]:
            for url in urls(path, own):
                if COVERED not in url:
                    continue
                limited, everyone = await _read(app, url, keys.limited), await _read(app, url, keys.open)
                read += 1
                if limited.status_code != everyone.status_code or limited.content != everyone.content:
                    differ[url] = (limited.status_code, limited.text[:200], everyone.status_code, everyone.text[:200])
    assert read > 40, f"the walk read the key's own repository {read} times: it measures little"
    assert differ == {}, f"reads of the key's own repository that differ from a key's without a list: {differ}"
