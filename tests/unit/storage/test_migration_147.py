"""The credential resolver's old failure text, rewritten wherever it was stored (#445).

Before #445 a resolution that found nothing ended its message with its trace
(every credential's label it passed, why it skipped each, the environment's
variable names), and a credential pinned at an owner, a host or the
deployment named itself; neither said where to read why. A job that failed on
one stored that text: its error,
its events, the setup proposal that copies its error, a healing case's note
and history, and what a watcher tick or a Slop-O-Meter reading stored of a
forge call's exception. The migration rewrites each to exactly what the
resolver writes now.

The corpus is real: the resolver of this tree resolves over a real registry,
and each case's old text is what the resolver before #445 built from the same
steps (its message without the dry run it names now, then
`` (trace: <steps>)``; a pin's ``detail``). Near
misses (text the new resolver writes, other messages that carry a trace or name
a pin, a trace cut short, one that would run from one JSON field into the next)
must come out byte for byte as they went in.

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from embry0.execution import events
from embry0.services.credentials.resolver import (
    CredentialMissingError,
    CredentialResolver,
    CredentialUnavailableError,
)
from embry0.storage.database import DatabasePool
from embry0.storage.encryption import FernetSecretsProvider
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations
from embry0.storage.repositories.credentials import CredentialsRepository
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.repos import ReposRepository

#: This migration's number.
VERSION = 147

FUTURE = datetime.now(UTC) + timedelta(days=3)
CONFIG = SimpleNamespace(
    github_token="", gitlab_token="", gitlab_url="https://gitlab.example.com", default_auth_mode="api_key"
)


def test_the_credential_text_migration_is_listed_once() -> None:
    """The "last entry" assertion passed to tests/unit/storage/test_migration_148.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


async def _credential(
    db: DatabasePool,
    secrets: FernetSecretsProvider,
    *,
    label: str,
    provider: str,
    kind: str,
    host: str,
    status: str = "ok",
    reason: str | None = None,
    quarantined_until: datetime | None = None,
    ciphertext: str | None = None,
) -> str:
    row = await CredentialsRepository(db).create(
        kind=kind,
        provider=provider,
        host=host,
        label=label,
        secret_ciphertext=ciphertext or await secrets.encrypt("s" * 24),
        masked_preview="ssss…ssss",
        created_by="tests",
    )
    await db.execute(
        "UPDATE credentials SET status = $2, status_reason = $3, quarantined_until = $4 WHERE id = $1",
        row["id"],
        status,
        reason,
        quarantined_until,
    )
    return str(row["id"])


async def _bind(
    db: DatabasePool, credential: str, scope_kind: str, scope_value: str, role: str, provider: str, pinned: bool = False
) -> None:
    await db.execute(
        "INSERT INTO credential_bindings (scope_kind, scope_value, role, provider, credential_id, pinned, created_by) "
        "VALUES ($1, $2, $3, $4, $5, $6, 'tests')",
        scope_kind,
        scope_value,
        role,
        provider,
        credential,
        pinned,
    )


