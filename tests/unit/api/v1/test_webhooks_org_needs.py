"""The organization gate and the needs list are one path (ADR 0011, ADR 0012).

A delivery under a registered GitHub organization for a repository no
discovery run has listed is recorded on a bare fact row. The needs sync reads
that row as ``not_registered``, and the gate runs that sync, so the need and
its case awaiting a decision appear without waiting for the next discovery
run. A repository the gate registers leaves the need the same way.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services.healing.service import HealingCaseService
from embry0.services.org_discovery import RepoFacts
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.healing_cases import HealingCasesRepository
from embry0.storage.repositories.org_needs import OrgNeedsRepository
from embry0.storage.repositories.org_repos import OrgReposRepository
from embry0.storage.repositories.organizations import OrganizationsRepository
from embry0.storage.repositories.repos import ReposRepository

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

SECRET = "test-secret"
# Everything discovery could see, a hook pointing here: only registration is missing.
SEEN = {"root_visible": True, "hooks_visible": True, "hook_present": True}


def _app(db: DatabasePool) -> Any:
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_webhook_secret=SECRET)
    app = create_app(config)
    app.state.issues_repo = MagicMock()
    app.state.inputs_repo = MagicMock()
    app.state.issue_executor = MagicMock()
    app.state.github_sync = MagicMock()
    app.state.github_sync.handle_webhook_event = AsyncMock(return_value={"status": "accepted"})
    app.state.db = db
    app.state.repos_repo = ReposRepository(db)
    app.state.organizations_repo = OrganizationsRepository(db)
    app.state.org_repos_repo = OrgReposRepository(db)
    app.state.org_needs_repo = OrgNeedsRepository(db)
    app.state.healing_cases_repo = HealingCasesRepository(db)
    app.state.healing_cases = HealingCaseService(app.state.healing_cases_repo)
    return app


async def _discovered(app: Any, *repos: str) -> None:
    """A registered organization whose finished run listed ``repos``, none registered."""
    await app.state.organizations_repo.register("acme", "github")
    facts = app.state.org_repos_repo
    run = await facts.start_run("acme")
    for repo in repos:
        await facts.upsert_fact(
            RepoFacts(owner="acme", repo=repo, facts={**SEEN, "registered": False}), run_id=run["id"]
        )
    await facts.finish_run(run["id"], summary={"repos": len(repos)})


async def _deliver(app: Any, repo: str, delivery: str) -> Any:
    body = json.dumps(
        {
            "action": "opened",
            "issue": {"number": 7, "title": "Add a widget", "body": "", "labels": [], "html_url": ""},
            "repository": {"full_name": repo},
            "sender": {"login": "octocat"},
        }
    ).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/api/v1/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "issues",
                "X-GitHub-Delivery": delivery,
                "Content-Type": "application/json",
            },
        )


async def _needs(app: Any) -> dict[str, Any]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/orgs/acme/needs")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _open(listing: dict[str, Any]) -> list[tuple[str, list[str]]]:
    return [(n["kind"], n["repos"]) for n in listing["needs"] if n["status"] == "open"]


async def test_a_delivery_for_a_repository_no_run_listed_files_the_not_registered_need(
    db_with_migrations: DatabasePool,
) -> None:
    app = _app(db_with_migrations)
    await _discovered(app, "acme/api")
    before = await _needs(app)
    assert _open(before) == [("not_registered", ["acme/api"])]

    resp = await _deliver(app, "acme/widgets", "d-1")
    assert resp.status_code == 200 and resp.json() == {"status": "accepted"}, "the delivery goes on as before"

    after = await _needs(app)
    assert _open(after) == [("not_registered", ["acme/api", "acme/widgets"])], (
        "the repository the forge delivered for is a need now, not after the next discovery run"
    )
    assert after["run_id"] == before["run_id"]
    cases = app.state.healing_cases_repo
    waiting, total = await cases.list_page(awaiting_decision=True)
    need = next(n for n in after["needs"] if n["status"] == "open")
    assert total == 1 and waiting[0]["id"] == need["case_id"] and waiting[0]["origin"] == "org"
    assert waiting[0]["repo"] == "acme", "a need over two repositories is filed on the owner"

    # More deliveries for the same repository change nothing.
    await _deliver(app, "acme/widgets", "d-2")
    await _deliver(app, "acme/widgets", "d-1")
    assert await _needs(app) == after
    assert (await cases.list_page(awaiting_decision=True))[1] == 1


async def test_a_repository_the_gate_registers_leaves_the_not_registered_need(
    db_with_migrations: DatabasePool,
) -> None:
    app = _app(db_with_migrations)
    await _discovered(app, "acme/api", "acme/web")
    await app.state.organizations_repo.patch("acme", settings={"auto_register": True})
    before = await _needs(app)
    assert _open(before) == [("not_registered", ["acme/api", "acme/web"])]

    resp = await _deliver(app, "acme/api", "d-1")
    assert resp.json() == {"status": "accepted", "auto_registered": "acme/api"}

    after = await _needs(app)
    assert ("not_registered", ["acme/web"]) in _open(after)
    assert not any(kind == "not_registered" and "acme/api" in repos for kind, repos in _open(after))
    old = next(n for n in before["needs"] if n["kind"] == "not_registered")
    closed = await app.state.healing_cases_repo.get(old["case_id"])
    assert closed is not None and closed["stage"] == "closed" and closed["resolution_path"] == "self_resolved"
