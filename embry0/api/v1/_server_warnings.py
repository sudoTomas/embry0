"""What a save tells the operator about the MCP servers a job will run an agent without.

A server that runs a command reaches only an agent whose tools, as the
operator gave them, name a shell tool (``embry0.agents.mcp_servers``). Those
tools are the agent's definition's, or a template step's, which replace them:
so saving an agent and saving a template can each leave an agent without a
server, its browser included. Both say so, with the rule the run uses.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from embry0.agents.mcp_servers import has_shell_tool, servers_without_shell
from embry0.safety.policy import parse_tool_list, tools_for_run
from embry0.services.image_mcp_servers import image_mcp_servers
from embry0.workflows.qa.browser_backends import backend_for, backend_names


def _browser_servers() -> list[str]:
    return [backend_for(name).mcp_server_name for name in backend_names()]


def servers_run_without(agent_type: str, tools: Any, servers: Any) -> list[str]:
    """The servers, and the browsers ``tools`` name, a job runs ``agent_type`` without when these are its tools.

    ``tools`` as stored: None is the type's defaults, a list is that list.
    ``servers`` is the agent's ``mcp_servers``; anything but a mapping is none.
    """
    run_tools = tools_for_run(agent_type, parse_tool_list(tools))
    if has_shell_tool(run_tools):
        return []
    stored = dict(servers) if isinstance(servers, Mapping) else {}
    dropped = servers_without_shell(stored, run_tools, image_mcp_servers())
    named = [b for b in _browser_servers() if any(t.startswith(f"mcp__{b}__") for t in run_tools)]
    return list(dict.fromkeys([*dropped, *named]))


def no_shell_server_warning(names: list[str], *, tools: str = "tools", runs: str = "this agent") -> str:
    """The warning for ``names`` (``servers_run_without``): ``tools`` names whose tools, ``runs`` what runs."""
    text = (
        f"{tools} name no shell tool (Bash or Monitor), so a job runs {runs} without the MCP server(s) "
        f"{', '.join(names)}: a server that runs a command needs a shell tool."
    )
    if set(_browser_servers()).intersection(names):
        text += " An agent with browser tools and no shell tool runs without its browser."
    return text
