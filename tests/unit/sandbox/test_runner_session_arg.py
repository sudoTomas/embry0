"""Runner resume args: --session-blob / --session-id forwarding into the
executor, mode-agnostic (EMB-35)."""

import json
from unittest.mock import AsyncMock, patch

import pytest


def test_runner_passes_messages_to_executor_when_blob_given(tmp_path):
    from embry0.sandbox.runner import _build_run_kwargs

    blob = tmp_path / "msgs.json"
    blob.write_text(json.dumps([{"role": "user", "content": "hi"}]))

    config = {"agent_type": "developer", "execution_mode": "sdk", "auth_mode": "api_key"}
    kwargs = _build_run_kwargs(config, session_blob_path=str(blob), session_id=None)
    assert kwargs["resume_messages"] == [{"role": "user", "content": "hi"}]


def test_runner_ignores_blob_when_not_given():
    from embry0.sandbox.runner import _build_run_kwargs

    config = {"agent_type": "developer", "execution_mode": "sdk", "auth_mode": "api_key"}
    kwargs = _build_run_kwargs(config, session_blob_path=None, session_id=None)
    assert kwargs.get("resume_messages") is None


def test_runner_forwards_session_id_in_any_auth_mode():
    """EMB-35: --session-id is mode-agnostic — the CLI session file works
    under api_key auth too (it was previously gated to oauth)."""
    from embry0.sandbox.runner import _build_run_kwargs

    for auth_mode in ("api_key", "oauth"):
        config = {"agent_type": "developer", "execution_mode": "sdk", "auth_mode": auth_mode}
        kwargs = _build_run_kwargs(config, session_blob_path=None, session_id="sess-9")
        assert kwargs["resume_session_id"] == "sess-9", auth_mode


def test_runner_forwards_blob_in_any_auth_mode(tmp_path):
    from embry0.sandbox.runner import _build_run_kwargs

    blob = tmp_path / "msgs.json"
    blob.write_text(json.dumps([{"role": "user", "content": "hi"}]))
    config = {"agent_type": "developer", "execution_mode": "sdk", "auth_mode": "oauth"}
    kwargs = _build_run_kwargs(config, session_blob_path=str(blob), session_id=None)
    assert kwargs["resume_messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_run_agent_threads_resume_kwargs_into_executor():
    """The Task 5/6 gap is closed: run_agent forwards both resume kwargs
    into the executor instead of deleting them."""
    from embry0.execution.agent_runner import AgentOutput
    from embry0.sandbox.runner import run_agent

    mock_executor = AsyncMock()
    mock_executor.run = AsyncMock(return_value=AgentOutput(agent_type="developer"))

    with patch("embry0.sandbox.runner.select_executor", return_value=mock_executor):
        await run_agent(
            {
                "agent_type": "developer",
                "prompt": "go",
                "execution_mode": "sdk",
                "auth_mode": "api_key",
            },
            resume_session_id="sess-42",
            resume_messages=[{"role": "user", "content": "old"}],
        )

    call = mock_executor.run.await_args
    assert call.kwargs["resume_session_id"] == "sess-42"
    assert call.kwargs["resume_messages"] == [{"role": "user", "content": "old"}]
