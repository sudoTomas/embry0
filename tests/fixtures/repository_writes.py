"""The real app on a real database, with work of every kind in two repositories, called through API keys.

A key may carry a repository allowlist (:mod:`embry0.api.repo_allowlist`). The
routes that change a repository's state refuse a key whose list does not cover
that repository. To show that each one does, a test needs routes that
actually answer: a route that fails for want of a table or a service answers
500 whatever the key, and a check of its answer measures nothing. So the app
here is built as production builds its state (``_init_app_state``) on a
migrated database, with no Docker and no network, and :func:`seed` gives
:data:`COVERED` and :data:`UNCOVERED` one item of each kind a write route
names: a case, an issue and its question, a job and its question, a merge
approval, an organization need, a setup proposal, a configuration revision, a
variable, a watcher stack, a knowledge map, a repository's design standard,
and the offered work (a proposal, its decisions, a hand-off and an ask).

Keys: :data:`LIMITED` is admin scope and covers :data:`COVERED` alone, so its
scope never refuses it and only its list can; :data:`OPEN` is admin scope with
no list; :data:`MASTER` is the deployment's key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from embry0.api.app import _init_app_state, create_app
from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.services.merge_approvals import MergeApprovalService
from embry0.storage.database import DatabasePool
from embry0.storage.encryption import FernetSecretsProvider
from embry0.storage.repositories.api_keys import ApiKeysRepository
from embry0.storage.repositories.job_verifications import JobVerificationsRepository
from embry0.storage.repositories.merge_approvals import MergeApprovalsRepository
from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

#: The repository the limited key covers, and one it does not, each under its own owner.
COVERED, UNCOVERED = "acme/web", "globex/gizmos"
OWNER = {COVERED: "acme", UNCOVERED: "globex"}
#: Each repository's items are named with its tag.
TAG = {COVERED: "widget", UNCOVERED: "gizmo"}
MASTER = "master-key-" + "0" * 30
NOW = datetime.now(UTC)


@dataclass(frozen=True)
class Keys:
    limited: str
    open: str
    master: str = MASTER


def _no_network(*_args: Any, **_kwargs: Any) -> Any:
    raise httpx.ConnectError("tests reach no network")


class _NoForge:
    """A forge that holds nothing open: an issue reads as closed, and a change sent to it does nothing."""

    async def get_issue(self, repo: str, number: int) -> dict[str, Any]:
        return {"state": "closed"}

    async def push_create(self, *_args: Any) -> None:
        return None

    async def push_update(self, *_args: Any) -> None:
        return None


class _NoFinders:
    """The leads loop, with nothing to find: a pass reads the repositories asked and finds nothing."""

    async def tick(self, repos: list[str] | None, **_kwargs: Any) -> list[dict[str, Any]]:
        return [{"repo": repo, "found": 0} for repo in repos or []]


class _NoFollowUps:
    """Comment dispatch that starts no follow-up job and says so."""

    async def start_followup(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"dispatched": False, "reason": "no_jobs_in_tests"}


class _NoDueAudits:
    """The context-audit schedule, with nothing due: ``run`` starts nothing and says so."""

    async def run(self, org: Any, *, repo: str | None = None, **_kwargs: Any) -> Any:
        return SimpleNamespace(repo=repo, limit=None, started=[], skipped=[], to_dict=lambda: {"started": []})


def ids(repo: str) -> dict[str, str]:
    """Each path parameter's value naming ``repo``'s item."""
    tag = TAG[repo]
    return {
        "case_id": f"hc-{tag}",
        "issue_id": f"iss-{tag}",
        "job_id": f"job-done-{tag}",
        "input_id": f"in-{tag}",
        "approval_id": f"ma-{tag}",
        "need_id": f"need-{tag}",
        "owner": OWNER[repo],
        "full_repo": repo,
        "repo": repo,
        "name": repo.split("/", 1)[1],
        "kind": "design",
        "key": "API_URL",
        "revision_id": f"rev-{tag}",
        "slug": f"map-{tag}",
        "proposal_id": f"issue:iss-{tag}",
        "setup_proposal_id": f"setup-{tag}",
        "decision_id": f"offer:issue:iss-{tag}",
        "handoff_id": f"ho-{tag}",
        "ask_id": f"ask-{tag}",
        "run_id": f"qa-{tag}",
    }


async def seed(db: DatabasePool) -> dict[str, dict[str, str]]:
    """Work of every kind in both repositories; each repository's ids, by path parameter, after what was minted."""
    from embry0.services.leads import Lead
    from embry0.storage.repositories.handoffs import HandoffsRepository
    from embry0.storage.repositories.leads import LeadsRepository

    out: dict[str, dict[str, str]] = {}
    for repo, tag in TAG.items():
        owner = OWNER[repo]
        values = ids(repo)
        await db.execute("INSERT INTO organizations (owner, forge) VALUES ($1, 'github') ON CONFLICT DO NOTHING", owner)
        await db.execute("INSERT INTO repos (repo, active) VALUES ($1, true) ON CONFLICT DO NOTHING", repo)
        await db.execute(
            "INSERT INTO repo_preferences (repo, owners) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (repo) DO UPDATE SET owners = EXCLUDED.owners",
            repo,
            ["ada"],
        )
        await db.execute(
            "INSERT INTO watcher_stacks (repo, enabled, logql) VALUES ($1, true, '{job=\"x\"}') ON CONFLICT DO NOTHING",
            repo,
        )
        await db.execute(
            "INSERT INTO issues (id, title, repo, status, created_at) VALUES ($1, $2, $3, 'open', $4)",
            values["issue_id"],
            f"Issue {tag}",
            repo,
            NOW - timedelta(hours=5),
        )
        await db.execute(
            "INSERT INTO healing_cases (id, repo, origin, stage, title, tracker, tracker_key, tracker_url, "
            "created_at, tracked_at) VALUES ($1, $2, 'watcher', 'tracked', $3, 'github', '90', $4, $5, $5)",
            values["case_id"],
            repo,
            f"Case {tag}",
            f"https://github.com/{repo}/issues/90",
            NOW - timedelta(hours=4),
        )
        await LeadsRepository(db).upsert(
            [
                Lead(
                    kind="ci",
                    repo=repo,
                    source_key=f"check:{tag}",
                    title=f"Lead {tag}",
                    summary="s",
                    details="d",
                    action="a",
                    severity="high",
                    paths=("src/app.py",),
                    data={"check": tag},
                )
            ],
            now=NOW - timedelta(hours=3),
        )
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, finished_at, "
            "pr_url, delivery_state, delivery_state_at, dispatched_by) "
            "VALUES ($1, $2, $3, 'completed', 'full-magnum-opus', $4, $4, $5, $6, 'pr_open', $5, 'ada@example.com')",
            values["job_id"],
            repo,
            f"Job {tag}",
            NOW - timedelta(hours=2),
            NOW - timedelta(hours=1),
            f"https://github.com/{repo}/pull/7",
        )
        await db.execute(
            "INSERT INTO issue_inputs (id, issue_id, job_id, question) VALUES ($1, $2, $3, 'Which one?')",
            values["input_id"],
            values["issue_id"],
            values["job_id"],
        )
        await db.execute(
            "INSERT INTO merge_approvals (id, repo, pr_number, pr_url, head_sha, job_id, status) "
            "VALUES ($1, $2, 7, $3, 'abc', $4, 'pending')",
            values["approval_id"],
            repo,
            f"https://github.com/{repo}/pull/7",
            values["job_id"],
        )
        await db.execute(
            "INSERT INTO org_needs (id, owner, kind, signature, title, repos, status) "
            "VALUES ($1, $2, 'webhook', $3, $4, $5::jsonb, 'open')",
            values["need_id"],
            owner,
            f"sig-{tag}",
            f"Need {tag}",
            [repo],
        )
        await db.execute(
            "INSERT INTO repo_setup_proposals (id, repo, status, proposed_yaml) VALUES ($1, $2, 'pending', $3)",
            values["setup_proposal_id"],
            repo,
            "version: 2\n",
        )
        revision = await db.fetchval(
            "INSERT INTO repo_config_revisions (repo, surface, before, after, actor) "
            "VALUES ($1, 'preferences', NULL, $2::jsonb, 'ada') RETURNING id",
            repo,
            {"owners": ["ada"]},
        )
        values["revision_id"] = str(revision)
        await db.execute(
            "INSERT INTO knowledge_maps (slug, owner, title, repo, graph) VALUES ($1, $2, $3, $4, $5)",
            values["slug"],
            owner,
            f"Map {tag}",
            repo,
            '{"nodes": [{"id": "n1", "kind": "system", "label": "n1"}], "edges": []}',
        )
        await db.execute(
            "INSERT INTO repo_standard_state (repo, owner, kind, mode) VALUES ($1, $2, 'design', 'advise')",
            repo,
            owner,
        )
        handoff = await HandoffsRepository(db).send(
            decision_id=f"done:{values['job_id']}",
            repo=repo,
            title=f"Handoff {tag}",
            from_actor="bo@example.com",
            to_email="ada@example.com",
            note="",
            now=NOW - timedelta(minutes=20),
        )
        values["handoff_id"] = str(handoff["id"])
        await db.execute(
            "INSERT INTO asks (id, decision_id, recipient, due_at, created_at) VALUES ($1, $2, 'ada@example.com', $3, $3)",
            values["ask_id"],
            values["decision_id"],
            NOW - timedelta(minutes=15),
        )
        out[repo] = values
    return out


