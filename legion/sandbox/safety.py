"""Safety enforcement inside the sandbox container."""

from typing import Any

from legion.safety.patterns import is_dangerous_command

WORKSPACE_PREFIX = "/workspace"


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
        if file_path and not (
            file_path.startswith(WORKSPACE_PREFIX + "/") or file_path == WORKSPACE_PREFIX
        ):
            return {
                "decision": "deny",
                "reason": f"Blocked: file operation outside {WORKSPACE_PREFIX}",
            }

    if tool_name == "Read":
        file_path = tool_input.get("file_path", "")
        if file_path and not (
            file_path.startswith(WORKSPACE_PREFIX + "/") or file_path == WORKSPACE_PREFIX
        ):
            return {
                "decision": "deny",
                "reason": f"Blocked: read outside {WORKSPACE_PREFIX}",
            }

    return None
