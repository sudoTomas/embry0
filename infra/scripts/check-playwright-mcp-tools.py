#!/usr/bin/env python3
"""Compare the Playwright backend allowlist with what the MCP server serves.

Run inside the built QA image (CI's build-qa-sandbox-image job), where
`playwright-mcp` is the pinned binary the QA agent will talk to:

    python check-playwright-mcp-tools.py /src/embry0/workflows/qa/browser_backends.py

Reads PLAYWRIGHT.tool_names and PLAYWRIGHT_EXCLUDED_TOOLS from that file
with `ast` (no embry0 import — the image carries no embry0 package), asks
the server for tools/list over stdio, and fails when the two sets together
are not exactly the advertised set. A tool the server stops serving, or a
new one nobody decided about, both fail here instead of as a denied call
on a QA run (#81). PLAYWRIGHT_MCP_CMD overrides the server command (tests).

Stdlib only; runs with the image's python.
"""

from __future__ import annotations

import ast
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

PREFIX = "mcp__playwright__"


def declared(source: Path) -> tuple[set[str], set[str]]:
    tree = ast.parse(source.read_text(), filename=str(source))
    tools: set[str] | None = None
    excluded: set[str] | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target = node.target
        else:
            continue
        if not isinstance(target, ast.Name):
            continue
        if target.id == "PLAYWRIGHT" and isinstance(node.value, ast.Call):
            for kw in node.value.keywords:
                if kw.arg == "tool_names":
                    tools = set(ast.literal_eval(kw.value))
        elif target.id == "PLAYWRIGHT_EXCLUDED_TOOLS" and isinstance(node.value, ast.Call):
            excluded = set(ast.literal_eval(node.value.args[0]))
    if tools is None or excluded is None:
        sys.exit(f"{source}: PLAYWRIGHT.tool_names or PLAYWRIGHT_EXCLUDED_TOOLS not found")
    return tools, excluded


def advertised(command: list[str]) -> set[str]:
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "embry0-tool-check", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    stdin = "".join(json.dumps(m) + "\n" for m in messages)
    proc = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=120)
    for line in proc.stdout.splitlines():
        try:
            reply = json.loads(line)
        except ValueError:
            continue
        if reply.get("id") == 2 and "result" in reply:
            return {f"{PREFIX}{t['name']}" for t in reply["result"]["tools"]}
    sys.exit(f"no tools/list reply from {command!r} (exit {proc.returncode}): {proc.stderr[-2000:]}")


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    tools, excluded = declared(Path(sys.argv[1]))
    command = shlex.split(os.environ.get("PLAYWRIGHT_MCP_CMD", "playwright-mcp --headless --browser chromium"))
    served = advertised(command)
    problems = []
    if overlap := tools & excluded:
        problems.append(f"both granted and excluded: {sorted(overlap)}")
    if missing := (tools | excluded) - served:
        problems.append(f"declared but not served by the image's playwright-mcp: {sorted(missing)}")
    if undecided := served - tools - excluded:
        problems.append(
            f"served but neither granted nor excluded (add to PLAYWRIGHT.tool_names or "
            f"PLAYWRIGHT_EXCLUDED_TOOLS): {sorted(undecided)}"
        )
    if problems:
        print("playwright backend allowlist does not match the server:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"playwright backend allowlist matches tools/list: {len(tools)} granted, {len(excluded)} excluded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
