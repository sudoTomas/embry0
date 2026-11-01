"""Every write route that names a repository's item, a repository or an owner applies the key's repository allowlist.

Each such route is called on the real app over a real database
(:mod:`tests.fixtures.repository_writes`), through an admin key whose list
covers one repository, on the other repository's item (or on the other
repository, or its owner). The call must be refused, write nothing to any
table, and leave one ``apikey.repo_refused`` audit row naming that repository
or owner. Then a key without a list makes the same call, which must reach the
item: an answer of the route's own, not a 404, a body that did not validate,
or a server error. A route that cannot answer in this app measures nothing, so
it fails here until it can.

A route is found by its path parameters. Each one that names a repository's
item, a repository or an owner has a value in
:func:`tests.fixtures.repository_writes.ids`; :data:`NOT_A_REPOSITORYS` lists
the ones that name something of no one repository, with what. A new parameter
fails until it is one or the other, and a new route with a known one is called
as soon as it exists. :data:`READS` lists the write routes that store nothing,
and :data:`OWN_RULES` the routes refused by a rule of their own.

A route can name its target in its body or its query instead. The walk reads
each write route's body model, nested models included, and its query
parameters; one with a field named for a repository or an owner
(:data:`TARGET_NAMES`) is called here the same way (:data:`BODY_TARGETS`), or
:data:`BODY_TARGETS_ELSEWHERE` says which test holds it. A body the route
reads raw, a YAML document, is not walked.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel

from embry0.api.v1.repo_old_paths import OldPathRoute
from embry0.config import Embry0Config
from embry0.orchestration.dispatch_actors import REPO_ALLOWLIST
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.repository_writes import COVERED, OWNER, UNCOVERED, Keys, call, ids, repository_writes_app
from tests.fixtures.routes import app_routes

_PARAMETER = re.compile(r"\{(\w+)(?::\w+)?\}")

#: Path parameters that name something of no one repository, and what each names.
NOT_A_REPOSITORYS: dict[str, str] = {
    "agent_type": "an agent definition, the deployment's",
    "skill_name": "a skill of an agent definition, the deployment's",
    "key_id": "an API key; a key with an allowlist revokes only keys within it (test below)",
    "provider_name": "a model provider of the deployment's catalog",
    "section": "a section of the settings document, whose routes check the layer ?owner= or ?repo= names",
    "credential_id": "a credential of the deployment's registry; binding one to a repository is that repository's",
    "host_id": "a host of the deployment",
    "template_id": "a pipeline template, the deployment's",
    "version": "a version of the progression pack, the deployment's",
    "source_id": "a skill source, the deployment's",
    "user_id": "a person",
}

#: Routes whose parameter of a shared name names something of no one repository here.
NOT_A_REPOSITORYS_AT: dict[str, str] = {
    "/api/v1/sandbox-profiles/{name}": "a sandbox profile, the deployment's",
    "/api/v1/sandbox-profiles/{name}/reset": "a sandbox profile, the deployment's",
    "/api/v1/skills/inline/{name}": "an inline skill, the deployment's",
    "/api/v1/config/settings/revisions/{revision_id}/undo": (
        "a revision of the settings document, whose routes check the layer it changed"
    ),
}

#: Repository routes refused to every key with an allowlist, its own repository's included: the deployment's.
DEPLOYMENT_AT: dict[str, str] = {
    "/api/v1/repos/{full_repo:repo}/-/docker-access": (
        "the shared daemon runs the proxies and every sandbox: test_deployment_writes_inventory.py"
    ),
}

#: Write routes that store nothing: a read sent as a POST.
READS: dict[str, str] = {
    "/api/v1/jobs/{job_id}/events-token": "mints a signed token to stream the job's events; stores nothing",
    "/api/v1/jobs/{job_id}/qa/log-stream-token": "mints a signed token to stream the job's QA log; stores nothing",
    "/api/v1/qa/runs/{run_id}/events-token": "mints a signed token to stream the run's events; stores nothing",
    "/api/v1/repos/{full_repo:repo}/-/qa-config/check": "checks a test setup as a run would use it; stores nothing",
}

#: Routes refused by a rule of their own, which answers as a missing item and audits nothing: checked here to
#: be refused and to write nothing.
OWN_RULES: dict[str, str] = {
    path: "a map belongs to an owner, and one the key does not cover is not found (test_knowledge_maps_owner_api.py)"
    for path in (
        "/api/v1/maps/{slug}",
        "/api/v1/maps/{slug}/marks",
        "/api/v1/maps/{slug}/owner",
        "/api/v1/maps/{slug}/send",
    )
}

_QA_SETUP = (Path(__file__).parents[2] / "fixtures" / "qa-yaml-corpus" / "v2" / "single-app.yaml").read_text()

#: A body each route takes, so its answer comes from the route and not from validation; a callable is given the
#: path parameters' values. A route not here is sent none.
BODIES: dict[str, Any] = {
    "/api/v1/asks/{ask_id}/sent": {"message_ref": "m-1"},
    "/api/v1/cases/{case_id}/accept": {},
    "/api/v1/cases/{case_id}/close": {"resolution_path": "abandoned"},
    "/api/v1/config/context/repos/{repo:path}": {"system_context": "Be brief."},
    "/api/v1/decisions/{decision_id}/send-to": {"to": "cy@example.com", "note": ""},
    "/api/v1/handoffs/{handoff_id}/return": {"note": ""},
    "/api/v1/issues/{issue_id}": {"title": "Renamed"},
    "/api/v1/issues/{issue_id}/inputs/{input_id}/answer": {"answer": "this one"},
    "/api/v1/jobs/{job_id}/done-when": {"items": [{"done": "it works"}]},
    "/api/v1/jobs/{job_id}/evaluation": {"rating": "good"},
    "/api/v1/jobs/{job_id}/inputs/{input_id}/answer": {"answer": "this one"},
    "/api/v1/jobs/{job_id}/resume": {"choice": "abandon"},
    "/api/v1/jobs/{job_id}/reject": {},
    "/api/v1/jobs/{job_id}/send-back": {"notes": "again, smaller"},
    "/api/v1/maps/{slug}": lambda v: {
        "format": "embry0.map/1",
        "slug": v["slug"],
        "title": "Map",
        "repo": v["full_repo"],
        "nodes": [{"id": "n1", "kind": "system", "label": "n1"}],
        "edges": [],
    },
    "/api/v1/maps/{slug}/marks": {"node_id": "n1", "verdict": "keep", "by": "ada"},
    "/api/v1/maps/{slug}/owner": {"owner": None},
    "/api/v1/maps/{slug}/send": {"node_ids": ["n1"], "by": "ada"},
    "/api/v1/merge-approvals/{approval_id}/decide": {"decision": "reject"},
    "/api/v1/orgs/{owner}": {"display_name": "Renamed"},
    "/api/v1/orgs/{owner}/actions": lambda v: {"action": "deactivate", "repos": [v["full_repo"]]},
    "/api/v1/orgs/{owner}/context-audit/run": lambda v: {"repo": v["full_repo"]},
    "/api/v1/orgs/{owner}/status": {"status": "paused"},
    "/api/v1/proposals/{proposal_id}/accept": {"by": "ada"},
    "/api/v1/proposals/{proposal_id}/decline": {"by": "ada", "reason": "not now"},
    "/api/v1/proposals/{proposal_id}/snooze": {"by": "ada", "until": "2099-01-01T00:00:00Z"},
    "/api/v1/repos/{full_repo:repo}/-/active": {"active": False},
    "/api/v1/repos/{full_repo:repo}/-/credentials": {"bindings": []},
    "/api/v1/repos/{full_repo:repo}/-/docker-access": {"enabled": True},
    "/api/v1/repos/{full_repo:repo}/-/preferences": {"notes": "Small changes."},
    "/api/v1/repos/{full_repo:repo}/-/environment": {"variables": [{"key": "API_URL", "value": "https://e.test"}]},
    "/api/v1/repos/{full_repo:repo}/-/qa-config": _QA_SETUP,
    "/api/v1/repos/{full_repo:repo}/-/setup/propose": {"confirm_spend": True},
    "/api/v1/standards/{owner}/{kind}": {"schema": {}, "values": {}},
    "/api/v1/standards/{owner}/{kind}/audit/{repo:path}": {},
    "/api/v1/standards/{owner}/{kind}/repos/{repo:path}": {"mode": "advise"},
    "/api/v1/watcher/stacks/{owner}/{name}": {"logql": '{app="web"}'},
    "/api/v1/world/quests/{job_id}/evaluation": {"rating": "good"},
    "/api/v1/world/quests/{job_id}/reject": {},
    "/api/v1/world/quests/{job_id}/send-back": {"notes": "again, smaller"},
}

#: The tables a refused call may write: the refusal's audit row, and the key's last use.
_BOOKKEEPING = ("audit_log", "api_keys")


def _app() -> FastAPI:
    from embry0.api.app import create_app

    with patch.dict(os.environ, {}, clear=True):
        return create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))  # type: ignore[call-arg]


def write_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Each write route under ``/api/v1`` with a path parameter, as ``(method, path)``; an old path's route is its
    new path's handler, and is left out."""
    return sorted(
        (method, route.path)
        for route in app_routes(app)
        if route.is_api_route
        and route.path.startswith("/api/v1/")
        and _PARAMETER.search(route.path)
        and not isinstance(route.route, OldPathRoute)
        for method in (route.methods or set()) - {"GET", "HEAD", "OPTIONS"}
    )


