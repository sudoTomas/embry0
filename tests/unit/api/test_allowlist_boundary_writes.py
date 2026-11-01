"""A key's repository allowlist as a boundary for credentials, owners and keys, on the real app over a real database.

- A credential something beyond the key's list uses is not the key's to bind,
  even to its own repository, nor to change; ``GET /credentials`` lists to it
  only the credentials within its list.
- A key covers an owner only when it covers every repository registered under
  it: an organization's routes change all of them.
- ``GET /apikeys`` lists to a key with an allowlist only the keys within it.
- The check of what uses a credential and the write it allows are one
  transaction holding the credential's row, so a binding made in between waits
  for it.
- ``POST /pipelines/validate`` stores nothing and stays open to such a key.

The app and its keys are :mod:`tests.fixtures.repository_writes`'.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, MASTER, OWNER, UNCOVERED, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

#: A sibling of the covered repository under its owner, which the limited key does not cover.
SIBLING = f"{OWNER[COVERED]}/api"


async def _credential(app: Any, token: str, label: str, kind: str = "forge_token", provider: str = "github") -> str:
    made = await call(
        app,
        "POST",
        "/api/v1/credentials",
        token,
        {"kind": kind, "provider": provider, "label": label, "secret": "x" * 30},
    )
    assert made.status_code in (200, 201), made.text
    return str(made.json()["id"])


async def test_another_repository_s_credential_is_not_the_key_s_to_bind_list_or_change(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, values):
        llm = await _credential(app, MASTER, "theirs-llm", kind="llm_api_key", provider="anthropic")
        forge = await _credential(app, MASTER, "theirs-forge")
        bound = await call(
            app,
            "PUT",
            f"/api/v1/repos/{UNCOVERED}/-/credentials",
            MASTER,
            {"bindings": [{"credential_id": llm, "role": "llm"}, {"credential_id": forge, "role": "forge"}]},
        )
        assert bound.status_code == 200, bound.text
        own = await _credential(app, keys.limited, "own-forge")
        listed = await call(app, "GET", "/api/v1/credentials", keys.limited)
        reuse = await call(
            app,
            "PUT",
            f"/api/v1/repos/{COVERED}/-/credentials",
            keys.limited,
            {"bindings": [{"credential_id": llm, "role": "llm"}, {"credential_id": forge, "role": "forge"}]},
        )
        reuse_owner = await call(
            app,
            "PUT",
            "/api/v1/credentials/bindings",
            keys.limited,
            {
                "scope_kind": "owner",
                "scope_value": f"github:{OWNER[COVERED]}",
                "bindings": [{"credential_id": forge, "role": "forge"}],
            },
        )
        rotate = await call(app, "POST", f"/api/v1/credentials/{llm}/rotate", keys.limited, {"secret": "w" * 30})
        own_bound = await call(
            app,
            "PUT",
            f"/api/v1/repos/{COVERED}/-/credentials",
            keys.limited,
            {"bindings": [{"credential_id": own, "role": "forge"}]},
        )
        bindings = {
            str(r["scope_value"]): sorted(str(x) for x in r["ids"])
            for r in await db_with_migrations.fetch(
                "SELECT scope_value, array_agg(credential_id) AS ids FROM credential_bindings GROUP BY scope_value"
            )
        }
    labels = sorted(c["label"] for c in listed.json())
    assert labels == ["fixture-forge", "own-forge"], f"the limited key lists its own and the unused: {labels}"
    assert (reuse.status_code, reuse_owner.status_code, rotate.status_code) == (403, 403, 403), (
        reuse.text,
        reuse_owner.text,
        rotate.text,
    )
    assert own_bound.status_code == 200, own_bound.text
    assert bindings == {UNCOVERED.lower(): sorted([llm, forge]), COVERED.lower(): [own]}, bindings


#: Each route that changes an owner as a whole: its method, its path and a body naming the covered owner.
OWNER_ROUTES: dict[str, tuple[str, str, Any]] = {
    "register": ("POST", "/api/v1/orgs", {"owner": OWNER[COVERED], "forge": "github", "probe": False}),
    "change": ("PATCH", f"/api/v1/orgs/{OWNER[COVERED]}", {"display_name": "Renamed"}),
    "status": ("PUT", f"/api/v1/orgs/{OWNER[COVERED]}/status", {"status": "paused"}),
    "standard": ("PUT", f"/api/v1/standards/{OWNER[COVERED]}/design", {"schema": {}, "values": {}}),
    "webhook": ("POST", f"/api/v1/orgs/{OWNER[COVERED]}/webhook", None),
    "actions": ("POST", f"/api/v1/orgs/{OWNER[COVERED]}/actions", {"action": "deactivate", "repos": [COVERED]}),
    "need": ("POST", f"/api/v1/orgs/{OWNER[COVERED]}/needs/need-widget/dismiss", None),
    "binding": (
        "PUT",
        "/api/v1/credentials/bindings",
        {"scope_kind": "owner", "scope_value": f"github:{OWNER[COVERED]}", "bindings": []},
    ),
}


@pytest.mark.parametrize("name", OWNER_ROUTES)
async def test_a_key_changes_an_owner_only_when_it_covers_every_repository_registered_under_it(
    name: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The limited key covers the covered owner's one registered repository; once a sibling it does not cover is
    registered under that owner, every change to the owner is refused, naming it."""
    method, path, body = OWNER_ROUTES[name]
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        alone = await call(app, method, path, keys.limited, body)
        await db_with_migrations.execute("INSERT INTO repos (repo, active) VALUES ($1, true)", SIBLING)
        with_sibling = await call(app, method, path, keys.limited, body)
    assert alone.status_code != 403 or "repo_not_allowed" not in alone.text, (
        f"{name}: covering the owner's only repository, the key may change the owner: {alone.status_code} {alone.text}"
    )
    assert with_sibling.status_code == 403 and f"{OWNER[COVERED]}/*" in with_sibling.json()["detail"], (
        f"{name}: with {SIBLING} registered and not covered: {with_sibling.status_code} {with_sibling.text}"
    )


