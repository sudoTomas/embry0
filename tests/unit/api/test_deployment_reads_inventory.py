"""Every GET route reads, to a key with a repository allowlist, the same whatever the deployment's own records hold.

The repository and job inventories find a route that shows a key another
repository's item or name. What belongs to the deployment itself, rather than
to any repository, is another kind: its people, its global variables' values,
where its model credentials come from and when they were last tested, a
provider's spend, its integrations, what the retention sweeps would remove,
and who changed its settings. A key with an allowlist reads each such record
as a deployment that has none would answer it (``embry0.api.deployment_reads``),
and is refused a secret of it.

On the real app over a real database (:mod:`tests.fixtures.deployment_reads`),
the limited key covers :data:`~tests.fixtures.repository_writes.COVERED`.
Every GET route under ``/api/v1`` is read through it and through an admin key
without a list, addressed by the covered repository's items, first while the
deployment has none of its own records, then again once it has records of
every kind (:meth:`~tests.fixtures.deployment_reads.Deployment.add_deployment_wide`).
Each answer is compared field by field (a list's items as one field each,
``[]``).

- **Nothing moves for the limited key**, but the fields :data:`SHARED` names,
  each with why the key reads it (its own jobs run on it), and those
  :data:`ELSEWHERE` names, each with the work that narrows it.
- **Nothing of the deployment's own is named** to the limited key, in any
  answer's body or headers, while a key without a list reads each record
  somewhere (:data:`~tests.fixtures.deployment_reads.OWN`).
- **The walk measures something.** Each route in :data:`EMPTY` answers the key
  without a list differently once the records are added.
- **As a deployment with none.** Each route in :data:`EMPTY` answers the
  limited key, once the records are added, exactly as it answers a key without
  a list while the deployment has none, but for the fields :data:`SHARED`
  names.

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

from embry0.api.repo_allowlist import DEPLOYMENT
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.deployment_reads import (
    GLOBAL_SECRET,
    OWN,
    PERSON,
    Deployment,
    deployment_reads_app,
    forget_caches,
)
from tests.fixtures.repository_writes import COVERED, MASTER, call
from tests.unit.api.test_job_reads_inventory import address, get_routes

pytestmark = pytest.mark.requires_postgres

_RUNS_ON = "what every repository's jobs run on, its own among them"

#: Every GET route that reads one of the deployment's own records, and what it answers a key with an allowlist.
EMPTY: dict[str, str] = {
    "/api/v1/users": "the people who sign in: none",
    "/api/v1/people": "who a decision can be sent to: nobody",
    "/api/v1/config/providers/{provider_name}/usage": "the provider's spend: not_configured, asked of nobody",
    "/api/v1/admin/retention/preview": "what the sweeps would remove: nothing, each at the days it runs at, unread",
    "/api/v1/config/integrations": "the trigger labels, and every other field as a deployment that set none",
    "/api/v1/config/models": (
        "the catalog, the models in force and whether a provider can run them, with no credential source, no "
        "address the environment sets and no connection test"
    ),
    "/api/v1/config/provider": "the models in force, with neither Anthropic credential set",
}

#: The fields of a route a key with an allowlist reads of the deployment's records, and why. ``*`` is the whole answer.
SHARED: dict[str, dict[str, str]] = {
    **{
        path: {"*": f"the agents, and whether a provider can run each one's model: {_RUNS_ON}"}
        for path in ("/api/v1/agents", "/api/v1/agents/{agent_type}")
    },
    "/api/v1/pipelines/templates": {"*": f"the pipeline templates: {_RUNS_ON}"},
    "/api/v1/sandbox-profiles": {"*": f"the sandbox profiles: {_RUNS_ON}"},
    "/api/v1/skills/inline": {"*": f"the skills: {_RUNS_ON}"},
    "/api/v1/config/context": {"*": f"the global agent context: {_RUNS_ON}"},
    "/api/v1/hosts": {"*": "the hosts, the deployment's, each linked only to the repositories the key covers"},
    "/api/v1/config/models": {
        "body.providers[].available": f"whether a provider can run a model, as GET /agents says: {_RUNS_ON}",
        "body.providers[].models[].enabled": f"the models in force: {_RUNS_ON}",
    },
    "/api/v1/config/provider": {"body.disabled_models": f"the models in force: {_RUNS_ON}"},
    **{
        path: {
            "body.variables": (
                "each global variable's name, kind, scope and whether it is required, what its sandboxes are "
                "given; no value, preview or description"
            )
        }
        for path in (
            "/api/v1/environment/global",
            "/api/v1/repos/{full_repo:repo}/-/environment/resolve",
            "/api/v1/repos/{full_repo:repo}/environment/resolve",
        )
    },
    **{
        path: {"body.paid_from": "that a setup run of its repository is paid from an account of the deployment's"}
        for path in ("/api/v1/repos/{full_repo:repo}/-/setup/cost", "/api/v1/repos/{full_repo:repo}/setup/cost")
    },
    "/api/v1/run-with/keys": {
        "*": "the model accounts a job on its repository may run with, by id and label, the deployment's among "
        "them: a key picks one by its id (Run with)"
    },
    **{
        path: {"*": f"the settings document's deployment layer, who changed it shown as to a non-admin: {_RUNS_ON}"}
        for path in (
            "/api/v1/config/settings",
            "/api/v1/config/settings/effective",
            "/api/v1/config/settings/revisions",
            "/api/v1/config/settings/revisions/{revision_id}",
        )
    },
    **{
        path: {"*": f"the content pack in force, who changed it shown as to a non-admin: {_RUNS_ON}"}
        for path in (
            "/api/v1/progression/pack",
            "/api/v1/progression/pack/versions",
            "/api/v1/progression/pack/default/diff",
        )
    },
    "/api/v1/progression": {"body.pack": f"the content pack in force: {_RUNS_ON}"},
}

#: GET routes, or the fields of one, that still read the deployment's records to such a key, and the work that
#: narrows them. ``*`` is the whole answer.
ELSEWHERE: dict[str, dict[str, str]] = {
    "/api/v1/audit": {"*": "every audit row: the audit reads"},
    "/api/v1/stats": {"*": "the credentials' counts, the environment's keys and every job: the totals"},
    **{
        path: {"*": "the deployment's credentials a resolution of its repository steps through: the credential reads"}
        for path in ("/api/v1/repos/{full_repo:repo}/-/credentials", "/api/v1/repos/{full_repo:repo}/credentials")
    },
}

#: Fields that differ between two reads of the same rows, and why: the walk does not compare them.
VOLATILE: dict[str, dict[str, str]] = {
    "/api/v1/config/providers/{provider_name}/usage": {"body.checked_at": "when it was read"},
    "/api/v1/progression": {
        "body.computed_at": "when it was computed",
        "body.rollup": "the rollup the background writer is folding",
    },
    "/api/v1/world": {"body.generated_at": "when it was read"},
}

_ONE_JOB = "a job's QA artifacts and deliverables, from stores this app has none of: test_job_reads_inventory.py"
#: GET routes that cannot answer in this app, and where they are read; any other that answers 5xx fails the walk.
UNREAD_HERE: dict[str, str] = {
    **{
        path: _ONE_JOB
        for path in (
            "/api/v1/jobs/{job_id}/artifacts/logs/{service}",
            "/api/v1/jobs/{job_id}/artifacts/{path:path}",
            "/api/v1/jobs/{job_id}/artifacts/screenshots/latest",
            "/api/v1/jobs/{job_id}/deliverables/{deliverable_id}/download",
            "/api/v1/jobs/{job_id}/qa/attempts",
            "/api/v1/jobs/{job_id}/qa/attempts/{attempt_n}/artifacts/{path:path}",
            "/api/v1/jobs/{job_id}/qa/attempts/{attempt_n}/result",
            "/api/v1/jobs/{job_id}/qa/screenshots/latest",
            "/api/v1/jobs/{job_id}/qa/services",
            "/api/v1/qa/runs/{run_id}/apps/{app}/artifacts/{kind}",
            "/api/v1/qa/runs/{run_id}/apps/{app}/artifacts/{kind}/{filename}",
        )
    },
    "/api/v1/logs/containers": "the container logs, from Loki, which this app has no client of",
    "/api/v1/logs/query": "the container logs, from Loki, which this app has no client of",
    "/api/v1/watcher/runs": "the watcher's ticks, planted in test_job_reads_inventory.py",
    "/api/v1/orgs/{owner}/context-audit": "the context-audit schedule, planted in test_repository_reads_inventory.py",
    "/api/v1/github/discoverable-repos": "the forge, stood in by test_repository_discovery_allowlist.py",
}

#: The queries each route is also read with.
QUERIES: dict[str, tuple[str, ...]] = {
    "/api/v1/config/providers/{provider_name}/usage": ("?fresh=true",),
    "/api/v1/admin/retention/preview": ("?job_log_days=1&agent_session_days=1&qa_image_days=1",),
    "/api/v1/users": ("?limit=1&offset=0",),
    "/api/v1/config/settings/revisions": ("?limit=1",),
}

_SECONDS = 10.0


def named(values: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    """Each path parameter's value naming the covered repository's item, or a record of the deployment's: the agent
    changed, the provider whose spend is read, the global secret."""
    return {
        **values[COVERED],
        "agent_type": "developer",
        "provider_name": "openrouter",
        "key": GLOBAL_SECRET,
        "app": "web",
    }


def urls(path: str, values: Mapping[str, Mapping[str, str]]) -> list[str]:
    url = address(path, named(values))
    return [url, *(url + query for query in QUERIES.get(path, ()))]


def fields(value: Any, at: str = "") -> dict[str, Any]:
    """Every leaf of a JSON value by its path, a list's items under ``[]`` each in turn, and each list's length."""
    out: dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key in sorted(value):
            out.update(fields(value[key], f"{at}.{key}" if at else str(key)))
        if not value:
            out[at] = "{}"
    elif isinstance(value, list):
        out[f"{at}[]#"] = len(value)
        for n, item in enumerate(value):
            for path, leaf in fields(item, f"{at}[]").items():
                out[f"{path}@{n}"] = leaf
    else:
        out[at] = value
    return out


def _general(path: str) -> str:
    """A field's path without the items' positions: ``users[].email``."""
    return re.sub(r"@\d+", "", path)


def answer(response: httpx.Response) -> dict[str, Any]:
    """The answer's fields, its status and its body's, and its whole text with its headers, kept as ``text``."""
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    found = {"status": response.status_code, **{f"body.{k}" if k else "body": v for k, v in fields(body).items()}}
    return {**found, "text": f"{response.headers}\n{response.text}"}


def _covers(prefixes: Iterable[str], path: str) -> bool:
    """Whether a field is one of ``prefixes``, or within one: ``*`` is every field."""
    return any(p == "*" or path == p or path.startswith((p + ".", p + "[")) for p in prefixes)


def differing(a: Mapping[str, Any], b: Mapping[str, Any], volatile: Iterable[str] = ()) -> set[str]:
    """The fields, without positions, in which two answers differ, but the ``volatile`` ones and the whole text."""
    loose = [*volatile, "text"]
    return {
        general
        for path in set(a) | set(b)
        if a.get(path, ...) != b.get(path, ...) and not _covers(loose, general := _general(path))
    }


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
    """Every GET route's answer to ``token``, by URL, read after the app's caches are dropped."""
    forget_caches(deployment.app)
    out = {}
    for path in get_routes(deployment.app):
        for url in urls(path, deployment.values):
            out[url] = await _read(deployment.app, url, token)
    return out


async def walk(deployment: Deployment) -> dict[str, dict[str, dict[str, Any]]]:
    """Every answer to both keys, by URL, before and after the deployment's records are added, and the limited
    key's again before."""
    keys = deployment.keys
    reads = {"limited": await read_all(deployment, keys.limited)}
    reads["open"] = await read_all(deployment, keys.open)
    reads["limited again"] = await read_all(deployment, keys.limited)
    await deployment.add_deployment_wide()
    reads["limited after"] = await read_all(deployment, keys.limited)
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
            moved = differing(reads[a][url], reads[b][url], loose)
            if moved:
                found.setdefault(path, set()).update(moved)
    return found


