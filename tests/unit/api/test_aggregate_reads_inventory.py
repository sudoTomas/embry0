"""Every GET route reads, to a key with a repository allowlist, the same whatever another repository holds.

The earlier inventories find a route that shows a key another repository's
item or name. A total names neither: a count, a sum or a list of agents moves
when another repository's work is added, and nothing in the answer says
whose. So this walk compares answers instead of looking for names.

On the real app over a real database (:mod:`tests.fixtures.aggregate_reads`),
the limited key covers :data:`COVERED`. Every GET route under ``/api/v1`` is
read through it and through an admin key without a list, addressed by the
covered repository's items, first while the deployment holds the covered
repository's work alone, then again once :data:`UNCOVERED` holds work of
every kind a total counts (:meth:`Deployment.add_uncovered`). Each answer is
compared field by field (a list's items as one field each, ``[]``).

- **Nothing moves for the limited key.** No field of any answer to it
  differs between the two reads, except the fields :data:`ELSEWHERE` names,
  each with the work that narrows it. A route that adds up another
  repository's work fails until it narrows its query, or is placed there.
- **The walk measures something.** Each route in :data:`TOTALS` answers the
  key without a list differently once the other repository holds work, so
  each of them reads what was added.
- **Over the key's repositories, not less.** Each route in :data:`TOTALS`
  answers the limited key, once both repositories hold work, exactly as it
  answers a key without a list while the deployment holds the covered
  repository's work alone, but for the fields :data:`THE_KEY_S` names, which
  are the key's own by another rule.

An answer is read twice before anything is added: a field that differs between
the two reads, the clock's, is not compared, and is listed in :data:`VOLATILE`
with why. A route that answers a server error measures nothing here: it is
listed in :data:`UNREAD_HERE` with where it is read.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Request

from embry0.storage.database import DatabasePool
from tests.fixtures.aggregate_reads import Deployment, aggregate_reads_app, forget_caches, job_id
from tests.fixtures.job_reads import APP, JOB, deliverable_id
from tests.fixtures.repository_reads import HOST
from tests.fixtures.repository_writes import COVERED, UNCOVERED, call
from tests.unit.api.test_job_reads_inventory import address, get_routes

pytestmark = pytest.mark.requires_postgres

_OLD_PATH = "the old path of the same handler"
_TOTALS_STAGE = "the progression's experience, levels and achievements, folded per day for the deployment"
_LATER = "the levels reached, the rares dropped and the achievements unlocked: " + _TOTALS_STAGE

#: Every GET route that adds up, counts or lists work across repositories, and how it holds to a key's allowlist.
TOTALS: dict[str, str] = {
    "/api/v1/stats": (
        "counts the jobs, their costs and outcomes, the agents' runs and tokens, the issues, the background "
        "features' model calls and the pre-built QA images of the repositories the key covers, in each query"
    ),
    "/api/v1/traces": "lists and counts the runs of the jobs the key reads, narrowed before the page",
    "/api/v1/world": (
        "the characters' quest totals over the key's repositories, and the quest points, cloak trim, quest cloak "
        "and quests among the recent entries from their work (its lists since #297)"
    ),
    "/api/v1/world/characters/{agent_type}": "the character's quest totals and quest log over the key's repositories",
    "/api/v1/progression": "the quests, the quest points and the repo diaries from the key's repositories' work",
    "/api/v1/progression/preview": "the quests, the quest points and the repo diaries, as /progression, under the draft",
    "/api/v1/repos/{full_repo:repo}/-/setup/cost": "the recorded setup runs, and their typical cost, of the key's",
    "/api/v1/repos/{full_repo:repo}/setup/cost": _OLD_PATH,
}

#: The fields of a route in TOTALS that are the key's by another rule than its repositories' work, and which.
THE_KEY_S: dict[str, dict[str, str]] = {
    "/api/v1/stats": {
        "body.credentials": (
            "the credentials the key reads, as GET /credentials lists them, and no environment key "
            "(test_aggregate_reads_narrowed.py)"
        ),
    },
}

#: The experience stage's fields of a character, of the account, and of the recent entries (kept apart by kind).
_CHARACTER = ("xp", "level", "total_level", "best_skill", "skills", "tier", "gear", "capes", "rares")
_EXPERIENCE = {
    "body.account.xp": _TOTALS_STAGE,
    "body.account.total_level": _TOTALS_STAGE,
    **{f"body.characters[].{name}": _TOTALS_STAGE for name in _CHARACTER},
    **{f"body.recent[kind={kind}]": _LATER for kind in ("level", "agent_level", "rare", "achievement")},
    # A character only another repository's work made: the experience a quest it completed rewards.
    "body.characters[]#": _TOTALS_STAGE,
    "body.characters[]order": _TOTALS_STAGE,
    "body.account.characters": _TOTALS_STAGE,
}

#: The fields of a route that are entries of a list cut to its newest 20, which the experience stage's entries share:
#: an entry of the key's may leave or join it as levels are reached and achievements unlocked elsewhere, but an entry in
#: both answers is the same, newest first.
CUT: dict[str, tuple[str, ...]] = {
    path: ("body.recent[kind=quest]", "body.recent[kind=diary]")
    for path in ("/api/v1/progression", "/api/v1/progression/preview", "/api/v1/world")
}

#: GET routes, or the fields of one, that still count every repository's work, and the work that narrows them.
#: ``*`` is the whole answer.
ELSEWHERE: dict[str, dict[str, str]] = {
    "/api/v1/audit": {"*": "every audit row: the audit reads"},
    "/api/v1/progression": {**_EXPERIENCE, "body.achievements": _TOTALS_STAGE},
    "/api/v1/progression/preview": {
        **_EXPERIENCE,
        "body.achievements": _TOTALS_STAGE,
        "body.changes": "the levels and experience before and after the draft: " + _TOTALS_STAGE,
    },
    "/api/v1/world": _EXPERIENCE,
    "/api/v1/world/characters/{agent_type}": {f"body.{name}": _TOTALS_STAGE for name in _CHARACTER},
}

#: Fields that differ between two reads of the same rows, and why: the walk does not compare them.
VOLATILE: dict[str, dict[str, str]] = {
    "/api/v1/progression": {
        "body.computed_at": "when it was computed",
        "body.rollup": "the rollup the background writer is folding",
    },
    "/api/v1/progression/preview": {"body.computed_at": "when it was computed, on every read"},
    "/api/v1/world": {"body.generated_at": "when it was read"},
}

_QA_STORE = "a QA run's artifacts are in the QA store, which only a QA run writes; the stand-in holds the job's"

#: GET routes that answer only errors here to a key without an allowlist, with why: each measures nothing in this walk,
#: and is read where the reason says. Any other route that answers only errors fails the walk.
NOT_ANSWERED: dict[str, str] = {
    "/api/v1/admin/retention/preview": (
        "the retention sweeps, which run in the production lifespan only; not narrowed (docs/api.md says so)"
    ),
    "/api/v1/auth/callback": "the SSO login flow, which this app has not configured",
    "/api/v1/auth/login": "the SSO login flow, which this app has not configured",
    "/api/v1/auth/me": "the caller's SSO session, which an API key has none of",
    "/api/v1/credentials/xai/device-login/{login_id}": "a device login in flight with xAI, which none is here",
    "/api/v1/logs/containers": "the container logs, from Loki, which this app has no client of: another stage",
    "/api/v1/logs/query": "the container logs, from Loki, which this app has no client of: another stage",
    "/api/v1/github/discoverable-repos": "the forge, stood in by test_repository_discovery_allowlist.py: another stage",
    "/api/v1/github/repos": "the forge, stood in by test_repository_discovery_allowlist.py",
    "/api/v1/orgs/{owner}/webhook": "the owner's hook, read from the forge (test_repository_reads_inventory.py)",
    "/api/v1/pipelines/templates/{template_id}": "a stored pipeline template, which no repository's work makes",
    "/api/v1/progression/pack/versions/{version}": "a stored version of the content pack, of which there is a draft only",
    "/api/v1/config/providers/{provider_name}/usage": (
        "what the provider says the deployment's key spent, from the provider, which this app configures none of: "
        "another stage"
    ),
    "/api/v1/sandbox-profiles/{name}": "a stored sandbox profile, which no repository's work makes",
    "/api/v1/skills/sources/{source_id}": "a skill source, which no repository's work makes",
}

#: GET routes another stage holds and this walk does not measure, and why: they read nothing a repository's work makes
#: here, or nothing this app has.
OTHER_STAGES: dict[str, str] = {
    "/api/v1/health/ready": "the watched stacks' health, which this app's watcher does not run: another stage",
}

#: Each route's path parameters that are not the covered repository's item, where one name means two things.
ROUTE_PARAMS: dict[str, dict[str, str]] = {
    "/api/v1/config/providers/{provider_name}/usage": {"provider_name": "openrouter"},
    "/api/v1/hosts/{host_id}": {"host_id": HOST},
}

#: The queries each route is also read with: ``{job}`` is a job of the covered repository, ``{other}`` one that does
#: not exist before the other repository's work is added and is that repository's after.
QUERIES: dict[str, tuple[str, ...]] = {
    "/api/v1/traces": ("?job_id={job}", "?job_id={other}", "?agent_type=developer", "?result=error", "?limit=1"),
    "/api/v1/jobs": ("?repo={other_repo}",),
    "/api/v1/asks/of": ("?person=ada@example.com",),
    "/api/v1/asks/recap": ("?since=2020-01-01T00:00:00Z",),
    "/api/v1/credentials/bindings": ("?scope_kind=repo&scope_value={repo}",),
    "/api/v1/credentials/resolve": ("?repo={repo}",),
}

_SECONDS = 10.0


def named(values: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    """Each path parameter's value naming the covered repository's item: its finished job, with everything stored
    for it (tests.fixtures.job_reads), for a route that reads one job."""
    job = JOB[COVERED]
    return {
        **values[COVERED],
        "job_id": job,
        "run_id": job,
        "decision_id": f"done:{job}",
        "agent_type": "developer",
        "deliverable_id": deliverable_id(COVERED),
        "attempt_n": "1",
        "path": "result.json",
        "service": APP,
        "app": APP,
        "filename": "1-home.png",
        "node_id": "n1",
    }


def urls(path: str, values: Mapping[str, Mapping[str, str]]) -> list[str]:
    url = address(path, {**named(values), **ROUTE_PARAMS.get(path, {})})
    fill = {
        "job": job_id(COVERED, "merged"),
        "other": job_id(UNCOVERED, "merged"),
        "other_repo": UNCOVERED,
        "repo": COVERED,
    }
    return [url, *(url + query.format(**fill) for query in QUERIES.get(path, ()))]


#: What names an item of a list, when every item has one and no two the same: its fields are compared by it.
_IDENTITIES = ("id", "trace_id", "job_id", "agent_type", "repo", "stat", "feature", "key", "code", "name", "owner")


def _places(items: list[Any]) -> tuple[str, list[str]]:
    """How a list's items are told apart: ``[]`` and each one's identity (:data:`_IDENTITIES`), or ``[kind=<kind>]``
    groups and each item's place in its group, or ``[]`` and each item's position."""
    if items and all(isinstance(i, Mapping) for i in items):
        for name in _IDENTITIES:
            keys = [i.get(name) for i in items]
            if all(isinstance(k, str | int) for k in keys) and len(set(keys)) == len(keys):
                return "[]", [str(k) for k in keys]
        if all(isinstance(i.get("kind"), str) for i in items):
            seen: dict[str, int] = {}
            places = []
            for item in items:
                kind = item["kind"]
                places.append(f"{kind}:{seen.setdefault(kind, 0)}")
                seen[kind] += 1
            return "[kind]", places
    return "[]", [str(n) for n in range(len(items))]


def fields(value: Any, at: str = "") -> dict[str, Any]:
    """Every leaf of a JSON value by its path; a list's items each in turn, by their identity, by kind or by
    position (:func:`_places`), with the list's length and order."""
    out: dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key in sorted(value):
            out.update(fields(value[key], f"{at}.{key}" if at else str(key)))
        if not value:
            out[at] = "{}"
    elif isinstance(value, list):
        how, places = _places(value)
        if how == "[kind]":
            for item, place in zip(value, places, strict=True):
                kind, n = place.split(":")
                for path, leaf in fields(item, f"{at}[kind={kind}]").items():
                    out[f"{path}@{n}@"] = leaf
            for kind in {place.split(":")[0] for place in places}:
                out[f"{at}[kind={kind}]#"] = sum(1 for place in places if place.startswith(f"{kind}:"))
        else:
            out[f"{at}[]#"] = len(value)
            out[f"{at}[]order"] = tuple(places)
            for item, place in zip(value, places, strict=True):
                for path, leaf in fields(item, f"{at}[]").items():
                    out[f"{path}@{place}@"] = leaf
    else:
        out[at] = value
    return out


