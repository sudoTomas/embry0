"""A check that errors, or does not finish in time, refuses its call, measured on the real CLI (the one the SDK
bundles).

The executor registers its hooks with ``executor.gate_hooks``. Here the PreToolUse check raises, or never finishes;
the CLI, run against a scripted stand-in for the Messages API on localhost, is asked for one Bash call that would
create a file in the test's directory. The call is refused: the file is not made.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from embry0.agents.executor import _hold_config, gate_hooks
from tests.unit.safety.test_ring2_cli_parity import _bundled_cli, scripted_api  # noqa: F401 — the fixture by name

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not os.access(_bundled_cli(), os.X_OK),
    reason="needs Linux and the CLI bundled with claude_agent_sdk",
)


async def _failing_check(hook_input: Any, tool_use_id: str | None, context: Any) -> Any:  # noqa: ARG001
    raise RuntimeError("the check could not complete")


async def _hanging_check(hook_input: Any, tool_use_id: str | None, context: Any) -> Any:  # noqa: ARG001
    await asyncio.sleep(3600)


def _run(api: Any, project: Path, home: Path, hooks: dict[Any, list[Any]]) -> None:
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        permission_mode="bypassPermissions",
        cwd=str(project),
        max_turns=3,
        setting_sources=[],
        hooks=hooks,
        model="claude-sonnet-4-6",
        env={
            "ANTHROPIC_BASE_URL": api.url,
            "ANTHROPIC_API_KEY": "sk-ant-test-not-a-key",
            "HOME": str(home),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
        },
    )

    async def go() -> None:
        async for _ in query(prompt="make the marker", options=options):
            pass

    asyncio.run(asyncio.wait_for(go(), 120))


def test_a_check_that_errors_refuses_its_call(tmp_path: Path, scripted_api: Any) -> None:  # noqa: F811
    from claude_agent_sdk import HookMatcher

    project, home = tmp_path / "workspace", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    marker = tmp_path / "marker"
    api = scripted_api([("Bash", {"command": f"touch {marker}", "description": "make the marker"})])

    _run(api, project, home, gate_hooks(HookMatcher, _failing_check, _hold_config))

    assert "toolu_000" in api.results, f"the CLI never answered the scripted call: {api.results}"
    assert not marker.exists(), "a call whose PreToolUse check errored was not refused"


def test_a_check_that_does_not_finish_in_time_refuses_its_call(tmp_path: Path, scripted_api: Any) -> None:  # noqa: F811
    from claude_agent_sdk import HookMatcher

    project, home = tmp_path / "workspace", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    marker = tmp_path / "marker"
    api = scripted_api([("Bash", {"command": f"touch {marker}", "description": "make the marker"})])

    started = time.monotonic()
    _run(api, project, home, gate_hooks(HookMatcher, _hanging_check, _hold_config, timeout=2))
    took = time.monotonic() - started

    assert "toolu_000" in api.results, f"the CLI never answered the scripted call: {api.results}"
    assert not marker.exists(), "a call whose PreToolUse check did not finish was not refused"
    assert took < 30, f"the run waited {took:.0f}s on a check given 2s"
