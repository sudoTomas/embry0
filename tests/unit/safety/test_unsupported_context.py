from athanor.orchestration.state import UnsupportedContextError
from athanor.safety.error_codes import ErrorCode


def test_unsupported_context_error_code_value():
    assert ErrorCode.UNSUPPORTED_CONTEXT.value == "ERR_UNSUPPORTED_CONTEXT"


def test_unsupported_context_error_carries_type():
    err = UnsupportedContextError("http")
    assert err.context_type == "http"
    assert "http" in str(err)