def kind(path: str) -> str:
    """``read``, ``none`` (no repository's), ``repository`` (called here), or what is unknown about the route."""
    if path in READS:
        return "read"
    if path in DEPLOYMENT_AT:
        return "deployment"
    if path in NOT_A_REPOSITORYS_AT:
        return "none"
    names = _PARAMETER.findall(path)
    if all(name in NOT_A_REPOSITORYS for name in names):
        return "none"
    known = ids(UNCOVERED)
    unknown = [name for name in names if name not in known and name not in NOT_A_REPOSITORYS]
    if unknown:
        return f"unknown parameter {unknown}"
    return "repository"


def address(path: str, values: Mapping[str, str]) -> str:
    """``path`` naming ``values``' items; a setup proposal is the repository's own, not a proposal of work."""
    named = dict(values, proposal_id=values["setup_proposal_id"]) if "/setup/proposal/" in path else values
    return _PARAMETER.sub(lambda m: named[m.group(1)], path)


def _body(path: str, values: Mapping[str, str]) -> Any:
    body = BODIES.get(path)
    return body(values) if callable(body) else body


async def _tables(db: DatabasePool) -> dict[str, str]:
    """A digest of each table's rows, the bookkeeping aside."""
    names = [
        str(r["tablename"])
        for r in await db.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        if str(r["tablename"]) not in _BOOKKEEPING
    ]
    query = " UNION ALL ".join(
        f"SELECT '{name}' AS t, md5(coalesce(string_agg(r::text, '|' ORDER BY r::text), '')) AS d FROM \"{name}\" r"
        for name in names
    )
    return {str(r["t"]): str(r["d"]) for r in await db.fetch(query)}


