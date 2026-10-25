"""Run with (spec 2026-10-25): the keys a job may use, the check every action makes, and the routes handing it on."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services.run_with import RunWith

HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def _key(cid: str, provider: str, label: str, status: str = "ok") -> dict[str, Any]:
    return {"id": cid, "kind": "llm_api_key", "provider": provider, "label": label, "status": status}


class Creds:
    rows = {
        "cred-team": _key("cred-team", "openrouter", "team router"),
        "cred-claude": _key("cred-claude", "anthropic", "claude key"),
        "cred-owner": _key("cred-owner", "anthropic", "owner key"),
        "cred-off": _key("cred-off", "anthropic", "retired key", status="disabled"),
        "cred-elsewhere": _key("cred-elsewhere", "anthropic", "another team's key"),
        "cred-forge": {"id": "cred-forge", "kind": "forge_token", "provider": "github", "label": "bot"},
    }

    @staticmethod
    def _binding(cid: str, role: str = "llm") -> dict[str, Any]:
        row = Creds.rows[cid]
        return {
            "credential_id": cid,
            "role": role,
            "provider": row["provider"],
            "label": row["label"],
            "status": row.get("status"),
        }

    async def get(self, credential_id: str) -> dict[str, Any] | None:
        return self.rows.get(credential_id)

    async def bindings_for_scope(self, scope_kind: str, scope_value: str) -> list[dict[str, Any]]:
        return {
            ("repo", "owner/repo"): [self._binding("cred-team")],
            ("owner", "github:owner"): [
                self._binding("cred-owner"),
                self._binding("cred-team"),
                self._binding("cred-off"),
            ],
            ("global", ""): [self._binding("cred-claude"), self._binding("cred-forge", role="forge")],
            ("repo", "other/repo"): [self._binding("cred-elsewhere")],
        }.get((scope_kind, scope_value), [])


@pytest.fixture
def app() -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.credentials_repo = Creds()
    jobs = MagicMock()
    jobs.create = AsyncMock(return_value="job-1")
    jobs.get = AsyncMock(
        return_value={"job_id": "job-1", "status": "pending", "repo": "owner/repo", "task": "t", "total_cost_usd": 0.0}
    )
    app.state.jobs_repo = jobs
    app.state.issue_executor = MagicMock()
    app.state.db = None  # audit rows go nowhere
    return app


async def _post(app: Any, path: str, body: dict[str, Any]) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, json=body, headers=HEADERS)


async def test_the_keys_a_job_may_use_are_its_repo_s_its_owner_s_and_the_global_ones(app: Any) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = (await client.get("/api/v1/run-with/keys", params={"repo": "owner/repo"})).json()
        anywhere = (await client.get("/api/v1/run-with/keys")).json()
    assert [(k["id"], k["scope"]) for k in listed["keys"]] == [
        ("cred-team", "repo"),
        ("cred-owner", "owner"),
        ("cred-off", "owner"),
        ("cred-claude", "global"),
    ], "each key once, from its most specific binding; forge accounts are not LLM keys"
    assert listed["keys"][0] == {
        "id": "cred-team",
        "label": "team router",
        "provider": "openrouter",
        "scope": "repo",
        "status": "ok",
    }
    assert [k["id"] for k in anywhere["keys"]] == ["cred-claude"]


async def test_a_job_runs_with_the_chosen_model_and_key(app: Any) -> None:
    response = await _post(
        app,
        "/api/v1/jobs",
        {
            "repo": "owner/repo",
            "task": "Fix it",
            "run_with": {"model": "claude-sonnet-5", "credential_id": "cred-owner"},
        },
    )
    assert response.status_code == 201, response.text
    config = app.state.jobs_repo.create.call_args.kwargs["pipeline_config"]
    assert config == {
        "agent_models_override": {"*": "claude-sonnet-5"},
        "run_with": {
            "model": "claude-sonnet-5",
            "credential_id": "cred-owner",
            "provider": "anthropic",
            "credential_label": "owner key",
        },
    }


@pytest.mark.parametrize(
    ("run_with", "said"),
    [
        ({"model": "grok-nope-9"}, "no price entry"),
        ({"credential_id": "cred-404"}, "not a stored LLM key"),
        ({"credential_id": "cred-forge"}, "not a stored LLM key"),
        ({"credential_id": "cred-off"}, "disabled"),
        ({"credential_id": "cred-elsewhere"}, "not bound for owner/repo"),
        ({"model": "claude-sonnet-5", "credential_id": "cred-team"}, "a openrouter key"),
    ],
)
async def test_a_choice_that_cannot_run_is_refused_before_anything_starts(
    app: Any, run_with: dict[str, str], said: str
) -> None:
    response = await _post(app, "/api/v1/jobs", {"repo": "owner/repo", "task": "Fix it", "run_with": run_with})
    assert response.status_code == 422 and said in response.text, response.text
    app.state.jobs_repo.create.assert_not_called()


async def test_triage_hands_the_choice_to_the_job(app: Any) -> None:
    issues = MagicMock()
    now = datetime.now(UTC)
    row = {
        "id": "iss-1",
        "title": "t",
        "body": "",
        "status": "open",
        "priority": "medium",
        "labels": [],
        "repo": "owner/repo",
        "parent_issue_id": None,
        "github_sync_enabled": False,
        "github_synced_at": None,
        "created_by": "octo",
        "created_at": now,
        "updated_at": now,
    }
    issues.get = AsyncMock(return_value=row)
    issues.update = AsyncMock()
    app.state.issues_repo = issues
    app.state.issue_executor.execute = AsyncMock(return_value="job-9")
    response = await _post(app, "/api/v1/issues/iss-1/triage", {"run_with": {"credential_id": "cred-team"}})
    assert response.status_code == 200, response.text
    chosen = app.state.issue_executor.execute.await_args.kwargs["run_with"]
    assert chosen == RunWith(credential_id="cred-team", provider="openrouter", credential_label="team router")
    response = await _post(app, "/api/v1/issues/iss-1/triage", {})
    assert "run_with" not in app.state.issue_executor.execute.await_args.kwargs, "no choice, the usual call"


async def test_a_watcher_tick_by_hand_runs_with_the_choice(app: Any) -> None:
    service = MagicMock()
    service.run_once = AsyncMock(return_value={"action": "analysed"})
    app.state.watcher_services = {"owner/repo": service}
    response = await _post(app, "/api/v1/watcher/run?stack=owner/repo", {"run_with": {"model": "claude-sonnet-5"}})
    assert response.status_code == 200, response.text
    service.run_once.assert_awaited_once_with(RunWith(model="claude-sonnet-5"))
