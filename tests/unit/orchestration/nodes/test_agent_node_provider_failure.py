"""#466: the trace of a run that died on a spent usage window says so.

End to end across the sandbox boundary: the in-sandbox executor reads the
SDK stream a spent account produces, the sandbox runner serializes its
result, the orchestrator's AgentRunner parses that line off the exec's
stdout, and run_agent_node writes the trace row and the state entry.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from embry0.execution.agent_runner import AgentRunner
from embry0.orchestration.nodes.agent import run_agent_node
from embry0.sandbox.runner import run_agent

QUOTA = "ERR_PROVIDER_QUOTA_EXHAUSTED"
# Verbatim from production event logs (2026-10-02 05:15 UTC).
OUT_OF_USAGE = "You're out of extra usage · resets Oct 5, 12am (UTC)"


async def _spent_account_stream():
    yield AssistantMessage(content=[TextBlock(text=OUT_OF_USAGE)], model="<synthetic>", error="rate_limit")
    yield ResultMessage(
        subtype="success",
        duration_ms=1200,
        duration_api_ms=0,
        is_error=True,
        num_turns=1,
        session_id="s1",
        total_cost_usd=0.0,
        usage={},
        result=OUT_OF_USAGE,
        api_error_status=429,
    )
    raise Exception("Claude Code returned an error result: success")


async def _sandbox_final_result_line(monkeypatch, tmp_path) -> bytes:
    """What `python -m embry0.sandbox.runner` prints last, for the stream above."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    with patch("claude_agent_sdk.query", return_value=_spent_account_stream()):
        result = await run_agent(
            {"agent_type": "triage", "prompt": "triage this", "execution_mode": "sdk", "auth_mode": "oauth"}
        )
    return (json.dumps({"type": "final_result", **result}, default=str) + "\n").encode()


class _Lines:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self) -> _Lines:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _NoStderr:
    async def read(self, _n: int) -> bytes:
        return b""


class _Proc:
    def __init__(self, line: bytes) -> None:
        self.stdout = _Lines([line])
        self.stderr = _NoStderr()
        self.returncode = 1

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:  # pragma: no cover — timeout path only
        pass


def _agent_runner(line: bytes) -> AgentRunner:
    docker = AsyncMock()
    docker.stream_exec = AsyncMock(return_value=_Proc(line))
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec", "c", "true"])
    return AgentRunner(sandbox_manager=AsyncMock(), docker=docker)


async def _run_node(line: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    traces_repo = AsyncMock()
    traces_repo.create = AsyncMock(return_value="run-1")
    updates = await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {}},
        agent_runner=_agent_runner(line),
        agent_type="triage",
        prompt="triage this",
        agent_definition={
            "model": "claude-sonnet-4-6",
            "tools": ["Read"],
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={"configurable": {"job_id": "job-1", "traces_repo": traces_repo}},
    )
    return traces_repo.finish.call_args.kwargs, updates


@pytest.mark.asyncio
async def test_trace_summary_is_the_provider_sentence(monkeypatch, tmp_path) -> None:
    trace, _ = await _run_node(await _sandbox_final_result_line(monkeypatch, tmp_path))
    assert trace["result"] == "error"
    assert trace["result_summary"] == OUT_OF_USAGE, (
        f"the trace summary must be the provider's sentence, got {trace['result_summary']!r}"
    )
    assert trace["error_code"] == QUOTA, f"the trace must carry {QUOTA}, got {trace['error_code']}"


@pytest.mark.asyncio
async def test_agent_output_entry_carries_text_code_and_reset(monkeypatch, tmp_path) -> None:
    _, updates = await _run_node(await _sandbox_final_result_line(monkeypatch, tmp_path))
    entry = updates["agent_outputs"][-1]
    assert entry["error_message"] == OUT_OF_USAGE
    assert entry["error_code"] == QUOTA
    assert updates["errors"] == [f"triage: {OUT_OF_USAGE}"]
    # The executor reads the reset against the real clock here; the year is
    # therefore not pinned, the rest of "Oct 5, 12am (UTC)" is.
    raw = entry.get("provider_resets_at")
    assert isinstance(raw, str), f"the reset must cross the sandbox boundary, got {raw!r}"
    reset = datetime.fromisoformat(raw)
    assert (reset.month, reset.day, reset.hour, reset.minute) == (10, 5, 0, 0)
    assert reset.utcoffset() is not None and reset.utcoffset().total_seconds() == 0
