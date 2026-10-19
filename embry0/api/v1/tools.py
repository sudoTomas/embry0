"""GET /tools: the tool names an agent definition can list (spec 3.4)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from embry0.workflows.issue_to_pr.log_tool import CONTEXT_LOGS_TOOL
from embry0.workflows.qa.browser_backends import backend_for, backend_names

router = APIRouter()

BUILTIN_TOOLS: tuple[tuple[str, str], ...] = (
    ("Read", "Read a file"),
    ("Write", "Create or overwrite a file"),
    ("Edit", "Replace text in a file"),
    ("Bash", "Run a shell command in the sandbox"),
    ("Glob", "Find files by pattern"),
    ("Grep", "Search file contents"),
    ("WebFetch", "Fetch a URL (through the sandbox egress policy)"),
    ("WebSearch", "Search the web"),
    ("NotebookEdit", "Edit a Jupyter notebook cell"),
    ("Task", "Delegate to a subagent"),
)


def tool_catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [{"name": n, "source": "builtin", "description": d} for n, d in BUILTIN_TOOLS]
    for backend_name in backend_names():
        rows.extend(
            {"name": tool, "source": f"mcp:{backend_name}", "description": f"{backend_name} browser tool"}
            for tool in backend_for(backend_name).browser_tools()
        )
    rows.append({"name": CONTEXT_LOGS_TOOL, "source": "mcp:context", "description": "Query the deployment's logs"})
    return sorted(rows, key=lambda r: (r["source"] != "builtin", r["source"], r["name"]))


@router.get("/tools")
async def list_tools() -> list[dict[str, Any]]:
    return tool_catalog()
