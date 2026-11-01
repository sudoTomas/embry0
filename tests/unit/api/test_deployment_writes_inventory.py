"""Every write route that belongs to the deployment, rather than to a repository or an owner, refuses a key with an
allowlist.

The global variables, the agent context, the agents, the skills, the sandbox
profiles, the pipeline templates, the hosts, the people, the integrations and
the models steer every repository's jobs. A key with a repository allowlist
changes none of them, as the settings document's deployment layer already
refuses it (``embry0.api.repo_allowlist.enforce_deployment_allowlist``).

Each write route under ``/api/v1`` is one of:

- scoped to a repository or an owner, or a read sent as a POST: the routes
  ``test_repository_writes_inventory.py`` calls or places;
- the deployment's (:data:`DEPLOYMENT_WIDE`): called here on the real app over
  a real database through an admin key whose list covers one repository. It
  must answer the refusal, a 403 ``repo_not_allowed`` naming the deployment,
  write nothing to any table, leave one audit row and nothing the key can read
  back in ``GET /audit``; through a key without a list it must reach the
  route's own answer;
- held to a rule of its own, or not called with an API key at all
  (:data:`OTHER_RULES`), with why.

A new write route fails until it is one of them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, Request

from embry0.api.repo_allowlist import DEPLOYMENT
from embry0.storage.database import DatabasePool
from embry0.storage.seeds.pipeline_templates_builtin import seed_builtin_pipeline_templates
from embry0.storage.seeds.sandbox_profiles_builtin import seed_builtin_sandbox_profiles
from tests.fixtures.repository_writes import COVERED, MASTER, OWNER, UNCOVERED, Keys, call, repository_writes_app
from tests.unit.api import test_repository_writes_inventory as scoped

_PARAMETER = re.compile(r"\{(\w+)(?::\w+)?\}")
#: This module's name, as the repository writes inventory names it for the routes it leaves here.
HERE = "test_deployment_writes_inventory.py"

#: Each deployment-wide write route, with a body it takes, a callable of the values :func:`_deployment` makes.
DEPLOYMENT_WIDE: dict[tuple[str, str], Callable[[Mapping[str, str]], Any]] = {
    ("PUT", "/api/v1/environment/global"): lambda v: {
        "variables": [{"key": "PIP_INDEX_URL", "value": "https://packages.example.test/simple"}]
    },
    ("PUT", "/api/v1/config/context"): lambda v: {"system_context": "Be brief.", "assistant_context": ""},
    ("PUT", "/api/v1/config/integrations"): lambda v: {"trigger_labels": ["embry0"]},
    ("PUT", "/api/v1/config/provider"): lambda v: {"default_model": "claude-sonnet-4-5"},
    ("POST", "/api/v1/config/provider/test"): lambda v: None,
    ("POST", "/api/v1/config/providers/{provider_name}/test"): lambda v: None,
    ("POST", "/api/v1/config/providers/openrouter/catalog/refresh"): lambda v: None,
    ("POST", "/api/v1/agents"): lambda v: {"type": "helper", "description": "Helps.", "model": "claude-sonnet-4-5"},
    ("PUT", "/api/v1/agents/{agent_type}"): lambda v: {"description": "Forges the change, carefully."},
    ("DELETE", "/api/v1/agents/{agent_type}"): lambda v: None,
    ("POST", "/api/v1/agents/{agent_type}/reset"): lambda v: None,
    ("POST", "/api/v1/agents/{agent_type}/skills/{skill_name}"): lambda v: None,
    ("DELETE", "/api/v1/agents/{agent_type}/skills/{skill_name}"): lambda v: None,
    ("PUT", "/api/v1/skills/inline/{name}"): lambda v: {"description": "A note.", "body": "Say less."},
    ("DELETE", "/api/v1/skills/inline/{name}"): lambda v: None,
    ("POST", "/api/v1/skills/sources"): lambda v: {"id": "extra", "kind": "path", "url": "/tmp/skills"},
    ("PATCH", "/api/v1/skills/sources/{source_id}"): lambda v: {"enabled": False},
    ("DELETE", "/api/v1/skills/sources/{source_id}"): lambda v: None,
    ("POST", "/api/v1/skills/sources/{source_id}/sync"): lambda v: None,
    ("POST", "/api/v1/sandbox-profiles"): lambda v: {"name": "probe-profile"},
    ("PUT", "/api/v1/sandbox-profiles/{name}"): lambda v: {"name": v["profile"], "memory": "4g"},
    ("DELETE", "/api/v1/sandbox-profiles/{name}"): lambda v: None,
    ("POST", "/api/v1/sandbox-profiles/{name}/reset"): lambda v: None,
    ("POST", "/api/v1/pipelines/templates"): lambda v: {"name": "probe", "graph_definition": v["graph"]},
    ("PUT", "/api/v1/pipelines/templates/{template_id}"): lambda v: {"description": "Changed."},
    ("DELETE", "/api/v1/pipelines/templates/{template_id}"): lambda v: None,
    ("POST", "/api/v1/pipelines/templates/{template_id}/duplicate"): lambda v: {"name": "probe-copy"},
    ("PUT", "/api/v1/repos/{full_repo:repo}/-/docker-access"): lambda v: {"enabled": True},
    ("POST", "/api/v1/hosts"): lambda v: {"name": "probe-host", "transport": "http", "address": "https://h.test"},
    ("PATCH", "/api/v1/hosts/{host_id}"): lambda v: {"tags": ["probe"]},
    ("DELETE", "/api/v1/hosts/{host_id}"): lambda v: None,
    ("POST", "/api/v1/hosts/{host_id}/probe"): lambda v: None,
    ("POST", "/api/v1/hosts/import/tailnet"): lambda v: {"confirm": True},
    ("PUT", "/api/v1/users/{user_id}/disabled"): lambda v: {"disabled": True},
    ("POST", "/api/v1/users/{user_id}/force-logout"): lambda v: None,
    ("PUT", "/api/v1/users/{user_id}/github-login"): lambda v: {"login": "someone-else"},
    ("POST", "/api/v1/credentials/import-env"): lambda v: {"replace": False},
    ("POST", "/api/v1/credentials/xai/device-login"): lambda v: {"label": "probe"},
    ("POST", "/api/v1/progression/rebuild"): lambda v: None,
}

#: Deployment-wide routes whose unrestricted call needs a service or a network this app does not have, and so does
#: not reach its own answer here; the refusal of the limited key, a dependency that runs first, is still checked.
NO_ANSWER_HERE: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/config/providers/{provider_name}/test"): "the providers are named by OPENAI_COMPATIBLE_PROVIDERS",
    ("POST", "/api/v1/config/providers/openrouter/catalog/refresh"): "OpenRouter's list is read where it is configured",
    ("POST", "/api/v1/skills/sources/{source_id}/sync"): "skill sync starts with the production lifespan",
    ("POST", "/api/v1/credentials/xai/device-login"): "asks xAI for a device code over the network",
}

#: Write routes without a path parameter that store nothing: a read sent as a POST, open to a key with an allowlist.
READS_HERE: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/pipelines/validate"): "checks a template's graph and stores nothing (the MCP template_validate)",
}

#: Write routes held to a rule of their own, or not called with an API key, and why.
OTHER_RULES: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/apikeys"): "a key with an allowlist mints no key (test_api_key_repo_allowlist.py)",
    ("PUT", "/api/v1/apikeys/{key_id}/allowed-repos"): "a key with an allowlist changes no key's list",
    ("DELETE", "/api/v1/apikeys/{key_id}"): "revokes only keys within the caller's list (repository writes inventory)",
    ("POST", "/api/v1/config/settings/revisions/{revision_id}/undo"): "the settings document, by the layer undone",
    ("PUT", "/api/v1/config/budget"): "writes the settings document's budget section, refused as it is",
    **{
        (method, path): "the progression pack: refused to a key with an allowlist (test_progression_pack_api.py)"
        for method, path in (
            ("PUT", "/api/v1/progression/pack"),
            ("PUT", "/api/v1/progression/pack/draft"),
            ("DELETE", "/api/v1/progression/pack/draft"),
            ("POST", "/api/v1/progression/pack/draft/activate"),
            ("POST", "/api/v1/progression/pack/reset"),
            ("POST", "/api/v1/progression/pack/versions/{version}/restore"),
        )
    },
    ("POST", "/api/v1/credentials"): "creates a credential nothing uses yet: binding it is checked where it is bound",
    **{
        (method, path): "a credential: refused only when something beyond the key's list uses it (test below)"
        for method, path in (
            ("PUT", "/api/v1/credentials/{credential_id}"),
            ("DELETE", "/api/v1/credentials/{credential_id}"),
            ("POST", "/api/v1/credentials/{credential_id}/rotate"),
            ("POST", "/api/v1/credentials/{credential_id}/verify"),
        )
    },
    ("POST", "/api/v1/asks/answer"): "a decision named by its code (test_offered_work_actions_inventory.py)",
    ("PUT", "/api/v1/me/github-login"): "a signed-in person's own login; a key is refused",
    ("POST", "/api/v1/auth/logout"): "ends the caller's own session",
    ("POST", "/api/v1/internal/qa/presign"): "a sandbox's own request, with its sandbox token, not an API key",
    ("POST", "/api/v1/telegram/callback"): "Telegram's, signed with the bot's secret, not an API key",
    **{
        ("POST", path): "a forge's or a tool's delivery, signed with its secret, not an API key"
        for path in (
            "/api/v1/webhook",
            "/api/v1/webhook/alertmanager",
            "/api/v1/webhook/gitlab",
            "/api/v1/webhook/linear",
        )
    },
}


def _scoped_elsewhere() -> set[tuple[str, str]]:
    """The write routes the repository writes inventory calls or places, and the reads sent as a POST."""
    reads = {(m, p) for m, p in scoped._ROUTES if p in scoped.READS} | set(READS_HERE)
    elsewhere = {route for route, why in scoped.BODY_TARGETS_ELSEWHERE.items() if HERE not in why}
    return set(scoped.ROUTES) | set(scoped.BODY_TARGETS) | elsewhere | reads


def all_writes(app: FastAPI) -> set[tuple[str, str]]:
    """Every write route under ``/api/v1``, old paths aside."""
    return {
        (method, route.path)
        for route in scoped.app_routes(app)
        if route.is_api_route and route.path.startswith("/api/v1/") and not isinstance(route.route, scoped.OldPathRoute)
        for method in (route.methods or set()) - {"GET", "HEAD", "OPTIONS"}
    }


def test_every_write_route_is_scoped_the_deployment_s_or_held_to_a_rule_of_its_own() -> None:
    writes = all_writes(scoped._app())
    elsewhere = _scoped_elsewhere()
    placed = elsewhere | set(DEPLOYMENT_WIDE) | set(OTHER_RULES)
    unplaced = sorted(f"{m} {p}" for m, p in writes - placed)
    assert unplaced == [], (
        f"write routes not placed: {unplaced}. A route that changes what belongs to the deployment refuses a key "
        "with an allowlist (embry0.api.repo_allowlist.deployment_writes) and is listed in DEPLOYMENT_WIDE; one "
        "scoped to a repository or an owner goes to test_repository_writes_inventory.py"
    )
    twice = sorted(f"{m} {p}" for m, p in (set(DEPLOYMENT_WIDE) | set(OTHER_RULES)) & elsewhere)
    assert twice == [], f"placed here and in the repository writes inventory: {twice}"
    stale = sorted(f"{m} {p}" for m, p in (set(DEPLOYMENT_WIDE) | set(OTHER_RULES)) - writes)
    assert stale == [], f"listed here but not served: {stale}"


async def _deployment(app: FastAPI, db: DatabasePool) -> dict[str, str]:
    """A value for each path parameter of a deployment-wide route, made through the master key or stored."""
    # What the production lifespan seeds beside _init_app_state: the builtin templates and profiles.
    await seed_builtin_pipeline_templates(app.state.templates_repo)
    await seed_builtin_sandbox_profiles(app.state.profiles_repo)
    templates = (await call(app, "GET", "/api/v1/pipelines/templates", MASTER)).json()
    template = templates[0] if isinstance(templates, list) else templates["templates"][0]
    host = await call(
        app, "POST", "/api/v1/hosts", MASTER, {"name": "a-host", "transport": "http", "address": "h.test"}
    )
    assert host.status_code in (200, 201), host.text
    skill = await call(app, "PUT", "/api/v1/skills/inline/a-note", MASTER, {"description": "A note.", "body": "Hi."})
    assert skill.status_code in (200, 201), skill.text
    source = await call(
        app, "POST", "/api/v1/skills/sources", MASTER, {"id": "a-source", "kind": "path", "url": "/srv"}
    )
    assert source.status_code in (200, 201), source.text
    await db.execute(
        "INSERT INTO users (id, auth0_sub, email, name, tier) VALUES ('usr-bo', 'auth0|bo', 'bo@example.com', 'bo', 'operator')"
    )
    return {
        "agent_type": "developer",
        "skill_name": "commit-standard",
        "provider_name": "openrouter",
        "name": "a-note",
        "profile": "dev-python",
        "source_id": "a-source",
        "template_id": str(template["id"]),
        "graph": template.get("graph_definition") or {},
        "host_id": str(host.json()["id"]),
        # A key's own repository: the refusal holds there too.
        "full_repo": COVERED,
        "user_id": "usr-bo",
    }


def _url(path: str, values: Mapping[str, Any]) -> str:
    if path.startswith("/api/v1/sandbox-profiles/{name}"):
        values = {**values, "name": values["profile"]}
    return _PARAMETER.sub(lambda m: str(values[m.group(1)]), path)


async def _problems(
    app: FastAPI, keys: Keys, db: DatabasePool, method: str, path: str, values: Mapping[str, Any]
) -> list[str]:
    url, body = _url(path, values), DEPLOYMENT_WIDE[(method, path)](values)
    out: list[str] = []
    tables, refused, readable = (
        await scoped._tables(db),
        await scoped._refusals(db),
        await scoped._audit_seen(app, keys.limited),
    )
    limited = await call(app, method, url, keys.limited, body)
    detail = (
        limited.json().get("detail") if limited.headers.get("content-type", "").startswith("application/json") else None
    )
    if limited.status_code != 403 or not str(detail).startswith("repo_not_allowed") or DEPLOYMENT not in str(detail):
        out.append(f"the limited key was not refused as the deployment's: {limited.status_code} {limited.text[:160]}")
    changed = sorted(n for n, d in (await scoped._tables(db)).items() if tables.get(n) != d)
    if changed:
        out.append(f"the limited key's call wrote to {changed}")
    if (await scoped._refusals(db))[len(refused) :] != [DEPLOYMENT]:
        out.append("the refusal is not audited once naming the deployment")
    if await scoped._audit_seen(app, keys.limited) != readable:
        out.append("the limited key reads back in GET /audit a row its refused call left")
    reached = await call(app, method, url, keys.open, body)
    why = scoped._not_reached(reached)
    if why is not None and (method, path) not in NO_ANSWER_HERE:
        out.append(f"a key without an allowlist did not reach the route: {why}")
    return out


@pytest.mark.parametrize(("method", "path"), list(DEPLOYMENT_WIDE), ids=[f"{m} {p}" for m, p in DEPLOYMENT_WIDE])
async def test_a_key_with_an_allowlist_changes_nothing_of_the_deployment(
    method: str, path: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        values = await _deployment(app, db_with_migrations)
        found = await _problems(app, keys, db_with_migrations, method, path, values)
    assert found == [], f"{method} {path}: {found}"


async def test_a_key_with_an_allowlist_changes_a_credential_only_when_nothing_beyond_its_list_uses_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bound only to its own repository, or created by it and bound to nothing, the credential is the key's to
    change; bound to another repository, to an owner (its own included: an owner's credential reaches repositories
    registered later), to a host or to every repository, it is the deployment's, refused."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, values):

        async def credential(label: str) -> str:
            made = await call(
                app,
                "POST",
                "/api/v1/credentials",
                keys.limited,
                {"kind": "forge_token", "provider": "github", "label": label, "secret": "x" * 24},
            )
            assert made.status_code in (200, 201), f"a key with an allowlist creates a credential: {made.text}"
            return str(made.json()["id"])

        uses = {
            "nothing": None,
            "its repository": ("repo", COVERED),
            "its owner": ("owner", f"github:{OWNER[COVERED]}"),
            "another repository": ("repo", UNCOVERED),
            "another owner": ("owner", f"github:{OWNER[UNCOVERED]}"),
            "every repository": ("global", ""),
        }
        ids = {}
        for name, use in uses.items():
            ids[name] = await credential(name.replace(" ", "-"))
            if use is not None:
                await db.execute(
                    "INSERT INTO credential_bindings (scope_kind, scope_value, role, provider, credential_id, created_by) "
                    "VALUES ($1, $2, 'forge', 'github', $3, 't')",
                    use[0],
                    use[1],
                    ids[name],
                )
        answers = {
            name: (
                await call(app, "PUT", f"/api/v1/credentials/{cid}", keys.limited, {"label": f"{cid}-renamed"})
            ).status_code
            for name, cid in ids.items()
        }
        bound = await call(
            app,
            "PUT",
            "/api/v1/credentials/bindings",
            keys.limited,
            {
                "scope_kind": "global",
                "scope_value": "",
                "bindings": [{"credential_id": ids["nothing"], "role": "forge"}],
            },
        )
    assert answers == {
        "nothing": 200,
        "its repository": 200,
        "its owner": 403,
        "another repository": 403,
        "another owner": 403,
        "every repository": 403,
    }, answers
    assert bound.status_code == 403 and DEPLOYMENT in bound.text, f"binding for every repository: {bound.text}"


_planted = APIRouter()


@_planted.put("/global-banner-for-a-test")
async def _banner(request: Request) -> dict[str, Any]:
    """A deployment-wide write without the refusal: it changes every repository's notes."""
    await request.app.state.db.execute("UPDATE repos SET notes = 'banner'")
    return {"banner": True}


async def test_the_inventory_finds_a_new_deployment_wide_route_without_the_refusal(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not quiet by construction: a new write route is unplaced until listed, and once listed here without the
    refusal it is reported."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        app.include_router(_planted, prefix="/api/v1")
        route = ("PUT", "/api/v1/global-banner-for-a-test")
        assert route in all_writes(app) - _scoped_elsewhere() - set(DEPLOYMENT_WIDE) - set(OTHER_RULES)
        DEPLOYMENT_WIDE[route] = lambda v: None
        try:
            found = await _problems(app, keys, db_with_migrations, *route, await _deployment(app, db_with_migrations))
        finally:
            del DEPLOYMENT_WIDE[route]
    assert found[:2] == [
        'the limited key was not refused as the deployment\'s: 200 {"banner":true}',
        "the limited key's call wrote to ['repos']",
    ], found