def _general(path: str) -> str:
    """A field's path without the items' identities or positions: ``characters[].xp``, ``recent[kind=quest].at``."""
    return re.sub(r"@[^@]*@", "", path)


def answer(response: httpx.Response) -> dict[str, Any]:
    """The answer's fields: its status, and its body's."""
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    return {"status": response.status_code, **{f"body.{k}" if k else "body": v for k, v in fields(body).items()}}


def _covers(prefixes: Iterable[str], path: str) -> bool:
    """Whether a field is one of ``prefixes``, or within one: ``*`` is every field."""
    return any(p == "*" or path == p or path.startswith((p + ".", p + "[", p + "#")) for p in prefixes)


_ITEM = re.compile(r"^.*?@[^@]*@")


def _item_absent(path: str, other: Mapping[str, Any]) -> bool:
    """Whether the list item ``path`` is a field of is not in the ``other`` answer at all: the list's length and
    order say it came or went, and each of its fields is not compared on its own."""
    item = _ITEM.match(path)
    return item is not None and not any(key.startswith(item.group(0)) for key in other)


def differing(
    a: Mapping[str, Any], b: Mapping[str, Any], volatile: Iterable[str] = (), cut: Iterable[str] = ()
) -> set[str]:
    """The fields, without identities or positions, in which two answers differ, but the ``volatile`` ones, and those
    of an item only one answer has. Within ``cut``, the entries of a list cut to its newest, an entry only one answer
    has is not compared either (the cut moved), and an entry both have must be the same."""
    loose, cuts = list(volatile), list(cut)
    out = set()
    for path in set(a) | set(b):
        general = _general(path)
        if a.get(path, ...) == b.get(path, ...) or _covers(loose, general):
            continue
        if path not in a and _item_absent(path, a) or path not in b and _item_absent(path, b):
            continue
        if _covers(cuts, general) and (path not in a or path not in b or path.endswith("#")):
            continue
        out.add(general)
    return out


