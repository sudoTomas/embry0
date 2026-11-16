"""Tests for the sticky embry0/qa PR comment renderer."""

from __future__ import annotations

import json

import httpx
import pytest

from embry0.execution.github_client import GitHubClient
from embry0.notifications.qa_comment import (
    COMMENT_MARKER,
    edit_sticky_comment,
    render_qa_comment,
    upsert_sticky_comment,
)
from embry0.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)


def _client(handler) -> GitHubClient:
    """Real GitHubClient over a MockTransport — exercises headers and paging."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubClient(default_token="tok", http_client=http)


def _r(app: str, status: SubTaskStatus, **kw) -> SubTaskResult:
    return SubTaskResult(
        app_name=app,
        status=status,
        duration_ms=kw.get("duration_ms", 1000),
        cache_hits=CacheHits(),
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
            _r("companion", SubTaskStatus.QA_FAILURE, failure_summary="customer list did not render"),
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


def test_comment_has_no_trace_column():
    """#644: no QA node ever set a trace URL, so the table's Trace column was always "—"."""
    out = render_qa_comment(
        run_number=45,
        overall_status="failed",
        apps_to_qa=["hub"],
        results=[_r("hub", SubTaskStatus.QA_FAILURE, failure_summary="loads page did not render")],
    )
    assert "| App | Result | Duration | Cache |\n" in out, f"the summary table header changed: {out}"
    assert "Trace" not in out, f"the comment still names a trace: {out}"
    row = next(line for line in out.splitlines() if line.startswith("| apps/hub"))
    assert row.count("|") == 5, f"a summary row must have four cells, like its header: {row}"


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
    assert companion_idx < hub_idx < lane_idx


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
    from embry0.notifications.qa_comment import _human_duration

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


def test_comment_shows_cache_hit_indicators_per_app():
    """A SubTaskResult with all 3 cache layers hit should show 🔥 glyphs."""
    result = SubTaskResult(
        app_name="hub",
        status=SubTaskStatus.PASSED,
        duration_ms=1500,
        cache_hits=CacheHits(
            prebaked_image=True,
            shared_volume=True,
            turbo_remote_hits=["apps/hub#build", "apps/hub#lint"],
            turbo_remote_misses=[],
        ),
        failure_summary=None,
    )
    out = render_qa_comment(
        run_number=50,
        overall_status="passed",
        apps_to_qa=["hub"],
        results=[result],
    )
    # All three layers hit → three fire glyphs
    assert "\U0001f525\U0001f525\U0001f525" in out


def test_comment_summary_table_includes_cache_column():
    """The summary table header must contain a Cache column."""
    out = render_qa_comment(
        run_number=51,
        overall_status="passed",
        apps_to_qa=["hub"],
        results=[_r("hub", SubTaskStatus.PASSED)],
    )
    assert "| Cache |" in out


from unittest.mock import MagicMock  # noqa: E402


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
async def test_upsert_creates_when_no_existing_comment_with_marker():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 1, "body": "some other comment"}])
        if request.method == "POST":
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": 99})
        raise AssertionError("should not patch when no existing comment")

    new_id = await upsert_sticky_comment(
        github_token="tok",
        repo="org/repo",
        issue_number=42,
        body="rendered content",
        client=_client(handler),
    )
    assert new_id == 99
    assert "issues/42/comments" in seen["url"]


@pytest.mark.asyncio
async def test_upsert_updates_when_existing_comment_has_marker():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 7, "body": f"before {COMMENT_MARKER} after"}])
        if request.method == "PATCH":
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"id": 7})
        raise AssertionError("should not post when a marker comment exists")

    got = await upsert_sticky_comment(
        github_token="tok",
        repo="org/repo",
        issue_number=42,
        body="updated content",
        client=_client(handler),
    )
    assert got == 7
    assert seen["url"].endswith("/repos/org/repo/issues/comments/7")
    assert seen["body"]["body"] == "updated content"


@pytest.mark.asyncio
async def test_upsert_handles_multiple_marker_matches_by_picking_first():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"id": 3, "body": f"first {COMMENT_MARKER}"},
                    {"id": 8, "body": f"second {COMMENT_MARKER}"},
                ],
            )
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"id": 3})

    got = await upsert_sticky_comment(
        github_token="tok",
        repo="org/repo",
        issue_number=42,
        body="x",
        client=_client(handler),
    )
    assert got == 3
    assert seen["url"].endswith("/issues/comments/3")


