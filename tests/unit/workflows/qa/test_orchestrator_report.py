"""Tests for qa_report_node — aggregate check + sticky comment writer."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from athanor.workflows.qa.orchestrator_report import qa_report_node


def _state(**qa_overrides):
    qa = {
        "outcome": {
            "overall_status": "passed",
            "apps_to_qa": ["hub"],
            "failure_summary": None,
            "validation_errors": [],
            "validation_warnings": [],
        },
        "per_app_results": [
            {
                "app_name": "hub",
                "status": "passed",
                "duration_ms": 1000,
                "cache_hits": {"prebaked_image": False, "shared_volume": False, "turbo_remote": False},
                "trace_url": None,
                "failure_summary": None,
                "raw_result": {},
            }
        ],
        "apps_to_qa": ["hub"],
        "head_sha": "abc123",
        "validation_warnings": [],
    }
    qa.update(qa_overrides)
    return {
        "job_id": "run-1",
        "repo": "org/repo",
        "issue_number": 42,
        "attempt_number": 1,
        "qa": qa,
    }


@pytest.mark.asyncio
async def test_writes_check_and_comment_on_happy_path(monkeypatch):
    written_check = AsyncMock(return_value={"id": 1})
    written_comment = AsyncMock(return_value=99)
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.write_aggregate_check",
        written_check,
    )
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )

    config = {"configurable": {"github_token": "tok"}}
    out = await qa_report_node(_state(), config)
    assert out == {}

    written_check.assert_awaited_once()
    written_comment.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_writes_when_no_github_token(monkeypatch):
    written_check = AsyncMock()
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.write_aggregate_check",
        written_check,
    )

    config = {"configurable": {}}  # no github_token
    await qa_report_node(_state(), config)
    written_check.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_check_when_no_head_sha(monkeypatch):
    written_check = AsyncMock()
    written_comment = AsyncMock(return_value=99)
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.write_aggregate_check",
        written_check,
    )
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )

    config = {"configurable": {"github_token": "tok"}}
    out = await qa_report_node(_state(head_sha=None), config)
    assert out == {}

    written_check.assert_not_awaited()
    written_comment.assert_awaited_once()  # comment still written


@pytest.mark.asyncio
async def test_skips_comment_when_no_issue_number(monkeypatch):
    written_check = AsyncMock(return_value={"id": 1})
    written_comment = AsyncMock()
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.write_aggregate_check",
        written_check,
    )
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )

    state = _state()
    state["issue_number"] = None
    config = {"configurable": {"github_token": "tok"}}
    await qa_report_node(state, config)

    written_check.assert_awaited_once()
    written_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_failure_does_not_block_comment(monkeypatch):
    """One side failing logs a warning but the other still runs."""
    written_check = AsyncMock(side_effect=RuntimeError("github 503"))
    written_comment = AsyncMock(return_value=99)
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.write_aggregate_check",
        written_check,
    )
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )

    config = {"configurable": {"github_token": "tok"}}
    await qa_report_node(_state(), config)

    written_check.assert_awaited_once()
    written_comment.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_run_passes_failure_summary_through(monkeypatch):
    written_check = AsyncMock(return_value={"id": 1})
    written_comment = AsyncMock(return_value=99)
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.write_aggregate_check",
        written_check,
    )
    monkeypatch.setattr(
        "athanor.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )

    config = {"configurable": {"github_token": "tok"}}
    state = _state(outcome={
        "overall_status": "infra_error",
        "apps_to_qa": [],
        "failure_summary": "qa.yaml not loaded",
        "validation_errors": [],
        "validation_warnings": [],
    })
    await qa_report_node(state, config)

    check_kwargs = written_check.await_args.kwargs
    assert check_kwargs["overall_status"] == "infra_error"
    assert check_kwargs["failure_summary"] == "qa.yaml not loaded"