async def test_the_key_list_shows_a_key_with_an_allowlist_only_the_keys_within_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from embry0.storage.repositories.api_keys import ApiKeysRepository

    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        made = ApiKeysRepository(db_with_migrations)
        await made.create(name="within", scope="operate", user_id=None, created_by="t", allowed_repos=[COVERED])
        await made.create(
            name="wider", scope="operate", user_id=None, created_by="t", allowed_repos=[COVERED, UNCOVERED]
        )
        limited = await call(app, "GET", "/api/v1/apikeys", keys.limited)
        everyone = await call(app, "GET", "/api/v1/apikeys", keys.open)
    assert sorted(k["name"] for k in limited.json()["keys"]) == ["limited", "within"], limited.text
    assert sorted(k["name"] for k in everyone.json()["keys"]) == ["limited", "open", "wider", "within"], everyone.text


@pytest.mark.parametrize("change", ["rotate", "bind"])
async def test_a_binding_made_while_a_key_changes_a_credential_waits_and_is_seen(
    change: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another connection binds the key's credential to a repository the key does not cover, and has not committed.
    The key's change waits for it, then sees the binding and is refused: the check and the write are one
    transaction on the credential's row."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        own = await _credential(app, keys.limited, "own-forge")
        request = {
            "rotate": ("POST", f"/api/v1/credentials/{own}/rotate", {"secret": "w" * 30}),
            "bind": (
                "PUT",
                f"/api/v1/repos/{COVERED}/-/credentials",
                {"bindings": [{"credential_id": own, "role": "forge"}]},
            ),
        }[change]
        async with db.transaction() as conn:
            await conn.execute(
                "INSERT INTO credential_bindings (scope_kind, scope_value, role, provider, credential_id, created_by) "
                "VALUES ('repo', $1, 'forge', 'github', $2, 'someone')",
                UNCOVERED.lower(),
                own,
            )
            task = asyncio.create_task(call(app, *request[:2], keys.limited, request[2]))
            await asyncio.sleep(0.5)
            waited = not task.done()
        answer = await task
    assert waited, f"the key's {change} did not wait for the binding being made: {task.result().status_code}"
    assert answer.status_code == 403 and "repo_not_allowed" in answer.text, (
        f"once the binding is made the {change} sees it and is refused: {answer.status_code} {answer.text}"
    )


async def test_validating_a_template_stays_open_to_a_key_with_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from embry0.storage.seeds.pipeline_templates_builtin import seed_builtin_pipeline_templates

    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        await seed_builtin_pipeline_templates(app.state.templates_repo)
        listed = (await call(app, "GET", "/api/v1/pipelines/templates", MASTER)).json()
        first = (listed if isinstance(listed, list) else listed["templates"])[0]
        template = (await call(app, "GET", f"/api/v1/pipelines/templates/{first['id']}", MASTER)).json()
        checked = await call(app, "POST", "/api/v1/pipelines/validate", keys.limited, template["graph_definition"])
        refused = await call(
            app,
            "POST",
            "/api/v1/pipelines/templates",
            keys.limited,
            {"name": "x", "graph_definition": template["graph_definition"]},
        )
    assert checked.status_code == 200, f"it stores nothing: {checked.status_code} {checked.text}"
    assert refused.status_code == 403, "a template it may not store"


async def test_a_context_audit_of_the_key_s_own_repository_is_its_work_not_its_owner_s(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting a context audit is work on the repository it names: a sibling it does not cover, registered under the
    same owner, does not stop it, as it stops a change to the owner."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        await db_with_migrations.execute("INSERT INTO repos (repo, active) VALUES ($1, true)", SIBLING)
        own = await call(
            app, "POST", f"/api/v1/orgs/{OWNER[COVERED]}/context-audit/run", keys.limited, {"repo": COVERED}
        )
        sibling = await call(
            app, "POST", f"/api/v1/orgs/{OWNER[COVERED]}/context-audit/run", keys.limited, {"repo": SIBLING}
        )
    assert own.status_code == 200, own.text
    assert sibling.status_code == 403 and SIBLING in sibling.json()["detail"], sibling.text