@pytest.mark.asyncio
async def test_upsert_follows_pagination_to_find_the_marker():
    """The marker may be on a later page; this module used to hand-roll that."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            return httpx.Response(200, json={"id": 55})
        if request.url.params.get("page") is None:
            return httpx.Response(
                200,
                json=[{"id": 1, "body": "chatter"}],
                headers={"Link": '<https://api.github.com/repos/org/repo/issues/42/comments?page=2>; rel="next"'},
            )
        return httpx.Response(200, json=[{"id": 55, "body": f"late {COMMENT_MARKER}"}])

    got = await upsert_sticky_comment(
        github_token="tok", repo="org/repo", issue_number=42, body="x", client=_client(handler)
    )
    assert got == 55


@pytest.mark.asyncio
async def test_upsert_uses_the_per_owner_token(monkeypatch):
    """The bug #158 fixes: this path ignored GITHUB_TOKEN__<OWNER> entirely."""
    monkeypatch.setenv("GITHUB_TOKEN__ORG", "tok-org")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": 1})

    await upsert_sticky_comment(
        github_token="tok-default",
        repo="org/repo",
        issue_number=42,
        body="x",
        client=_client(handler),
    )
    assert seen["auth"] == "Bearer tok-org"


# ---- a comments read GitHub refuses is not "no comment" (#536) ----


def _refusing(seen: dict):
    """A GitHub whose every read of the comments answers 502 (the PR already carries the marked comment)."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.setdefault("calls", []).append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(502, json={"message": "Bad Gateway"})
        return httpx.Response(201, json={"id": 99})

    return handler


def _no_sleep_client(handler) -> GitHubClient:
    from unittest.mock import AsyncMock

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubClient(default_token="tok", http_client=http, sleep=AsyncMock())


async def test_a_refused_read_posts_no_second_comment():
    seen: dict = {}
    with pytest.raises(httpx.HTTPStatusError):
        await upsert_sticky_comment(
            github_token="tok", repo="org/repo", issue_number=42, body="b", client=_no_sleep_client(_refusing(seen))
        )
    posted = [c for c in seen["calls"] if c[0] != "GET"]
    assert posted == [], f"a refused read was taken for no comment: {seen['calls']}"


async def test_a_refused_read_is_not_said_to_find_no_comment():
    """``edit_sticky_comment`` answers False only when there is no marked comment; a refused read raises."""
    seen: dict = {}
    with pytest.raises(httpx.HTTPStatusError):
        await edit_sticky_comment(
            _no_sleep_client(_refusing(seen)), repo="org/repo", issue_number=42, body="b", marker=COMMENT_MARKER
        )
    assert [c for c in seen["calls"] if c[0] != "GET"] == []


async def test_a_marker_before_a_refused_page_is_still_the_comment_edited():
    """The marker on page 1, page 2 refused: page 2 is never needed, so the comment is edited, not refused."""
    seen: dict = {"calls": []}
    marked = {"id": 7, "body": f"{COMMENT_MARKER}\nold"}

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        seen["calls"].append((request.method, page))
        if request.method == "GET" and page == 1:
            link = f'<https://api.github.com{request.url.path}?page=2>; rel="next"'
            return httpx.Response(200, json=[{"id": 1, "body": "other"}, marked], headers={"Link": link})
        if request.method == "GET":
            return httpx.Response(502, json={"message": "Bad Gateway"})
        if request.method == "PATCH":
            seen["patched"] = request.url.path
            return httpx.Response(200, json={"id": 7})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    comment_id = await upsert_sticky_comment(
        github_token="tok", repo="org/repo", issue_number=42, body="new", client=_no_sleep_client(handler)
    )
    assert comment_id == 7, "the marked comment on page 1 is not the one edited"
    assert seen.get("patched") == "/repos/org/repo/issues/comments/7", seen
    assert [c for c in seen["calls"] if c == ("GET", 2)] == [], "a page past the marker was read"
    assert [c for c in seen["calls"] if c[0] == "POST"] == [], "a second comment was posted"


async def test_a_refused_page_before_the_marker_raises():
    """The marker on page 2, page 2 refused: it cannot be known whether a comment exists, so nothing is posted."""
    seen: dict = {"calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        seen["calls"].append((request.method, page))
        if request.method == "GET" and page == 1:
            link = f'<https://api.github.com{request.url.path}?page=2>; rel="next"'
            return httpx.Response(200, json=[{"id": 1, "body": "other"}], headers={"Link": link})
        if request.method == "GET":
            return httpx.Response(502, json={"message": "Bad Gateway"})
        return httpx.Response(201, json={"id": 99})

    with pytest.raises(httpx.HTTPStatusError):
        await upsert_sticky_comment(
            github_token="tok", repo="org/repo", issue_number=42, body="new", client=_no_sleep_client(handler)
        )
    assert [c for c in seen["calls"] if c[0] != "GET"] == [], seen["calls"]