def beyond(found: Iterable[str], allowed: Iterable[str]) -> list[str]:
    """The fields of ``found`` that no entry of ``allowed`` covers, sorted."""
    entries = list(allowed)
    return sorted(f for f in found if not _covers(entries, f))


async def _read(app: FastAPI, url: str, token: str) -> dict[str, Any]:
    try:
        response = await asyncio.wait_for(call(app, "GET", url, token), _SECONDS)
    except TimeoutError:
        pytest.fail(f"GET {url} did not answer in {_SECONDS}s: it measures nothing")
    return answer(response)


async def read_all(deployment: Deployment, token: str) -> dict[str, dict[str, Any]]:
    """Every GET route's answer to ``token``, by URL."""
    out = {}
    for path in get_routes(deployment.app):
        for url in urls(path, deployment.values):
            out[url] = await _read(deployment.app, url, token)
    return out


async def walk(deployment: Deployment) -> dict[str, dict[str, dict[str, Any]]]:
    """Every answer to both keys, by URL, before and after the other repository's work is added, and the limited
    key's again before it, each read after the app's caches are dropped."""
    keys = deployment.keys
    reads = {"limited": await read_all(deployment, keys.limited)}
    forget_caches(deployment.app)
    reads["open"] = await read_all(deployment, keys.open)
    forget_caches(deployment.app)
    reads["limited again"] = await read_all(deployment, keys.limited)
    await deployment.add_uncovered()
    reads["limited after"] = await read_all(deployment, keys.limited)
    forget_caches(deployment.app)
    reads["open after"] = await read_all(deployment, keys.open)
    return reads