async def _cases(db: DatabasePool) -> dict[str, tuple[str, str]]:
    """Each case: (the text the resolver before #445 stored, the text it writes now), made by resolving for real."""
    secrets = FernetSecretsProvider(secret_key="tests-secret-key")
    other = FernetSecretsProvider(secret_key="another-key")
    # A model account that found nothing: the owner's disabled (its label has a parenthesis and a semicolon), the
    # global pool's quarantined.
    owned = await _credential(
        db,
        secrets,
        label="Team key (old); v2",
        provider="xai",
        kind="llm_api_key",
        host="api.x.ai",
        status="disabled",
        reason="rotated",
    )
    await _bind(db, owned, "owner", "github:acme", "llm", "xai")
    pooled = await _credential(
        db,
        secrets,
        label="pool-1",
        provider="xai",
        kind="llm_api_key",
        host="api.x.ai",
        status="quarantined",
        quarantined_until=FUTURE,
    )
    await _bind(db, pooled, "global", "", "llm", "xai")
    # A forge account that found nothing: GitLab's host account disabled.
    hosted = await _credential(
        db,
        secrets,
        label="Bot (host)",
        provider="gitlab",
        kind="forge_token",
        host="gitlab.example.com",
        status="disabled",
    )
    await _bind(db, hosted, "host", "gitlab.example.com", "forge", "gitlab")
    # A model account its owner pins, disabled; its label quoted and accented, as a JSON event escapes it.
    pinned_owner = await _credential(
        db,
        secrets,
        label='Ops "main" café',
        provider="anthropic",
        kind="llm_api_key",
        host="api.anthropic.com",
        status="disabled",
    )
    await _bind(db, pinned_owner, "owner", "github:init-tech", "llm", "anthropic", pinned=True)
    # A model account is looked up at the owner on the repository's own forge (#478): Init-Tech/app is on GitHub.
    await db.execute("INSERT INTO repos (repo, forge) VALUES ('init-tech/app', 'github')")
    # One the repository itself pins, quarantined: written as before.
    pinned_repo = await _credential(
        db,
        secrets,
        label="site-key",
        provider="anthropic",
        kind="llm_api_key",
        host="api.anthropic.com",
        status="quarantined",
        quarantined_until=FUTURE,
    )
    await _bind(db, pinned_repo, "repo", "umbrella/site", "llm", "anthropic", pinned=True)
    # A forge account the host pins that cannot be decrypted: the deployment's.
    pinned_host = await _credential(
        db,
        secrets,
        label="host-bot",
        provider="github",
        kind="forge_token",
        host="github.com",
        ciphertext=await other.encrypt("t" * 24),
    )
    await _bind(db, pinned_host, "host", "github.com", "forge", "github", pinned=True)
    # Forge accounts a repository and an owner pin, disabled: a forge account's dry run, whoever pins it.
    pinned_repo_forge = await _credential(
        db, secrets, label="deploy-bot", provider="github", kind="forge_token", host="github.com", status="disabled"
    )
    await _bind(db, pinned_repo_forge, "repo", "umbrella/api", "forge", "github", pinned=True)
    pinned_owner_forge = await _credential(
        db, secrets, label="org-bot", provider="github", kind="forge_token", host="github.com", status="disabled"
    )
    await _bind(db, pinned_owner_forge, "owner", "github:umbrella-corp", "forge", "github", pinned=True)
    # A forge account found nowhere past 500 characters, as a pool of long-named accounts all skipped leaves it: its
    # owner's quarantined, the host's disabled (above), the deployment's quarantined.
    for scope_kind, scope_value in (("owner", "gitlab:longco"), ("global", "")):
        account = await _credential(
            db,
            secrets,
            label=f"{scope_kind} service account for the long-running release pipeline " + "x" * 120,
            provider="gitlab",
            kind="forge_token",
            host="gitlab.example.com",
            status="quarantined",
            quarantined_until=FUTURE,
        )
        await _bind(db, account, scope_kind, scope_value, "forge", "gitlab")

    resolver = CredentialResolver(
        CredentialsRepository(db), secrets, CONFIG, environ={}, repo_forge=ReposRepository(db).forge_or_none
    )
    out: dict[str, tuple[str, str]] = {}

    async def missing(name: str, call: Any) -> None:
        with pytest.raises(CredentialMissingError) as raised:
            await call
        exc = raised.value
        assert exc.pointer and str(exc).endswith(exc.pointer), f"{name}: the message names no dry run"
        out[name] = (f"{str(exc).removesuffix(exc.pointer)} (trace: {'; '.join(exc.trace)})", str(exc))

    async def pinned(name: str, call: Any) -> None:
        with pytest.raises(CredentialUnavailableError) as raised:
            await call
        exc = raised.value
        out[name] = (exc.detail, str(exc))

    await missing("a model account found nowhere", resolver.resolve_llm("acme/web", "xai"))
    await missing("a forge account found nowhere", resolver.resolve_forge("acme/web", "gitlab"))
    await pinned("its owner's pinned model account", resolver.resolve_llm("Init-Tech/app", "anthropic"))
    await pinned("its own pinned model account", resolver.resolve_llm("umbrella/site", "anthropic"))
    await pinned("the host's pinned forge account", resolver.resolve_forge("hooli/xyz", "github"))
    await pinned("its own pinned forge account", resolver.resolve_forge("umbrella/api", "github"))
    await pinned("its owner's pinned forge account", resolver.resolve_forge("Umbrella-Corp/app", "github"))
    await missing("a forge account found nowhere, past 500 characters", resolver.resolve_forge("longco/web", "gitlab"))
    assert len(out["a forge account found nowhere, past 500 characters"][0]) > 500, "the long case is not long"
    # A pin whose binding is gone since: the deployment's, naming nothing.
    out["a pin no longer bound"] = (
        "credential 'retired-bot' is pinned for hooli/old but disabled:decryption_failed",
        f"the deployment's credential pinned for hooli/old cannot be used{_FORGE_WHY.format(repo='hooli/old')}",
    )
    # Values that hold more than one: errors joined with "; " (_executor_outcomes), a provider's words appended.
    owner_old, owner_new = out["its owner's pinned model account"]
    own_old, own_new = out["its own pinned model account"]
    host_old, host_new = out["the host's pinned forge account"]
    out["two pins joined"] = (f"{owner_old}; {host_old}", f"{owner_new}; {host_new}")
    out["its own pin beside its owner's"] = (f"{own_old}; {owner_old}", f"{own_new}; {owner_new}")
    words = "; developer: You're out of extra usage · resets 5pm (Europe/Berlin)"
    out["a pin, then a provider's words"] = (owner_old + words, owner_new + words)
    # A model account the deployment pinned, which the resolver no longer stops at: its kind names its dry run.
    out["a pin, then more words"] = (
        "credential 'pool-1' is pinned for acme/web but disabled, so the agent waited",
        "the deployment's credential pinned for acme/web cannot be used"
        f"{_MODEL_WHY.format(repo='acme/web', provider='xai')}, so the agent waited",
    )
    # One pin twice, its second reason a prefix of its first: each is rewritten whole.
    out["one pin twice, with two reasons"] = (
        "credential 'retired-bot' is pinned for hooli/old but disabled:decryption_failed; "
        "credential 'retired-bot' is pinned for hooli/old but disabled",
        f"the deployment's credential pinned for hooli/old cannot be used{_FORGE_WHY.format(repo='hooli/old')}; "
        f"the deployment's credential pinned for hooli/old cannot be used{_FORGE_WHY.format(repo='hooli/old')}",
    )
    # The repository's own pin twice, its second reason a prefix of its first: each gets its dry run once.
    site_why = _MODEL_WHY.format(repo="umbrella/site", provider="anthropic")
    out["its own pin twice, with two reasons"] = (
        "credential 'site-key' is pinned for umbrella/site but disabled:decryption_failed; "
        "credential 'site-key' is pinned for umbrella/site but disabled",
        f"credential 'site-key' is pinned for umbrella/site but disabled:decryption_failed{site_why}; "
        f"credential 'site-key' is pinned for umbrella/site but disabled{site_why}",
    )
    # A time ends with a digit: a sentence's full stop after it is not part of it.
    out["a pin's time, then a full stop"] = (
        "credential 'pool-1' is pinned for acme/web but quarantined:until-2026-11-15T18:52:50.721290+00:00. Retrying.",
        "the deployment's credential pinned for acme/web cannot be used"
        f"{_MODEL_WHY.format(repo='acme/web', provider='xai')}. Retrying.",
    )
    missing_old, missing_new = out["a model account found nowhere"]
    out["a failure, then a pin"] = (f"{missing_old}; {host_old}", f"{missing_new}; {host_new}")
    return out


