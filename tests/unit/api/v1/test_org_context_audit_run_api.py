"""POST /orgs/{owner}/context-audit/run: a person starts the due context audits now, through the schedule's start."""

from __future__ import annotations

from collections.abc import Collection, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api import repo_allowlist
from embry0.api.app import create_app
from embry0.api.deps import bust_api_key_cache
from embry0.api.v1 import organizations as organizations_api
from embry0.config import Embry0Config
from embry0.services.context_audit import ContextAuditLoop, RepoContext
from embry0.services.organizations import Organization
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

HEAD = "1" * 40
NOW = datetime(2026, 10, 2, tzinfo=UTC)

OPERATOR_TOKEN = "emb_" + "o" * 43
READ_TOKEN = "emb_" + "r" * 43
LIMITED_TOKEN = "emb_" + "l" * 43


def _key(key_id: str, name: str, scope: str, allowed: tuple[str, ...] | None = None) -> ApiKeyRow:
    return ApiKeyRow(
        id=key_id,
        user_id=None,
        name=name,
        prefix="emb_xxxxxxxx",
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=allowed,
    )


KEYS = {
    hash_token(OPERATOR_TOKEN): _key("key-auditop00001", "audit-operator", "operate"),
    hash_token(READ_TOKEN): _key("key-auditread001", "audit-reader", "read"),
    hash_token(LIMITED_TOKEN): _key("key-auditbot0001", "audit-bot", "operate", ("acme/web",)),
}


class FakeOrgsRepo:
    def __init__(self, *orgs: Organization) -> None:
        self.orgs = {o.owner: o for o in orgs}

    async def get(self, owner: str) -> Organization | None:
        return self.orgs.get(owner.lower())

    async def list_all(self, *, include_removed: bool = False) -> list[Organization]:
        return list(self.orgs.values())


