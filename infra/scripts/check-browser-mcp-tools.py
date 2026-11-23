#!/usr/bin/env python3
"""Compare a browser backend's allowlist with what its MCP server serves.

Run inside the built QA image (CI's build-qa-sandbox-image job), where the
backend's server is the pinned binary the QA agent will talk to:

    python check-browser-mcp-tools.py /src/embry0/workflows/qa/browser_backends.py playwright
    python check-browser-mcp-tools.py /src/embry0/workflows/qa/browser_backends.py vibium

Reads the backend's tool_names, mcp_server_name, mcp_command and mcp_args,
and its <NAME>_EXCLUDED_TOOLS, from that file with `ast` (no embry0 import —
the image carries no embry0 package). Starts the server as the agent does,
asks it for tools/list over stdio, and fails when the granted and excluded
sets together are not exactly the advertised set. A tool the server stops
serving, or a new one nobody decided about, both fail here instead of as a
denied call on a QA run (#81). <NAME>_MCP_CMD (PLAYWRIGHT_MCP_CMD,
VIBIUM_MCP_CMD) overrides the server command (tests).

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
from typing import Any


def declared(source: Path, backend: str) -> tuple[dict[str, Any], set[str]]:
    """The backend's constructor keywords that are literals, and its excluded tools."""
    constant = backend.upper()
    tree = ast.parse(source.read_text(), filename=str(source))
    fields: dict[str, Any] | None = None
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
        if target.id == constant and isinstance(node.value, ast.Call):
            fields = {}
            for kw in node.value.keywords:
                if kw.arg in ("tool_names", "mcp_server_name", "mcp_command", "mcp_args"):
                    fields[kw.arg] = ast.literal_eval(kw.value)
        elif target.id == f"{constant}_EXCLUDED_TOOLS" and isinstance(node.value, ast.Call):
            excluded = set(ast.literal_eval(node.value.args[0]))
    if (
        fields is None
        or excluded is None
        or set(fields) != {"tool_names", "mcp_server_name", "mcp_command", "mcp_args"}
    ):
        sys.exit(
            f"{source}: {constant} (with literal tool_names, mcp_server_name, mcp_command, mcp_args) "
            f"or {constant}_EXCLUDED_TOOLS not found"
        )
    return fields, excluded


def advertised(command: list[str], prefix: str) -> set[str]:
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
            return {f"{prefix}{t['name']}" for t in reply["result"]["tools"]}
    sys.exit(f"no tools/list reply from {command!r} (exit {proc.returncode}): {proc.stderr[-2000:]}")


def main() -> int:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    backend = sys.argv[2]
    fields, excluded = declared(Path(sys.argv[1]), backend)
    tools = set(fields["tool_names"])
    default = shlex.join([fields["mcp_command"], *fields["mcp_args"]])
    command = shlex.split(os.environ.get(f"{backend.upper()}_MCP_CMD", default))
    served = advertised(command, f"mcp__{fields['mcp_server_name']}__")
    problems = []
    if overlap := tools & excluded:
        problems.append(f"both granted and excluded: {sorted(overlap)}")
    if missing := (tools | excluded) - served:
        problems.append(f"declared but not served by the image's {command[0]}: {sorted(missing)}")
    if undecided := served - tools - excluded:
        problems.append(
            f"served but neither granted nor excluded (add to {backend.upper()}.tool_names or "
            f"{backend.upper()}_EXCLUDED_TOOLS): {sorted(undecided)}"
        )
    if problems:
        print(f"{backend} backend allowlist does not match the server:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"{backend} backend allowlist matches tools/list: {len(tools)} granted, {len(excluded)} excluded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