async def _refusals(db: DatabasePool) -> list[str]:
    """The repository each refusal's row names, in order; every one under the deployment's actor."""
    rows = await db.fetch(
        "SELECT actor, details->>'repo' AS repo FROM audit_log WHERE action = 'apikey.repo_refused' ORDER BY id"
    )
    assert {str(r["actor"]) for r in rows} <= {REPO_ALLOWLIST}, f"a refusal's row names the key: {rows}"
    return [str(r["repo"]) for r in rows]


async def _audit_seen(app: FastAPI, token: str) -> int:
    """How many rows ``GET /audit`` shows ``token``'s caller."""
    response = await call(app, "GET", "/api/v1/audit?limit=1", token)
    assert response.status_code == 200, response.text
    return int(response.json()["total"])


def _not_reached(response: Any) -> str | None:
    """Why ``response`` is not the route's own answer about the item, or None when it is."""
    if response.status_code >= 500:
        return f"a server error, {response.status_code} {response.text[:160]}"
    if response.status_code == 404:
        return f"404 {response.text[:160]}: seed the item in tests/fixtures/repository_writes.py"
    if response.status_code == 422:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, list):
            return f"the body did not validate, {response.text[:160]}: give the route a body in BODIES"
    return None


async def problems(
    app: FastAPI,
    keys: Keys,
    values: Mapping[str, Mapping[str, str]],
    db: DatabasePool,
    method: str,
    path: str,
    *,
    request: tuple[str, Any] | None = None,
) -> list[str]:
    """What is wrong with ``method path`` acting for the limited key on the uncovered repository; empty when nothing.

    ``request``, the URL and body, for a route whose target is in its body or query rather than its path.
    """
    uncovered = values[UNCOVERED]
    url, body = request if request is not None else (address(path, uncovered), _body(path, uncovered))
    out: list[str] = []
    tables, refused, readable = await _tables(db), await _refusals(db), await _audit_seen(app, keys.limited)
    limited = await call(app, method, url, keys.limited, body)
    if limited.status_code < 400:
        out.append(f"the limited key was not refused: {limited.status_code} {limited.text[:160]}")
    changed = sorted(name for name, digest in (await _tables(db)).items() if tables.get(name) != digest)
    if changed:
        out.append(f"the limited key's call wrote to {changed}")
    audited = (await _refusals(db))[len(refused) :]
    if path not in OWN_RULES and audited not in ([UNCOVERED], [f"{OWNER[UNCOVERED]}/*"]):
        out.append(f"the refusal is not audited once naming {UNCOVERED} or {OWNER[UNCOVERED]}/*: {audited}")
    if await _audit_seen(app, keys.limited) != readable:
        out.append("the limited key reads back in GET /audit a row its refused call left")
    refused = await _refusals(db)
    reached = await call(app, method, url, keys.open, body)
    why = _not_reached(reached)
    if why is not None:
        out.append(f"a key without an allowlist did not reach the item: {why}")
    if (await _refusals(db))[len(refused) :]:
        out.append(f"a key without an allowlist was refused: {reached.status_code} {reached.text[:160]}")
    return out