def failing(deployment: Deployment, reads: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, int]:
    """Each route that answered a key with a server error, with the status."""
    return {
        path: answer["status"]
        for path in get_routes(deployment.app)
        for url in urls(path, deployment.values)
        for answer in (reads["open"][url], reads["limited after"][url])
        if answer["status"] >= 500
    }


def naming(
    deployment: Deployment, reads: Mapping[str, Mapping[str, Mapping[str, Any]]], who: str
) -> dict[str, list[str]]:
    """Each of the deployment's own records (:data:`~tests.fixtures.deployment_reads.OWN`) that the reads ``who``
    name, with the routes that name it, but those :data:`ELSEWHERE` places as a whole."""
    found: dict[str, list[str]] = {}
    for record, value in OWN.items():
        for path in get_routes(deployment.app):
            if "*" in ELSEWHERE.get(path, {}):
                continue
            if any(value in reads[who][url]["text"] for url in urls(path, deployment.values)):
                found.setdefault(record, []).append(path)
    return found


async def test_no_get_route_reads_the_deployment_s_own_records_to_a_key_with_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with deployment_reads_app(db_with_migrations, monkeypatch) as deployment:
        served = set(get_routes(deployment.app))
        reads = await walk(deployment)
        clock = compare(deployment, reads, "limited", "limited again")
        moved = compare(deployment, reads, "limited", "limited after")
        down = failing(deployment, reads)
    listed = set(EMPTY) | set(SHARED) | set(ELSEWHERE) | set(VOLATILE) | set(UNREAD_HERE) | set(QUERIES)
    stale = sorted(listed - served)
    assert stale == [], f"listed here but not served: {stale}"
    assert {path: s for path, s in down.items() if path not in UNREAD_HERE} == {}, (
        f"routes that answered a server error, so the walk measures nothing of them: {down}; make them answer in "
        "tests/fixtures/deployment_reads.py, or list them in UNREAD_HERE with where they are read"
    )
    # A route in ELSEWHERE as a whole is compared for nothing but moving: the audit log grows with the walk's reads.
    unstable = {
        path: f for path, f in clock.items() if beyond(f, {**VOLATILE.get(path, {}), **ELSEWHERE.get(path, {})})
    }
    assert unstable == {}, (
        f"fields that differ between two reads of the same rows: {unstable}; list each in VOLATILE with why"
    )
    shown = {
        path: missed
        for path, f in moved.items()
        if (missed := beyond(f, {**SHARED.get(path, {}), **ELSEWHERE.get(path, {})}))
    }
    assert shown == {}, (
        f"routes whose answer to a key covering only {COVERED} moved when the deployment was given records of its "
        f"own: {shown}. Such a key reads them as a deployment with none (embry0.api.deployment_reads), or the "
        "route is placed in SHARED with why the key reads it, or in ELSEWHERE with the work that narrows it"
    )
    quiet = sorted(set(ELSEWHERE) - set(moved))
    assert quiet == [], (
        f"routes in ELSEWHERE whose answer to the limited key no longer moves: {quiet}; take them out, or plant "
        "what they read in tests/fixtures/deployment_reads.py"
    )


