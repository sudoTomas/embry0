import pytest

from athanor.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
    is_terminal_status,
    overall_status,
)


def test_status_enum_values():
    assert SubTaskStatus.PASSED.value == "passed"
    assert SubTaskStatus.QA_FAILURE.value == "qa_failure"
    assert SubTaskStatus.E2E_FAILURE.value == "e2e_failure"
    assert SubTaskStatus.BOOT_FAILURE.value == "boot_failure"
    assert SubTaskStatus.READY_CHECK_FAILED.value == "ready_check_failed"
    assert SubTaskStatus.INFRA_FAILURE.value == "infra_failure"
    assert SubTaskStatus.INCONCLUSIVE.value == "inconclusive"
    assert SubTaskStatus.SKIPPED.value == "skipped"


def test_overall_status_failed_when_only_inconclusive():
    """INCONCLUSIVE is a 'did not pass' verdict, not an infra failure.
    overall_status should return 'failed', never 'infra_error'."""
    results = [_r("hub", SubTaskStatus.INCONCLUSIVE)]
    assert overall_status(results) == "failed"


def test_overall_status_inconclusive_does_not_shadow_infra_error():
    """INFRA_FAILURE still takes precedence over INCONCLUSIVE."""
    results = [
        _r("hub", SubTaskStatus.INCONCLUSIVE),
        _r("companion", SubTaskStatus.INFRA_FAILURE),
    ]
    assert overall_status(results) == "infra_error"


def test_is_terminal_status_true_for_known():
    for s in SubTaskStatus:
        assert is_terminal_status(s) is True


def test_overall_status_passed_when_all_passed():
    results = [
        _r("hub", SubTaskStatus.PASSED),
        _r("companion", SubTaskStatus.PASSED),
    ]
    assert overall_status(results) == "passed"


def test_overall_status_failed_on_any_qa_or_boot_failure():
    results = [
        _r("hub", SubTaskStatus.PASSED),
        _r("companion", SubTaskStatus.QA_FAILURE),
    ]
    assert overall_status(results) == "failed"


def test_overall_status_infra_error_takes_precedence_over_failed():
    results = [
        _r("hub", SubTaskStatus.QA_FAILURE),
        _r("companion", SubTaskStatus.INFRA_FAILURE),
    ]
    assert overall_status(results) == "infra_error"


def test_overall_status_passed_when_all_skipped_is_passed():
    """A run with zero affected apps (everything skipped) is a pass."""
    results = [_r("hub", SubTaskStatus.SKIPPED)]
    assert overall_status(results) == "passed"


def test_overall_status_empty_list_passed():
    """Phase 1 short-circuits to skipped when there are no apps to QA;
    overall_status of [] is 'passed' so the run gates green."""
    assert overall_status([]) == "passed"


def test_subtask_result_round_trips_to_dict():
    r = _r("hub", SubTaskStatus.PASSED, duration_ms=1234)
    d = r.to_dict()
    assert d["status"] == "passed"
    assert d["duration_ms"] == 1234
    assert d["app_name"] == "hub"


def test_cache_hits_default_all_false():
    h = CacheHits()
    assert h.prebaked_image is False
    assert h.shared_volume is False
    assert h.turbo_remote_hits == []
    assert h.turbo_remote_misses == []


def test_cache_hits_turbo_remote_lists():
    h = CacheHits(
        turbo_remote_hits=["apps/hub#build", "apps/companion#build"],
        turbo_remote_misses=["apps/sales#build"],
    )
    assert h.turbo_remote_hits == ["apps/hub#build", "apps/companion#build"]
    assert h.turbo_remote_misses == ["apps/sales#build"]


def _r(app_name: str, status: SubTaskStatus, duration_ms: int = 0) -> SubTaskResult:
    return SubTaskResult(
        app_name=app_name,
        status=status,
        duration_ms=duration_ms,
        cache_hits=CacheHits(),
        trace_url=None,
        failure_summary=None,
    )