#: What a message ends with now: the dry run that shows why, a model account's with its provider.
_FORGE_WHY = "; GET /credentials/resolve?repo={repo} shows why"
_MODEL_WHY = "; GET /credentials/resolve?repo={repo}&role=llm&provider={provider} shows why"

#: Text that must come out as it went in: what the resolver writes now, other messages with a trace or a pin, a reason
#: the resolver never gives, and a trace that cannot be read whole in a store that does not cut its values.
NEAR_MISSES = [
    "no xai credential for 'acme/web': bind one in the registry",
    "no xai credential for 'acme/web': bind one in the registry" + _MODEL_WHY.format(repo="acme/web", provider="xai"),
    "no github credential for 'acme/web': bind one in the registry or set GITHUB_TOKEN"
    + _FORGE_WHY.format(repo="acme/web"),
    "credential 'pool-1' is pinned for acme/web but disabled" + _MODEL_WHY.format(repo="acme/web", provider="xai"),
    "credential 'deploy-bot' is pinned for umbrella/api but disabled" + _FORGE_WHY.format(repo="umbrella/api"),
    # A repository's own pin as written now, its reason one a shorter reading of could end early.
    "credential 'site-key' is pinned for umbrella/site but quarantined:until-2026-11-15T18:52:50.721290+00:00"
    + _MODEL_WHY.format(repo="umbrella/site", provider="anthropic"),
    "credential 'deploy-bot' is pinned for umbrella/api but disabled:decryption_failed"
    + _FORGE_WHY.format(repo="umbrella/api"),
    "the owner's credential pinned for acme/web cannot be used" + _MODEL_WHY.format(repo="acme/web", provider="xai"),
    "the deployment's credential pinned for acme/web cannot be used" + _FORGE_WHY.format(repo="acme/web"),
    "no xai credential for 'acme/web': bind one in the registry (trace: see the runbook)",
    "no xai credential for acme/web: bind one in the registry (trace: env:xai -> none)",
    "no credential with role tailnet (trace: global: -> none)",
    # The tailnet key's message as the resolver writes it since #445: no store holds it, and nothing here reads it.
    "no tailnet credential: create a tailnet_api_key credential and bind it at global scope with role tailnet",
    "the key chosen for this job, 'pool-1', is disabled",
    "the owner's credential pinned for acme/web cannot be used",
    "credential 'pool-1' is pinned for acme/web but expired",
    "credential 'pool-1' is pinned for acme/web but disabledly",
    "12 passed, 3 tests skipped (marked xfail); env:CI -> none)",
    "request failed (trace: span-1234; env:PROD -> none)",
    "set global: -> none in the config (trace: x)",
    "Agent said: `no xai credential for 'a/b': bind one in the registry` so I stopped",
    "the deployment's credential pinned for acme/web cannot be used",
    "Sandbox initialization failed: no xai credential for 'acme/web': bind one in the registry (trace: repo:acme/web "
    "-> none; owner:github:acme -> Team key (old",
]


