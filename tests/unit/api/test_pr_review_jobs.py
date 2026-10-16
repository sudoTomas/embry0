"""POST /api/v1/jobs {pipeline: "pr-review"} — the manual review trigger (#30)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services.pr_review.dispatcher import DispatchResult
from embry0.storage.repositories.repos import UnregisteredRepoError


class _Dispatcher:
    def __init__(self, result: DispatchResult | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def dispatch_manual(self, *, repo: str, number: int, actor: str) -> DispatchResult:
        self.calls.append({"repo": repo, "number": number, "actor": actor})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Jobs:
    async def get(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "pipeline_template": "pr-review"}


def _app(dispatcher: _Dispatcher | None):
    @asynccontextmanager
    async def _noop(_app):
        yield

    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config, lifespan_override=_noop)
    app.state.config = config
    app.state.jobs_repo = _Jobs()
    if dispatcher is not None:
        app.state.pr_review_dispatcher = dispatcher
    return app


async def _post(app, body: dict[str, Any]):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("embry0.api.v1.jobs.emit_audit", new=AsyncMock()):
            return await client.post("/api/v1/jobs", json=body, headers={"X-Requested-With": "XMLHttpRequest"})


async def test_a_manual_review_is_dispatched_and_returns_the_job() -> None:
    dispatcher = _Dispatcher(DispatchResult(True, "ok", "job-9"))
    resp = await _post(_app(dispatcher), {"pipeline": "pr-review", "repo": "acme/widgets", "number": 7})
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"job_id": "job-9", "pipeline_template": "pr-review"}
    assert dispatcher.calls[0]["repo"] == "acme/widgets" and dispatcher.calls[0]["number"] == 7


@pytest.mark.parametrize(
    ("body", "missing"),
    [
        ({"pipeline": "pr-review", "repo": "acme/widgets"}, "number is required"),
        ({"pipeline": "pr-review", "number": 7}, "repo is required"),
    ],
)
async def test_a_manual_review_needs_a_repo_and_a_number(body: dict[str, Any], missing: str) -> None:
    resp = await _post(_app(_Dispatcher(DispatchResult(True, "ok", "job-9"))), body)
    assert resp.status_code == 422, resp.text
    assert missing in resp.text, resp.text


async def test_a_refused_review_says_why() -> None:
    resp = await _post(
        _app(_Dispatcher(DispatchResult(False, "own_pull_request"))),
        {"pipeline": "pr-review", "repo": "acme/widgets", "number": 7},
    )
    assert resp.status_code == 409 and "own_pull_request" in resp.text


async def test_an_unregistered_repo_is_a_400() -> None:
    resp = await _post(
        _app(_Dispatcher(UnregisteredRepoError("acme/widgets"))),
        {"pipeline": "pr-review", "repo": "acme/widgets", "number": 7},
    )
    assert resp.status_code == 400


async def test_without_a_dispatcher_it_is_a_503() -> None:
    resp = await _post(_app(None), {"pipeline": "pr-review", "repo": "acme/widgets", "number": 7})
    assert resp.status_code == 503


async def test_a_repo_the_dispatcher_does_not_know_is_a_400() -> None:
    dispatcher = _Dispatcher(DispatchResult(False, "repo_not_registered"))
    resp = await _post(_app(dispatcher), {"pipeline": "pr-review", "repo": "acme/widgets", "number": 7})
    assert resp.status_code == 400, resp.text
    assert "repo_not_registered" in resp.text


@pytest.mark.parametrize(
    "override",
    [
        {"agent_models": {"pr-reviewer": "claude-haiku-4-5"}},
        {"pipeline_config": {"agent_tools": {"pr-reviewer": ["Write"]}}},
        {"pipeline_template": "issue-to-pr"},
        {"sandbox_profile": "full"},
        {"max_budget_usd": 5},
        {"execution_mode_override": "cli"},
        {"auth_mode_override": "api_key"},
        {"additional_context": "focus on the parser"},
        {"task": "review the parser"},
        {"branch": "feature/login"},
        {"qa": {}},
        {"context": {"type": "git", "repo": "acme/widgets"}},
        {"issue_number": 3},
        {"skip_smoke": True},
    ],
)
async def test_a_manual_review_takes_no_overrides(override: dict[str, Any]) -> None:
    """The review runs on the repo's pr_review settings only; an override is refused, not dropped."""
    dispatcher = _Dispatcher(DispatchResult(True, "ok", "job-9"))
    resp = await _post(_app(dispatcher), {"pipeline": "pr-review", "repo": "acme/widgets", "number": 7, **override})
    assert resp.status_code == 422, resp.text
    assert next(iter(override)) in resp.text
    assert dispatcher.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"repo": "acme/widgets", "task": "fix it", "number": 7},
        {"pipeline": "issue-to-pr", "repo": "acme/widgets", "task": "fix it", "number": 7},
        {"pipeline": "qa", "repo": "acme/widgets", "number": 7},
        {"pipeline": "onboard", "repo": "acme/widgets", "number": 7},
    ],
)
async def test_number_is_refused_for_every_other_pipeline(body: dict[str, Any]) -> None:
    """``number`` names the pull request to review; any other pipeline would drop it silently."""
    resp = await _post(_app(_Dispatcher(DispatchResult(True, "ok", "job-9"))), body)
    assert resp.status_code == 422, f"{body.get('pipeline')}: number was accepted ({resp.status_code})"
    assert "number" in resp.text, resp.text
