"""GET /orgs/{owner}/context-audit (ADR 0020): what the schedule reads, and why each repository is due or not."""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import organizations as organizations_api
from embry0.config import Embry0Config
from embry0.services.context_audit import TEMPLATE, ContextAuditLoop, RepoContext, fingerprint
from embry0.services.organizations import Organization

HEAD = "1" * 40


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
    async def newest_per_repo(
        self, repos: Collection[str], *, pipeline_template: str, dispatched_by: str | None = None
    ) -> dict[str, dict[str, Any]]:
        assert pipeline_template == TEMPLATE and dispatched_by == "embry0:context-audit"
        assert sorted(repos) == ["acme/plain", "acme/web"], "only the owner's own registered repositories"
        when = datetime(2026, 10, 1, tzinfo=UTC)
        return {
            "acme/web": {
                "job_id": "job-7",
                "status": "completed",
                "created_at": when,
                "finished_at": when + timedelta(minutes=9),
                "pipeline_config": {"context_fingerprint": "0" * 64, "head_sha": HEAD},
                "live": False,
            }
        }


class Resp:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


FILES = {"CLAUDE.md": "a" * 40, ".cursor/rules/style.mdc": "b" * 40, "src/app.py": "c" * 40}


class FakeForge:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(self, path: str, **kwargs: Any) -> Resp:
        self.calls.append(path)
        repo = "/".join(path.split("/")[2:4])
        files = FILES if repo == "acme/web" else {"README.md": "d" * 40}
        if path.endswith("/commits"):
            return Resp(200, [{"sha": HEAD, "commit": {"tree": {"sha": f"tree-{repo}"}}}])
        return Resp(200, {"tree": [{"path": p, "type": "blob", "sha": s} for p, s in files.items()]})


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="env-token")
    app = create_app(config)
    app.state.organizations_repo = FakeOrgsRepo(
        Organization(owner="acme", forge="github", settings={"context_audit_days": 7}),
        Organization(owner="lab", forge="gitlab", settings={"context_audit_days": 7}),
    )
    forge = FakeForge()
    tokens: list[str] = []

    def fake_client(request: Any, org: Organization, token: str) -> FakeForge:
        tokens.append(token)
        return forge

    monkeypatch.setattr(organizations_api, "make_forge_client", fake_client)

    async def client_for(org: Organization) -> Any:
        return await organizations_api.org_forge_client(organizations_api.app_request(app), org)

    async def start_job(context: RepoContext, budget_usd: float) -> str:
        raise AssertionError("a read starts no job")

    app.state.context_audit = ContextAuditLoop(
        organizations=app.state.organizations_repo,
        registry=FakeRegistry(),
        jobs=FakeJobs(),
        client_for=client_for,
        start_job=start_job,
        max_jobs_per_tick=3,
        budget_usd=2.0,
        clock=lambda: datetime(2026, 10, 2, tzinfo=UTC),
    )
    app.state.forge, app.state.tokens = forge, tokens
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_the_read_lists_each_repository_with_instruction_files_and_why_it_is_due(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/orgs/acme/context-audit")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "owner": "acme",
        "context_audit_days": 7,
        "template": "context-audit",
        "repos": [
            {
                "repo": "acme/web",
                "files": 2,
                "fingerprint": fingerprint({"CLAUDE.md": "a" * 40, ".cursor/rules/style.mdc": "b" * 40}),
                "head_sha": HEAD,
                "newest_job": {"job_id": "job-7", "status": "completed", "created_at": "2026-10-01T00:00:00+00:00"},
                "refused_at": None,
                "due": True,
                "reason": "changed",
            }
        ],
        "unread": [],
    }
    assert app.state.tokens == ["env-token"], "the forge is read with the organization's credential"
    assert not any(c.startswith("/repos/other/") for c in app.state.forge.calls)


@pytest.mark.asyncio
async def test_the_read_refuses_an_unknown_owner_another_forge_and_a_missing_schedule(app: Any) -> None:
    async with _client(app) as client:
        unknown = await client.get("/api/v1/orgs/nobody/context-audit")
        gitlab = await client.get("/api/v1/orgs/lab/context-audit")
        app.state.context_audit = None
        missing = await client.get("/api/v1/orgs/acme/context-audit")
    assert unknown.status_code == 404
    assert gitlab.status_code == 422 and "GitHub repositories" in gitlab.json()["detail"]
    assert missing.status_code == 503


@pytest.mark.asyncio
async def test_the_read_answers_a_credential_it_cannot_resolve_as_the_routes_do(app: Any) -> None:
    app.state.config.github_token = ""
    async with _client(app) as client:
        resp = await client.get("/api/v1/orgs/acme/context-audit")
    assert resp.status_code == 422
    assert "no github credential for acme" in resp.json()["detail"]
