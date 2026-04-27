"""Orchestrator-side SDK fallback still returns the same AgentResult shape."""

from unittest.mock import patch

import pytest

from athanor.agents.sdk import AgentResult, run_agent


class _FakeResult:
    def __init__(self) -> None:
        self.result = "ok"
        self.total_cost_usd = 0.01
        self.duration_ms = 500
        self.num_turns = 1
        self.usage = {"input_tokens": 1, "output_tokens": 1}


@pytest.mark.asyncio
async def test_run_agent_returns_agent_result(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LEGION_WORKSPACE_ROOT", str(tmp_path))

    async def _gen(msgs):
        for m in msgs:
            yield m

    with patch("claude_agent_sdk.query", return_value=_gen([_FakeResult()])):
        res = await run_agent(
            prompt="hi",
            agent_name="triage",
            model="claude-sonnet-4-6",
            tools=["Read"],
            system_prompt="be terse",
            timeout_seconds=60,
        )
    assert isinstance(res, AgentResult)
    assert res.success is True
