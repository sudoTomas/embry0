"""Safety enforcement inside the sandbox container."""

import os
from typing import Any

from legion.safety.patterns import is_dangerous_command

WORKSPACE_PREFIX = "/workspace"


def _is_within_workspace(file_path: str) -> bool:
    """Check if a file path resolves to within /workspace, following symlinks."""
    if not file_path:
        return False
    # Resolve symlinks to prevent symlink-based path escapes
    try:
        resolved = os.path.realpath(file_path)
    except (OSError, ValueError):
        return False
    return resolved.startswith(WORKSPACE_PREFIX + "/") or resolved == WORKSPACE_PREFIX


def check_tool_safety(tool_name: str, tool_input: dict[str, Any]) -> dict[str, str] | None:
    """Check if a tool call is safe. Returns denial dict if unsafe, None if safe."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        pattern = is_dangerous_command(cmd)
        if pattern:
            return {
                "decision": "deny",
                "reason": f"Blocked: dangerous bash pattern ({pattern})",
            }

    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if file_path and not _is_within_workspace(file_path):
            return {
                "decision": "deny",
                "reason": f"Blocked: file operation outside {WORKSPACE_PREFIX}",
            }

    if tool_name == "Read":
        file_path = tool_input.get("file_path", "")
        if file_path and not _is_within_workspace(file_path):
            return {
                "decision": "deny",
                "reason": f"Blocked: read outside {WORKSPACE_PREFIX}",
            }

    if tool_name == "Glob":
        # Glob uses 'path' for the directory and 'pattern' for the glob expression.
        # If path is provided, it must be within /workspace.
        # If only pattern is provided and it's an absolute path, check it.
        path = tool_input.get("path", "")
        pattern = tool_input.get("pattern", "")

        if path:
            if not _is_within_workspace(path):
                return {
                    "decision": "deny",
                    "reason": f"Blocked: glob outside {WORKSPACE_PREFIX}",
                }
        elif pattern.startswith("/") and not pattern.startswith(WORKSPACE_PREFIX):
            return {
                "decision": "deny",
                "reason": f"Blocked: glob pattern targets outside {WORKSPACE_PREFIX}",
            }

    return None
