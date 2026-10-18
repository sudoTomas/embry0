"""GET /orgs/{owner}/repos carries what each repository takes to maintain (ADR 0014)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services.org_discovery import RepoFacts
from tests.unit.api.v1.test_org_needs_api import NewestFirstFactsRepo
from tests.unit.api.v1.test_organizations_api import FakeOrgsRepo

SEEN = {"root_visible": True, "hooks_visible": True}


async def _run(facts_repo: NewestFirstFactsRepo, *found: RepoFacts) -> str:
    run = await facts_repo.start_run("acme")
    for facts in found:
        await facts_repo.upsert_fact(facts, run_id=run["id"])
    await facts_repo.finish_run(run["id"], summary={"repos": len(found)})
    return str(run["id"])


@pytest.fixture
async def app() -> Any:
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="env-token")
    app = create_app(config)
    app.state.organizations_repo = FakeOrgsRepo()
    app.state.org_repos_repo = NewestFirstFactsRepo()
    app.state.db = None
    await app.state.organizations_repo.register("acme", "github")
    await app.state.organizations_repo.update_access("acme", {"read": True, "hooks": True})
    facts_repo = app.state.org_repos_repo
    # The first run listed a repository the second no longer lists.
    await _run(facts_repo, RepoFacts(owner="acme", repo="acme/gone", facts={**SEEN, "registered": False}))
    await _run(
        facts_repo,
        RepoFacts(
            owner="acme",
            repo="acme/web",
            facts={
                **SEEN,
                "hook_present": True,
                "registered": True,
                "qa_config": True,
                "watcher_stack": True,
                "deploy_marker": True,
                "boot_hint": "npm run dev",
            },
        ),
        RepoFacts(
            owner="acme",
            repo="acme/api",
            facts={**SEEN, "hook_present": False, "registered": False, "boot_hint": "docker compose -f compose.yml up"},
        ),
        RepoFacts(owner="acme", repo="acme/old", archived=True, facts={"skipped": "archived"}),
    )
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    )


async def _repos(app: Any) -> dict[str, dict[str, Any]]:
    async with _client(app) as client:
        resp = await client.get("/api/v1/orgs/acme/repos")
    assert resp.status_code == 200, resp.text
    return {row["repo"]: row for row in resp.json()["repos"]}


@pytest.mark.asyncio
async def test_every_row_carries_a_hint_and_its_steps(app: Any) -> None:
    repos = await _repos(app)
    assert set(repos) == {"acme/gone", "acme/web", "acme/api", "acme/old"}
    for row in repos.values():
        assert isinstance(row["maintain_hint"], str) and row["maintain_hint"]
        assert isinstance(row["maintain_steps"], list)
        assert "facts" in row and "run_id" in row, "the fact row is returned as before"
    assert repos["acme/web"]["maintain_hint"] == "Registered and watched; QA configured; nothing to do."
    assert repos["acme/web"]["maintain_steps"] == []
    assert repos["acme/api"]["maintain_steps"] == ["register", "hook", "onboard"]
    assert repos["acme/api"]["maintain_hint"] == (
        "Not registered; a service with a compose file; no webhook (the token can manage hooks), "
        "so register, then add the webhook, then onboard to draft QA."
    )
    assert repos["acme/old"]["maintain_hint"] == "Archived; nothing to do."


@pytest.mark.asyncio
async def test_a_repository_the_latest_run_did_not_list_asks_for_nothing(app: Any) -> None:
    gone = (await _repos(app))["acme/gone"]
    assert gone["maintain_hint"].startswith("Not listed by the latest discovery run")
    assert gone["maintain_steps"] == []


@pytest.mark.asyncio
async def test_without_hook_administration_the_webhook_step_is_the_credential(app: Any) -> None:
    await app.state.organizations_repo.update_access("acme", {"read": True, "hooks": False})
    api = (await _repos(app))["acme/api"]
    assert api["maintain_steps"] == ["register", "credential", "onboard"]
    assert "the probe found the token cannot manage hooks" in api["maintain_hint"]


@pytest.mark.asyncio
async def test_unknown_owner_is_404(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/orgs/nobody/repos")
    assert resp.status_code == 404
