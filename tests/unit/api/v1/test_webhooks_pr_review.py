"""Webhook routes hand review triggers to the dispatcher (#30)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services.merge_sync.recorder import RecordResult
from embry0.services.pr_review.dispatcher import DispatchResult
from tests.fixtures.webhooks import (
    github_issue_comment,
    github_review_pull_request,
    gitlab_issue,
    gitlab_merge_request,
    gitlab_mr_note,
)


class _Dispatcher:
    def __init__(
        self, result: DispatchResult | None = DispatchResult(True, "ok", "job-1"), *, boom: bool = False
    ) -> None:
        self.result = result
        self.boom = boom
        self.calls: list[str] = []

    async def _answer(self, name: str) -> DispatchResult | None:
        self.calls.append(name)
        if self.boom:
            raise RuntimeError("forge down")
        return self.result

    async def handle_github_pull_request(self, payload: Any, action: str) -> DispatchResult | None:
        return await self._answer(f"pull_request:{action}")

    async def handle_github_comment(self, payload: Any) -> DispatchResult | None:
        return await self._answer("comment")

    async def handle_gitlab_merge_request(self, payload: Any) -> DispatchResult | None:
        return await self._answer("merge_request")

    async def handle_gitlab_note(self, payload: Any) -> DispatchResult | None:
        return await self._answer("note")


class _Recorder:
    def __init__(self) -> None:
        self.calls = 0

    async def record(self, fact: Any, **_kwargs: Any) -> RecordResult:
        self.calls += 1
        return RecordResult(pr_url=fact.url, state=fact.state.value, matched_jobs=["job-0"])


class _CommentDispatch:
    def __init__(self) -> None:
        self.calls = 0

    async def handle_pr_comment(self, payload: Any) -> dict[str, Any]:
        self.calls += 1
        return {"dispatched": True, "job_id": "job-followup"}


class _GitLabSync:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    @property
    def name(self) -> str:
        return "gitlab"

    async def handle_webhook_event(self, payload: Any, ctx: Any) -> dict[str, Any]:
        self.calls.append(payload)
        return {"status": "accepted", "dispatched": True}


class _IntegrationRepo:
    async def get_trigger_labels(self) -> list[str]:
        return ["embry0"]


def _app(dispatcher: _Dispatcher | None) -> FastAPI:
    @asynccontextmanager
    async def _noop(_app: FastAPI) -> AsyncIterator[None]:
        yield

    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_webhook_secret="")  # type: ignore[call-arg]
    app = create_app(config, lifespan_override=_noop)
    app.state.config = config
    app.state.delivery_recorder = _Recorder()
    app.state.comment_dispatch = _CommentDispatch()
    app.state.gitlab_sync = _GitLabSync()
    app.state.issues_repo = object()
    app.state.issue_executor = object()
    app.state.integration_repo = _IntegrationRepo()
    if dispatcher is not None:
        app.state.pr_review_dispatcher = dispatcher
    return app


async def _post(app: FastAPI, path: str, payload: dict[str, Any], event: str | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "X-GitHub-Delivery": "d-1"}
    if event:
        headers["X-GitHub-Event"] = event
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(path, content=json.dumps(payload).encode(), headers=headers)
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


async def test_ready_for_review_reaches_the_dispatcher_and_skips_merge_sync() -> None:
    dispatcher = _Dispatcher()
    app = _app(dispatcher)
    body = await _post(app, "/api/v1/webhook", github_review_pull_request(), "pull_request")
    assert dispatcher.calls == ["pull_request:ready_for_review"]
    assert body["pr_review"] == {"dispatched": True, "reason": "ok", "job_id": "job-1"}
    assert app.state.delivery_recorder.calls == 0, "ready_for_review carries no delivery state to record"


async def test_opened_keeps_its_merge_sync_bookkeeping_beside_the_review() -> None:
    dispatcher = _Dispatcher()
    app = _app(dispatcher)
    body = await _post(app, "/api/v1/webhook", github_review_pull_request(action="opened"), "pull_request")
    assert app.state.delivery_recorder.calls == 1, "the review must not replace merge-sync bookkeeping"
    assert body["status"] == "accepted" and body["action"] == "pr_opened"
    assert body["pr_review"]["job_id"] == "job-1"


async def test_a_dispatcher_that_says_not_a_trigger_leaves_the_response_unchanged() -> None:
    dispatcher = _Dispatcher(result=None)
    app = _app(dispatcher)
    body = await _post(app, "/api/v1/webhook", github_review_pull_request(action="opened"), "pull_request")
    assert dispatcher.calls == ["pull_request:opened"], "every pull_request delivery must be offered to the reviewer"
    assert "pr_review" not in body


async def test_a_failing_dispatcher_never_fails_the_delivery() -> None:
    app = _app(_Dispatcher(boom=True))
    body = await _post(app, "/api/v1/webhook", github_review_pull_request(action="opened"), "pull_request")
    assert app.state.delivery_recorder.calls == 1
    assert body["pr_review"] == {"dispatched": False, "reason": "dispatch_error"}


async def test_a_review_command_is_never_a_follow_up_or_an_answer() -> None:
    dispatcher = _Dispatcher()
    app = _app(dispatcher)
    payload = github_issue_comment(body="@embry0 have a look\n/embry0 review", on_pull_request=True)
    body = await _post(app, "/api/v1/webhook", payload, "issue_comment")
    assert dispatcher.calls == ["comment"]
    assert app.state.comment_dispatch.calls == 0, "a /embry0 review comment was also dispatched as a follow-up"
    assert body == {"status": "accepted", "action": "pr_review", "dispatched": True, "reason": "ok", "job_id": "job-1"}

    # …and a comment with only the tag still reaches comment dispatch, never the reviewer.
    await _post(
        app, "/api/v1/webhook", github_issue_comment(body="@embry0 fix it", on_pull_request=True), "issue_comment"
    )
    assert dispatcher.calls == ["comment"], "a comment without the command reached the reviewer"
    assert app.state.comment_dispatch.calls == 1


async def test_a_review_command_without_a_dispatcher_is_still_not_a_follow_up() -> None:
    app = _app(None)
    payload = github_issue_comment(body="@embry0 /embry0 review\n/embry0 review", on_pull_request=True)
    body = await _post(app, "/api/v1/webhook", payload, "issue_comment")
    assert app.state.comment_dispatch.calls == 0
    assert body["status"] == "ignored" and body["reason"] == "pr_review_not_configured"


@pytest.mark.parametrize(
    ("payload", "call"),
    [(gitlab_merge_request(), "merge_request"), (gitlab_mr_note(), "note")],
    ids=["merge_request", "note"],
)
async def test_gitlab_merge_request_and_note_events_reach_the_dispatcher(payload: dict[str, Any], call: str) -> None:
    dispatcher = _Dispatcher()
    app = _app(dispatcher)
    body = await _post(app, "/api/v1/webhook/gitlab", payload)
    assert dispatcher.calls == [call]
    assert body == {"status": "accepted", "action": "pr_review", "dispatched": True, "reason": "ok", "job_id": "job-1"}
    assert app.state.gitlab_sync.calls == [], f"the {call} event still went to the GitLab issue sync"

    # An issue event still goes to the GitLab issue sync, never the reviewer.
    await _post(app, "/api/v1/webhook/gitlab", gitlab_issue(action="update", labels=["embry0"], previous_labels=[]))
    assert dispatcher.calls == [call]
    assert len(app.state.gitlab_sync.calls) == 1
