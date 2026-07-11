"""Tests for embry0/qa Check Run writer."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from embry0.notifications.qa_check import (
    CHECK_NAME,
    build_check_payload,
    write_aggregate_check,
)
from embry0.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)


def _result(name: str, status: SubTaskStatus, **kw) -> SubTaskResult:
    return SubTaskResult(
        app_name=name,
        status=status,
        duration_ms=kw.get("duration_ms", 100),
        cache_hits=CacheHits(),
        trace_url=kw.get("trace_url"),
        failure_summary=kw.get("failure_summary"),
    )


# ----- build_check_payload -----


def test_pending_state_for_in_progress():
    p = build_check_payload(
        overall_status="pending",
        apps_to_qa=["hub", "companion"],
        results=[_result("hub", SubTaskStatus.PASSED)],
        in_progress=True,
    )
    assert p.conclusion is None
    assert p.status == "in_progress"
    assert "Running QA on 2 apps — 1 completed" in p.title


def test_success_state_when_all_passed():
    p = build_check_payload(
        overall_status="passed",
        apps_to_qa=["hub", "companion"],
        results=[
            _result("hub", SubTaskStatus.PASSED),
            _result("companion", SubTaskStatus.PASSED),
        ],
        in_progress=False,
    )
    assert p.conclusion == "success"
    assert "QA passed" in p.title
    assert "2 apps validated" in p.title


def test_failure_state_lists_failing_apps():
    p = build_check_payload(
        overall_status="failed",
        apps_to_qa=["hub", "companion", "lane"],
        results=[
            _result("hub", SubTaskStatus.PASSED),
            _result("companion", SubTaskStatus.QA_FAILURE),
            _result("lane", SubTaskStatus.BOOT_FAILURE),
        ],
        in_progress=False,
    )
    assert p.conclusion == "failure"
    assert "companion" in p.title
    assert "lane" in p.title


def test_skipped_state_when_no_apps_affected():
    p = build_check_payload(
        overall_status="passed",
        apps_to_qa=[],
        results=[],
        in_progress=False,
    )
    assert p.conclusion == "neutral"
    assert "skipped" in p.title.lower()


def test_infra_error_state_distinct_title():
    p = build_check_payload(
        overall_status="infra_error",
        apps_to_qa=["hub"],
        results=[_result("hub", SubTaskStatus.INFRA_FAILURE)],
        in_progress=False,
        failure_summary="provider crashed",
    )
    assert p.conclusion == "failure"
    assert "infra error" in p.title.lower()


# ----- write_aggregate_check -----


@pytest.mark.asyncio
async def test_write_creates_when_no_existing_check(monkeypatch):
    """First call POSTs because no check with this name exists for the sha."""
    posted: dict[str, Any] = {}

    class _StubResp:
        def __init__(self, status_code: int, payload: dict | None = None):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = ""

        def json(self) -> dict[str, Any]:
            return self._payload

        def raise_for_status(self) -> None:
            if not (200 <= self.status_code < 300):
                raise httpx.HTTPStatusError(
                    "fail", request=MagicMock(), response=MagicMock(status_code=self.status_code)
                )

    class _StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, headers=None):
            return _StubResp(200, {"check_runs": []})

        async def post(self, url, json=None, headers=None):
            posted["url"] = url
            posted["body"] = json
            return _StubResp(201, {"id": 4242})

        async def patch(self, url, json=None, headers=None):
            raise AssertionError("should not patch when no existing check")

    monkeypatch.setattr("embry0.notifications.qa_check.httpx.AsyncClient", lambda **kw: _StubClient())

    result = await write_aggregate_check(
        github_token="tok",
        repo="org/repo",
        head_sha="abc123",
        overall_status="passed",
        apps_to_qa=["hub"],
        results=[_result("hub", SubTaskStatus.PASSED)],
        in_progress=False,
    )
    assert result["id"] == 4242
    assert posted["body"]["name"] == CHECK_NAME
    assert posted["body"]["head_sha"] == "abc123"
    assert posted["body"]["conclusion"] == "success"


@pytest.mark.asyncio
async def test_write_patches_when_existing_check(monkeypatch):
    """Second call PATCHes because lookup returned a matching check."""
    patched: dict[str, Any] = {}

    class _StubResp:
        def __init__(self, status_code: int, payload: dict | None = None):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = ""

        def json(self):
            return self._payload

        def raise_for_status(self):
            if not (200 <= self.status_code < 300):
                raise httpx.HTTPStatusError(
                    "fail", request=MagicMock(), response=MagicMock(status_code=self.status_code)
                )

    class _StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, headers=None):
            return _StubResp(200, {"check_runs": [{"id": 99}]})

        async def post(self, url, json=None, headers=None):
            raise AssertionError("should not post when existing check found")

        async def patch(self, url, json=None, headers=None):
            patched["url"] = url
            patched["body"] = json
            return _StubResp(200, {"id": 99})

    monkeypatch.setattr("embry0.notifications.qa_check.httpx.AsyncClient", lambda **kw: _StubClient())

    result = await write_aggregate_check(
        github_token="tok",
        repo="org/repo",
        head_sha="abc123",
        overall_status="failed",
        apps_to_qa=["hub"],
        results=[_result("hub", SubTaskStatus.QA_FAILURE, failure_summary="bad")],
        in_progress=False,
    )
    assert result["id"] == 99
    assert "head_sha" not in patched["body"]
    assert patched["body"]["conclusion"] == "failure"


@pytest.mark.asyncio
async def test_write_raises_on_github_error(monkeypatch):
    class _StubResp:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = "rate limited"

        def json(self):
            return self._payload

        def raise_for_status(self):
            raise httpx.HTTPStatusError("fail", request=MagicMock(), response=MagicMock(status_code=self.status_code))

    class _StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, headers=None):
            return _StubResp(200, {"check_runs": []})

        async def post(self, url, json=None, headers=None):
            return _StubResp(403)

    monkeypatch.setattr("embry0.notifications.qa_check.httpx.AsyncClient", lambda **kw: _StubClient())

    with pytest.raises(httpx.HTTPStatusError):
        await write_aggregate_check(
            github_token="tok",
            repo="org/repo",
            head_sha="abc",
            overall_status="passed",
            apps_to_qa=["hub"],
            results=[_result("hub", SubTaskStatus.PASSED)],
            in_progress=False,
        )
