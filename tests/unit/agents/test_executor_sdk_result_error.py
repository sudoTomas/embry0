"""The real SDK's closing ResultError keeps the CLI's error text (#485, #501).

claude-agent-sdk 0.2 replaces the CLI's closing ``ProcessError`` with a
``ResultError``. Its message is the SDK's error text plus ProcessError's
``" (exit code: N)"`` suffix, so a check that compares the text after
"error result:" with the subtype stops matching, and the payload the CLI
reported (``api_error_status``) is dropped.

These tests do not type that message. They run ``SdkAgentExecutor`` through the
installed SDK's own ``query()`` against a stand-in CLI process that answers the
control handshake, prints one ``result`` frame and exits 1, exactly as the real
CLI ends a failed run. The SDK builds the exception itself.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import patch

import claude_agent_sdk
import pytest
from claude_agent_sdk._cli_version import __cli_version__

from embry0.agents import executor as executor_module
from embry0.agents.executor import SdkAgentExecutor
from embry0.agents.invocation import AgentInvocation
from embry0.agents.session import is_max_turns_error
from embry0.safety.policy import default_policy_for_agent

_FAKE_CLI = textwrap.dedent(
    """\
    #!{python}
    import json
    import sys

    if "-v" in sys.argv[1:] or "--version" in sys.argv[1:]:
        print("{cli_version} (Claude Code)")
        sys.exit(0)

    RESULT = json.loads({result!r})
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("type") == "control_request":
            # As the CLI the SDK bundles answers initialize: it honours a hook's on_failure.
            answer = {{"hook_on_failure_supported": True}} if message["request"].get("subtype") == "initialize" else {{}}
            reply = {{
                "type": "control_response",
                "response": {{"subtype": "success", "request_id": message["request_id"], "response": answer}},
            }}
            sys.stdout.write(json.dumps(reply) + "\\n")
            sys.stdout.flush()
        elif message.get("type") == "user":
            sys.stdout.write(json.dumps(RESULT) + "\\n")
            sys.stdout.flush()
            sys.exit(1)
    """
)


def _result_frame(**fields: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "duration_ms": 1,
        "duration_api_ms": 1,
        "num_turns": 1,
        "session_id": "s1",
        "total_cost_usd": 0.0,
        "usage": {},
        "result": "",
        "errors": [],
    }
    frame.update(fields)
    return frame


def _fake_cli(tmp_path: Path, frame: dict[str, Any]) -> Path:
    script = tmp_path / "claude"
    script.write_text(
        _FAKE_CLI.format(
            python=sys.executable,
            cli_version=__cli_version__,
            result=json.dumps(frame),
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


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


async def _run(tmp_path: Path, frame: dict[str, Any]) -> tuple[Any, list[str]]:
    """Run the executor through the real SDK; return its output and the raw SDK exception text."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cli = _fake_cli(tmp_path, frame)
    real_build = executor_module.build_sdk_options
    real_query = claude_agent_sdk.query
    raised: list[str] = []

    def build_with_fake_cli(invocation: AgentInvocation) -> Any:
        options = real_build(invocation)
        options.cli_path = str(cli)
        options.cwd = str(workspace)
        return options

    async def recording_query(**kwargs: Any) -> Any:
        try:
            async for message in real_query(**kwargs):
                yield message
        except Exception as exc:
            raised.append(str(exc))
            raise

    with (
        patch.dict(os.environ, {"EMBRY0_WORKSPACE_ROOT": str(workspace)}),
        patch.object(executor_module, "build_sdk_options", build_with_fake_cli),
        patch("claude_agent_sdk.query", recording_query),
    ):
        out = await SdkAgentExecutor().run(_inv(), {"configurable": {}, "_test_writer": lambda _event: None})
    return out, raised


@pytest.mark.asyncio
async def test_error_during_execution_keeps_the_api_status_behind_the_exit_code_suffix(tmp_path) -> None:
    """#501's example: an error_during_execution with status 503 and no text."""
    out, raised = await _run(tmp_path, _result_frame(subtype="error_during_execution", api_error_status=503))

    assert raised and raised[0].endswith("(exit code: 1)"), f"not the 0.2 ResultError shape: {raised!r}"
    assert out.is_error is True
    assert "api_error_status=503" in out.error_message, (
        f"the CLI's api_error_status was dropped behind the SDK's exit-code suffix: {out.error_message!r}"
    )


@pytest.mark.asyncio
async def test_an_empty_error_result_is_described_not_reported_as_unknown(tmp_path) -> None:
    """subtype success with nothing to say: the SDK's fallback label is not the diagnosis."""
    out, raised = await _run(tmp_path, _result_frame())

    assert raised and raised[0].endswith("(exit code: 1)"), f"not the 0.2 ResultError shape: {raised!r}"
    assert out.is_error is True
    assert "subtype=success" in out.error_message and "no result" in out.error_message, (
        f"the SDK's empty-result label was kept instead of the run's own description: {out.error_message!r}"
    )


@pytest.mark.asyncio
async def test_a_turn_cap_keeps_the_sdk_wording(tmp_path) -> None:
    """The closing exception already carries the errors text; stripping the suffix must not replace it."""
    out, raised = await _run(
        tmp_path,
        _result_frame(subtype="error_max_turns", errors=["Reached maximum number of turns (5)"]),
    )

    assert raised and raised[0].endswith("(exit code: 1)"), f"not the 0.2 ResultError shape: {raised!r}"
    assert out.error_message == raised[0], out.error_message
    assert is_max_turns_error(out.error_message), out.error_message
