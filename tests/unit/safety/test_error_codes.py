"""ErrorCode enum shape + value-stability tests."""


def test_error_codes_use_err_prefix():
    from athanor.safety.error_codes import ErrorCode

    for code in ErrorCode:
        assert code.value.startswith("ERR_"), f"{code.name} value {code.value!r} missing ERR_ prefix"


def test_error_codes_are_str_enum():
    """ErrorCode values must compare equal to their string form — JSON-friendly."""
    from athanor.safety.error_codes import ErrorCode

    assert ErrorCode.AGENT_TIMEOUT == "ERR_AGENT_TIMEOUT"
    assert str(ErrorCode.BUDGET_OVERRUN) == "ERR_BUDGET_OVERRUN"


def test_error_code_catalogue_minimum():
    """The 10 v1 codes must all be present — catches accidental removal."""
    from athanor.safety.error_codes import ErrorCode

    required = {
        "AGENT_TIMEOUT",
        "NO_RESULT",
        "BUDGET_OVERRUN",
        "MAX_RETRIES",
        "TRIAGE_MALFORMED",
        "ORPHANED",
        "WORKFLOW_UNKNOWN",
        "SANDBOX_INIT",
        "DOCKER_TIMEOUT",
        "MAX_AGENT_QUESTIONS",
        "UNKNOWN",
    }
    present = {c.name for c in ErrorCode}
    missing = required - present
    assert not missing, f"missing required codes: {missing}"