async def test_no_answer_names_a_record_of_the_deployment_s_own_to_a_key_with_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each record a key without a list reads somewhere, in a body or a header, and the limited key nowhere."""
    async with deployment_reads_app(db_with_migrations, monkeypatch) as deployment:
        reads = await walk(deployment)
        to_open, to_limited = naming(deployment, reads, "open after"), naming(deployment, reads, "limited after")
    unread = sorted(set(OWN) - set(to_open))
    assert unread == [], f"records no route shows a key without an allowlist, so the walk measures nothing: {unread}"
    assert to_limited == {}, f"the deployment's own records named to a key covering only {COVERED}: {to_limited}"


async def test_each_route_that_reads_the_deployment_s_records_reads_them_to_a_key_without_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not quiet by construction: each route in EMPTY reads the records the walk adds."""
    async with deployment_reads_app(db_with_migrations, monkeypatch) as deployment:
        reads = await walk(deployment)
        measured = compare(deployment, reads, "open", "open after")
    unmoved = sorted(path for path in EMPTY if not beyond(measured.get(path, set()), SHARED.get(path, {})))
    assert unmoved == [], (
        f"routes whose answer to a key without an allowlist did not move when the deployment was given records: "
        f"{unmoved}; the walk measures nothing of them. Plant what they read in tests/fixtures/deployment_reads.py"
    )


