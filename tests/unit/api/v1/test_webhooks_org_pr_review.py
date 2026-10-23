"""The organization gate and pull request review meet on one delivery (ADR 0012, #30).

With ``auto_register`` on, the gate registers a repository before the
``pull_request`` handlers run, so the reviewer's first gate (the repository
is registered) passes for a repository nobody configured. The review must
still start only when the repository's ``pr_review.enabled`` is on: a
``repo_preferences.pr_review`` that was never set (NULL) means off.
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
from embry0.services.pr_review.dispatcher import PrReviewDispatcher
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.org_repos import OrgReposRepository
from embry0.storage.repositories.organizations import OrganizationsRepository
from embry0.storage.repositories.pr_reviews import PrReviewsRepository
from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository
from embry0.storage.repositories.repos import ReposRepository
from tests.fixtures.webhooks import github_review_pull_request

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

SECRET = "test-secret"
REPO = "acme/widgets"


class _Executor:
    """Records every review job asked for; refuses nothing on budget."""

    def __init__(self, db: DatabasePool) -> None:
        self.db = db
        self.started: list[dict[str, Any]] = []

    async def dispatch_refusal(self, *, actor: str | None = None) -> None:
        return None

    async def start_pr_review_job(self, *, repo: str, review: dict[str, Any], dispatched_by: str) -> str:
        self.started.append({"repo": repo, "review": review})
        return await JobsRepository(self.db).create(
            repo=repo, task="t", pipeline_template="pr-review", pipeline_config=review, dispatched_by=dispatched_by
        )


def _app(db: DatabasePool, executor: _Executor) -> Any:
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
    app.state.repo_preferences_repo = RepoPreferencesRepository(db)
    app.state.pr_review_dispatcher = PrReviewDispatcher(
        config=config,
        repos_repo=app.state.repos_repo,
        prefs_repo=app.state.repo_preferences_repo,
        jobs_repo=JobsRepository(db),
        reviews_repo=PrReviewsRepository(db),
        executor=executor,
    )
    return app


async def _deliver_ready(app: Any) -> Any:
    body = json.dumps(github_review_pull_request(action="ready_for_review", repo=REPO, head_repo=REPO)).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/api/v1/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "d-ready-1",
                "Content-Type": "application/json",
            },
        )


async def test_a_repository_the_gate_registers_is_not_reviewed_while_pr_review_is_null(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    executor = _Executor(db)
    app = _app(db, executor)
    await app.state.organizations_repo.register("acme", "github")
    await app.state.organizations_repo.patch("acme", settings={"auto_register": True})
    assert await app.state.repos_repo.registered_as(REPO) is None, "precondition: nobody registered the repository"

    resp = await _deliver_ready(app)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("auto_registered") == REPO, "precondition: the gate registered the repository on this delivery"
    registered = await app.state.repos_repo.registered_as(REPO)
    assert registered is not None
    prefs = await app.state.repo_preferences_repo.get(registered)
    assert prefs is None or prefs.get("pr_review") is None, "precondition: pr_review was never configured"

    assert executor.started == [], "an auto-registered repository with pr_review NULL started a review job"
    assert body.get("pr_review") == {"dispatched": False, "reason": "review_disabled"}, body
    assert await db.fetchval("SELECT count(*) FROM pr_reviews") == 0, "a pr_reviews row was claimed"
