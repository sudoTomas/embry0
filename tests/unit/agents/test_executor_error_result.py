"""An is_error Claude result whose subtype is "success" is a failed run.

The Python SDK's closing exception names only the subtype when errors[] is
empty ("Claude Code returned an error result: success"). The failure text
lives in result / errors / api_error_status. Prefer that. A subtype of
success must not be treated as a successful run, and a closing exception
that already carries the errors text (a turn cap) keeps the SDK's wording.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from claude_agent_sdk import ResultMessage

from embry0.agents.executor import SdkAgentExecutor
from embry0.agents.invocation import AgentInvocation
from embry0.safety.policy import default_policy_for_agent

SUBTYPE_ONLY = "Claude Code returned an error result: success"
API_ERROR = "API Error: 529 overloaded — the request did not complete"
API_ERROR_NO_STATUS = "overloaded — the request did not complete"


def _inv() -> AgentInvocation:
    return AgentInvocation(
        agent_type="triage",
        prompt="triage this",
        system_prompt="",
        system_context="",
        model="claude-sonnet-4-6",
        tools=["Read"],
        skills=[],
        mcp_servers={},
        max_turns=5,
        timeout_seconds=60,
        execution_mode="sdk",
        auth_mode="oauth",
        safety_policy=default_policy_for_agent("triage"),
        channel_config=None,
    )


def _result(
    *,
    is_error: bool,
    subtype: str = "success",
    result: str = "",
    errors: list[str] | None = None,
    api_error_status: int | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id="s1",
        total_cost_usd=0.0,
        usage={},
        result=result,
        errors=errors,
        api_error_status=api_error_status,
    )


async def _stream(messages: list[Any], *, closing: Exception | None = None):
    for message in messages:
        yield message
    if closing is not None:
        raise closing


async def _run(messages: list[Any], tmp_path, *, closing: Exception | None = None, events: list[dict] | None = None):
    import os

    os.environ["EMBRY0_WORKSPACE_ROOT"] = str(tmp_path)

    def _writer(event: dict) -> None:
        if events is not None:
            events.append(event)

    with patch("claude_agent_sdk.query", return_value=_stream(messages, closing=closing)):
        return await SdkAgentExecutor().run(_inv(), {"configurable": {}, "_test_writer": _writer})


@pytest.mark.asyncio
async def test_subtype_success_with_result_text_is_a_failure(tmp_path) -> None:
    """The production shape: is_error, subtype success, text in result, then the SDK exception."""
    events: list[dict] = []
    out = await _run(
        [_result(is_error=True, result=API_ERROR, api_error_status=529)],
        tmp_path,
        closing=Exception(SUBTYPE_ONLY),
        events=events,
    )
    assert out.is_error is True, "subtype success on an is_error result must not be a successful run"
    assert out.error_message == API_ERROR, out.error_message
    assert "error result: success" not in out.error_message
    assert any(event.get("type") == "error" and API_ERROR in event.get("message", "") for event in events)


@pytest.mark.asyncio
async def test_subtype_success_without_a_closing_exception_keeps_the_result(tmp_path) -> None:
    out = await _run([_result(is_error=True, result=API_ERROR_NO_STATUS, api_error_status=529)], tmp_path)
    assert out.is_error is True
    assert out.error_message == f"{API_ERROR_NO_STATUS}; api_error_status=529", out.error_message
    assert "error result: success" not in out.error_message


@pytest.mark.asyncio
async def test_api_error_status_alone_is_not_reported_as_success(tmp_path) -> None:
    out = await _run(
        [_result(is_error=True, api_error_status=500)],
        tmp_path,
        closing=Exception(SUBTYPE_ONLY),
    )
    assert out.is_error is True
    assert out.error_message == "api_error_status=500", out.error_message
    assert "error result: success" not in out.error_message


@pytest.mark.asyncio
async def test_empty_is_error_result_is_still_a_failure(tmp_path) -> None:
    out = await _run([_result(is_error=True)], tmp_path, closing=Exception(SUBTYPE_ONLY))
    assert out.is_error is True
    assert "error result: success" not in out.error_message
    assert "subtype=success" in out.error_message
    assert "no result" in out.error_message


@pytest.mark.asyncio
async def test_subtype_success_without_is_error_stays_a_success(tmp_path) -> None:
    out = await _run([_result(is_error=False, result="triage decision")], tmp_path)
    assert out.is_error is False
    assert out.error_message == ""
    assert out.output == "triage decision"


@pytest.mark.asyncio
async def test_non_success_subtype_prefers_errors_over_the_subtype_label(tmp_path) -> None:
    closing = "Claude Code returned an error result: error_during_execution"
    out = await _run(
        [_result(is_error=True, subtype="error_during_execution", result="connection reset by peer")],
        tmp_path,
        closing=Exception(closing),
    )
    assert out.is_error is True
    assert out.error_message == "connection reset by peer", out.error_message
    assert "error_during_execution" not in out.error_message


@pytest.mark.asyncio
async def test_turn_cap_closing_exception_keeps_the_sdk_wording(tmp_path) -> None:
    """A closing exception that already names the errors text is not a subtype-only label."""
    closing = "Claude Code returned an error result: Reached maximum number of turns (40)"
    out = await _run(
        [
            _result(
                is_error=True,
                subtype="error_max_turns",
                errors=["Reached maximum number of turns (40)"],
            )
        ],
        tmp_path,
        closing=Exception(closing),
    )
    assert out.error_code == "ERR_AGENT_MAX_TURNS"
    assert out.error_message == closing, out.error_message
