"""A repository's credential resolution names, to a key with an allowlist, none of the deployment's credentials.

On the real app over a real database (:mod:`tests.fixtures.repository_reads`):
the forge's host binds a forge credential and the global pool a model
account, each labelled :data:`DEPLOYMENT_MARK`, and each owner binds a model
account, disabled. A key with an allowlist reads the bindings of the
repositories it covers and of an owner it covers as a whole, and none of the
host's or the global pool's (``GET /credentials/bindings``). A resolution of
one of its repositories (``GET /repos/{repo}/-/credentials``,
``GET /credentials/resolve``, and the forge credential
``GET /repos/{repo}/-/environment/detect`` resolves) is held to the same rule:
a step at a scope it does not read is one line that names nothing, and a
credential resolved there, or a pinned one there that is unavailable, is the
deployment's, unnamed. A key without a list reads every resolution as before.
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
    POOL_PROVIDER,
    SIBLING,
    repository_reads_app,
    seed_lone,
)
from tests.fixtures.repository_writes import COVERED, MASTER, call

pytestmark = pytest.mark.requires_postgres

#: A model account in the environment, which only a caller without an allowlist is told about.
ENV_KEY = "XAI_API_KEY"
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


async def test_a_resolution_reaching_the_global_pool_names_none_of_its_credentials_to_a_key_with_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``acme/web`` binds its own forge credential and no model account of the pool's provider: its owner's is
    disabled, so it resolves from the global pool. The limited key covers ``acme`` only in part: it reads the
    repository's step and one line for the rest. A key covering ``acme`` as a whole reads the owner's steps too. Its
    own forge credential reads to it as to a key without a list. Once the owner's account is pinned, the resolution
    stops at it, named (409) only to a key that reads the owner's bindings; the limited key is answered byte for byte
    as when no account resolves at all (404), so the answer does not tell that the owner pins one."""
    db = db_with_migrations
    async with repository_reads_app(db, monkeypatch) as (app, keys, _values):
        whole = await _key(db, "whole", [COVERED, SIBLING])
        views = {
            name: await _json(app, f"/api/v1/repos/{COVERED}/-/credentials", token)
            for name, token in (("limited", keys.limited), ("whole", whole), ("open", keys.open))
        }
        url = f"/api/v1/credentials/resolve?repo={COVERED}&role=llm&provider={POOL_PROVIDER}"
        resolved = {
            name: await _json(app, url, token) for name, token in (("limited", keys.limited), ("open", keys.open))
        }
        # The owner's model account pinned, and disabled: the resolution stops at it.
        owner = await _json(app, "/api/v1/credentials/bindings?scope_kind=owner&scope_value=github:acme", MASTER)
        bindings = [
            {"role": b["role"], "credential_id": b["credential_id"], "pinned": b["role"] == "llm"}
            for b in owner["bindings"]
        ]
        body = {"scope_kind": "owner", "scope_value": "github:acme", "bindings": bindings}
        assert (await call(app, "PUT", "/api/v1/credentials/bindings", MASTER, body)).status_code == 200
        pinned = {
            name: await call(app, "GET", url, token)
            for name, token in (("limited", keys.limited), ("whole", whole), ("open", keys.open))
        }
        pinned_view = (await _json(app, f"/api/v1/repos/{COVERED}/-/credentials", keys.limited))["effective"]
        # What a job of the repository that stops at the pin stores as its error, for every reader of the job.
        try:
            await app.state.credential_resolver.resolve_llm(COVERED, POOL_PROVIDER)
            stored = "resolved"
        except CredentialUnavailableError as exc:
            stored = str(exc)
        # No account at all: the pin taken off, the global pool's disabled, none in the environment.
        body = {**body, "bindings": [{**b, "pinned": False} for b in bindings]}
        assert (await call(app, "PUT", "/api/v1/credentials/bindings", MASTER, body)).status_code == 200
        await db.execute("UPDATE credentials SET status = 'disabled' WHERE label = $1", f"{DEPLOYMENT_MARK}-global-llm")
        monkeypatch.delenv(ENV_KEY, raising=False)
        missing = await call(app, "GET", url, keys.limited)
        missing_view = (await _json(app, f"/api/v1/repos/{COVERED}/-/credentials", keys.limited))["effective"]
    owner_steps = [
        f"owner:github:acme -> {OWNER_MARK['acme']}-llm skipped (disabled)",
        "owner:github:acme -> none",
        "owner:gitlab:acme -> none",
    ]
    pool = views["open"]["effective"]["llm"][POOL_PROVIDER]
    assert pool["label"] == f"{DEPLOYMENT_MARK}-global-llm" and pool["source"] == "registry", pool
    assert pool["trace"] == [f"repo:{COVERED} -> none", *owner_steps, f"global: -> {DEPLOYMENT_MARK}-global-llm"], pool
    assert views["limited"]["effective"]["llm"][POOL_PROVIDER] == _hidden(
        POOL_PROVIDER, [f"repo:{COVERED} -> none", DEPLOYMENT_STEPS]
    ), views["limited"]
    assert views["whole"]["effective"]["llm"][POOL_PROVIDER] == _hidden(
        POOL_PROVIDER, [f"repo:{COVERED} -> none", *owner_steps, DEPLOYMENT_STEPS]
    ), views["whole"]
    assert resolved["limited"] == _hidden(POOL_PROVIDER, [f"repo:{COVERED} -> none", DEPLOYMENT_STEPS]), resolved
    assert resolved["open"]["label"] == f"{DEPLOYMENT_MARK}-global-llm", resolved
    named = f"credential '{OWNER_MARK['acme']}-llm' is pinned for {COVERED} but disabled"
    nothing = (
        f"no {POOL_PROVIDER} credential for '{COVERED}': bind one in the registry "
        f"(trace: repo:{COVERED} -> none; {DEPLOYMENT_STEPS})"
    )
    assert {name: (r.status_code, r.json()["detail"]) for name, r in pinned.items()} == {
        "limited": (404, nothing),
        "whole": (409, named),
        "open": (409, named),
    }, "a pinned owner's account that is unavailable is named only to a key that reads the owner's bindings"
    assert (missing.status_code, missing.content) == (pinned["limited"].status_code, pinned["limited"].content), (
        f"the limited key tells a pinned owner's account from no account: {missing.text} {pinned['limited'].text}"
    )
    assert stored == f"the owner's credential pinned for {COVERED} cannot be used", (
        f"a job stopped by its owner's pinned account stores an error naming it: {stored}"
    )
    assert POOL_PROVIDER not in pinned_view.get("llm", {}) and pinned_view == missing_view, (
        f"the limited key's view tells a pinned owner's account from no account: {pinned_view} {missing_view}"
    )
    for name in ("limited", "whole"):
        own = {k: v for k, v in views[name]["effective"].items() if k != "llm"}
        assert own == {k: v for k, v in views["open"]["effective"].items() if k != "llm"}, (
            f"the {name} key reads its repository's own forge credential as a key without a list: {views[name]}"
        )
        assert views[name]["bindings"] == views["open"]["bindings"], views[name]


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
        monkeypatch.delenv(ENV_KEY)
        missing = {name: await call(app, "GET", url, token) for name, token in (("lone", lone), ("open", keys.open))}
        # A pinned host credential that is disabled stops the forge's resolution. The API pins only a model account
        # of a repository or an owner; the resolver still honours a pin stored on any binding.
        host = next(
            c for c in await _json(app, "/api/v1/credentials", MASTER) if c["label"] == f"{DEPLOYMENT_MARK}-host-forge"
        )
        await db.execute("UPDATE credential_bindings SET pinned = true WHERE credential_id = $1", host["id"])
        assert (await call(app, "PUT", f"/api/v1/credentials/{host['id']}", MASTER, {"status": "disabled"})).is_success
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
    assert readiness == {
        "lone": "Uses the deployment's GitHub account.",
        "open": f"Uses the GitHub account {DEPLOYMENT_MARK}-host-forge.",
    }, readiness
    assert paid_from == {"lone": DEPLOYMENT_CREDENTIAL, "open": f"{DEPLOYMENT_MARK}-global-anthropic"}, paid_from
    stored = f"no {POOL_PROVIDER} credential for '{LONE}': bind one in the registry"
    assert job["lone"]["error_message"] == job["open"]["error_message"] == stored, job
    messages = {name: [e.get("message") for e in got["events"]] for name, got in job_events.items()}
    assert messages["lone"] == messages["open"] == [f"Sandbox init failed: {stored}"], messages

    llm_steps = [f"repo:{LONE} -> none", "owner:github:initech -> none", "owner:gitlab:initech -> none"]
    assert env["open"]["label"] == ENV_KEY and env["open"]["source"] == "env", env
    assert env["lone"] == _hidden(POOL_PROVIDER, [*llm_steps, DEPLOYMENT_STEPS]), env

    assert missing["open"].status_code == missing["lone"].status_code == 404, missing
    assert missing["open"].json()["detail"] == (
        f"no {POOL_PROVIDER} credential for '{LONE}': bind one in the registry "
        f"(trace: {'; '.join(llm_steps)}; global: -> none; env:{POOL_PROVIDER} -> none)"
    ), missing["open"].text
    assert missing["lone"].json()["detail"] == (
        f"no {POOL_PROVIDER} credential for '{LONE}': bind one in the registry "
        f"(trace: {'; '.join(llm_steps)}; {DEPLOYMENT_STEPS})"
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
    expected = {"host": ["host-forge"], "global": ["global-llm", "global-anthropic"]}
    for scope in ("host", "global"):
        labels = sorted(str(b.get("label")) for b in read[(scope, "open")]["bindings"])
        assert labels == sorted(f"{DEPLOYMENT_MARK}-{tail}" for tail in expected[scope]), read
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
    assert f"{DEPLOYMENT_MARK}-global-llm" in labels, f"the key still chooses among the pool's accounts: {own}"