async def _job(db: DatabasePool, job_id: str, error: str) -> None:
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, error_message) VALUES ($1, 'acme/web', 't', 'failed', $2)",
        job_id,
        error,
    )


async def _stores(db: DatabasePool, texts: dict[str, str]) -> None:
    """Each text, where a failure put it: a job's error (as init_node wraps it, and bare), its init event and a raw
    log line, a setup proposal's failure, a healing case's note and history, a watcher tick, a Slop-O-Meter reading."""
    jobs = JobsRepository(db)
    for n, (name, text) in enumerate(texts.items()):
        await _job(db, f"job-{n}-wrapped", f"Sandbox initialization failed: {text}")
        await _job(db, f"job-{n}-bare", text)
        await jobs.append_log_event(
            f"job-{n}-wrapped", events.error(message=f"Sandbox init failed: {text}", node="init")
        )
        await db.execute(
            "INSERT INTO job_logs (job_id, stream, text) VALUES ($1, 'stdout', $2)", f"job-{n}-wrapped", text
        )
        await db.execute(
            "INSERT INTO repo_setup_proposals (id, repo, status, failure) VALUES ($1, 'acme/web', 'failed', $2)",
            f"setup-{n}",
            f"embry0 stopped before it finished: Sandbox initialization failed: {text}",
        )
        await db.execute(
            "INSERT INTO healing_cases (id, repo, origin, stage, title, note) "
            "VALUES ($1, 'acme/web', 'watcher', 'closed', $2, $3)",
            f"hc-{n}",
            name,
            f"Sandbox initialization failed: {text}",
        )
        await db.execute(
            "INSERT INTO healing_case_events (case_id, event, to_stage, detail) VALUES ($1, 'abandoned', 'closed', $2)",
            f"hc-{n}",
            {"note": f"Sandbox initialization failed: {text}", "job_id": f"job-{n}-wrapped"},
        )
        await db.execute(
            "INSERT INTO watcher_runs (id, window_start, window_end, action, detail) VALUES ($1, now(), now(), 'error', $2)",
            f"wr-{n}",
            text[:500],
        )
        await db.execute("INSERT INTO slop_meter_repos (repo, error) VALUES ($1, $2)", f"acme/repo-{n}", text[:500])


