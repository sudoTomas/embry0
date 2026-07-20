"""Unit tests for retry_node — Section 6.2 escalation routing."""

import pytest
from langgraph.graph import END
from langgraph.types import Command

from embry0.workflows.qa.nodes import retry_node


def _mk_state(*, exit_reason: str, attempts: int, max_retries: int = 2):
    return {
        "qa": {
            "attempts": [{"attempt_n": i + 1, "exit_reason": exit_reason} for i in range(attempts)],
            "qa_yaml_parsed": {
                "startup": {"boot_timeout_seconds": 100},
            },
            "max_qa_retries": max_retries,
            "final_status": "pending",
        },
    }


@pytest.mark.asyncio
async def test_boot_timeout_within_retries_routes_to_init_qa():
    state = _mk_state(exit_reason="boot_timeout", attempts=1)
    result = await retry_node(state, {})
    assert isinstance(result, Command)
    assert result.goto == "init_qa"


@pytest.mark.asyncio
async def test_boot_timeout_exhausted_ends_with_error_code():
    state = _mk_state(exit_reason="boot_timeout", attempts=3, max_retries=2)
    result = await retry_node(state, {})
    assert isinstance(result, Command)
    assert result.goto == END
    assert result.update["qa"]["final_status"] == "exhausted"


@pytest.mark.asyncio
async def test_idle_timeout_hard_fails_immediately():
    state = _mk_state(exit_reason="idle_timeout", attempts=1)
    result = await retry_node(state, {})
    assert isinstance(result, Command)
    assert result.goto == END
    assert result.update["qa"]["final_status"] == "exhausted"


@pytest.mark.asyncio
async def test_validation_failed_routes_to_END_for_standalone_pipeline():
    """In Phase 2 (standalone), validation_failed terminates."""
    state = _mk_state(exit_reason="validation_failed", attempts=1)
    result = await retry_node(state, {})
    assert result.goto == END
    assert result.update["qa"]["final_status"] == "failed"


@pytest.mark.asyncio
async def test_infra_error_retries_with_backoff():
    state = _mk_state(exit_reason="infra_error", attempts=1)
    result = await retry_node(state, {})
    assert result.goto == "init_qa"


@pytest.mark.asyncio
async def test_passed_routes_to_END():
    """If the last attempt succeeded (no exit_reason), end cleanly."""
    state = {
        "qa": {
            "attempts": [{"attempt_n": 1, "exit_reason": None}],
            "qa_yaml_parsed": {"startup": {"boot_timeout_seconds": 60}},
            "max_qa_retries": 2,
            "final_status": "passed",
        },
    }
    result = await retry_node(state, {})
    assert result.goto == END


@pytest.mark.asyncio
async def test_agent_transient_gets_one_retry():
    """EMB-34: a classified-transient agent failure retries exactly once."""
    state = _mk_state(exit_reason="agent_transient: connection reset by peer", attempts=1)
    result = await retry_node(state, {})
    assert isinstance(result, Command)
    assert result.goto == "init_qa"


@pytest.mark.asyncio
async def test_agent_transient_second_occurrence_terminates():
    state = _mk_state(exit_reason="agent_transient: connection reset by peer", attempts=2)
    result = await retry_node(state, {})
    assert result.goto == END
    assert result.update["qa"]["final_status"] == "exhausted"
    assert result.update["qa"]["error_code"] == "ERR_QA_BUDGET_EXHAUSTED"


@pytest.mark.asyncio
async def test_agent_error_stays_terminal():
    """A non-transient agent failure is NOT retried."""
    state = _mk_state(exit_reason="agent_error: tool misuse", attempts=1)
    result = await retry_node(state, {})
    assert result.goto == END
    assert result.update["qa"]["final_status"] == "exhausted"
