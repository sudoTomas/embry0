"""Phase 1 new error codes exist and are stable strings."""

from embry0.safety.error_codes import ErrorCode


def test_phase1_error_codes_present() -> None:
    expected = {
        "INCOHERENT_CONFIG": "ERR_INCOHERENT_CONFIG",
        "MISSING_OAUTH_TOKEN": "ERR_MISSING_OAUTH_TOKEN",
        "MISSING_API_KEY": "ERR_MISSING_API_KEY",
        "INVALID_CONFIG": "ERR_INVALID_CONFIG",
        "AUTH_REJECTED": "ERR_AUTH_REJECTED",
        "AGENT_KILLED": "ERR_AGENT_KILLED",
        "AGENT_NO_RESULT": "ERR_AGENT_NO_RESULT",
        "SAFETY_HOOK_FAILED": "ERR_SAFETY_HOOK_FAILED",
        "TOOL_DENIED": "ERR_TOOL_DENIED",
    }
    for name, value in expected.items():
        assert getattr(ErrorCode, name).value == value, (
            f"ErrorCode.{name} must equal {value!r}; codes must be stable for DB / dashboards."
        )
