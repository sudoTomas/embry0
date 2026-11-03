"""The MCP servers embry0 keeps for an agent with no shell tool: the log tool's server alone.

``embry0.agents.mcp_servers`` drops every server that runs a command for an
agent whose tools name no shell tool, except a server equal to one listed
here, its command line and every other key included. Only the log tool's
server (``embry0.workflows.issue_to_pr.log_tool``) is listed. It is
``python3 -P -s -m embry0.sandbox.context_mcp``: Python then imports only from
the image's ``/app`` and its system site-packages, which the agent cannot
write, and from neither the working directory nor the agent's user
site-packages, which it can.

The QA browser backends' servers are not listed. They are Node programs that
load modules, and launch browsers, from places the agent can write, so a
program the job wrote could run in them. An agent with no shell tool runs
without its browser (``mcp_servers_dropped``, and a warning when the agent is
saved). Orchestrator code: it reads the workflows.
"""

from __future__ import annotations

from typing import Any


def image_mcp_servers() -> tuple[dict[str, Any], ...]:
    """The server configs an agent with no shell tool keeps, as embry0 builds them."""
    from embry0.workflows.issue_to_pr.log_tool import CONTEXT_MCP_SERVER

    return tuple(dict(cfg) for cfg in CONTEXT_MCP_SERVER.values())
