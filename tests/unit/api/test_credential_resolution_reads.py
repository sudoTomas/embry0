"""A repository's credential resolution names, to a key with an allowlist, none of the deployment's credentials.

On the real app over a real database (:mod:`tests.fixtures.repository_reads`):
the forge's host binds a forge credential labelled :data:`DEPLOYMENT_MARK`, the
global pool a model account of each provider and each owner one, disabled,
labelled :data:`POOL_MARK`. A key with an allowlist reads the bindings of the
repositories it covers and of an owner it covers as a whole, and none of the
host's or the global pool's (``GET /credentials/bindings``). A forge resolution
of one of its repositories (``GET /repos/{repo}/-/credentials``,
``GET /credentials/resolve``, the readiness checklist and the forge credential
``GET /repos/{repo}/-/environment/detect`` resolves) is held to the same rule:
a step at a scope it does not read is one line that names nothing, and a
credential resolved there, or a pinned one there that is unavailable, is the
deployment's, unnamed. A model account of the repository, its owner or the
global pool is one a job on the repository may run with, which the key
chooses by label: it reads it as any key does, the account a setup run would
pay with included; one the environment serves stays unnamed. A key without a
list reads every resolution as before.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.api.deps import bust_api_key_cache
from embry0.services.credentials.resolver import CredentialUnavailableError
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.repository_reads import (
    DEPLOYMENT_MARK,
    LONE,
    LONE_JOB,
    OWNER_MARK,
    POOL_MARK,
    POOL_PROVIDER,
    SIBLING,
    repository_reads_app,
    seed_lone,
)
from tests.fixtures.repository_writes import COVERED, MASTER, call

pytestmark = pytest.mark.requires_postgres

#: A model account in the environment, which only a caller without an allowlist is told about.
ENV_KEY = "XAI_API_KEY"
#: Where the environment serves an Anthropic account from, as the deployment's auth mode reads it.
ANTHROPIC_ENV = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
#: What the answer names in place of a credential of a scope the key does not read, the source it gives it, and the
#: one trace line that stands for those steps (``embry0.api.resolution_view``), as the API writes them.
DEPLOYMENT_CREDENTIAL, DEPLOYMENT_SOURCE, DEPLOYMENT_STEPS = (
    "(the deployment's)",
    "deployment",
    "(the deployment's credentials)",
)


async def _key(db: DatabasePool, name: str, allowed: list[str]) -> str:
    _row, token = await ApiKeysRepository(db).create(
        name=name, scope="admin", user_id=None, created_by="tests", allowed_repos=allowed
    )
    bust_api_key_cache()
    return token


async def _json(app: Any, url: str, token: str) -> Any:
    response = await call(app, "GET", url, token)
    assert response.status_code == 200, f"GET {url}: {response.status_code} {response.text}"
    return response.json()


def _hidden(provider: str, trace: list[str], *, host: bool = True) -> dict[str, Any]:
    """A credential resolved at a scope the key does not read, as the key reads it."""
    out: dict[str, Any] = {"id": None, "label": DEPLOYMENT_CREDENTIAL, "provider": provider}
    if host:
        out["host"] = ""
    return {**out, "source": DEPLOYMENT_SOURCE, "trace": trace}


async def test_a_model_account_a_job_may_run_with_reads_to_a_key_with_an_allowlist_as_to_any_key(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``acme/web`` binds no model account of the pool's provider; its owner's is disabled, so it resolves from the
    global pool. Both are accounts a job on ``acme/web`` may run with (``GET /run-with/keys`` offers them by label),
    so the limited key, which covers ``acme`` only in part, reads the resolution, its steps and the account as a key
    covering ``acme`` as a whole and a key without a list do. Once the owner's account is pinned, the resolution
    stops at it, named (409) to each of them; the error a job of the repository stores names it to none."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        whole = await _key(db, "whole", [COVERED, SIBLING])
        readers = (("limited", keys.limited), ("whole", whole), ("open", keys.open))
        views = {name: await _json(app, f"/api/v1/repos/{COVERED}/-/credentials", token) for name, token in readers}
        url = f"/api/v1/credentials/resolve?repo={COVERED}&role=llm&provider={POOL_PROVIDER}"
        resolved = {name: await _json(app, url, token) for name, token in readers}
        # The owner's model account pinned, and disabled: the resolution stops at it.
        owner = await _json(app, "/api/v1/credentials/bindings?scope_kind=owner&scope_value=github:acme", MASTER)
        bindings = [
            {"role": b["role"], "credential_id": b["credential_id"], "pinned": b["role"] == "llm"}
            for b in owner["bindings"]
        ]
        body = {"scope_kind": "owner", "scope_value": "github:acme", "bindings": bindings}
        assert (await call(app, "PUT", "/api/v1/credentials/bindings", MASTER, body)).status_code == 200
        pinned = {name: await call(app, "GET", url, token) for name, token in readers}
        pinned_view = {
            name: (await _json(app, f"/api/v1/repos/{COVERED}/-/credentials", token))["effective"]
            for name, token in readers
        }
        # What a job of the repository that stops at the pin stores as its error, for every reader of the job.
        try:
            await app.state.credential_resolver.resolve_llm(COVERED, POOL_PROVIDER)
            stored = "resolved"
        except CredentialUnavailableError as exc:
            stored = str(exc)
    # A model account is looked up at the owner on the repository's own forge only.
    owner_steps = [
        f"owner:github:acme -> {POOL_MARK}-acme skipped (disabled)",
        "owner:github:acme -> none",
    ]
    pool = views["open"]["effective"]["llm"][POOL_PROVIDER]
    assert pool["label"] == f"{POOL_MARK}-global" and pool["source"] == "registry", pool
    assert pool["trace"] == [f"repo:{COVERED} -> none", *owner_steps, f"global: -> {POOL_MARK}-global"], pool
    assert views["limited"] == views["whole"] == views["open"], (
        f"a key with an allowlist reads the model accounts its repository may run with as any key: {views}"
    )
    assert resolved["limited"] == resolved["whole"] == resolved["open"], resolved
    named = f"credential '{POOL_MARK}-acme' is pinned for {COVERED} but disabled"
    assert {name: (r.status_code, r.json()["detail"]) for name, r in pinned.items()} == {
        "limited": (409, named),
        "whole": (409, named),
        "open": (409, named),
    }, "a pinned owner's model account that is unavailable is named to every key covering the repository"
    assert pinned_view["limited"] == pinned_view["whole"] == pinned_view["open"], pinned_view
    why = f"; GET /credentials/resolve?repo={COVERED}&role=llm&provider={POOL_PROVIDER} shows why"
    assert stored == f"the owner's credential pinned for {COVERED} cannot be used{why}", (
        f"a job stopped by its owner's pinned account stores an error naming it: {stored}"
    )


async def test_the_host_s_credential_and_the_environment_s_name_nothing_to_a_key_with_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``initech/app`` is the only repository under its owner, so a key listing it covers the owner as a whole and
    reads its owner's steps. Its forge credential is the host's, which the readiness checklist names, its setup run
    would pay with the global pool's Anthropic account, its model account is the global pool's, then the
    environment's, then none, and at last a pinned host credential that is disabled stops the forge's resolution and
    the variable template's read: each named to a key without a list, none to the key, which is answered as when
    nothing resolves. Its job that failed for want of an account stored an error that names none, whoever reads it.
    """
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        lone = await seed_lone(app, db, monkeypatch)
        readers = (("lone", lone), ("open", keys.open))
        forge_steps = [f"repo:{LONE} -> none", "owner:github:initech -> none"]
        forge = {
            name: (await _json(app, f"/api/v1/repos/{LONE}/-/credentials", token))["effective"]["forge"]
            for name, token in readers
        }
        readiness = {
            name: next(
                row["says"]
                for row in (await _json(app, f"/api/v1/repos/{LONE}/-/readiness", token))["rows"]
                if row["key"] == "code_access"
            )
            for name, token in readers
        }
        paid_from = {
            name: (await _json(app, f"/api/v1/repos/{LONE}/-/setup/cost", token))["paid_from"]
            for name, token in readers
        }
        job = {name: await _json(app, f"/api/v1/jobs/{LONE_JOB}", token) for name, token in readers}
        job_events = {name: await _json(app, f"/api/v1/jobs/{LONE_JOB}/logs/events", token) for name, token in readers}
        # The global pool's forge credential, once the host's is disabled: a forge credential is never one to choose.
        host = next(
            c for c in await _json(app, "/api/v1/credentials", MASTER) if c["label"] == f"{DEPLOYMENT_MARK}-host-forge"
        )
        made = await call(
            app,
            "POST",
            "/api/v1/credentials",
            MASTER,
            {
                "kind": "forge_token",
                "provider": "github",
                "label": f"{DEPLOYMENT_MARK}-global-forge",
                "secret": "g" * 30,
            },
        )
        pool = (await _json(app, "/api/v1/credentials/bindings?scope_kind=global", MASTER))["bindings"]
        body = {
            "scope_kind": "global",
            "scope_value": "",
            "bindings": [
                *({"role": b["role"], "credential_id": b["credential_id"]} for b in pool),
                {"role": "forge", "credential_id": made.json()["id"]},
            ],
        }
        assert (await call(app, "PUT", "/api/v1/credentials/bindings", MASTER, body)).status_code == 200
        assert (await call(app, "PUT", f"/api/v1/credentials/{host['id']}", MASTER, {"status": "disabled"})).is_success
        global_forge = {
            name: (
                (await _json(app, f"/api/v1/repos/{LONE}/-/credentials", token))["effective"]["forge"],
                await _json(app, f"/api/v1/credentials/resolve?repo={LONE}", token),
            )
            for name, token in readers
        }
        # The environment's account, once the global pool binds none.
        await call(
            app,
            "PUT",
            "/api/v1/credentials/bindings",
            MASTER,
            {"scope_kind": "global", "scope_value": "", "bindings": []},
        )
        monkeypatch.setenv(ENV_KEY, "xai-env-000000000001")
        url = f"/api/v1/credentials/resolve?repo={LONE}&role=llm&provider={POOL_PROVIDER}"
        env = {name: await _json(app, url, token) for name, token in (("lone", lone), ("open", keys.open))}
        # The account a setup run would pay with, served by the environment too: no account a job may be run with by
        # choice, so unnamed to the key.
        for name in ANTHROPIC_ENV:
            monkeypatch.setenv(name, "anthropic-env-000000000001")
        env_paid_from = {
            name: (await _json(app, f"/api/v1/repos/{LONE}/-/setup/cost", token))["paid_from"]
            for name, token in readers
        }
        for name in ANTHROPIC_ENV:
            monkeypatch.delenv(name)
        monkeypatch.delenv(ENV_KEY)
        missing = {name: await call(app, "GET", url, token) for name, token in (("lone", lone), ("open", keys.open))}
        # A provider nothing resolves is left out of the repository's view, for every reader.
        unresolved = {
            name: (await _json(app, f"/api/v1/repos/{LONE}/-/credentials", token))["effective"].get("llm", {})
            for name, token in readers
        }
        # A pinned host credential that is disabled stops the forge's resolution. The API pins only a model account
        # of a repository or an owner; the resolver still honours a pin stored on any binding.
        await db.execute("UPDATE credential_bindings SET pinned = true WHERE credential_id = $1", host["id"])
        unavailable = {
            name: (
                await call(app, "GET", f"/api/v1/credentials/resolve?repo={LONE}", token),
                (await _json(app, f"/api/v1/repos/{LONE}/-/credentials", token))["effective"]["forge"],
                await call(app, "GET", f"/api/v1/repos/{LONE}/-/environment/detect", token),
            )
            for name, token in readers
        }
    assert forge["open"]["label"] == f"{DEPLOYMENT_MARK}-host-forge", forge
    assert forge["open"]["trace"] == [*forge_steps, f"host:github.com -> {DEPLOYMENT_MARK}-host-forge"], forge
    assert forge["lone"] == _hidden("github", [*forge_steps, DEPLOYMENT_STEPS], host=False), forge
    host_skipped = [f"host:github.com -> {DEPLOYMENT_MARK}-host-forge skipped (disabled)", "host:github.com -> none"]
    view, dry_run = global_forge["open"]
    assert view["label"] == dry_run["label"] == f"{DEPLOYMENT_MARK}-global-forge", global_forge
    assert view["trace"] == [*forge_steps, *host_skipped, f"global: -> {DEPLOYMENT_MARK}-global-forge"], view
    view, dry_run = global_forge["lone"]
    assert view == _hidden("github", [*forge_steps, DEPLOYMENT_STEPS], host=False), (
        f"the global pool's forge credential is no model account to choose: unnamed to the key {view}"
    )
    assert dry_run == _hidden("github", [*forge_steps, DEPLOYMENT_STEPS]), dry_run
    assert readiness == {
        "lone": "Uses the deployment's GitHub account.",
        "open": f"Uses the GitHub account {DEPLOYMENT_MARK}-host-forge.",
    }, readiness
    assert paid_from == {"lone": f"{POOL_MARK}-global-anthropic", "open": f"{POOL_MARK}-global-anthropic"}, (
        f"the global pool's account a setup run would pay with is one the key may choose for a job: {paid_from}"
    )
    why = f"; GET /credentials/resolve?repo={LONE}&role=llm&provider={POOL_PROVIDER} shows why"
    stored = f"no {POOL_PROVIDER} credential for '{LONE}': bind one in the registry{why}"
    assert job["lone"]["error_message"] == job["open"]["error_message"] == stored, job
    messages = {name: [e.get("message") for e in got["events"]] for name, got in job_events.items()}
    assert messages["lone"] == messages["open"] == [f"Sandbox init failed: {stored}"], messages

    llm_steps = [f"repo:{LONE} -> none", "owner:github:initech -> none"]
    assert env["open"]["label"] == ENV_KEY and env["open"]["source"] == "env", env
    assert env["lone"] == _hidden(POOL_PROVIDER, [*llm_steps, "global: -> none", DEPLOYMENT_STEPS]), (
        f"an account the environment serves is no account a job may be run with by choice: unnamed {env}"
    )

    assert env_paid_from["lone"] == DEPLOYMENT_CREDENTIAL and env_paid_from["open"] in ANTHROPIC_ENV, env_paid_from
    assert all(POOL_PROVIDER not in view for view in unresolved.values()), (
        f"a provider nothing resolves is left out of the repository's view: {unresolved}"
    )
    assert missing["open"].status_code == missing["lone"].status_code == 404, missing
    assert missing["open"].json()["detail"] == (
        f"no {POOL_PROVIDER} credential for '{LONE}': bind one in the registry "
        f"(trace: {'; '.join(llm_steps)}; global: -> none; env:{POOL_PROVIDER} -> none)"
    ), missing["open"].text
    assert missing["lone"].json()["detail"] == (
        f"no {POOL_PROVIDER} credential for '{LONE}': bind one in the registry "
        f"(trace: {'; '.join(llm_steps)}; global: -> none; {DEPLOYMENT_STEPS})"
    ), missing["lone"].text

    named = f"credential '{DEPLOYMENT_MARK}-host-forge' is pinned for {LONE.lower()} but disabled"
    nothing = (
        f"no github credential for '{LONE}': bind one in the registry or set GITHUB_TOKEN "
        f"(trace: {'; '.join(forge_steps)}; {DEPLOYMENT_STEPS})"
    )
    response, view, detect = unavailable["open"]
    assert (response.status_code, response.json()["detail"]) == (409, named), response.text
    assert view == {"error": named} and (detect.status_code, detect.json()["detail"]) == (409, named), view
    response, view, detect = unavailable["lone"]
    assert (response.status_code, response.json()["detail"]) == (404, nothing), response.text
    assert view == {"error": nothing}, view
    assert (detect.status_code, detect.json()) == (
        200,
        {"source_file": "", "variables": [], "unconfigured_count": 0},
    ), (
        f"the template read with a pinned host credential answers the key as one nothing it may use can read: "
        f"{detect.text}"
    )