def compare(
    deployment: Deployment, reads: Mapping[str, Mapping[str, Mapping[str, Any]]], a: str, b: str
) -> dict[str, set[str]]:
    """The fields in which the reads ``a`` and ``b`` differ, by route, but the :data:`VOLATILE` ones."""
    found: dict[str, set[str]] = {}
    for path in get_routes(deployment.app):
        loose = VOLATILE.get(path, {})
        for url in urls(path, deployment.values):
            moved = differing(reads[a][url], reads[b][url], loose, CUT.get(path, ()))
            if moved:
                found.setdefault(path, set()).update(moved)
    return found


def unanswered(deployment: Deployment, reads: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, list[int]]:
    """Each route none of whose URLs answered the key without an allowlist below 400, before or after the other
    repository's work was added, or one that answered the limited key with a server error, with the statuses."""
    found: dict[str, list[int]] = {}
    for path in get_routes(deployment.app):
        answers = [
            reads[r][url] for url in urls(path, deployment.values) for r in ("open", "open after", "limited after")
        ]
        if not any(a["status"] < 400 for a in answers[:-1]) or any(a["status"] >= 500 for a in answers):
            found[path] = sorted({a["status"] for a in answers})
    return found


async def test_no_get_route_reads_another_repository_s_work_to_a_key_with_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with aggregate_reads_app(db_with_migrations, monkeypatch) as deployment:
        served = set(get_routes(deployment.app))
        reads = await walk(deployment)
        clock = compare(deployment, reads, "limited", "limited again")
        moved = compare(deployment, reads, "limited", "limited after")
        down = unanswered(deployment, reads)
    listed = set(TOTALS) | set(ELSEWHERE) | set(VOLATILE) | set(NOT_ANSWERED) | set(OTHER_STAGES) | set(ROUTE_PARAMS)
    stale = sorted(listed - served)
    assert stale == [], f"listed here but not served: {stale}"
    assert {path: s for path, s in down.items() if path not in NOT_ANSWERED} == {}, (
        f"routes that answered only errors, or a server error, so the walk measures nothing of them: {down}; make them "
        "answer in tests/fixtures/aggregate_reads.py (its seeds, ROUTE_PARAMS, QUERIES), or list them in NOT_ANSWERED "
        "with why and where they are read"
    )
    answering = sorted(set(NOT_ANSWERED) - set(down))
    assert answering == [], f"routes in NOT_ANSWERED that answer now: {answering}; take them out, so they are measured"
    # A route in ELSEWHERE as a whole is compared for nothing but moving: the audit log grows with the walk's reads.
    unstable = {
        path: f for path, f in clock.items() if beyond(f, {**VOLATILE.get(path, {}), **ELSEWHERE.get(path, {})})
    }
    assert unstable == {}, (
        f"fields that differ between two reads of the same rows: {unstable}; list each in VOLATILE with why"
    )
    shown = {path: missed for path, f in moved.items() if (missed := beyond(f, ELSEWHERE.get(path, {})))}
    assert shown == {}, (
        f"routes whose answer to a key covering only {COVERED} moved when {UNCOVERED} was given work: {shown}. A "
        "total counts the repositories the key covers only, in its query (embry0.api.repo_allowlist.covered_slugs), "
        "or the route is placed in ELSEWHERE with the work that narrows it"
    )
    quiet = sorted(set(ELSEWHERE) - set(moved))
    assert quiet == [], (
        f"routes in ELSEWHERE whose answer to the limited key no longer moves: {quiet}; take them out, or plant "
        "what they count in tests/fixtures/aggregate_reads.py"
    )


