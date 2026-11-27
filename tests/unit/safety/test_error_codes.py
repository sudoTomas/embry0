"""ErrorCode enum shape + value-stability tests."""


def test_error_codes_use_err_prefix():
    from embry0.safety.error_codes import ErrorCode

    for code in ErrorCode:
        assert code.value.startswith("ERR_"), f"{code.name} value {code.value!r} missing ERR_ prefix"


def test_error_codes_are_str_enum():
    """ErrorCode values must compare equal to their string form — JSON-friendly."""
    from embry0.safety.error_codes import ErrorCode

    assert ErrorCode.AGENT_TIMEOUT == "ERR_AGENT_TIMEOUT"
    assert str(ErrorCode.BUDGET_OVERRUN) == "ERR_BUDGET_OVERRUN"


def test_error_code_catalogue_minimum():
    """The 10 v1 codes must all be present — catches accidental removal."""
    from embry0.safety.error_codes import ErrorCode

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


def test_a_proxy_s_budget_refusal_reads_as_one_whatever_its_figures_say():
    """Its dollar figures can contain "429"; the refusal is still a budget refusal, never a rate limit."""
    from embry0.safety.error_codes import ERROR_CATEGORIES, ErrorCode, FailureCategory, classify_agent_error

    code = getattr(ErrorCode, "BUDGET_CALL_REFUSED", None)
    assert code is not None, "no code says a call was refused for its run's budget"
    message = (
        "xai answered HTTP 402: embry0 budget_exceeded: this call could cost up to $1,429.00 at its ceiling (its "
        "output limit and its input), and the run has $3.10 left of the job's budget, so it was not sent."
    )
    assert classify_agent_error(message) is code, classify_agent_error(message)
    assert ERROR_CATEGORIES[code] is FailureCategory.BUDGET