async def test_each_such_route_reads_to_the_key_as_a_deployment_with_none(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key with an allowlist reads each route in EMPTY, once the deployment has its records, exactly as a key without
    a list reads it while the deployment has none, but for the fields it reads of them (SHARED)."""
    async with deployment_reads_app(db_with_migrations, monkeypatch) as deployment:
        reads = await walk(deployment)
        over = compare(deployment, reads, "open", "limited after")
    differ = {path: missed for path in EMPTY if (missed := beyond(over.get(path, set()), SHARED.get(path, {})))}
    assert differ == {}, (
        f"routes the limited key reads otherwise than a deployment with no records of its own: {differ}"
    )


async def _refusals(db: DatabasePool) -> list[tuple[str, str]]:
    rows = await db.fetch(
        "SELECT details->>'repo' AS repo, details->>'action' AS action FROM audit_log "
        "WHERE action = 'apikey.repo_refused' ORDER BY id"
    )
    return [(str(row["repo"]), str(row["action"])) for row in rows]


async def test_revealing_a_global_variable_is_refused_to_a_key_with_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal of a deployment-wide write, before the variable is looked up: the same answer for a variable that
    exists and one that does not, each audited naming the deployment, none read back by the key."""
    db = db_with_migrations
    async with deployment_reads_app(db, monkeypatch) as deployment:
        await deployment.add_deployment_wide()
        app, keys = deployment.app, deployment.keys
        before = await _refusals(db)
        there = await call(app, "GET", f"/api/v1/environment/global/{GLOBAL_SECRET}/reveal", keys.limited)
        missing = await call(app, "GET", "/api/v1/environment/global/NOT_THERE/reveal", keys.limited)
        refused = (await _refusals(db))[len(before) :]
        audit = (await call(app, "GET", "/api/v1/audit?limit=200", keys.limited)).text
        opened = await call(app, "GET", f"/api/v1/environment/global/{GLOBAL_SECRET}/reveal", keys.open)
    assert there.status_code == 403 and DEPLOYMENT in there.json()["detail"], f"an existing variable: {there.text}"
    assert (missing.status_code, missing.json()) == (there.status_code, there.json()), (
        f"a variable that does not exist answers as one that does: {missing.text} against {there.text}"
    )
    assert refused == [(DEPLOYMENT, "read the deployment's global variables")] * 2, f"the refusals audited: {refused}"
    assert "repo_refused" not in audit, "the key reads its refusals back in GET /audit"
    assert opened.status_code == 200 and opened.json()["value"] == OWN["a global secret's value"], opened.text


async def test_a_setup_run_names_the_account_of_the_key_s_own_repository(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The account a setup run of the key's repository is paid from is named when it is the repository's own, and is
    the deployment's, unnamed, when it comes from the global pool or the environment; a key without a list reads
    every name."""
    db = db_with_migrations
    async with deployment_reads_app(db, monkeypatch) as deployment:
        await deployment.add_deployment_wide()
        app, keys = deployment.app, deployment.keys
        url = f"/api/v1/repos/{COVERED}/-/setup/cost"
        pooled = {k: (await call(app, "GET", url, key)).json()["paid_from"] for k, key in vars(keys).items()}
        own = await call(
            app,
            "POST",
            "/api/v1/credentials",
            MASTER,
            {"kind": "llm_api_key", "provider": "anthropic", "label": "widgets-account", "secret": "z" * 24},
        )
        bound = await call(
            app,
            "PUT",
            f"/api/v1/repos/{COVERED}/-/credentials",
            MASTER,
            {"bindings": [{"credential_id": own.json()["id"], "role": "llm"}]},
        )
        assert bound.status_code == 200, bound.text
        owned = {k: (await call(app, "GET", url, key)).json()["paid_from"] for k, key in vars(keys).items()}
    environment = "CLAUDE_CODE_OAUTH_TOKEN"
    assert pooled == {"limited": "(the deployment's)", "open": environment, "master": environment}, (
        f"paid from the environment's account: {pooled}"
    )
    assert owned == dict.fromkeys(("limited", "open", "master"), "widgets-account"), f"paid from its own: {owned}"


async def test_who_changed_a_layer_of_the_key_s_repository_reads_as_before(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the deployment's records hide who changed them: a person's change to the layer of the key's own
    repository names them to the key, as to an admin, while their change to the deployment's layer does not."""
    db = db_with_migrations
    async with deployment_reads_app(db, monkeypatch) as deployment:
        await deployment.add_deployment_wide()
        app, keys = deployment.app, deployment.keys
        ops = await db.fetchval("SELECT id FROM users WHERE email = $1", PERSON)
        _row, token = await ApiKeysRepository(db).create(name="ops-2", scope="admin", user_id=ops, created_by=PERSON)
        layer = await call(
            app, "PATCH", f"/api/v1/config/settings/slop_meter?repo={COVERED}", token, {"excluded_dirs": ["vendor"]}
        )
        assert layer.status_code == 200, layer.text
        reads = {
            name: (await call(app, "GET", url, keys.limited)).json()
            for name, url in {
                "repository": f"/api/v1/config/settings/revisions?repo={COVERED}",
                "deployment": "/api/v1/config/settings/revisions",
                "layer": f"/api/v1/config/settings?repo={COVERED}",
                "effective": f"/api/v1/config/settings/effective?repo={COVERED}",
            }.items()
        }
        # Each document's change by the person, by its id, as a key without a list lists them.
        made = {
            name: next(
                r["id"] for r in (await call(app, "GET", url, keys.open)).json()["revisions"] if r["actor"] == PERSON
            )
            for name, url in {
                "repository": f"/api/v1/config/settings/revisions?repo={COVERED}",
                "deployment": "/api/v1/config/settings/revisions",
            }.items()
        }
        one = {
            name: (await call(app, "GET", f"/api/v1/config/settings/revisions/{rid}", keys.limited)).json()["actor"]
            for name, rid in made.items()
        }
    actors = {name: [r["actor"] for r in reads[name]["revisions"]] for name in ("repository", "deployment")}
    assert actors["repository"][:1] == [PERSON], f"a change to the key's repository names who made it: {actors}"
    assert one == {"repository": PERSON, "deployment": None}, f"each change read by its id: {one}"
    assert PERSON not in actors["deployment"], f"a change to the deployment's layer names nobody: {actors}"
    assert reads["layer"]["updated_by"] == PERSON, f"who last wrote the repository's layer: {reads['layer']}"
    keys_set = {
        (section["name"], key["key"]): key for section in reads["effective"]["sections"] for key in section["keys"]
    }
    excluded, revisit = keys_set[("slop_meter", "excluded_dirs")], keys_set[("scout", "revisit_hours")]
    assert (excluded["set_by"], [layer["set_by"] for layer in excluded["layers"]][-1]) == (PERSON, PERSON), (
        f"the repository's layer set it, and names who: {excluded}"
    )
    assert (revisit["source"], revisit["set_by"], revisit["layers"][0]["set_by"]) == ("document", None, None), (
        f"the deployment's document set it, and names nobody: {revisit}"
    )


async def test_the_walk_finds_a_new_route_that_reads_the_deployment_s_people(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not quiet by construction: a planted route listing every person's email, without the rule, is found by the
    walk and by the search for the deployment's records."""
    async with deployment_reads_app(db_with_migrations, monkeypatch) as deployment:
        extra = APIRouter()

        @extra.get("/everyone-for-a-test")
        async def everyone(request: Request) -> dict[str, Any]:
            return {
                "emails": [str(row["email"]) for row in await request.app.state.db.fetch("SELECT email FROM users")]
            }

        deployment.app.include_router(extra, prefix="/api/v1")
        reads = await walk(deployment)
        moved = compare(deployment, reads, "limited", "limited after")
        named_to = naming(deployment, reads, "limited after")
    shown = {
        path: sorted(f) for path, f in moved.items() if beyond(f, {**SHARED.get(path, {}), **ELSEWHERE.get(path, {})})
    }
    assert shown == {"/api/v1/everyone-for-a-test": ["body.emails[]", "body.emails[]#"]}, f"the walk reports: {shown}"
    assert named_to == {"a person's email": ["/api/v1/everyone-for-a-test"]}, f"the search reports: {named_to}"
