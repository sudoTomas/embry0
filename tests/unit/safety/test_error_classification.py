from embry0.orchestration.state import UnsupportedContextError
from embry0.safety.error_codes import ErrorCode, error_code_for_exception


def test_unsupported_context_maps_to_code():
    assert error_code_for_exception(UnsupportedContextError("http")) == ErrorCode.UNSUPPORTED_CONTEXT


def test_sandbox_init_runtimeerror_maps():
    assert error_code_for_exception(RuntimeError("Sandbox initialization failed: boom")) == ErrorCode.SANDBOX_INIT


def test_unknown_maps_to_unknown():
    assert error_code_for_exception(ValueError("nope")) == ErrorCode.UNKNOWN