async def test_an_owner_s_forge_credential_reads_only_to_a_key_covering_the_owner_as_a_whole(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``acme/new`` is registered under ``acme`` and bound nothing, so its forge credential is its owner's. A key
    listing it alone covers ``acme`` in part (``acme/web`` and ``acme/api`` are registered too): the owner's step is
    one line to it and the credential the deployment's. A key listing all three covers ``acme`` as a whole and reads
    it as a key without a list does. A forge credential is never one to choose, whoever binds it."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        await db.execute("INSERT INTO repos (repo, active) VALUES ('acme/new', true)")
        part = await _key(db, "part", ["acme/new"])
        whole = await _key(db, "whole", [COVERED, SIBLING, "acme/new"])
        readers = (("part", part), ("whole", whole), ("open", keys.open))
        dry_run = {
            name: await _json(app, "/api/v1/credentials/resolve?repo=acme/new", token) for name, token in readers
        }
    owner_forge = f"{OWNER_MARK['acme']}-forge"
    assert dry_run["open"]["label"] == owner_forge, dry_run
    assert dry_run["open"]["trace"] == ["repo:acme/new -> none", f"owner:github:acme -> {owner_forge}"], dry_run
    assert dry_run["whole"] == dry_run["open"], dry_run
    assert dry_run["part"] == _hidden("github", ["repo:acme/new -> none", DEPLOYMENT_STEPS]), dry_run


async def test_the_host_s_and_the_global_pool_s_bindings_are_none_to_a_key_with_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the forge's host and the global pool bind is every repository's: a key with an allowlist reads none of
    it, whatever its list covers, and the read is not refused, logged or audited."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        whole = await _key(db, "whole", [COVERED, SIBLING])
        refusals = await db.fetchval("SELECT count(*) FROM audit_log WHERE action = 'apikey.repo_refused'")
        read = {
            (scope, name): await _json(app, f"/api/v1/credentials/bindings?{query}", token)
            for scope, query in (("host", "scope_kind=host&scope_value=github.com"), ("global", "scope_kind=global"))
            for name, token in (("limited", keys.limited), ("whole", whole), ("open", keys.open))
        }
        after = await db.fetchval("SELECT count(*) FROM audit_log WHERE action = 'apikey.repo_refused'")
    expected = {
        "host": [f"{DEPLOYMENT_MARK}-host-forge"],
        "global": [f"{POOL_MARK}-global", f"{POOL_MARK}-global-anthropic"],
    }
    for scope in ("host", "global"):
        labels = sorted(str(b.get("label")) for b in read[(scope, "open")]["bindings"])
        assert labels == sorted(expected[scope]), read
        assert read[(scope, "limited")] == read[(scope, "whole")] == {"bindings": []}, read
    assert after == refusals, "a read of the deployment's bindings is not refused or audited"


async def test_a_key_with_an_allowlist_reads_the_keys_a_job_may_use_only_for_a_repository_it_covers(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GET /run-with/keys`` is where a key chooses a model account for a job, the owner's and the global pool's
    among them, by label and status. To a key with an allowlist it answers for a repository its list covers only:
    without ``repo`` it would list the global pool's alone, for a job with no repository, which such a key cannot
    start, so it is refused as work with no repository is, and audited. A key without a list reads both."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        refused = await db.fetchval("SELECT count(*) FROM audit_log WHERE action = 'apikey.repo_refused'")
        bare = {
            name: await call(app, "GET", "/api/v1/run-with/keys", token)
            for name, token in (("limited", keys.limited), ("open", keys.open))
        }
        after = await db.fetchval("SELECT count(*) FROM audit_log WHERE action = 'apikey.repo_refused'")
        own = await _json(app, f"/api/v1/run-with/keys?repo={COVERED}", keys.limited)
    assert bare["limited"].status_code == 403 and "(no repository)" in bare["limited"].json()["detail"], bare[
        "limited"
    ].text
    assert after == refused + 1, "the refusal is audited once"
    assert bare["open"].status_code == 200 and {k["scope"] for k in bare["open"].json()["keys"]} == {"global"}, bare[
        "open"
    ].text
    labels = {k["label"] for k in own["keys"]}
    assert f"{POOL_MARK}-global" in labels, f"the key still chooses among the pool's accounts: {own}"