#: The name of a body field (at any depth) or a query parameter that names the repository or owner a write acts on.
TARGET_NAMES = frozenset({"repo", "repos", "owner", "scope_value", "stack"})


def _fields(model: Any, prefix: str = "") -> list[str]:
    """``model``'s fields, nested models' as ``outer.inner``."""
    out: list[str] = []
    for name, field in getattr(model, "model_fields", {}).items():
        out.append(prefix + name)
        for arg in _types(field.annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                out += _fields(arg, f"{prefix}{name}.")
    return out


def _types(annotation: Any) -> list[Any]:
    """Every type ``annotation`` names: itself, and the arguments of a union, a list or an optional, in depth."""
    args = getattr(annotation, "__args__", ()) or ()
    return [annotation, *(t for a in args for t in _types(a))]


def body_targets(app: FastAPI) -> dict[tuple[str, str], list[str]]:
    """Each write route under ``/api/v1`` whose body or query names a repository or an owner, with those names."""
    found: dict[tuple[str, str], list[str]] = {}
    for route in app_routes(app):
        if not route.is_api_route or not route.path.startswith("/api/v1/") or isinstance(route.route, OldPathRoute):
            continue
        body = route.route.body_field
        models = _types(body.field_info.annotation) if body is not None else []
        names = [
            *(_fields(arg) for arg in models),
            [param.name for param in (route.dependant.query_params if route.dependant else [])],
        ]
        targets = sorted({n for group in names for n in group if n.rsplit(".", 1)[-1] in TARGET_NAMES})
        for method in (route.methods or set()) - {"GET", "HEAD", "OPTIONS"}:
            if targets:
                found[(method, route.path)] = targets
    return found


#: Write routes whose body or query names the repository or owner they act on, called here as the routes that name
#: it in their path are: the URL, and a body naming the uncovered repository's, a callable of its values.
BODY_TARGETS: dict[tuple[str, str], tuple[Callable[[Mapping[str, str]], str], Any]] = {
    ("PUT", "/api/v1/credentials/bindings"): (
        lambda v: "/api/v1/credentials/bindings",
        lambda v: {
            "scope_kind": "owner",
            "scope_value": f"github:{v['owner']}",
            "bindings": [{"credential_id": v["credential_id"], "role": "forge"}],
        },
    ),
    ("POST", "/api/v1/orgs"): (
        lambda v: "/api/v1/orgs",
        lambda v: {"owner": v["owner"], "forge": "github", "display_name": "Renamed", "probe": False},
    ),
    ("POST", "/api/v1/repos"): (
        lambda v: "/api/v1/repos",
        lambda v: {"repo": v["full_repo"], "forge": "github", "notes": "Again"},
    ),
    ("POST", "/api/v1/leads/run"): (lambda v: "/api/v1/leads/run", lambda v: {"repo": v["full_repo"]}),
}

#: Write routes whose body or query names a repository or an owner, held to the list elsewhere, and where.
BODY_TARGETS_ELSEWHERE: dict[tuple[str, str], str] = {
    **{
        ("POST", path): "test_api_key_repo_allowlist.py, the repository named"
        for path in ("/api/v1/jobs", "/api/v1/issues", "/api/v1/qa/images/build", "/api/v1/repos/bootstrap")
    },
    ("POST", "/api/v1/watcher/run"): "test_api_key_repo_allowlist.py, the stack named",
    ("PUT", "/api/v1/issues/{issue_id}"): "test_api_key_repo_allowlist.py, moving an issue to another repository",
    ("PUT", "/api/v1/config/settings"): "test_settings_layers_api.py: ?owner= and ?repo= name the layer written",
    ("PATCH", "/api/v1/config/settings/{section}"): "test_settings_layers_api.py, as PUT /config/settings",
    ("POST", "/api/v1/orgs/{owner}/actions"): "test_org_actions_api.py, each repository it names",
    ("POST", "/api/v1/orgs/{owner}/context-audit/run"): "called above by its path, with the repository it names",
    **{
        (method, path): "test_knowledge_maps_owner_api.py: ?owner= names the map's owner"
        for method, path in (
            ("PUT", "/api/v1/maps/{slug}"),
            ("DELETE", "/api/v1/maps/{slug}"),
            ("POST", "/api/v1/maps/{slug}/marks"),
            ("PUT", "/api/v1/maps/{slug}/owner"),
            ("POST", "/api/v1/maps/{slug}/send"),
        )
    },
    ("POST", "/api/v1/orgs/probe"): "stores nothing: asks the forge what a credential can do at an owner",
    ("POST", "/api/v1/hosts"): "a host of the deployment: refused to a key with an allowlist, "
    "test_deployment_writes_inventory.py",
    ("PATCH", "/api/v1/hosts/{host_id}"): "a host of the deployment: refused to a key with an allowlist, "
    "test_deployment_writes_inventory.py",
}


_ROUTES = write_routes(_app())
ROUTES = [(method, path) for method, path in _ROUTES if kind(path) == "repository"]


def test_every_write_route_with_a_path_parameter_is_called_here_or_says_why_not() -> None:
    unknown = {f"{method} {path}": kind(path) for method, path in _ROUTES if kind(path).startswith("unknown")}
    assert unknown == {}, (
        f"routes with a path parameter not known here: {unknown}. Give it a value naming the uncovered repository's "
        "item in tests/fixtures/repository_writes.ids, or say what it names in NOT_A_REPOSITORYS"
    )
    paths = {path for _method, path in _ROUTES}
    stale = sorted((set(READS) | set(DEPLOYMENT_AT) | set(NOT_A_REPOSITORYS_AT) | set(OWN_RULES) | set(BODIES)) - paths)
    assert stale == [], f"listed here but not served: {stale}"
    assert len(ROUTES) >= 60, f"only {len(ROUTES)} of {len(_ROUTES)} write routes are called: the walk lost some"


def test_every_write_route_whose_body_names_a_repository_or_an_owner_is_called_here_or_says_where() -> None:
    found = body_targets(_app())
    unlisted = {
        f"{m} {p}": names for (m, p), names in found.items() if (m, p) not in {**BODY_TARGETS, **BODY_TARGETS_ELSEWHERE}
    }
    assert unlisted == {}, (
        f"write routes whose body or query names a repository or an owner: {unlisted}. Call each in BODY_TARGETS, "
        "with a body naming the uncovered repository's, or say in BODY_TARGETS_ELSEWHERE which test holds it"
    )
    stale = sorted(f"{m} {p}" for m, p in {**BODY_TARGETS, **BODY_TARGETS_ELSEWHERE} if (m, p) not in found)
    assert stale == [], f"listed here, but their body and query name no repository or owner: {stale}"


@pytest.mark.parametrize(("method", "path"), list(BODY_TARGETS), ids=[f"{m} {p}" for m, p in BODY_TARGETS])
async def test_a_key_changes_nothing_of_a_repository_or_owner_its_body_names_and_its_list_does_not_cover(
    method: str, path: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, body = BODY_TARGETS[(method, path)]
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, values):
        request = (url(values[UNCOVERED]), body(values[UNCOVERED]))
        found = await problems(app, keys, values, db_with_migrations, method, path, request=request)
    assert found == [], f"{method} {path}: {found}"


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
async def test_a_key_changes_nothing_of_a_repository_its_list_does_not_cover(
    method: str, path: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, values):
        found = await problems(app, keys, values, db_with_migrations, method, path)
    assert found == [], f"{method} {path}: {found}"


async def test_the_inventory_calls_a_new_route_that_acts_on_a_case_without_the_rule_and_reports_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not quiet by construction: a route that changes any repository's case is called and reported."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, values):
        extra = APIRouter()

        @extra.post("/cases/{case_id}/poke")
        async def poke(case_id: str, request: Request) -> dict[str, Any]:
            await request.app.state.db.execute("UPDATE healing_cases SET title = title || '!' WHERE id = $1", case_id)
            return {"poked": case_id}

        app.include_router(extra, prefix="/api/v1")
        path = "/api/v1/cases/{case_id}/poke"
        assert ("POST", path) in write_routes(app) and kind(path) == "repository", "the walk finds it by its parameter"
        found = await problems(app, keys, values, db_with_migrations, "POST", path)
    assert found == [
        f'the limited key was not refused: 200 {{"poked":"{ids(UNCOVERED)["case_id"]}"}}',
        "the limited key's call wrote to ['healing_cases']",
        f"the refusal is not audited once naming {UNCOVERED} or {OWNER[UNCOVERED]}/*: []",
    ], found


async def test_an_organization_need_s_case_filed_on_its_owner_is_closed_by_a_key_covering_the_owner(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A need covering several repositories files its case on the owner, not on one repository."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        for owner in OWNER.values():
            await db_with_migrations.execute(
                "INSERT INTO healing_cases (id, repo, origin, stage, title) VALUES ($1, $2, 'org', 'tracked', 'Need')",
                f"hc-org-{owner}",
                owner,
            )
        body = {"resolution_path": "abandoned"}
        other = await call(app, "POST", f"/api/v1/cases/hc-org-{OWNER[UNCOVERED]}/close", keys.limited, body)
        own = await call(app, "POST", f"/api/v1/cases/hc-org-{OWNER[COVERED]}/close", keys.limited, body)
    assert (other.status_code, other.json()) == (404, {"detail": "case not found"}), other.text
    assert own.status_code == 200 and own.json()["stage"] == "closed", own.text
    assert await _refusals(db_with_migrations) == [f"{OWNER[UNCOVERED]}/*"]


async def test_a_key_with_an_allowlist_revokes_only_keys_within_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never a key without an allowlist, nor one that lists a repository its own does not; those answer as a key
    that does not exist. A key within its list, itself included, it revokes."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        made = ApiKeysRepository(db)
        wider, _ = await made.create(
            name="wider", scope="operate", user_id=None, created_by="t", allowed_repos=[COVERED, UNCOVERED]
        )
        within, _ = await made.create(
            name="within", scope="operate", user_id=None, created_by="t", allowed_repos=[COVERED]
        )
        open_id = str(await db.fetchval("SELECT id FROM api_keys WHERE name = 'open'"))
        limited_id = str(await db.fetchval("SELECT id FROM api_keys WHERE name = 'limited'"))
        answers = {
            name: await call(app, "DELETE", f"/api/v1/apikeys/{key_id}", keys.limited)
            for name, key_id in (("open", open_id), ("wider", wider.id), ("missing", "key-0000000000ff"))
        }
        revoked = {str(r["name"]) for r in await db.fetch("SELECT name FROM api_keys WHERE revoked_at IS NOT NULL")}
        within_answer = await call(app, "DELETE", f"/api/v1/apikeys/{within.id}", keys.limited)
        own_answer = await call(app, "DELETE", f"/api/v1/apikeys/{limited_id}", keys.limited)
    ids = {"open": open_id, "wider": wider.id, "missing": "key-0000000000ff"}
    shown = {name: (r.status_code, r.text.replace(ids[name], "<id>")) for name, r in answers.items()}
    assert shown["open"] == shown["wider"] == shown["missing"], f"each answers as a missing key: {shown}"
    assert shown["missing"][0] == 404
    assert revoked == set(), f"neither is revoked: {revoked}"
    assert within_answer.status_code == 200 and own_answer.status_code == 200, (within_answer.text, own_answer.text)


async def test_starting_a_context_audit_refuses_before_reading_the_organization(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registered organization, a paused one and one that does not exist answer the key that does not cover
    them alike."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await db.execute("INSERT INTO organizations (owner, forge, status) VALUES ('initech', 'github', 'paused')")
        answers = {
            owner: await call(
                app, "POST", f"/api/v1/orgs/{owner}/context-audit/run", keys.limited, {"repo": f"{owner}/gizmos"}
            )
            for owner in (OWNER[UNCOVERED], "initech", "nosuch")
        }
    shown = {owner: (r.status_code, r.text.replace(owner, "<owner>")) for owner, r in answers.items()}
    assert len(set(shown.values())) == 1 and shown["nosuch"][0] == 403, f"the same refusal for each: {shown}"


class _Target(BaseModel):
    repo: str


class _Nudge(BaseModel):
    target: _Target
    note: str = ""


def test_the_walk_finds_a_new_route_whose_body_names_a_repository() -> None:
    """Not quiet by construction: a new write route with a repository in its body, nested or not, is found, so the
    test above fails until it is called here or placed."""
    app = _app()
    extra = APIRouter()

    @extra.post("/nudges-for-a-test")
    async def nudge(body: _Nudge) -> dict[str, Any]:
        return {"nudged": body.target.repo}

    app.include_router(extra, prefix="/api/v1")
    assert body_targets(app).get(("POST", "/api/v1/nudges-for-a-test")) == ["target.repo"]
