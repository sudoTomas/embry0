"""Built-in tool definitions + implementations for the direct-xAI executor (EMB-45).

The Claude Code CLI provided Read/Write/Edit/Bash/Glob/Grep for free. A direct-API
tool-use loop must define and run them itself. These are the Anthropic-Messages tool
schemas plus pure Python implementations that operate inside the sandbox /workspace.

Gating is the executor's job: it calls ``embry0.safety.policy.gate_tool_call`` BEFORE
``execute_tool`` for every call, so these implementations assume the path/command has
already cleared Ring-2/Ring-3. They still fail gracefully (returning an is_error result
the model can react to) rather than raising.
"""

from __future__ import annotations

import glob as _glob
import os
import re
import subprocess
from typing import Any

# Tool names this module implements — the executor intersects these with the
# agent's allowlist so an agent only sees the tools it is permitted.
BUILTIN_TOOL_NAMES: tuple[str, ...] = ("Read", "Write", "Edit", "Bash", "Glob", "Grep")

_MAX_TOOL_RESULT_CHARS = 30_000
_DEFAULT_BASH_TIMEOUT = 120


def tool_defs(allowed: list[str]) -> list[dict[str, Any]]:
    """Anthropic tool schemas for the builtins present in *allowed*, order-stable."""
    defs: dict[str, dict[str, Any]] = {
        "Read": {
            "name": "Read",
            "description": "Read a text file from the filesystem. Returns the content with 1-based line numbers.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to the file."},
                    "offset": {"type": "integer", "description": "1-based line to start from (optional)."},
                    "limit": {"type": "integer", "description": "Max lines to read (optional)."},
                },
                "required": ["file_path"],
            },
        },
        "Write": {
            "name": "Write",
            "description": "Write (create or overwrite) a file with the given content.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
            },
        },
        "Edit": {
            "name": "Edit",
            "description": "Replace an exact string in a file. Fails unless old_string occurs exactly once "
            "(unless replace_all is true).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
        "Bash": {
            "name": "Bash",
            "description": "Run a shell command in /workspace and return combined stdout+stderr and the exit code.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Seconds (optional; capped)."},
                },
                "required": ["command"],
            },
        },
        "Glob": {
            "name": "Glob",
            "description": "List files matching a glob pattern (supports **). Returns newline-separated paths.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Search root (optional; defaults to /workspace)."},
                },
                "required": ["pattern"],
            },
        },
        "Grep": {
            "name": "Grep",
            "description": "Search file contents with a regex. Returns matching file:line: text lines.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Search root (optional; defaults to /workspace)."},
                    "ignore_case": {"type": "boolean"},
                },
                "required": ["pattern"],
            },
        },
    }
    return [defs[name] for name in allowed if name in defs]


def _truncate(text: str) -> str:
    if len(text) > _MAX_TOOL_RESULT_CHARS:
        return text[:_MAX_TOOL_RESULT_CHARS] + f"\n… [truncated {len(text) - _MAX_TOOL_RESULT_CHARS} chars]"
    return text


def _resolve(path: str, cwd: str) -> str:
    return path if os.path.isabs(path) else os.path.join(cwd, path)


def execute_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    cwd: str = "/workspace",
    bash_timeout: int = _DEFAULT_BASH_TIMEOUT,
) -> tuple[str, bool]:
    """Execute a builtin tool. Returns (result_text, is_error).

    Never raises for expected failures (missing file, bad edit, non-zero exit) —
    those come back as is_error results the model can recover from.
    """
    try:
        if tool_name == "Read":
            return _read(tool_input, cwd)
        if tool_name == "Write":
            return _write(tool_input, cwd)
        if tool_name == "Edit":
            return _edit(tool_input, cwd)
        if tool_name == "Bash":
            return _bash(tool_input, cwd, bash_timeout)
        if tool_name == "Glob":
            return _glob_tool(tool_input, cwd)
        if tool_name == "Grep":
            return _grep(tool_input, cwd)
        return (f"unknown tool: {tool_name!r}", True)
    except Exception as exc:  # defensive — a tool bug must not kill the loop
        return (f"{tool_name} failed: {exc!r}", True)


def _read(ti: dict[str, Any], cwd: str) -> tuple[str, bool]:
    path = _resolve(str(ti["file_path"]), cwd)
    if not os.path.exists(path):
        return (f"Read: file not found: {path}", True)
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    offset = int(ti.get("offset", 1) or 1)
    start = max(offset - 1, 0)
    limit = ti.get("limit")
    end = start + int(limit) if limit else len(lines)
    numbered = [f"{i + 1}\t{lines[i].rstrip(chr(10))}" for i in range(start, min(end, len(lines)))]
    if not numbered:
        return (f"Read: {path} is empty or offset past end of file", False)
    return (_truncate("\n".join(numbered)), False)


def _write(ti: dict[str, Any], cwd: str) -> tuple[str, bool]:
    path = _resolve(str(ti["file_path"]), cwd)
    content = str(ti.get("content", ""))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return (f"Wrote {len(content)} chars to {path}", False)


def _edit(ti: dict[str, Any], cwd: str) -> tuple[str, bool]:
    path = _resolve(str(ti["file_path"]), cwd)
    if not os.path.exists(path):
        return (f"Edit: file not found: {path}", True)
    old = str(ti["old_string"])
    new = str(ti["new_string"])
    replace_all = bool(ti.get("replace_all", False))
    with open(path, encoding="utf-8") as f:
        text = f.read()
    count = text.count(old)
    if count == 0:
        return (f"Edit: old_string not found in {path}", True)
    if count > 1 and not replace_all:
        return (f"Edit: old_string occurs {count} times in {path}; pass replace_all or add context", True)
    text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return (f"Edited {path} ({'all' if replace_all else '1'} occurrence(s))", False)


def _bash(ti: dict[str, Any], cwd: str, default_timeout: int) -> tuple[str, bool]:
    command = str(ti["command"])
    timeout = min(int(ti.get("timeout", default_timeout) or default_timeout), 600)
    try:
        proc = subprocess.run(
            command,
            shell=True,  # noqa: S602 — sandboxed; Ring-3 content rules gate the command upstream
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return (f"Bash: command timed out after {timeout}s", True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return (_truncate(f"[exit {proc.returncode}]\n{out}"), False)


def _glob_tool(ti: dict[str, Any], cwd: str) -> tuple[str, bool]:
    pattern = str(ti["pattern"])
    root = _resolve(str(ti.get("path") or cwd), cwd)
    full = pattern if os.path.isabs(pattern) else os.path.join(root, pattern)
    matches = sorted(_glob.glob(full, recursive=True))
    if not matches:
        return (f"Glob: no matches for {pattern!r} under {root}", False)
    return (_truncate("\n".join(matches)), False)


def _grep(ti: dict[str, Any], cwd: str) -> tuple[str, bool]:
    pattern = str(ti["pattern"])
    root = _resolve(str(ti.get("path") or cwd), cwd)
    flags = re.IGNORECASE if ti.get("ignore_case") else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        return (f"Grep: invalid pattern: {exc}", True)
    results: list[str] = []
    walk_root = root if os.path.isdir(root) else os.path.dirname(root) or cwd
    for dirpath, dirnames, filenames in os.walk(walk_root):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, 1):
                        if rx.search(line):
                            results.append(f"{fp}:{lineno}: {line.rstrip()}")
                            if len(results) >= 500:
                                results.append("… [500-match cap reached]")
                                return (_truncate("\n".join(results)), False)
            except OSError:
                continue
    if not results:
        return (f"Grep: no matches for {pattern!r} under {root}", False)
    return (_truncate("\n".join(results)), False)
