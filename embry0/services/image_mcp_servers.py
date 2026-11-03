"""The MCP servers embry0 itself adds to an agent for one run, whose programs ship in the sandbox images.

They are the QA browser backends' servers (``embry0.workflows.qa.browser_backends``)
and the log tool's (``embry0.workflows.issue_to_pr.log_tool``).
``embry0.agents.mcp_servers`` keeps a server equal to one of these, its command
line and every other key included, for an agent with no shell tool: its
program is the image's, at a path on the image's own PATH (root-owned
directories), and its arguments are embry0's, so the job cannot choose what it
runs. Orchestrator code: it reads the workflows.
"""

from __future__ import annotations

from typing import Any


def image_mcp_servers() -> tuple[dict[str, Any], ...]:
    """Every server config embry0 adds for a run, as it builds it."""
    from embry0.workflows.issue_to_pr.log_tool import CONTEXT_MCP_SERVER
    from embry0.workflows.qa.browser_backends import backend_for, backend_names

    browser = [cfg for name in backend_names() for cfg in backend_for(name).mcp_servers().values()]
    return (*browser, *(dict(cfg) for cfg in CONTEXT_MCP_SERVER.values()))
