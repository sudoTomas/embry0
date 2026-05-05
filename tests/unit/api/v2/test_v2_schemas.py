"""Schema contract tests for the v2 dashboard surface."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from athanor.api.v2.schemas import (
    AppHistoryItem,
    AppResult,
    CacheHitsModel,
    LoginRequest,
    LoginResponse,
    RepoEntry,
    RunDetail,
    RunListItem,
    RunSummaryItem,
)


def test_repo_entry_round_trip():
    e = RepoEntry(
        repo="org/r1",
        latest_run_id="j-1",
        latest_status="passed",
        latest_started_at=datetime.now(UTC),
        latest_app_count=3,
    )
    dump = e.model_dump_json()
    parsed = RepoEntry.model_validate_json(dump)
    assert parsed.repo == "org/r1"
    assert parsed.latest_status == "passed"
    assert parsed.latest_app_count == 3


def test_cache_hits_serializes_lists():
    ch = CacheHitsModel(
        prebaked_image=True,
        shared_volume=False,
        turbo_remote_hits=["apps/hub#build"],
        turbo_remote_misses=["apps/companion#build"],
    )
    parsed = CacheHitsModel.model_validate_json(ch.model_dump_json())
    assert parsed.turbo_remote_hits == ["apps/hub#build"]
    assert parsed.turbo_remote_misses == ["apps/companion#build"]
    assert parsed.prebaked_image is True


def test_app_result_with_failure_summary():
    ar = AppResult(
        app_name="companion",
        status="qa_failure",
        duration_ms=1500,
        cache_hits=CacheHitsModel(),
        trace_url="https://x/trace.zip",
        failure_summary="loads page did not render",
    )
    assert ar.app_name == "companion"
    assert ar.failure_summary == "loads page did not render"


def test_run_detail_inlines_apps():
    rd = RunDetail(
        job_id="j-1",
        repo="org/r1",
        started_at=datetime.now(UTC),
        overall_status="failed",
        apps=[
            AppResult(
                app_name="hub",
                status="passed",
                duration_ms=1000,
                cache_hits=CacheHitsModel(),
                trace_url=None,
                failure_summary=None,
            ),
            AppResult(
                app_name="companion",
                status="qa_failure",
                duration_ms=2000,
                cache_hits=CacheHitsModel(),
                trace_url="https://x/trace.zip",
                failure_summary="loads page did not render",
            ),
        ],
    )
    assert len(rd.apps) == 2
    parsed = RunDetail.model_validate_json(rd.model_dump_json())
    assert parsed.apps[1].failure_summary == "loads page did not render"


def test_login_request_validates_api_key_min_length():
    LoginRequest(api_key="x" * 16)
    with pytest.raises(Exception):
        LoginRequest(api_key="")


def test_login_response_has_jwt_field():
    r = LoginResponse(token="some.jwt.value", expires_at=datetime.now(UTC))
    assert r.token == "some.jwt.value"


def test_run_list_item_round_trip():
    item = RunListItem(
        job_id="j-2",
        repo="org/r1",
        started_at=datetime.now(UTC),
        overall_status="passed",
        app_count=2,
    )
    parsed = RunListItem.model_validate_json(item.model_dump_json())
    assert parsed.job_id == "j-2"


def test_app_history_item_round_trip():
    h = AppHistoryItem(
        job_id="hist-0",
        app_name="hub",
        status="passed",
        duration_ms=1100,
        started_at=datetime.now(UTC),
        failure_summary=None,
    )
    parsed = AppHistoryItem.model_validate_json(h.model_dump_json())
    assert parsed.status == "passed"


def test_run_summary_item_used_in_listings():
    # RunSummaryItem is a thinner shape used by the run-list response.
    s = RunSummaryItem(
        job_id="j-1",
        repo="org/r1",
        started_at=datetime.now(UTC),
        overall_status="passed",
        app_count=1,
    )
    assert s.app_count == 1
