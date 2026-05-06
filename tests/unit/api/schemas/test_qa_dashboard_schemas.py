"""Schema contract tests for the /api/v1/qa/... dashboard surface."""

from __future__ import annotations

from datetime import UTC, datetime

from athanor.api.schemas.qa_dashboard import (
    AppHistoryItem,
    AppResult,
    BootPhaseDetail,
    CacheHitsModel,
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


def test_boot_phase_detail_minimal_construction():
    bp = BootPhaseDetail(
        outcome="timeout",
        attempts=12,
        duration_ms=600_000,
        failed_checks=["http://localhost:3000/health: got 503 expected 200"],
        boot_stdout_tail="ready - started server on http://localhost:3000",
    )
    parsed = BootPhaseDetail.model_validate_json(bp.model_dump_json())
    assert parsed.outcome == "timeout"
    assert parsed.attempts == 12
    assert parsed.duration_ms == 600_000
    assert parsed.failed_checks == [
        "http://localhost:3000/health: got 503 expected 200",
    ]
    assert parsed.boot_stdout_tail.startswith("ready - started")


def test_app_result_accepts_boot_phase_field_optional():
    # Legacy AppResult dict (no boot_phase key) must still validate.
    legacy_dict = {
        "app_name": "hub",
        "status": "passed",
        "duration_ms": 1500,
        "cache_hits": {
            "prebaked_image": False,
            "shared_volume": False,
            "turbo_remote_hits": [],
            "turbo_remote_misses": [],
        },
        "trace_url": None,
        "failure_summary": None,
    }
    model = AppResult.model_validate(legacy_dict)
    assert model.boot_phase is None


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