async def _read(db: DatabasePool, n: int) -> dict[str, Any]:
    event = await db.fetchval("SELECT text FROM job_logs WHERE job_id = $1 AND stream = 'pipeline'", f"job-{n}-wrapped")
    return {
        "wrapped": await db.fetchval("SELECT error_message FROM jobs WHERE job_id = $1", f"job-{n}-wrapped"),
        "bare": await db.fetchval("SELECT error_message FROM jobs WHERE job_id = $1", f"job-{n}-bare"),
        "event": json.loads(event)["message"],
        "event_text": event,
        "line": await db.fetchval(
            "SELECT text FROM job_logs WHERE job_id = $1 AND stream = 'stdout'", f"job-{n}-wrapped"
        ),
        "setup": await db.fetchval("SELECT failure FROM repo_setup_proposals WHERE id = $1", f"setup-{n}"),
        "note": await db.fetchval("SELECT note FROM healing_cases WHERE id = $1", f"hc-{n}"),
        "history": (await db.fetchval("SELECT detail FROM healing_case_events WHERE case_id = $1", f"hc-{n}"))["note"],
        "tick": await db.fetchval("SELECT detail FROM watcher_runs WHERE id = $1", f"wr-{n}"),
        "reading": await db.fetchval("SELECT error FROM slop_meter_repos WHERE repo = $1", f"acme/repo-{n}"),
    }


def _expected(text: str) -> dict[str, Any]:
    return {
        "wrapped": f"Sandbox initialization failed: {text}",
        "bare": text,
        "event": f"Sandbox init failed: {text}",
        "line": text,
        "setup": f"embry0 stopped before it finished: Sandbox initialization failed: {text}",
        "note": f"Sandbox initialization failed: {text}",
        "history": f"Sandbox initialization failed: {text}",
        "tick": text[:500],
        "reading": text[:500],
    }


async def _migrate_again(db: DatabasePool) -> None:
    # Migration 147 runs before 148, on the owner bindings as the release before stored them: '-' and '.' read as
    # '_' (github:init_tech for Init-Tech). 148 then gives each binding its one owner, as the resolver reads it now.
    await db.execute(
        "UPDATE credential_bindings SET scope_value = translate(scope_value, '-.', '__') WHERE scope_kind = 'owner'"
    )
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    await run_migrations(db)