async def mint(db: DatabasePool) -> Keys:
    keys = ApiKeysRepository(db)
    _row, limited = await keys.create(
        name="limited", scope="admin", user_id=None, created_by="tests", allowed_repos=[COVERED]
    )
    _row, unlimited = await keys.create(name="open", scope="admin", user_id=None, created_by="tests")
    bust_api_key_cache()
    return Keys(limited=limited, open=unlimited)


@asynccontextmanager
async def repository_writes_app(
    db: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[FastAPI, Keys, Mapping[str, Mapping[str, str]]]]:
    """The app over ``db`` (migrated, empty), seeded, with its keys and each repository's ids."""
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _no_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _no_network)
    url = db._url  # noqa: SLF001

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = DatabasePool(url)
        await pool.connect()
        app.state.secrets_provider = FernetSecretsProvider(secret_key="tests-secret-key")
        await _init_app_state(app, pool, database_url=url)
        # What the production lifespan adds beside _init_app_state, for the routes that read it.
        app.state.watcher_stacks_repo = WatcherStacksRepository(pool)
        app.state.context_audit = _NoDueAudits()
        app.state.comment_dispatch = _NoFollowUps()
        app.state.merge_approvals = MergeApprovalService(
            repo=MergeApprovalsRepository(pool),
            github=_NoForge(),
            notifier=None,
            config=app.state.config,
            verifications=JobVerificationsRepository(pool),
            cases=app.state.healing_cases_repo,
            jobs=app.state.jobs_repo,
        )
        # The forge is not reached: a draft reads as closed there, and an issue's sync does nothing.
        app.state.github_issues_client = _NoForge()
        app.state.github_sync = _NoForge()
        app.state.leads_loop = _NoFinders()
        try:
            yield
        finally:
            await pool.close()

    config = Embry0Config(
        _env_file=None,
        database_url=url,
        auth_dev_mode=False,
        webhook_dev_mode=False,
        api_key=MASTER,
    )
    app = create_app(config, lifespan_override=lifespan)
    async with lifespan(app):
        values = await seed(db)
        keys = await mint(db)
        # A forge credential in the registry, for the routes that bind one.
        made = await call(
            app,
            "POST",
            "/api/v1/credentials",
            MASTER,
            {"kind": "forge_token", "provider": "github", "label": "fixture-forge", "secret": "x" * 24},
        )
        assert made.status_code in (200, 201), made.text
        for repo_values in values.values():
            repo_values["credential_id"] = str(made.json()["id"])
        yield app, keys, values
    bust_api_key_cache()


async def call(app: FastAPI, method: str, url: str, token: str, body: Any = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        if isinstance(body, str):
            return await client.request(method, url, content=body, headers={"Content-Type": "application/yaml"})
        return await client.request(method, url, json=body)
