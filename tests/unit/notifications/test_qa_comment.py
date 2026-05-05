"""Tests for the sticky athanor/qa PR comment renderer."""

from __future__ import annotations

from athanor.notifications.qa_comment import COMMENT_MARKER, render_qa_comment
from athanor.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)


def _r(app: str, status: SubTaskStatus, **kw) -> SubTaskResult:
    return SubTaskResult(
        app_name=app,
        status=status,
        duration_ms=kw.get("duration_ms", 1000),
        cache_hits=CacheHits(),
        trace_url=kw.get("trace_url"),
        failure_summary=kw.get("failure_summary"),
    )


def test_comment_includes_marker_for_sticky_lookup():
    out = render_qa_comment(
        run_number=1,
        overall_status="passed",
        apps_to_qa=["hub"],
        results=[_r("hub", SubTaskStatus.PASSED)],
    )
    assert COMMENT_MARKER in out


def test_comment_passed_run_collapses_passing_apps():
    out = render_qa_comment(
        run_number=42,
        overall_status="passed",
        apps_to_qa=["hub", "companion"],
        results=[
            _r("hub", SubTaskStatus.PASSED),
            _r("companion", SubTaskStatus.PASSED),
        ],
    )
    assert "Status: Passed" in out
    assert "run #42" in out
    assert "<details>" in out  # collapsed section


def test_comment_failed_run_expands_failing_apps():
    out = render_qa_comment(
        run_number=43,
        overall_status="failed",
        apps_to_qa=["hub", "companion"],
        results=[
            _r("hub", SubTaskStatus.PASSED),
            _r("companion", SubTaskStatus.QA_FAILURE,
               failure_summary="customer list did not render"),
        ],
    )
    assert "Status: Failed" in out
    assert "companion" in out
    assert "customer list did not render" in out


def test_comment_skipped_run():
    out = render_qa_comment(
        run_number=44,
        overall_status="passed",
        apps_to_qa=[],
        results=[],
    )
    assert "QA skipped" in out
    assert "no affected apps" in out


def test_comment_includes_trace_links_when_available():
    out = render_qa_comment(
        run_number=45,
        overall_status="failed",
        apps_to_qa=["hub"],
        results=[
            _r("hub", SubTaskStatus.QA_FAILURE,
               trace_url="https://athanor.example/traces/run-45/hub.zip",
               failure_summary="loads page did not render"),
        ],
    )
    assert "https://athanor.example/traces/run-45/hub.zip" in out


def test_comment_summary_table_orders_apps_alphabetically():
    out = render_qa_comment(
        run_number=46,
        overall_status="passed",
        apps_to_qa=["companion", "hub", "lane"],
        results=[
            _r("companion", SubTaskStatus.PASSED),
            _r("hub", SubTaskStatus.PASSED),
            _r("lane", SubTaskStatus.PASSED),
        ],
    )
    hub_idx = out.find("apps/hub")
    lane_idx = out.find("apps/lane")
    companion_idx = out.find("apps/companion")
    assert hub_idx < lane_idx < companion_idx


def test_comment_infra_error_distinguishable():
    out = render_qa_comment(
        run_number=47,
        overall_status="infra_error",
        apps_to_qa=["hub"],
        results=[],
        failure_summary="qa.yaml not found",
    )
    assert "infra error" in out.lower()
    assert "qa.yaml not found" in out


def test_human_duration_clamps_negative():
    from athanor.notifications.qa_comment import _human_duration
    assert _human_duration(-100) == "0.0s"
    assert _human_duration(0) == "0.0s"
    assert _human_duration(500) == "0.5s"
    assert _human_duration(60_000) == "1m  0s"
    assert _human_duration(125_000) == "2m  5s"


def test_comment_workspace_warnings_section_when_present():
    out = render_qa_comment(
        run_number=48,
        overall_status="passed",
        apps_to_qa=["hub"],
        results=[_r("hub", SubTaskStatus.PASSED)],
        validation_warnings=[
            "warning: app 'lane' present in workspace but missing from qa.yaml apps:",
        ],
    )
    assert "workspace warnings" in out
    assert "lane" in out


import httpx
import pytest
from unittest.mock import MagicMock

from athanor.notifications.qa_comment import upsert_sticky_comment


def _stub_resp(status_code: int, payload=None, text: str = ""):
    class _R:
        def __init__(self):
            self.status_code = status_code
            self._payload = payload if payload is not None else []
            self.text = text

        def json(self):
            return self._payload

        def raise_for_status(self):
            if not (200 <= self.status_code < 300):
                raise httpx.HTTPStatusError(
                    "fail",
                    request=MagicMock(),
                    response=MagicMock(status_code=self.status_code),
                )

    return _R()


@pytest.mark.asyncio
async def test_upsert_creates_when_no_existing_comment_with_marker(monkeypatch):
    posted: dict = {}

    class _StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, headers=None):
            # No existing comments
            return _stub_resp(200, [{"id": 1, "body": "some other comment"}])

        async def patch(self, url, json=None, headers=None):
            raise AssertionError("should not patch when no existing comment")

        async def post(self, url, json=None, headers=None):
            posted["url"] = url
            posted["body"] = json
            return _stub_resp(201, {"id": 99})

    monkeypatch.setattr("athanor.notifications.qa_comment.httpx.AsyncClient", lambda **kw: _StubClient())

    new_id = await upsert_sticky_comment(
        github_token="tok",
        repo="org/repo",
        issue_number=42,
        body="rendered content",
    )
    assert new_id == 99
    assert "issues/42/comments" in posted["url"]


@pytest.mark.asyncio
async def test_upsert_updates_when_existing_comment_has_marker(monkeypatch):
    patched: dict = {}

    class _StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, headers=None):
            return _stub_resp(200, [
                {"id": 1, "body": "some other comment"},
                {"id": 7, "body": f"{COMMENT_MARKER}\nold content"},
            ])

        async def post(self, url, json=None, headers=None):
            raise AssertionError("should not post when existing comment found")

        async def patch(self, url, json=None, headers=None):
            patched["url"] = url
            patched["body"] = json
            return _stub_resp(200, {"id": 7})

    monkeypatch.setattr("athanor.notifications.qa_comment.httpx.AsyncClient", lambda **kw: _StubClient())

    new_id = await upsert_sticky_comment(
        github_token="tok",
        repo="org/repo",
        issue_number=42,
        body="updated content",
    )
    assert new_id == 7
    assert "issues/comments/7" in patched["url"]
    assert patched["body"]["body"] == "updated content"


@pytest.mark.asyncio
async def test_upsert_handles_multiple_marker_matches_by_picking_first(monkeypatch):
    patched: dict = {}

    class _StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, headers=None):
            return _stub_resp(200, [
                {"id": 5, "body": f"{COMMENT_MARKER}\nfirst"},
                {"id": 6, "body": f"{COMMENT_MARKER}\nsecond"},
            ])

        async def patch(self, url, json=None, headers=None):
            patched["url"] = url
            return _stub_resp(200, {"id": 5})

        async def post(self, url, json=None, headers=None):
            raise AssertionError("should not post")

    monkeypatch.setattr("athanor.notifications.qa_comment.httpx.AsyncClient", lambda **kw: _StubClient())

    new_id = await upsert_sticky_comment(
        github_token="tok",
        repo="x/y",
        issue_number=1,
        body="...",
    )
    assert new_id == 5
    assert "comments/5" in patched["url"]
