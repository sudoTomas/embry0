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
    assert labels == ["own-forge"], f"the limited key lists its own only: {labels}"
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
    # With no credential named, the probe acts with the deployment's own token for the owner.
    "probe": ("POST", "/api/v1/orgs/probe", {"owner": OWNER[COVERED], "forge": "github"}),
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
    detail = with_sibling.json().get("detail", "") if with_sibling.status_code == 403 else ""
    assert "every repository registered under the owner" in detail and SIBLING not in detail, (
        f"{name}: the refusal says what the key lacks, naming no repository it does not cover: {detail}"
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


async def test_a_credential_for_a_whole_owner_takes_a_key_without_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key covers every repository of its owner today, yet an owner binding or an organization's credential
    would reach a repository registered tomorrow: it binds per repository instead."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        own = await _credential(app, keys.limited, "own-forge")
        owner_binding = await call(
            app,
            "PUT",
            "/api/v1/credentials/bindings",
            keys.limited,
            {
                "scope_kind": "owner",
                "scope_value": f"github:{OWNER[COVERED]}",
                "bindings": [{"credential_id": own, "role": "forge"}],
            },
        )
        organization = await call(
            app,
            "POST",
            "/api/v1/orgs",
            keys.limited,
            {"owner": OWNER[COVERED], "forge": "github", "credential_id": own, "probe": False},
        )
        await db_with_migrations.execute(
            "INSERT INTO organizations (owner, forge, credential_id) VALUES ($1, 'github', $2) "
            "ON CONFLICT (owner) DO UPDATE SET credential_id = EXCLUDED.credential_id",
            OWNER[COVERED],
            values_credential := (await _credential(app, MASTER, "admin-forge")),
        )
        clear = await call(
            app, "POST", "/api/v1/orgs", keys.limited, {"owner": OWNER[COVERED], "forge": "github", "probe": False}
        )
        per_repo = await call(
            app,
            "PUT",
            f"/api/v1/repos/{COVERED}/-/credentials",
            keys.limited,
            {"bindings": [{"credential_id": own, "role": "forge"}]},
        )
        stored = await db_with_migrations.fetchval(
            "SELECT credential_id FROM organizations WHERE owner = $1", OWNER[COVERED]
        )
        owner_bindings = await db_with_migrations.fetchval(
            "SELECT count(*) FROM credential_bindings WHERE scope_kind = 'owner'"
        )
    for name, answer in (
        ("owner binding", owner_binding),
        ("organization credential", organization),
        ("clearing it", clear),
    ):
        assert answer.status_code == 403 and "takes a key without an allowlist" in answer.json()["detail"], (
            f"{name}: {answer.status_code} {answer.text}"
        )
    assert per_repo.status_code == 200, per_repo.text
    assert (stored, owner_bindings) == (values_credential, 0), "the organization keeps the admin's credential"


def _missing_like(cid: str) -> str:
    """A credential id that does not exist, as long as ``cid``."""
    return "cred-" + "0" * (len(cid) - 5)


async def _changes(app: Any, token: str, cid: str) -> list[tuple[int, str]]:
    """What rotating, renaming, deleting and binding the credential ``cid`` to the covered repository answer, the id
    written ``<id>``."""
    out = []
    for method, path, body in (
        ("POST", f"/api/v1/credentials/{cid}/rotate", {"secret": "w" * 30}),
        ("PUT", f"/api/v1/credentials/{cid}", {"label": "renamed-" + cid[-4:]}),
        ("DELETE", f"/api/v1/credentials/{cid}", None),
        (
            "PUT",
            f"/api/v1/repos/{COVERED}/-/credentials",
            {"bindings": [{"credential_id": cid, "role": "forge"}]},
        ),
    ):
        r = await call(app, method, path, token, body)
        out.append((r.status_code, r.text.replace(cid, "<id>")))
    return out


async def _listed(app: Any, token: str) -> list[str]:
    return sorted(c["label"] for c in (await call(app, "GET", "/api/v1/credentials", token)).json())


async def test_another_s_credential_bound_to_nothing_is_not_there_for_the_key(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An admin made it and has not bound it yet: the key does not list it, and changing or binding it answers as a
    credential that does not exist. One the key made itself is its own."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        theirs = await _credential(app, MASTER, "admin-unbound")
        mine = await _credential(app, keys.limited, "mine-unbound")
        missing = _missing_like(theirs)
        listed = await _listed(app, keys.limited)
        hidden, absent = await _changes(app, keys.limited, theirs), await _changes(app, keys.limited, missing)
        own_rotated = await call(app, "POST", f"/api/v1/credentials/{mine}/rotate", keys.limited, {"secret": "w" * 30})
        still = await db_with_migrations.fetchval("SELECT label FROM credentials WHERE id = $1", theirs)
    assert listed == ["mine-unbound"], listed
    assert hidden == absent, f"each answers as for a credential that does not exist: {hidden} against {absent}"
    assert [code for code, _ in hidden] == [404, 404, 404, 422], hidden
    assert still == "admin-unbound" and own_rotated.status_code == 200, own_rotated.text


async def test_a_repository_registered_under_an_old_slug_renamed_into_the_owner_counts_under_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from embry0.storage import repo_renames

    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        # After the app's start, which installs the deployment's own REPO_RENAMES.
        monkeypatch.setattr(repo_renames, "REPO_RENAMES", {"oldacme/api": SIBLING})
        await db_with_migrations.execute("INSERT INTO repos (repo, active) VALUES ('oldacme/api', true)")
        answer = await call(
            app, "PUT", f"/api/v1/standards/{OWNER[COVERED]}/design", keys.limited, {"schema": {}, "values": {}}
        )
    assert answer.status_code == 403 and f"{OWNER[COVERED]}/*" in answer.json()["detail"], answer.text


async def test_an_unbound_credential_is_the_key_s_that_created_it_not_another_s_of_the_same_name(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two keys of one name, each covering a repository of its own. An unbound credential one created is that key's
    alone: the other neither lists, changes nor binds it, and each answer is a missing credential's. Each credential
    records the id of the key that created it."""
    from embry0.storage.repositories.api_keys import ApiKeysRepository

    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _values):
        made = ApiKeysRepository(db_with_migrations)
        here_row, here = await made.create(
            name="dispatch-bot", scope="admin", user_id=None, created_by="t", allowed_repos=[COVERED]
        )
        there_row, there = await made.create(
            name="dispatch-bot", scope="admin", user_id=None, created_by="t", allowed_repos=[UNCOVERED]
        )
        theirs = await _credential(app, there, "there-unbound")
        mine = await _credential(app, here, "here-unbound")
        listed = await _listed(app, here)
        hidden, absent = await _changes(app, here, theirs), await _changes(app, here, _missing_like(theirs))
        own_bound = await call(
            app,
            "PUT",
            f"/api/v1/repos/{COVERED}/-/credentials",
            here,
            {"bindings": [{"credential_id": mine, "role": "forge"}]},
        )
        their_bound = await call(
            app,
            "PUT",
            f"/api/v1/repos/{UNCOVERED}/-/credentials",
            there,
            {"bindings": [{"credential_id": theirs, "role": "forge"}]},
        )
    assert listed == ["here-unbound"], f"the key lists only what it created, not its namesake's: {listed}"
    assert hidden == absent, f"its namesake's credential answers as a missing one: {hidden} against {absent}"
    assert (own_bound.status_code, their_bound.status_code) == (200, 200), (own_bound.text, their_bound.text)
    creators = {
        str(r["id"]): r["created_by_key_id"]
        for r in await db_with_migrations.fetch(
            "SELECT id, created_by_key_id FROM credentials WHERE id = ANY($1::text[])", [theirs, mine]
        )
    }
    assert creators == {theirs: there_row.id, mine: here_row.id}, f"each records the key that created it: {creators}"


async def test_a_credential_from_before_the_creating_key_was_recorded_is_no_key_s_with_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row from before the key's id was recorded names its creator only as ``key:<name>``, which a key of the same
    name could claim. Unbound, it is not a key's with an allowlist, its creator's included; a key without one sees
    and changes it as before."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        # As the limited key created it before the column: its name, and no id.
        old = "cred-000000000042"
        await db_with_migrations.execute(
            "INSERT INTO credentials (id, kind, provider, host, label, secret_ciphertext, masked_preview, created_by) "
            "VALUES ($1, 'forge_token', 'github', 'github.com', 'made-before', $2, '****', 'key:limited')",
            old,
            await app.state.secrets_provider.encrypt("x" * 30),
        )
        listed = await _listed(app, keys.limited)
        hidden, absent = await _changes(app, keys.limited, old), await _changes(app, keys.limited, _missing_like(old))
        open_listed = await _listed(app, keys.open)
        open_rotated = await call(app, "POST", f"/api/v1/credentials/{old}/rotate", keys.open, {"secret": "v" * 30})
    assert "made-before" not in listed, f"a key with an allowlist does not list it: {listed}"
    assert hidden == absent, f"it answers as a missing credential: {hidden} against {absent}"
    assert "made-before" in open_listed and open_rotated.status_code == 200, (open_listed, open_rotated.text)


async def test_a_probe_with_a_credential_not_the_key_s_answers_as_a_missing_one(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``POST /orgs/probe`` decrypts the credential it names and calls the forge with it. A key with an allowlist
    probes only with its own; another's, unbound or bound beyond its list, answers as an id that does not exist,
    before its secret is decrypted, and the refusal is audited. A key without a list probes with any."""
    from embry0.api.v1 import organizations
    from embry0.services.organizations import AccessProbe

    probed: list[str] = []

    async def probe_access(_request: Any, owner: str, _forge: str, _token: str) -> AccessProbe:
        probed.append(owner)
        return AccessProbe("bot", "Organization", True, None, None, 1, None, "ok")

    monkeypatch.setattr(organizations, "probe_access", probe_access)
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        unbound = await _credential(app, MASTER, "admin-unbound")
        # Not a forge token: one the key may see would answer 422 for that, after the existence step.
        unbound_llm = await _credential(app, MASTER, "admin-llm", kind="llm_api_key", provider="anthropic")
        beyond = await _credential(app, MASTER, "theirs-forge")
        bound = await call(
            app,
            "PUT",
            f"/api/v1/repos/{UNCOVERED}/-/credentials",
            MASTER,
            {"bindings": [{"credential_id": beyond, "role": "forge"}]},
        )
        assert bound.status_code == 200, bound.text
        own = await _credential(app, keys.limited, "own-forge")

        async def probe(token: str, cid: str) -> tuple[int, bytes, int]:
            before = await db_with_migrations.fetchval(
                "SELECT count(*) FROM audit_log WHERE action = 'apikey.repo_refused'"
            )
            r = await call(
                app,
                "POST",
                "/api/v1/orgs/probe",
                token,
                {"owner": OWNER[COVERED], "forge": "github", "credential_id": cid},
            )
            after = await db_with_migrations.fetchval(
                "SELECT count(*) FROM audit_log WHERE action = 'apikey.repo_refused'"
            )
            return r.status_code, r.content, after - before

        answers = {
            "unbound": await probe(keys.limited, unbound),
            "unbound llm": await probe(keys.limited, unbound_llm),
            "beyond": await probe(keys.limited, beyond),
            "missing": await probe(keys.limited, _missing_like(unbound)),
        }
        refused_probes = list(probed)
        own_answer = await probe(keys.limited, own)
        open_answer = await probe(keys.open, unbound)
    missing = (404, b'{"detail":"credential not found"}')
    for name in ("unbound", "unbound llm", "beyond"):
        assert answers[name][:2] == missing, f"another's {name} credential answers as a missing one: {answers[name]}"
        assert answers[name][2] == 1, f"the {name} probe is refused on record: {answers[name][2]} audit rows"
    assert answers["missing"] == (*missing, 0), answers["missing"]
    assert refused_probes == [], f"no probe ran with a credential not the key's: {refused_probes}"
    assert own_answer[0] == 200 and open_answer[0] == 200, (own_answer, open_answer)


#: What else is wrong with a binding of a forge token to the covered repository, by role: what a missing credential
#: answers there.
WRONG_TOO = {
    "tailnet": "scope 'repo' is not valid for role 'tailnet'",
    "deploy": "scope 'repo' is not valid for role 'deploy'",
    # A forge token is not an llm account: the existence step comes first, so a missing one answers that.
    "llm": "does not exist",
}


@pytest.mark.parametrize("role", WRONG_TOO)
async def test_a_hidden_credential_answers_as_a_missing_one_whatever_else_is_wrong_with_the_binding(
    role: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The binding is wrong besides (the role does not bind at a repository, or the credential is of another kind):
    a missing credential answers with what is wrong at its first step, and so must one the key may not see, byte for
    byte, rather than with words of its own."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        theirs = await _credential(app, MASTER, "admin-unbound")

        async def bind(cid: str) -> tuple[int, bytes, dict[str, str]]:
            r = await call(
                app,
                "PUT",
                f"/api/v1/repos/{COVERED}/-/credentials",
                keys.limited,
                {"bindings": [{"credential_id": cid, "role": role}]},
            )
            return (
                r.status_code,
                r.content.replace(cid.encode(), b"<id>"),
                {k: v for k, v in r.headers.items() if k != "date"},
            )

        hidden, absent = await bind(theirs), await bind(_missing_like(theirs))
    assert hidden == absent, f"{role}: the hidden credential answers {hidden[:2]}, a missing one {absent[:2]}"
    assert absent[0] == 422 and WRONG_TOO[role].encode() in absent[1], absent


async def test_a_probe_with_the_deployment_s_token_answers_only_at_an_owner_the_key_covers(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no credential named, ``POST /orgs/probe`` acts with the deployment's own token for the owner. To a key
    with an allowlist an owner it does not cover is refused as every change to an owner is, before that token is
    looked up; an owner it covers is probed. A key without a list probes any owner."""
    from embry0.api.v1 import organizations
    from embry0.execution import github_tokens
    from embry0.services.organizations import AccessProbe

    looked_up: list[str] = []
    probed: list[str] = []

    def resolve_github_token(owner: str, _default: str) -> str:
        looked_up.append(owner)
        return "t" * 20

    async def probe_access(_request: Any, owner: str, _forge: str, _token: str) -> AccessProbe:
        probed.append(owner)
        return AccessProbe("bot", "Organization", True, None, None, 1, f"{owner}/repo", "ok")

    monkeypatch.setattr(github_tokens, "resolve_github_token", resolve_github_token)
    monkeypatch.setattr(organizations, "probe_access", probe_access)
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):

        async def probe(token: str, owner: str) -> Any:
            return await call(app, "POST", "/api/v1/orgs/probe", token, {"owner": owner, "forge": "github"})

        uncovered = await probe(keys.limited, OWNER[UNCOVERED])
        refused_lookups, refused_probes = list(looked_up), list(probed)
        covered = await probe(keys.limited, OWNER[COVERED])
        open_key = await probe(keys.open, OWNER[UNCOVERED])
    detail = uncovered.json().get("detail", "") if uncovered.status_code == 403 else ""
    assert detail.startswith("repo_not_allowed") and f"{OWNER[UNCOVERED]}/*" in detail, (
        f"an owner the key does not cover is refused as an owner: {uncovered.status_code} {uncovered.text}"
    )
    assert (refused_lookups, refused_probes) == ([], []), (
        f"the deployment's token was not looked up or used for it: {refused_lookups} {refused_probes}"
    )
    assert covered.status_code == 200 and covered.json()["source"].startswith("env:"), covered.text
    assert open_key.status_code == 200, open_key.text


async def _llm_credential(app: Any, token: str, label: str) -> str:
    return await _credential(app, token, label, kind="llm_api_key", provider="anthropic")


async def test_run_with_a_key_the_repository_does_not_resolve_answers_as_a_missing_one(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job's ``run_with`` names a stored LLM key. To a key with an allowlist, one that is not its own and that no job
    on a repository its list covers resolves answers byte for byte as an id that does not exist, with no label or
    status, and the refusal is audited: another repository's key, for the key's repository or for that one, and
    someone else's unbound key. A key the repository resolves, the global pool's included, stays offered and
    choosable, as does the key's own; a key without a list is answered as before. A key that passes the check stops at
    the branch a QA run needs, so nothing starts."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        theirs = await _llm_credential(app, MASTER, "theirs-llm")
        unbound = await _llm_credential(app, MASTER, "admin-llm")
        pool = await _llm_credential(app, MASTER, "pool-llm")
        mine = await _llm_credential(app, keys.limited, "mine-llm")
        mine_unbound = await _llm_credential(app, keys.limited, "mine-spare-llm")
        for token, path, body in (
            (
                MASTER,
                f"/api/v1/repos/{UNCOVERED}/-/credentials",
                {"bindings": [{"credential_id": theirs, "role": "llm"}]},
            ),
            (
                MASTER,
                "/api/v1/credentials/bindings",
                {"scope_kind": "global", "scope_value": "", "bindings": [{"credential_id": pool, "role": "llm"}]},
            ),
            (
                keys.limited,
                f"/api/v1/repos/{COVERED}/-/credentials",
                {"bindings": [{"credential_id": mine, "role": "llm"}]},
            ),
        ):
            bound = await call(app, "PUT", path, token, body)
            assert bound.status_code == 200, bound.text

        async def run_with(token: str, cid: str, repo: str = COVERED) -> tuple[int, bytes, dict[str, str], int]:
            before = await db_with_migrations.fetchval(
                "SELECT count(*) FROM audit_log WHERE action = 'apikey.repo_refused'"
            )
            r = await call(
                app,
                "POST",
                "/api/v1/jobs",
                token,
                {"repo": repo, "pipeline": "qa", "run_with": {"credential_id": cid}},
            )
            after = await db_with_migrations.fetchval(
                "SELECT count(*) FROM audit_log WHERE action = 'apikey.repo_refused'"
            )
            headers = {k: v for k, v in r.headers.items() if k != "date"}
            return r.status_code, r.content.replace(cid.encode(), b"<id>"), headers, after - before

        absent = await run_with(keys.limited, _missing_like(theirs))
        hidden = {
            "another repository's key": await run_with(keys.limited, theirs),
            "another repository's key, on that repository": await run_with(keys.limited, theirs, UNCOVERED),
            "someone else's unbound key": await run_with(keys.limited, unbound),
        }
        chosen = {
            "the pool's key": await run_with(keys.limited, pool),
            "its own key": await run_with(keys.limited, mine),
            "the pool's key, without a list": await run_with(keys.open, pool),
        }
        own_unbound = await run_with(keys.limited, mine_unbound)
        open_theirs = await run_with(keys.open, theirs)
        offered = await call(app, "GET", f"/api/v1/run-with/keys?repo={COVERED}", keys.limited)
        jobs = await db_with_migrations.fetchval("SELECT count(*) FROM jobs WHERE repo = $1", COVERED)
    assert absent[:2] == (422, b'{"detail":"run_with: <id> is not a stored LLM key"}') and absent[3] == 0, absent
    for name, answer in hidden.items():
        assert answer[:3] == absent[:3], f"{name} answers {answer[:2]}, a missing one {absent[:2]}"
        assert answer[3] == 1, f"{name}: the refusal is on record, {answer[3]} audit rows"
    needs_branch = (400, b'{"detail":"qa pipeline requires \'branch\'"}')
    for name, answer in chosen.items():
        assert answer[:2] == needs_branch and answer[3] == 0, f"{name} is choosable: {answer[:2]}"
    assert [k["label"] for k in offered.json()["keys"]] == ["mine-llm", "pool-llm"], (
        f"the keys the repository resolves are offered: {offered.text}"
    )
    assert own_unbound[0] == 422 and b"'mine-spare-llm' is not bound for acme/web" in own_unbound[1], (
        f"its own key, bound nowhere, is answered as its own: {own_unbound[:2]}"
    )
    assert open_theirs[0] == 422 and b"'theirs-llm' is not bound for acme/web" in open_theirs[1], open_theirs[:2]
    assert jobs == 1, f"nothing started: {jobs} jobs on {COVERED}, the fixture's own"
