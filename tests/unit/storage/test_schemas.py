from legion.storage.schemas import JobStatus, ValidationResult, FailedTestCase, TraceRecord


def test_job_status_values():
    assert JobStatus.PENDING == "pending"
    assert JobStatus.RUNNING == "running"
    assert JobStatus.COMPLETED == "completed"
    assert JobStatus.FAILED == "failed"
    assert JobStatus.CANCELLED == "cancelled"
    assert JobStatus.AWAITING_INPUT == "awaiting_input"


def test_validation_result_to_retry_prompt():
    result = ValidationResult(
        passed=False,
        category="full_fail",
        tests_passed=False,
        summary="2 tests failed",
        tests_failures=[
            FailedTestCase(
                test_name="test_login",
                file_path="tests/test_auth.py",
                error_message="AssertionError: expected 200 got 401",
                line_number=42,
            ),
        ],
    )
    prompt = result.to_retry_prompt()
    assert "test_login" in prompt
    assert "AssertionError" in prompt
    assert "full_fail" in prompt


def test_trace_record_now_iso():
    ts = TraceRecord.now_iso()
    assert "T" in ts  # ISO format
    assert "+" in ts or "Z" in ts or ts.endswith("+00:00")  # Timezone-aware
