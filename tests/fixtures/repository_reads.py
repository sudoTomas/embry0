"""Three repositories' and two owners' stored data, of every kind the repository and organization reads serve.

The app, its keys and its work are :mod:`tests.fixtures.job_reads`': the
limited key covers :data:`COVERED` (``acme/web``) and not :data:`UNCOVERED`
(``globex/gizmos``), each with a finished job and everything stored for it.
:func:`repository_reads_app` adds :data:`SIBLING` (``acme/api``), registered
under the covered repository's owner and not on the limited key's list, so
the limited key covers ``acme`` only in part and ``globex`` not at all. Each
of the three repositories gets what a repository's reads serve: its
registration notes and Docker access, a test setup, variables (a value and a
secret), preferences, its areas, a waiting map of them and their import's record, a forge credential bound to it, an agent context, a
settings layer, a slop-o-meter reading, a host link, discovery facts, a
watched stack, a waiting test setup proposal with its notes, failure, smoke
results and proposed YAML, a standard's state with an audit score, findings
and an exception, and a map. Each owner gets what an owner's reads serve:
its organization's name and forge probe, a discovery run, the facts of a
repository not registered, a need, a forge credential bound to the owner, a
standard, a settings layer and a map of no repository. Each owner also binds a
model account, disabled, so a resolution of one of its repositories passes it
by, naming it, on its way to the global pool.

The deployment binds a forge credential to the forge's host, labelled with
:data:`DEPLOYMENT_MARK`: what every repository may fall back to, which a key
with an allowlist does not read (:func:`shows_deployment`). The model
accounts of the owners and of the global pool are labelled with
:data:`POOL_MARK` instead: a job on a repository a key covers may run with
one, which ``GET /run-with/keys`` offers it by label, so such a key reads them
where its repositories resolve one, and nowhere else (:func:`shows_pool`).

:func:`seed_lone` adds :data:`LONE`, alone under its owner and bound nothing,
so everything it uses is the deployment's: its forge account, the model
account a setup run would pay with, and the job :data:`LONE_JOB` that failed
for want of a model account, its error and its events as the executor stores
them. A key listing it alone reads it as its own.

Everything stored for a repository carries its :data:`MARK`, and everything
stored for an owner its :data:`OWNER_MARK`, so an answer that shows any of
it is found (:func:`shows_hidden`). :data:`MISSING` names, for each
repository, a repository nobody registered with a slug as long, and
:data:`MISSING_OWNER` an owner nobody registered, so two answers compare
byte for byte once each name is put back as a placeholder
(:func:`same_answer`).
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from embry0.api.deps import bust_api_key_cache
from embry0.api.middleware.rate_limit import RateLimitMiddleware
from embry0.execution import events
from embry0.execution.sandbox_manager import SandboxInitError
from embry0.safety.error_codes import ErrorCode
from embry0.services.context_audit import RepoContext, _Reading
from embry0.services.credentials.resolver import CredentialMissingError
from embry0.services.instruction_files import DEFAULT_RULES
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.job_reads import MARK as JOB_MARK
from tests.fixtures.job_reads import job_reads_app
from tests.fixtures.repository_writes import COVERED, MASTER, NOW, OWNER, UNCOVERED, Keys, call

#: A repository under the covered repository's owner that the limited key does not cover.
SIBLING = f"{OWNER[COVERED]}/api"
REPOS = (COVERED, UNCOVERED, SIBLING)
#: A repository alone under its owner, bound nothing: every credential it uses is the deployment's (:func:`seed_lone`).
LONE = "initech/app"
#: Its job that failed for want of a model account.
LONE_JOB = "job-lone-failed"
OWNERS = {repo: repo.split("/", 1)[0] for repo in (*REPOS, LONE)}
#: What everything stored for each repository carries.
MARK = {COVERED: "widget-repo-data", UNCOVERED: "gizmo-repo-data", SIBLING: "sprocket-repo-data"}
#: What everything stored for each owner as a whole carries.
OWNER_MARK = {"acme": "acme-owner-data", "globex": "globex-owner-data"}
#: For each repository the limited key does not cover, one nobody registered whose slug is as long.
MISSING = {UNCOVERED: "zyxwvu/gizmos", SIBLING: "acme/zzz"}
#: For each owner, one nobody registered whose name is as long.
MISSING_OWNER = {"acme": "zzzz", "globex": "zyxwvu"}
#: The host every repository is linked to.
HOST = "host-000000000001"
#: Each owner's repository on the forge that nobody registered.
UNLISTED = {owner: f"{owner}/unlisted" for owner in OWNER_MARK}
#: What the label of the deployment's own forge credential, the forge host's, carries.
DEPLOYMENT_MARK = "deployment-wide-data"
#: What the labels of the model accounts of the owners and of the global pool carry: accounts a job may run with.
POOL_MARK = "pool-model-account"
#: The model provider whose accounts the owners and the global pool bind, and no repository binds.
POOL_PROVIDER = "xai"

for _repo, _missing in MISSING.items():
    assert len(_missing) == len(_repo), "a missing repository's slug is as long as the one it stands for"
for _owner, _missing_owner in MISSING_OWNER.items():
    assert len(_missing_owner) == len(_owner), "a missing owner's name is as long as the one it stands for"

_QA_SETUP = (Path(__file__).parent / "qa-yaml-corpus" / "v2" / "single-app.yaml").read_text()


async def _ok(app: FastAPI, method: str, url: str, body: Any = None) -> Any:
    response = await call(app, method, url, MASTER, body)
    assert response.status_code < 300, f"{method} {url}: {response.status_code} {response.text[:300]}"
    return response.json() if response.content and "json" in response.headers.get("content-type", "") else None


#: Each credential's secret differs, as discovery tells accounts apart by their token.
_SECRETS = itertools.count(1)


async def _credential(app: FastAPI, label: str, kind: str = "forge_token", provider: str = "github") -> str:
    secret = f"{'x' * 26}{next(_SECRETS):04d}"
    made = await _ok(
        app, "POST", "/api/v1/credentials", {"kind": kind, "provider": provider, "label": label, "secret": secret}
    )
    return str(made["id"])


async def _seed_repository(app: FastAPI, db: DatabasePool, repo: str) -> None:
    mark, owner = MARK[repo], OWNERS[repo]
    await db.execute("UPDATE repos SET notes = $2, docker_access = true WHERE repo = $1", repo, mark)
    await _ok(app, "PUT", f"/api/v1/repos/{repo}/-/qa-config", f"# {mark}\n{_QA_SETUP}")
    await _ok(
        app,
        "PUT",
        f"/api/v1/repos/{repo}/-/environment",
        {
            "variables": [
                {"key": "API_URL", "value": f"https://{mark}.example.com", "var_type": "config"},
                {"key": "API_TOKEN", "value": f"{mark}-token-000000000000", "var_type": "secret"},
                {"key": "QA_LOGIN_STATE", "value": f"{mark}-state-000000000000", "var_type": "secret"},
            ]
        },
    )
    await _ok(app, "PUT", f"/api/v1/repos/{repo}/-/preferences", {"notes": mark, "base_branch": "main"})
    # Spec 2026-11-10: the repository's areas, a map of them waiting for a person, and their import's ledger.
    await _ok(
        app,
        "PUT",
        f"/api/v1/repos/{repo}/-/areas",
        f"# {mark}\nareas:\n  - name: Area {mark}\n    paths: [src/**]\n    logs: [{mark}]\n",
    )
    await db.execute(
        "INSERT INTO repo_areas_proposals (id, repo, status) SELECT $1, $2, 'pending' "
        "WHERE NOT EXISTS (SELECT 1 FROM repo_areas_proposals WHERE repo = $2)",
        f"areas-{mark}",
        repo,
    )
    await db.execute(
        "UPDATE repo_areas_proposals SET proposed_text = $2, notes_md = $3, created_by = 'ada' WHERE repo = $1",
        repo,
        f"# {mark}\nareas:\n  - name: Mapped {mark}\n    paths: [lib/**]\n",
        f"notes of {mark}",
    )
    await db.execute(
        "INSERT INTO repo_areas_imports (repo, outcome, detail, blob_sha) VALUES ($1, 'imported', $2, $2)",
        repo,
        mark,
    )
    forge = await _credential(app, f"{mark}-forge")
    llm = await _credential(app, f"{mark}-llm", kind="llm_api_key", provider="anthropic")
    await _ok(
        app,
        "PUT",
        f"/api/v1/repos/{repo}/-/credentials",
        {"bindings": [{"role": "forge", "credential_id": forge}, {"role": "llm", "credential_id": llm}]},
    )
    await _ok(app, "PUT", f"/api/v1/config/context/repos/{repo}", {"system_context": mark, "assistant_context": mark})
    await _ok(app, "PUT", f"/api/v1/config/settings?repo={repo}", f"# {mark}\nbackground:\n  slop_meter: true\n")
    await db.execute(
        "INSERT INTO slop_meter_scores (repo, area, score, level, name) VALUES ($1, '', 0.5, 2, $2), ($1, 'api', 0.4, 2, $2)",
        repo,
        mark,
    )
    await db.execute("INSERT INTO slop_meter_repos (repo, head_sha) VALUES ($1, $2)", repo, mark)
    await db.execute("INSERT INTO repo_hosts (repo, host_id, role) VALUES ($1, $2, 'app')", repo.lower(), HOST)
    await db.execute(
        "INSERT INTO org_repo_facts (owner, repo, default_branch, language, facts) VALUES ($1, $2, 'main', $3, $4::jsonb)",
        owner,
        repo,
        mark,
        {"registered": True, "note": mark},
    )
    await db.execute(
        "INSERT INTO watcher_stacks (repo, enabled, logql) VALUES ($1, true, $2) "
        "ON CONFLICT (repo) DO UPDATE SET logql = EXCLUDED.logql",
        repo,
        f'{{job="{mark}"}}',
    )
    await db.execute(
        "INSERT INTO repo_setup_proposals (id, repo, status, proposed_yaml) SELECT $1, $2, 'pending', '' "
        "WHERE NOT EXISTS (SELECT 1 FROM repo_setup_proposals WHERE repo = $2)",
        f"setup-{mark}",
        repo,
    )
    # The uncovered repository's run failed and the sibling's waits for a person: what each found, why it failed and
    # where it stands are the repository's setup state.
    await db.execute(
        "UPDATE repo_setup_proposals SET status = $7, notes_md = $2, failure = $3, smoke = $4::jsonb, "
        "proposed_yaml = $5, created_at = $6, decided_at = $6 WHERE repo = $1",
        repo,
        f"notes of {mark}",
        f"failure of {mark}",
        {"ok": False, "log": mark},
        f"# {mark}\n{_QA_SETUP}",
        NOW - timedelta(minutes=30),
        "failed" if repo == UNCOVERED else "pending",
    )
    await db.execute(
        "INSERT INTO repo_standard_state (repo, owner, kind, mode) VALUES ($1, $2, 'design', 'advise') "
        "ON CONFLICT DO NOTHING",
        repo,
        owner,
    )
    await db.execute(
        "UPDATE repo_standard_state SET last_audit_at = $2, score = 77, findings_open = 5, exceptions = $3::jsonb "
        "WHERE repo = $1",
        repo,
        NOW - timedelta(hours=1),
        [{"rule": "type-scale", "reason": f"exception of {mark}", "paths": []}],
    )
    await db.execute(
        "INSERT INTO knowledge_maps (slug, owner, title, repo, graph) VALUES ($1, $2, $3, $4, $5) "
        "ON CONFLICT DO NOTHING",
        f"map-{mark}",
        owner,
        f"Map of {mark}",
        repo,
        '{"nodes": [{"id": "n1", "kind": "system", "label": "' + mark + '"}], "edges": []}',
    )
    await db.execute(
        "INSERT INTO knowledge_map_marks (map_slug, map_owner, node_id, marked_by, verdict, note) "
        "VALUES ($1, $2, 'n1', 'ada', 'keep', $3)",
        f"map-{mark}",
        owner,
        mark,
    )
    await db.execute(
        "INSERT INTO knowledge_map_sends (map_slug, map_owner, node_id, kind, repo, job_id, sent_by, note) "
        "VALUES ($1, $2, 'n1', 'analysis', $3, NULL, 'ada', $4)",
        f"map-{mark}",
        owner,
        repo,
        mark,
    )


async def _seed_owner(app: FastAPI, db: DatabasePool, owner: str) -> None:
    mark = OWNER_MARK[owner]
    hidden = UNCOVERED if owner == OWNER[UNCOVERED] else SIBLING
    await db.execute(
        "UPDATE organizations SET display_name = $2, access = $3::jsonb, repo_count = 4, discovered_at = $4 "
        "WHERE owner = $1",
        owner,
        mark,
        {"identity": mark, "read": True, "sample_repo": hidden, "repo_count": 4},
        NOW - timedelta(hours=1),
    )
    await db.execute(
        "INSERT INTO org_discovery_runs (id, owner, status, repos_total, repos_seen, summary, finished_at) "
        "VALUES ($1, $2, 'done', 4, 4, $3::jsonb, $4)",
        f"odr-{mark}",
        owner,
        {"repos": 4, "note": mark},
        NOW - timedelta(hours=1),
    )
    await db.execute(
        "INSERT INTO org_repo_facts (owner, repo, default_branch, language, facts) VALUES ($1, $2, 'main', $3, '{}')",
        owner,
        UNLISTED[owner],
        mark,
    )
    await db.execute(
        "INSERT INTO org_needs (id, owner, kind, signature, title, repos, status) "
        "VALUES ($1, $2, 'webhook', $3, $4, $5::jsonb, 'open')",
        f"need-{mark}",
        owner,
        f"sig-{mark}",
        f"Need of {mark}",
        [hidden, UNLISTED[owner]],
    )
    credential = await _credential(app, f"{mark}-forge")
    # Disabled: a resolution of one of the owner's repositories names it, skipped, and goes on to the global pool.
    account = await _credential(app, f"{POOL_MARK}-{owner}", kind="llm_api_key", provider=POOL_PROVIDER)
    await _ok(app, "PUT", f"/api/v1/credentials/{account}", {"status": "disabled"})
    await _ok(
        app,
        "PUT",
        "/api/v1/credentials/bindings",
        {
            "scope_kind": "owner",
            "scope_value": f"github:{owner}",
            "bindings": [
                {"role": "forge", "credential_id": credential},
                {"role": "llm", "credential_id": account},
            ],
        },
    )
    await _ok(app, "PUT", f"/api/v1/standards/{owner}/design", {"schema": {"roles": {}}, "values": {}, "prose": mark})
    await _ok(app, "PUT", f"/api/v1/config/settings?owner={owner}", f"# {mark}\nbackground:\n  slop_meter: true\n")
    await db.execute(
        "INSERT INTO knowledge_maps (slug, owner, title, repo, graph) VALUES ($1, $2, $3, NULL, $4)",
        f"map-{mark}",
        owner,
        f"Map of {mark}",
        '{"nodes": [{"id": "n1", "kind": "system", "label": "' + mark + '"}], "edges": []}',
    )


async def _seed_deployment(app: FastAPI) -> None:
    """The forge host's credential, labelled with :data:`DEPLOYMENT_MARK`, and the global pool's model accounts,
    labelled with :data:`POOL_MARK`: the pool provider's, and Anthropic's, which a setup run pays with where no
    repository binds one."""
    forge = await _credential(app, f"{DEPLOYMENT_MARK}-host-forge")
    await _ok(
        app,
        "PUT",
        "/api/v1/credentials/bindings",
        {"scope_kind": "host", "scope_value": "github.com", "bindings": [{"role": "forge", "credential_id": forge}]},
    )
    account = await _credential(app, f"{POOL_MARK}-global", kind="llm_api_key", provider=POOL_PROVIDER)
    anthropic = await _credential(app, f"{POOL_MARK}-global-anthropic", kind="llm_api_key", provider="anthropic")
    await _ok(
        app,
        "PUT",
        "/api/v1/credentials/bindings",
        {
            "scope_kind": "global",
            "scope_value": "",
            "bindings": [{"role": "llm", "credential_id": account}, {"role": "llm", "credential_id": anthropic}],
        },
    )


async def seed_lone(app: FastAPI, db: DatabasePool, monkeypatch: pytest.MonkeyPatch) -> str:
    """:data:`LONE`, registered and bound nothing, and :data:`LONE_JOB`, which failed for want of the pool provider's
    account while the global pool's was disabled: its error is the resolver's own message, as the executor stores
    an error (``str(exc)``), and its event the sandbox's, as the init node writes it. Answers an admin key whose list
    is :data:`LONE` alone."""
    await db.execute("INSERT INTO repos (repo, active) VALUES ($1, true)", LONE)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    pool = await db.fetchval("SELECT id FROM credentials WHERE label = $1", f"{POOL_MARK}-global")
    await db.execute("UPDATE credentials SET status = 'disabled' WHERE id = $1", pool)
    try:
        await app.state.credential_resolver.resolve_llm(LONE, POOL_PROVIDER)
        raise AssertionError(f"{LONE} resolved a {POOL_PROVIDER} account with the global pool's disabled")
    except CredentialMissingError as exc:
        failure = exc
    finally:
        await db.execute("UPDATE credentials SET status = 'ok' WHERE id = $1", pool)
    # As the sandbox raises it, the executor stores it and the init node writes its event.
    sandbox = SandboxInitError(str(failure), error_code=ErrorCode.CREDENTIAL_MISSING)
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, finished_at, error_message, "
        "error_code) VALUES ($1, $2, 'Set it up', 'failed', 'full-magnum-opus', $3, $3, $4, $5)",
        LONE_JOB,
        LONE,
        NOW - timedelta(minutes=10),
        str(sandbox),
        ErrorCode.CREDENTIAL_MISSING.value,
    )
    await app.state.jobs_repo.append_log_event(
        LONE_JOB, events.error(message=f"Sandbox init failed: {sandbox}", node="init")
    )
    _row, token = await ApiKeysRepository(db).create(
        name="lone", scope="admin", user_id=None, created_by="tests", allowed_repos=[LONE]
    )
    bust_api_key_cache()
    return str(token)


@asynccontextmanager
async def repository_reads_app(
    db: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[FastAPI, Keys, Mapping[str, Mapping[str, str]]]]:
    """:func:`tests.fixtures.job_reads.job_reads_app` with :data:`SIBLING` and every repository's and owner's data."""
    # A walk of every route reads far more than one key's minute of requests; the limiter is not what is read here.
    monkeypatch.setattr(RateLimitMiddleware, "_over_limit", lambda _self, _key, _max_rpm: False)
    async with job_reads_app(db, monkeypatch) as (app, keys, values):
        await db.execute("INSERT INTO repos (repo, active) VALUES ($1, true)", SIBLING)
        await db.execute(
            "INSERT INTO hosts (id, name, address, transport) VALUES ($1, 'shared-1', '10.0.0.1', 'ssh')", HOST
        )
        for repo in REPOS:
            await _seed_repository(app, db, repo)
        for owner in OWNER_MARK:
            await _seed_owner(app, db, owner)
        await _seed_deployment(app)
        # The context-audit schedule has read the sibling already, as the job fixture's two, so it reads no forge.
        context = RepoContext(
            repo=SIBLING, head_sha="abc123", files=("AGENTS.md",), fingerprint=f"fp-{SIBLING}", rules=DEFAULT_RULES
        )
        app.state.context_audit._last_read[SIBLING] = _Reading(NOW, context, None, DEFAULT_RULES)  # noqa: SLF001
        # The first read of the effective settings imports the environment's values into the deployment's document;
        # done here, so no read under test sees the document change between two of its own.
        await _ok(app, "GET", "/api/v1/config/settings/effective")
        yield app, keys, values


def shows_hidden(text: str) -> bool:
    """Whether an answer shows what a key covering only :data:`COVERED` may not read: a repository it does not
    cover, by its slug or anything stored for it, or an owner it does not cover as a whole."""
    lowered = text.lower()
    return any(
        needle in lowered
        for needle in (
            UNCOVERED,
            SIBLING,
            MARK[UNCOVERED],
            MARK[SIBLING],
            *OWNER_MARK.values(),
            *UNLISTED.values(),
            JOB_MARK[UNCOVERED],
        )
    )


def shows_deployment(text: str) -> bool:
    """Whether an answer shows the deployment's own forge credential by its label (:data:`DEPLOYMENT_MARK`): every
    repository's, which a key with an allowlist does not read."""
    return DEPLOYMENT_MARK in text.lower()


def shows_pool(text: str) -> bool:
    """Whether an answer shows a model account of an owner or of the global pool by its label (:data:`POOL_MARK`):
    one a job on a repository a key covers may run with."""
    return POOL_MARK in text.lower()


def placeholders(text: bytes, names: Mapping[str, str]) -> bytes:
    """``text`` with each name in ``names`` put back as its placeholder, longest first."""
    for name in sorted(names, key=len, reverse=True):
        text = text.replace(name.encode(), names[name].encode())
    return text


def same_answer(
    a: httpx.Response, a_names: Mapping[str, str], b: httpx.Response, b_names: Mapping[str, str]
) -> tuple[Any, Any] | None:
    """None when ``a`` and ``b`` are the same answer, status, headers and body, once each one's names (repository,
    owner) are put back as their placeholders; else both, for the message."""

    def shown(response: httpx.Response, names: Mapping[str, str]) -> tuple[int, list[tuple[str, str]], bytes]:
        headers = sorted((k.lower(), placeholders(v.encode(), names).decode()) for k, v in response.headers.items())
        return response.status_code, headers, placeholders(response.content, names)

    left, right = shown(a, a_names), shown(b, b_names)
    return None if left == right else (left, right)
