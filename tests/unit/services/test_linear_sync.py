"""LinearSyncService — trigger decision, dedupe, model labels, write-back (EMB-47)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from embry0.services.linear_sync import (
    LinearSyncService,
    LinearTriggerIssue,
    parse_repo_map,
)

_ISSUE = LinearTriggerIssue(
    linear_id="uuid-1",
    identifier="EMB-48",
    title="Fix the thing",
    description="It is broken.",
    url="https://linear.app/x/issue/EMB-48",
    labels=["embry0", "grok"],
    project="Raven AI Quoting Platform",
    team_key="EMB",
)


def _service(**kw: Any) -> LinearSyncService:
    return LinearSyncService(
        "lin_api_key",
        {"Raven AI Quoting Platform": "raven-cargo/ai-quoting"},
        **kw,
    )


class _FakeIssues:
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self._existing = existing
        self.created: dict[str, Any] | None = None
        self.updates: list[dict[str, Any]] = []

    async def get_by_linear(self, identifier: str) -> dict[str, Any] | None:
        return self._existing

    async def create(self, **kw: Any) -> str:
        self.created = kw
        return "iss-abc"

    async def update(self, issue_id: str, **kw: Any) -> None:
        self.updates.append({"issue_id": issue_id, **kw})


def _payload(action: str = "create") -> dict[str, Any]:
    return {"type": "Issue", "action": action, "data": {"id": "uuid-1", "identifier": "EMB-48"}}


async def test_dispatches_labeled_issue(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(svc, "post_comment", AsyncMock(side_effect=lambda i, b: posted.append((i, b)) or True))
    issues = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-1")

    result = await svc.handle_webhook_event(
        _payload(), issues_repo=issues, issue_executor=executor, dashboard_base_url="http://e0:8200"
    )

    assert result["status"] == "accepted" and result["job_id"] == "job-1"
    assert issues.created is not None
    assert issues.created["repo"] == "raven-cargo/ai-quoting"
    assert issues.created["title"] == "[EMB-48] Fix the thing"
    assert issues.updates[0]["linear_identifier"] == "EMB-48"
    assert issues.updates[0]["linear_issue_id"] == "uuid-1"
    # grok label → per-agent override threaded into execute()
    assert executor.execute.call_args.kwargs["agent_models"]["qa"] == "grok-4.5"
    # dispatch comment posted to the Linear issue
    assert posted and posted[0][0] == "uuid-1" and "job-1" in posted[0][1]


async def test_dedupes_already_dispatched(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    issues = _FakeIssues(existing={"id": "iss-old"})
    executor = AsyncMock()

    result = await svc.handle_webhook_event(_payload("update"), issues_repo=issues, issue_executor=executor)

    assert result == {"status": "ignored", "reason": "already dispatched", "issue_id": "iss-old"}
    executor.execute.assert_not_called()


async def test_ignores_without_trigger_label(monkeypatch) -> None:
    svc = _service()
    unlabeled = LinearTriggerIssue(**{**_ISSUE.__dict__, "labels": ["bug"]})
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=unlabeled))
    issues = _FakeIssues()
    executor = AsyncMock()

    result = await svc.handle_webhook_event(_payload(), issues_repo=issues, issue_executor=executor)

    assert result["status"] == "ignored" and "label" in result["reason"]
    assert issues.created is None


async def test_ignores_unmapped_project(monkeypatch) -> None:
    svc = LinearSyncService("k", {"Other Project": "o/r"})
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    issues = _FakeIssues()

    result = await svc.handle_webhook_event(_payload(), issues_repo=issues, issue_executor=AsyncMock())

    assert result["status"] == "ignored" and "mapping" in result["reason"]


async def test_ignores_non_issue_events() -> None:
    svc = _service()
    result = await svc.handle_webhook_event(
        {"type": "Comment", "action": "create"}, issues_repo=_FakeIssues(), issue_executor=AsyncMock()
    )
    assert result["status"] == "ignored"


async def test_post_job_outcome_formats_terminal_comment(monkeypatch) -> None:
    svc = _service()
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(svc, "post_comment", AsyncMock(side_effect=lambda i, b: posted.append((i, b)) or True))

    await svc.post_job_outcome(
        {"linear_issue_id": "uuid-1"},
        {"job_id": "job-9", "status": "completed", "pr_url": "https://github.com/o/r/pull/5", "total_cost_usd": 2.5},
    )
    assert posted[0][0] == "uuid-1"
    body = posted[0][1]
    assert "completed" in body and "pull/5" in body and "$2.50" in body

    # Issues without a Linear origin are a silent no-op.
    posted.clear()
    await svc.post_job_outcome({"id": "iss-x"}, {"job_id": "j", "status": "failed"})
    assert posted == []


def test_model_override_for_labels() -> None:
    svc = _service()
    assert svc.model_override_for(["bug"]) is None
    override = svc.model_override_for(["Grok", "embry0"])
    assert override is not None and override["developer"] == "grok-4.5"


def test_parse_repo_map() -> None:
    assert parse_repo_map("") == {}
    assert parse_repo_map('{"P": "o/r"}') == {"P": "o/r"}
    with pytest.raises(ValueError):
        parse_repo_map("not-json")
    with pytest.raises(ValueError):
        parse_repo_map('{"P": 1}')
