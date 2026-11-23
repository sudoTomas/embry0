"""infra/scripts/check-browser-mcp-tools.py: each backend's granted + excluded tools are exactly what its server serves.

CI runs it in the built QA image against the real servers. Here a stand-in server answers tools/list with the
names a test gives it, so the check is shown passing on a match and failing on every kind of mismatch.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from embry0.workflows.qa.browser_backends import (
    PLAYWRIGHT,
    PLAYWRIGHT_EXCLUDED_TOOLS,
    VIBIUM,
    VIBIUM_EXCLUDED_TOOLS,
)

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra" / "scripts" / "check-browser-mcp-tools.py"
BACKENDS = ROOT / "embry0" / "workflows" / "qa" / "browser_backends.py"

_SERVER = """
import json, sys
names = json.loads(sys.argv[1])
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("id") == 2:
        print(json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": n} for n in names]}}), flush=True)
"""


def _check(backend: str, served: set[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    server = tmp_path / "server.py"
    server.write_text(_SERVER)
    command = shlex.join([sys.executable, str(server), json.dumps(sorted(served))])
    env = {**os.environ, f"{backend.upper()}_MCP_CMD": command}
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(BACKENDS), backend], capture_output=True, text=True, env=env, timeout=60
    )


def _names(tools: set[str] | tuple[str, ...], server: str) -> set[str]:
    return {t.removeprefix(f"mcp__{server}__") for t in tools}


@pytest.mark.parametrize(
    ("backend", "granted", "excluded"),
    [
        ("playwright", PLAYWRIGHT.tool_names, PLAYWRIGHT_EXCLUDED_TOOLS),
        ("vibium", VIBIUM.tool_names, VIBIUM_EXCLUDED_TOOLS),
    ],
)
def test_a_server_serving_exactly_the_declared_tools_passes(backend, granted, excluded, tmp_path: Path) -> None:
    served = _names(set(granted) | set(excluded), backend)
    run = _check(backend, served, tmp_path)
    assert run.returncode == 0, run.stderr
    assert f"{len(granted)} granted, {len(excluded)} excluded" in run.stdout


def test_a_tool_the_server_stopped_serving_fails(tmp_path: Path) -> None:
    """vibium 26.11 dropped browser_check: an allowlist still granting it must fail, as it would have on the bump."""
    served = _names(set(VIBIUM.tool_names) | set(VIBIUM_EXCLUDED_TOOLS), "vibium") - {"browser_set"}
    run = _check("vibium", served, tmp_path)
    assert run.returncode == 1 and "declared but not served" in run.stderr and "browser_set" in run.stderr, run.stderr


def test_a_tool_nobody_decided_about_fails(tmp_path: Path) -> None:
    served = _names(set(VIBIUM.tool_names) | set(VIBIUM_EXCLUDED_TOOLS), "vibium") | {"vibium_new_model_tool"}
    run = _check("vibium", served, tmp_path)
    assert run.returncode == 1 and "neither granted nor excluded" in run.stderr, run.stderr
    assert "mcp__vibium__vibium_new_model_tool" in run.stderr


def test_an_unknown_backend_fails(tmp_path: Path) -> None:
    run = _check("selenium", set(), tmp_path)
    assert run.returncode != 0 and "SELENIUM" in (run.stderr + run.stdout)