async def test_each_total_counts_what_another_repository_was_given_to_a_key_without_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not quiet by construction: each total reads the work the walk adds, so the first test measures something."""
    async with aggregate_reads_app(db_with_migrations, monkeypatch) as deployment:
        reads = await walk(deployment)
        measured = compare(deployment, reads, "open", "open after")
    unmoved = sorted(path for path in TOTALS if not beyond(measured.get(path, set()), ELSEWHERE.get(path, {})))
    assert unmoved == [], (
        f"totals whose answer to a key without an allowlist did not move when {UNCOVERED} was given work: {unmoved}; "
        "the walk measures nothing of them. Plant what they count in tests/fixtures/aggregate_reads.py"
    )


async def test_each_total_reads_to_a_key_as_a_deployment_holding_only_its_repositories_does(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over the key's repositories, not less: a key with an allowlist reads a total, once every repository holds work,
    exactly as a key without one reads it while only the key's repository does."""
    async with aggregate_reads_app(db_with_migrations, monkeypatch) as deployment:
        reads = await walk(deployment)
        over = compare(deployment, reads, "open", "limited after")
    differ = {
        path: missed
        for path in TOTALS
        if (missed := beyond(over.get(path, set()), {**ELSEWHERE.get(path, {}), **THE_KEY_S.get(path, {})}))
    }
    assert differ == {}, (
        f"totals the limited key reads otherwise than a key without an allowlist reads a deployment of {COVERED} "
        f"alone: {differ}"
    )