class FakeRegistry:
    async def list_all(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        return [
            {"repo": "acme/plain", "forge": "github", "active": True},
            {"repo": "acme/web", "forge": "github", "active": True},
            {"repo": "other/web", "forge": "github", "active": True},
        ]


class FakeJobs:
    """The jobs the starts below wrote; none of them finishes, so each stays live."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def newest_per_repo(
        self,
        repos: Collection[str],
        *,
        pipeline_template: str,
        dispatched_by: str | None = None,
        exclude_dispatched_by: Collection[str] = (),
    ) -> dict[str, dict[str, Any]]:
        return {row["repo"]: {**row, "live": True} for row in self.rows if row["repo"] in repos}


class Resp:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


class FakeForge:
    async def get(self, path: str, **kwargs: Any) -> Resp:
        repo = "/".join(path.split("/")[2:4])
        files = {"CLAUDE.md": "a" * 40} if repo == "acme/web" else {"README.md": "d" * 40}
        if path.endswith("/commits"):
            return Resp(200, [{"sha": HEAD, "commit": {"tree": {"sha": f"tree-{repo}"}}}])
        return Resp(200, {"tree": [{"path": p, "type": "blob", "sha": s} for p, s in files.items()]})


def make_app(monkeypatch: pytest.MonkeyPatch, **config: Any) -> Any:
    settings: dict[str, Any] = {"auth_dev_mode": True, "webhook_dev_mode": True, "github_token": "env-token"}
    settings.update(config)
    app = create_app(Embry0Config(_env_file=None, **settings))
    app.state.organizations_repo = FakeOrgsRepo(
        # The schedule is off: a start by hand does not read the switch.
        Organization(owner="acme", forge="github", settings={"context_audit_budget_usd": 4.0}),
        Organization(owner="lab", forge="gitlab"),
        Organization(owner="paused", forge="github", status="paused"),
    )
    app.state.db = None
    app.state.users_repo = None
    app.state.provider_repo = None
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    monkeypatch.setattr(organizations_api, "make_forge_client", lambda request, org, token: FakeForge())
    jobs = FakeJobs()
    started: list[tuple[str, float]] = []

    async def client_for(org: Organization) -> Any:
        return await organizations_api.org_forge_client(organizations_api.app_request(app), org)

    async def start_job(context: RepoContext, budget_usd: float, open_pr: bool) -> str:
        job_id = f"job-{len(jobs.rows) + 1}"
        jobs.rows.append({"job_id": job_id, "repo": context.repo, "status": "pending", "created_at": NOW})
        started.append((context.repo, budget_usd))
        return job_id

    async def open_gate() -> None:
        return None

    app.state.context_audit = ContextAuditLoop(
        organizations=app.state.organizations_repo,
        registry=FakeRegistry(),
        jobs=jobs,
        client_for=client_for,
        start_job=start_job,
        ask_gate=open_gate,
        max_jobs_per_tick=3,
        budget_usd=2.0,
        clock=lambda: NOW,
    )
    audits: list[tuple[str, str, dict[str, Any]]] = []

    async def fake_emit_audit(
        db: Any, action: str, *, actor: str = "system", details: dict[str, Any] | None = None, **kw: Any
    ) -> None:
        audits.append((action, actor, dict(details or {})))

    for module in (organizations_api, repo_allowlist):
        monkeypatch.setattr(module, "emit_audit", fake_emit_audit)
    app.state.audits, app.state.started = audits, started
    return app


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    return make_app(monkeypatch)


@pytest.fixture
def keyed_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Authentication on, as in production: bearer keys from ``KEYS``."""
    bust_api_key_cache()
    yield make_app(monkeypatch, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")
    bust_api_key_cache()


def _client(app: Any, token: str | None = None) -> AsyncClient:
    headers = {"X-Requested-With": "XMLHttpRequest"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers)


def _runs(app: Any) -> list[tuple[str, dict[str, Any]]]:
    return [
        (actor, details) for action, actor, details in app.state.audits if action == "organization.context_audit_run"
    ]


RUN = "/api/v1/orgs/acme/context-audit/run"


@pytest.mark.asyncio
async def test_a_run_starts_the_due_repositories_and_the_audit_row_names_the_caller(app: Any) -> None:
    async with _client(app) as client:
        first = await client.post(RUN, json={})
        second = await client.post(RUN, json={})
    assert first.status_code == 200, first.text
    assert first.json() == {
        "owner": "acme",
        "context_audit_days": None,
        "repo": None,
        "force": False,
        "limit": 3,
        "budget_usd": 4.0,
        "started": [{"repo": "acme/web", "job_id": "job-1", "reason": "first"}],
        "skipped": [],
    }
    assert app.state.started == [("acme/web", 4.0)], "the owner's own cap reaches the job"
    assert second.json()["started"] == []
    assert second.json()["skipped"] == [{"repo": "acme/web", "reason": "live", "detail": None}]
    assert _runs(app) == [
        (
            "embry0:anonymous",
            {"owner": "acme", "repo": None, "force": False, "limit": 3, "started": ["job-1"], "skipped": 0},
        ),
        (
            "embry0:anonymous",
            {"owner": "acme", "repo": None, "force": False, "limit": 3, "started": [], "skipped": 1},
        ),
    ]


@pytest.mark.asyncio
async def test_a_run_for_one_repository_says_why_it_did_not_start(app: Any) -> None:
    async with _client(app) as client:
        plain = await client.post(RUN, json={"repo": "acme/plain", "force": True})
        web = await client.post(RUN, json={"repo": "ACME/web", "limit": 5})
        again = await client.post(RUN, json={"repo": "acme/web", "force": True})
    assert plain.status_code == 200, plain.text
    assert plain.json()["skipped"] == [{"repo": "acme/plain", "reason": "no_instruction_files", "detail": None}]
    assert (web.json()["repo"], web.json()["limit"]) == ("acme/web", None)
    assert web.json()["started"] == [{"repo": "acme/web", "job_id": "job-1", "reason": "first"}]
    assert again.json()["skipped"] == [{"repo": "acme/web", "reason": "live", "detail": None}]
    assert [details["repo"] for _actor, details in _runs(app)] == ["acme/plain", "acme/web", "acme/web"]


@pytest.mark.asyncio
async def test_a_run_is_refused_for_what_it_cannot_start(app: Any) -> None:
    async with _client(app) as client:
        refused = {
            "unknown owner": await client.post("/api/v1/orgs/nobody/context-audit/run", json={}),
            "GitLab owner": await client.post("/api/v1/orgs/lab/context-audit/run", json={}),
            "paused owner": await client.post("/api/v1/orgs/paused/context-audit/run", json={}),
            "another owner's repository": await client.post(RUN, json={"repo": "other/web"}),
            "an unregistered repository": await client.post(RUN, json={"repo": "acme/nope"}),
            "force without a repository": await client.post(RUN, json={"force": True}),
            "a limit of 0": await client.post(RUN, json={"limit": 0}),
            "a limit past 50": await client.post(RUN, json={"limit": 51}),
            "a misspelled field": await client.post(RUN, json={"forse": True}),
            "a repository path of one segment": await client.post(RUN, json={"repo": "web"}),
        }
        app.state.context_audit = None
        refused["no schedule"] = await client.post(RUN, json={})
    assert {label: r.status_code for label, r in refused.items()} == {
        "unknown owner": 404,
        "GitLab owner": 422,
        "paused owner": 409,
        "another owner's repository": 404,
        "an unregistered repository": 404,
        "force without a repository": 422,
        "a limit of 0": 422,
        "a limit past 50": 422,
        "a misspelled field": 422,
        "a repository path of one segment": 422,
        "no schedule": 503,
    }
    assert "name it with repo" in refused["force without a repository"].json()["detail"]
    assert "not an active registered repository of acme" in refused["another owner's repository"].json()["detail"]
    assert app.state.started == [] and _runs(app) == []


@pytest.mark.asyncio
async def test_a_run_takes_the_tier_and_the_allowlist_of_creating_a_job(keyed_app: Any) -> None:
    async with _client(keyed_app, READ_TOKEN) as client:
        read = await client.post(RUN, json={})
    async with _client(keyed_app, LIMITED_TOKEN) as client:
        unnamed = await client.post(RUN, json={})
        other = await client.post(RUN, json={"repo": "acme/plain"})
        listed = await client.post(RUN, json={"repo": "acme/web"})
    async with _client(keyed_app, OPERATOR_TOKEN) as client:
        operator = await client.post(RUN, json={})
    assert read.status_code == 403, read.text
    for refused in (unnamed, other):
        assert refused.status_code == 403 and refused.json()["detail"].startswith("repo_not_allowed"), refused.text
    assert listed.status_code == 200, listed.text
    assert listed.json()["started"] == [{"repo": "acme/web", "job_id": "job-1", "reason": "first"}]
    assert operator.status_code == 200, operator.text
    assert [actor for actor, _details in _runs(keyed_app)] == ["key:audit-bot", "key:audit-operator"]