@pytest.mark.requires_postgres
async def test_every_store_reads_as_the_resolver_writes_now_and_nothing_else_moves(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    cases = await _cases(db)
    assert len({old for old, _new in cases.values()}) == len(cases), "two cases share their old text"
    olds = {name: old for name, (old, _new) in cases.items()}
    near = {f"near {i}": text for i, text in enumerate(NEAR_MISSES)}
    await _stores(db, {**olds, **near})
    names = list({**olds, **near})
    before_updated = {str(r["job_id"]): r["updated_at"] for r in await db.fetch("SELECT job_id, updated_at FROM jobs")}
    near_events = {i: (await _read(db, names.index(name)))["event_text"] for i, name in enumerate(near)}

    await _migrate_again(db)

    wrong: dict[str, Any] = {}
    for name, (old, new) in cases.items():
        got = await _read(db, names.index(name))
        got.pop("event_text")
        if got != _expected(new):
            wrong[name] = {key: (got[key], _expected(new)[key]) for key in got if got[key] != _expected(new)[key]}
    for i, (name, text) in enumerate(near.items()):
        got = await _read(db, names.index(name))
        if got.pop("event_text") != near_events[i] or got != _expected(text):
            wrong[name] = got
    assert wrong == {}, f"stores that do not read as the resolver writes now, or near misses that moved: {wrong}"
    left = [name for name in cases if "(trace: " in json.dumps(await _read(db, names.index(name)), default=str)]
    assert left == [], f"a trace is still stored for {left}"

    after_updated = {str(r["job_id"]): r["updated_at"] for r in await db.fetch("SELECT job_id, updated_at FROM jobs")}
    assert after_updated == before_updated, "the rewrite changed jobs' updated_at"
    enabled = await db.fetchval("SELECT tgenabled = 'O' FROM pg_trigger WHERE tgname = 'trg_jobs_updated_at'")
    assert enabled is True, "the jobs trigger is not enabled again"
    assert await db.fetchval("SELECT count(*) FROM pg_proc WHERE proname = 'embry0_rewrite_credential_text'") == 0


@pytest.mark.requires_postgres
async def test_the_rewrite_names_what_each_case_should_and_runs_again_to_no_effect(
    db_with_migrations: DatabasePool,
) -> None:
    """The cases say what the resolver says now: no trace, no environment variable, no owner's or deployment's
    label; a pin of the repository itself as before. A second run changes nothing."""
    db = db_with_migrations
    cases = await _cases(db)
    assert cases["a model account found nowhere"][1] == (
        "no xai credential for 'acme/web': bind one in the registry"
        + _MODEL_WHY.format(repo="acme/web", provider="xai")
    )
    assert cases["a forge account found nowhere"][1] == (
        "no gitlab credential for 'acme/web': bind one in the registry or set GITLAB_TOKEN"
        + _FORGE_WHY.format(repo="acme/web")
    )
    assert cases["its owner's pinned model account"][1] == (
        "the owner's credential pinned for Init-Tech/app cannot be used"
        + _MODEL_WHY.format(repo="Init-Tech/app", provider="anthropic")
    )
    own_old, own_new = cases["its own pinned model account"]
    assert own_new == own_old + _MODEL_WHY.format(repo="umbrella/site", provider="anthropic")
    assert cases["its own pinned forge account"][1] == (
        "credential 'deploy-bot' is pinned for umbrella/api but disabled" + _FORGE_WHY.format(repo="umbrella/api")
    )
    assert cases["its owner's pinned forge account"][1] == (
        "the owner's credential pinned for Umbrella-Corp/app cannot be used"
        + _FORGE_WHY.format(repo="Umbrella-Corp/app")
    )
    assert cases["the host's pinned forge account"][1] == (
        "the deployment's credential pinned for hooli/xyz cannot be used" + _FORGE_WHY.format(repo="hooli/xyz")
    )
    assert "Team key (old); v2" in cases["a model account found nowhere"][0], "the corpus has no tricky label"
    await _stores(db, {name: old for name, (old, _new) in cases.items()})
    await _migrate_again(db)
    once = await db.fetch("SELECT job_id, error_message, updated_at FROM jobs ORDER BY job_id")
    logs = await db.fetch("SELECT id, text FROM job_logs ORDER BY id")
    await _migrate_again(db)
    assert await db.fetch("SELECT job_id, error_message, updated_at FROM jobs ORDER BY job_id") == once
    assert await db.fetch("SELECT id, text FROM job_logs ORDER BY id") == logs


@pytest.mark.requires_postgres
async def test_a_match_never_runs_from_one_field_of_an_event_into_the_next(db_with_migrations: DatabasePool) -> None:
    """An event's message cut short of its closing step, with another field ending as a step would: the JSON form
    stops at the message's closing quote, so the event is left as it is."""
    db = db_with_migrations
    await _job(db, "job-split", "x")
    text = json.dumps(
        {
            "type": "error",
            "message": "no xai credential for 'acme/web': bind one in the registry (trace: repo:acme/web -> none",
            "hint": "env:xai -> none)",
        }
    )
    await db.execute("INSERT INTO job_logs (job_id, stream, text) VALUES ('job-split', 'pipeline', $1)", text)
    await _migrate_again(db)
    assert await db.fetchval("SELECT text FROM job_logs WHERE job_id = 'job-split'") == text


@pytest.mark.requires_postgres
async def test_each_trace_ends_at_its_own_closing_step(db_with_migrations: DatabasePool) -> None:
    """Two failures in one text: each loses its own trace, and what stands between them stays."""
    db = db_with_migrations
    first = "no xai credential for 'acme/web': bind one in the registry"
    second = "no github credential for 'acme/api': bind one in the registry or set GITHUB_TOKEN"
    text = (
        f"{first} (trace: repo:acme/web -> none; env:xai -> none) then, on retry, "
        f"{second} (trace: host:github.com -> none; env:GITHUB_TOKEN -> none)"
    )
    await _job(db, "job-two", text)
    await _migrate_again(db)
    assert await db.fetchval("SELECT error_message FROM jobs WHERE job_id = 'job-two'") == (
        f"{first}{_MODEL_WHY.format(repo='acme/web', provider='xai')} then, on retry, "
        f"{second}{_FORGE_WHY.format(repo='acme/api')}"
    )


@pytest.mark.requires_postgres
async def test_a_trace_its_writer_cut_goes_only_where_values_are_cut(db_with_migrations: DatabasePool) -> None:
    """The watcher's tick and the Slop-O-Meter's reading store ``str(exc)[:500]``: a value of exactly that length
    loses what is left of a trace cut before its closing step. One shorter was not cut and keeps it; one without the
    resolver's words keeps it; a job's error, which is never cut, keeps it. A value the rewrite makes longer is cut at
    500 again, as its writer cuts the message now; a job's error is not."""
    db = db_with_migrations
    words = "no gitlab credential for 'acme/web': bind one in the registry or set GITLAB_TOKEN"
    cut = (words + " (trace: owner:gitlab:acme -> " + "x" * 600)[:500]
    short = cut[:499]
    other = ("request failed (trace: " + "y" * 600)[:500]
    why = _FORGE_WHY.format(repo="acme/web")
    # Words late in the value: with its dry run the message runs past 500 characters.
    lead = "forge call failed: " + "z" * (500 - len(words) - len(" (trace: ") - 20 - len("forge call failed: "))
    late = (lead + words + " (trace: owner:gitlab:acme -> " + "x" * 600)[:500]
    assert len(lead + words + why) > 500, "the late case does not grow past its cut"
    # A pin makes a value one the rewrite reads, cut or not: only a cut value's trace goes with the pin's words.
    pin = "credential 'retired-bot' is pinned for hooli/old but disabled; "
    pinned_short = pin + short[: 499 - len(pin)]
    pinned_cut = (pin + cut)[:500]
    # A value longer than 500 characters no writer cut: rewritten, and not cut.
    pinned_long = pin + "w" * 600
    values = {
        "cut": cut,
        "short": short,
        "other": other,
        "pinned short": pinned_short,
        "pinned cut": pinned_cut,
        "late": late,
        "pinned long": pinned_long,
    }
    for name, text in values.items():
        await db.execute(
            "INSERT INTO watcher_runs (id, window_start, window_end, action, detail) VALUES ($1, now(), now(), 'error', $2)",
            f"wr-{name}",
            text,
        )
        await db.execute("INSERT INTO slop_meter_repos (repo, error) VALUES ($1, $2)", f"acme/{name}", text)
        await _job(db, f"job-{name}", text)
    await _migrate_again(db)
    rewritten = (
        f"the deployment's credential pinned for hooli/old cannot be used{_FORGE_WHY.format(repo='hooli/old')}; "
    )
    expected = {
        "cut": words + why,
        "short": short,
        "other": other,
        "pinned short": (rewritten + pinned_short[len(pin) :])[:500],
        "pinned cut": (rewritten + words + why)[:500],
        "late": (lead + words + why)[:500],
        "pinned long": rewritten + pinned_long[len(pin) :],
    }
    # A job's error is never cut: its pin is rewritten, and what follows it is left as it is.
    in_jobs = {
        **values,
        "pinned short": rewritten + pinned_short[len(pin) :],
        "pinned cut": rewritten + pinned_cut[len(pin) :],
        "pinned long": expected["pinned long"],
    }
    for name, want in expected.items():
        assert await db.fetchval("SELECT detail FROM watcher_runs WHERE id = $1", f"wr-{name}") == want, name
        assert await db.fetchval("SELECT error FROM slop_meter_repos WHERE repo = $1", f"acme/{name}") == want, name
        got = await db.fetchval("SELECT error_message FROM jobs WHERE job_id = $1", f"job-{name}")
        assert got == in_jobs[name], name


@pytest.mark.requires_postgres
async def test_a_value_with_more_pins_than_the_bound_keeps_the_rest_as_they_are(
    db_with_migrations: DatabasePool,
) -> None:
    """At most 100 pins are rewritten in one value, the first ones; any after them stays as it is, and so does all
    that stands between and after them."""
    db = db_with_migrations
    old = "credential 'retired-bot' is pinned for hooli/old but disabled"
    new = f"the deployment's credential pinned for hooli/old cannot be used{_FORGE_WHY.format(repo='hooli/old')}"
    await _job(db, "job-many", " | ".join([old] * 101) + " | end")
    await _migrate_again(db)
    assert await db.fetchval("SELECT error_message FROM jobs WHERE job_id = 'job-many'") == (
        " | ".join([new] * 100 + [old]) + " | end"
    )