async def _cost(request: Request) -> Any:
    return float(await request.app.state.db.fetchval("SELECT SUM(total_cost_usd) FROM jobs"))


async def _quest_points(request: Request) -> Any:
    return (await request.app.state.progression.read()).quest_points


async def _agents(request: Request) -> Any:
    return await request.app.state.db.fetchval("SELECT COUNT(DISTINCT agent_type) FROM traces")


async def _any_failure(request: Request) -> Any:
    return await request.app.state.db.fetchval("SELECT EXISTS (SELECT 1 FROM jobs WHERE status = 'failed')")


async def _repos_merged(request: Request) -> Any:
    return await request.app.state.db.fetchval(
        "SELECT COUNT(DISTINCT lower(COALESCE(repo, context->>'repo'))) FROM jobs WHERE delivery_state = 'merged'"
    )


#: Totals that name nothing, each of another kind: a sum, the quest points, a count of the distinct, whether any row
#: is, the repositories that did something.
PLANTED: dict[str, Any] = {
    "cost": _cost,
    "quest-points": _quest_points,
    "distinct-agents": _agents,
    "any-failure": _any_failure,
    "repositories-merged": _repos_merged,
}


@pytest.mark.parametrize("name", list(PLANTED))
async def test_the_walk_finds_a_new_route_that_adds_up_every_repository_s_work(
    name: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not quiet by construction: a planted route that counts every repository's work, naming nothing, is found,
    whatever it counts."""
    async with aggregate_reads_app(db_with_migrations, monkeypatch) as deployment:
        extra = APIRouter()
        read = PLANTED[name]

        @extra.get(f"/{name}-for-a-test")
        async def planted(request: Request) -> dict[str, Any]:
            return {"value": await read(request)}

        deployment.app.include_router(extra, prefix="/api/v1")
        reads = await walk(deployment)
        moved = compare(deployment, reads, "limited", "limited after")
    shown = {path: sorted(f) for path, f in moved.items() if beyond(f, ELSEWHERE.get(path, {}))}
    assert shown == {f"/api/v1/{name}-for-a-test": ["body.value"]}, f"the walk reports the planted route alone: {shown}"
