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
