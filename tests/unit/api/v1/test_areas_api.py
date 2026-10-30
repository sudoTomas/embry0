"""Map areas (spec 2026-10-25): reading a repository's areas, and starting the agent job that writes them."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import areas as areas_api
from embry0.config import Embry0Config
from embry0.services.areas import AreaReader
from tests.fixtures.budget_settings import BudgetStore

HEADERS = {"X-Requested-With": "XMLHttpRequest"}
FILE = 'areas:\n  - name: Billing\n    paths: ["services/billing/**"]\n    owners: ["octo"]\n'


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    st = app.state
    st.db = None
    st.repos_repo = SimpleNamespace(
        registered_as=AsyncMock(side_effect=lambda r: "acme/web" if r.lower() == "acme/web" else None)
    )
    st.jobs_repo = MagicMock()
    st.jobs_repo.newest_per_repo = AsyncMock(return_value={})
    st.jobs_repo.create = AsyncMock(return_value="job-map")
    st.templates_repo = SimpleNamespace(list_all=AsyncMock(return_value=[{"name": "map-areas"}]))
    st.issue_executor = MagicMock()
    st.issue_executor.dispatch_refusal = AsyncMock(return_value=None)
    st.watcher_stacks_repo = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(logql='{container=~"web-.*"}')))
    # The map's hard cap is the budget section's area_map_job_usd, as the repository's organization reads it.
    st.settings = BudgetStore({"area_map_job_usd": 1.5}, acme={"area_map_job_usd": 0.75})

    async def read_file(request: Any, repo: str) -> str | None:
        return FILE

    monkeypatch.setattr(areas_api, "_read_file", read_file)
    monkeypatch.setattr(areas_api, "AREAS", AreaReader())
    return app


async def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_a_repository_s_areas_and_its_newest_map(app: Any) -> None:
    app.state.jobs_repo.newest_per_repo = AsyncMock(
        return_value={
            "acme/web": {"job_id": "job-1", "status": "completed", "pr_url": "https://forge/pull/3", "live": False}
        }
    )
    async with await _client(app) as client:
        body = (await client.get("/api/v1/repos/acme/web/-/areas")).json()
        missing = await client.get("/api/v1/repos/acme/nope/-/areas")
    assert body["path"] == ".embry0/areas.yml"
    assert body["areas"] == [
        {"name": "Billing", "slug": "billing", "paths": ["services/billing/**"], "logs": [], "owners": ["octo"]}
    ]
    assert body["latest_job"]["job_id"] == "job-1" and body["latest_job"]["pr_url"] == "https://forge/pull/3"
    assert missing.status_code == 404


async def test_mapping_starts_the_agent_with_what_the_repository_already_says(app: Any) -> None:
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/repos/acme/web/-/areas/map", json={"run_with": {"model": "claude-sonnet-5"}}, headers=HEADERS
        )
    assert response.status_code == 202, response.text
    assert response.json() == {"job_id": "job-map", "repo": "acme/web"}
    created = app.state.jobs_repo.create.await_args.kwargs
    assert created["pipeline_template"] == "map-areas" and created["context"] == {"type": "git", "repo": "acme/web"}
    config = created["pipeline_config"]
    assert config["open_pr"] is True and config["budget_overrun_mode"] == "hard"
    assert config["max_budget_usd"] == 0.75, "the cap is budget.area_map_job_usd of the repository's organization"
    assert config["agent_models_override"] == {"*": "claude-sonnet-5"}
    task = created["task"]
    assert task.startswith("Map the areas of acme/web")
    assert "name: Billing" in task and '{container=~"web-.*"}' in task
    submitted = app.state.issue_executor.submit_job.call_args
    assert submitted.args == ("job-map",) and submitted.kwargs["kind"] == "map_areas"


async def test_one_map_at_a_time_and_only_on_a_registered_repository(app: Any) -> None:
    app.state.jobs_repo.newest_per_repo = AsyncMock(return_value={"acme/web": {"job_id": "job-1", "live": True}})
    async with await _client(app) as client:
        busy = await client.post("/api/v1/repos/acme/web/-/areas/map", json={}, headers=HEADERS)
        missing = await client.post("/api/v1/repos/acme/nope/-/areas/map", json={}, headers=HEADERS)
    assert busy.status_code == 409 and "job-1" in busy.text
    assert missing.status_code == 404
    app.state.jobs_repo.create.assert_not_called()
