"""GitHub webhook ingestion of /answer N: directives in issue_comment events.

These tests drive the FastAPI handler in ``embry0/api/v1/webhooks.py``
through ``httpx.AsyncClient`` against an in-memory ASGI app whose
``app.state`` repos are mocks. ``webhook_dev_mode=True`` plus an empty
secret bypasses HMAC verification (smee.io flow), so the tests focus on
the routing logic — payload → repo lookup → dispatcher → resume — not
on signature handling (covered separately in ``test_webhooks.py``).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config


def _make_app(*, issue: dict | None, pending_inputs: list[dict] | None = None):
    """Build a test app with mocked repos.

    ``issue`` is what ``issues_repo.find_by_repo_and_github_number`` returns
    (or None to simulate an untracked repo/issue). ``pending_inputs`` seeds
    the inputs_repo so the dispatcher's ``list_pending_for_issue`` finds
    matching rows for ``/answer N:`` directives.
    """
    config = Embry0Config(
        _env_file=None,
        auth_dev_mode=True,
        webhook_dev_mode=True,
        github_webhook_secret="",  # dev_mode + empty secret = signature skipped
    )
    app = create_app(config)

    issues_repo = MagicMock()
    issues_repo.find_by_repo_and_github_number = AsyncMock(return_value=issue)
    issues_repo.get_by_github = AsyncMock(return_value=issue)
    issues_repo.get = AsyncMock(return_value=issue)

    inputs_repo = MagicMock()
    inputs_repo.list_pending_for_issue = AsyncMock(return_value=list(pending_inputs or []))
    inputs_repo.answer = AsyncMock()
    inputs_repo.skip = AsyncMock()
    inputs_repo.count_pending_blocking = AsyncMock(return_value=0)

    executor = MagicMock()
    executor.resume_for_issue = AsyncMock()

    github_sync = MagicMock()
    github_sync.handle_webhook_event = AsyncMock(return_value={"status": "ignored"})

    app.state.issues_repo = issues_repo
    app.state.inputs_repo = inputs_repo
    app.state.issue_executor = executor
    app.state.github_sync = github_sync
    app.state.jobs_repo = MagicMock()
    return app, issues_repo, inputs_repo, executor


@pytest.mark.asyncio
async def test_issue_comment_with_answer_directive_applies_to_issue():
    """GitHub posts an issue_comment with /answer 1: yes — the matching
    issue_inputs row gets answered via ``inputs_repo.answer`` and the
    workflow is resumed because no blocking inputs remain."""
    issue = {"id": "iss-test-gh-inbound", "status": "awaiting_input"}
    pending = [{"id": "inp-gh1", "question": "Q1?", "status": "pending"}]
    app, issues_repo, inputs_repo, executor = _make_app(issue=issue, pending_inputs=pending)

    payload = {
        "action": "created",
        "comment": {"body": "/answer 1: yes from github", "user": {"login": "octocat"}},
        "issue": {"number": 42},
        "repository": {"full_name": "owner/repo"},
    }
    body = json.dumps(payload).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/v1/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "issue_comment",
                "Content-Type": "application/json",
            },
        )

    assert r.status_code == 200, r.text
    body_json = r.json()
    assert body_json.get("applied") == 1
    issues_repo.find_by_repo_and_github_number.assert_awaited_once_with(repo="owner/repo", github_number=42)
    inputs_repo.answer.assert_awaited_once_with("inp-gh1", answer="yes from github", answered_by="github:octocat")
    executor.resume_for_issue.assert_awaited_once_with("iss-test-gh-inbound")


@pytest.mark.asyncio
async def test_issue_comment_without_directive_is_ignored():
    """A comment that doesn't include /answer is ignored (no error, no change)."""
    issue = {"id": "iss-test-gh-noop", "status": "open"}
    app, _, inputs_repo, executor = _make_app(issue=issue, pending_inputs=[])

    payload = {
        "action": "created",
        "comment": {"body": "thanks for the heads up"},
        "issue": {"number": 43},
        "repository": {"full_name": "owner/repo"},
    }
    body = json.dumps(payload).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/v1/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "issue_comment",
                "Content-Type": "application/json",
            },
        )
    assert r.status_code == 200
    assert r.json().get("applied", 0) == 0
    inputs_repo.answer.assert_not_awaited()
    executor.resume_for_issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_issue_comment_for_unknown_repo_is_ignored():
    """If the (repo, github_number) isn't tracked in embry0, the webhook is a no-op."""
    app, issues_repo, inputs_repo, executor = _make_app(issue=None, pending_inputs=[])

    payload = {
        "action": "created",
        "comment": {"body": "/answer 1: yes"},
        "issue": {"number": 1},
        "repository": {"full_name": "totally/unknown"},
    }
    body = json.dumps(payload).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/v1/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "issue_comment",
                "Content-Type": "application/json",
            },
        )
    assert r.status_code == 200
    assert r.json().get("applied", 0) == 0
    issues_repo.find_by_repo_and_github_number.assert_awaited_once_with(repo="totally/unknown", github_number=1)
    inputs_repo.answer.assert_not_awaited()
    executor.resume_for_issue.assert_not_awaited()
