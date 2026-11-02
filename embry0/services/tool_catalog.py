"""The tools an agent definition can list: the tools embry0 gates (spec 3.4).

``GET /tools`` serves this catalog to the dashboard, and every write of an
agent definition is checked against it, its stored names included: the API
refuses an unknown name with a 422, and ``AgentDefinitionsRepository.create``,
``update`` and ``reset`` (every seeder writes through ``create``) raise.
A template node's tools are checked when the agent is resolved, before
triage narrows them (``embry0.agents.resolver.gated_tools``). The Claude CLI
offers more tools than these, and a release can add one that runs commands; a
name outside the catalog is a tool embry0 does not know how to gate, so no
agent is given it. ``Task``, the subagent tool's older name, is accepted and
read as ``Agent``.

An ``mcp__<server>__<tool>`` name is also accepted when ``<server>`` is one of
the definition's own ``mcp_servers``: the tools of a server the definition
starts itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from embry0.agents.mcp_client import split_mcp_tool_name
from embry0.workflows.issue_to_pr.log_tool import CONTEXT_LOGS_TOOL
from embry0.workflows.qa.browser_backends import backend_for, backend_names

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
    ("Agent", "Delegate to a subagent"),
)

# Older names for catalog tools. A stored definition may still say Task, the
# subagent tool's name before the CLI called it Agent.
TOOL_ALIASES: dict[str, str] = {"Task": "Agent"}


def tool_catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [{"name": n, "source": "builtin", "description": d} for n, d in BUILTIN_TOOLS]
    for backend_name in backend_names():
        rows.extend(
            {"name": tool, "source": f"mcp:{backend_name}", "description": f"{backend_name} browser tool"}
            for tool in backend_for(backend_name).browser_tools()
        )
    rows.append({"name": CONTEXT_LOGS_TOOL, "source": "mcp:context", "description": "Query the deployment's logs"})
    return sorted(rows, key=lambda r: (r["source"] != "builtin", r["source"], r["name"]))


def canonical_tools(tools: Iterable[str]) -> list[str]:
    """``tools`` with every older name replaced by the catalog's (Task is Agent)."""
    out: list[str] = []
    for name in tools:
        name = TOOL_ALIASES.get(name, name)
        if name not in out:
            out.append(name)
    return out


def unknown_tools(tools: Iterable[str], *, mcp_servers: Mapping[str, Any] | None = None) -> list[str]:
    """The names in ``tools`` that are neither in the catalog (or an alias of one) nor a tool of one of ``mcp_servers``."""
    known = {row["name"] for row in tool_catalog()} | set(TOOL_ALIASES)
    own_servers = set(mcp_servers or {})
    unknown: list[str] = []
    for name in tools:
        if name in known:
            continue
        parsed = split_mcp_tool_name(name) if isinstance(name, str) else None
        if parsed is not None and parsed[0] in own_servers:
            continue
        unknown.append(name)
    return unknown


def unknown_tools_message(agent_type: str, unknown: list[str]) -> str:
    return (
        f"agent {agent_type!r} lists tools embry0 does not gate: {unknown}. "
        "An agent can list the tools GET /tools returns, and the tools of its own mcp_servers; "
        "remove the others to save the definition."
    )


def check_agent_tools(agent_type: str, tools: Iterable[str], *, mcp_servers: Mapping[str, Any] | None = None) -> None:
    """Raise ValueError when ``tools`` names a tool outside the catalog."""
    unknown = unknown_tools(tools, mcp_servers=mcp_servers)
    if unknown:
        raise ValueError(unknown_tools_message(agent_type, unknown))
