"""The MCP servers an agent runs with, and who can name them.

An MCP server's tools run with the sandbox's rights, and a stdio server is a
process the CLI starts there. So an agent's servers come only from its
definition: what an operator stored, or what an embry0 node merged into it
for one run (the QA browser backend, the log tool).

Nothing a job's own input decides adds one or replaces them. Triage is a model
reading the ticket, which whoever can label an issue writes, so the servers
its decision names (``pipeline_config.mcp_servers``) are ignored and logged as
``mcp_servers_ignored``.

A server the CLI starts as a command runs whatever its command line says in
the sandbox: it is a way to run commands. An agent whose tools name no shell
tool (``SHELL_TOOLS``) gets no such server, wherever it came from: it is
dropped and logged as ``mcp_servers_dropped``, the QA browser servers
included. The one exception is the log tool's server, matched on its whole
config, command line included (``embry0.services.image_mcp_servers`` says why
it alone is kept). The agent's tool list is left as it is: the
``mcp__<server>__*`` names in it no longer reach anything.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import Any

import structlog

from embry0.safety.policy import SHELL_TOOLS

logger = structlog.get_logger(__name__)

# The transports the CLI reaches over the network, starting no process. Any
# other type, an entry without one (the CLI reads it as stdio) and an entry
# with a command start a process.
_REMOTE_TYPES = frozenset({"http", "sse"})

# A requested server's name is a model's text: the log keeps a bounded copy.
_LOGGED_NAMES = 20
_LOGGED_NAME_CHARS = 64


def runs_command(server: Any) -> bool:
    """Whether the CLI starts ``server`` as a process in the sandbox.

    True for a stdio server, the type the CLI assumes when an entry names none,
    and for any entry embry0 cannot read as an http or sse server without a
    command, so an unknown kind counts as the riskier one.
    """
    if not isinstance(server, Mapping):
        return True
    return server.get("type", "stdio") not in _REMOTE_TYPES or "command" in server


def has_shell_tool(tools: Iterable[str]) -> bool:
    """Whether ``tools`` names a tool that runs a shell command."""
    return not SHELL_TOOLS.isdisjoint(tools)


def server_tool_names(tools: Iterable[str], servers: Iterable[str]) -> list[str]:
    """The ``mcp__<server>__<tool>`` names in ``tools`` that belong to one of ``servers``."""
    prefixes = tuple(f"mcp__{name}__" for name in servers)
    return [tool for tool in tools if tool.startswith(prefixes)]


def logged_names(names: Iterable[Any]) -> list[str]:
    """Server names as a log line keeps them: sorted, each and the list bounded."""
    return sorted(str(name)[:_LOGGED_NAME_CHARS] for name in names)[:_LOGGED_NAMES]


def grant_mcp_servers(
    agent_type: str,
    definition_servers: Any,
    tools: list[str],
    *,
    requested: Any = None,
    image_servers: Collection[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The definition's MCP servers an agent with ``tools`` runs with.

    ``requested`` is what the job asked for at run time: triage's
    ``pipeline_config.mcp_servers`` entry for this agent. It is never applied,
    and a request is logged (``mcp_servers_ignored``).

    Without a shell tool in ``tools``, every server that runs a command is
    dropped and logged with the tools that named it (``mcp_servers_dropped``),
    but one equal to an entry of ``image_servers`` (the log tool's).
    A definition whose servers are not a mapping grants none.
    """
    if requested is not None:
        logger.warning(
            "mcp_servers_ignored",
            agent_type=agent_type,
            servers=logged_names(requested if isinstance(requested, Mapping) else ()),
            source="pipeline_config",
            reason="a job's run-time config cannot name MCP servers",
        )
    if not definition_servers:
        return {}
    if not isinstance(definition_servers, Mapping):
        logger.warning(
            "mcp_servers_dropped",
            agent_type=agent_type,
            servers=[],
            tools=[],
            reason="the definition's mcp_servers is not a mapping",
        )
        return {}
    servers = dict(definition_servers)
    if has_shell_tool(tools):
        return servers
    dropped = [name for name, server in servers.items() if runs_command(server) and server not in image_servers]
    if dropped:
        logger.warning(
            "mcp_servers_dropped",
            agent_type=agent_type,
            servers=logged_names(dropped),
            tools=server_tool_names(tools, dropped),
            reason="the server runs a command and the agent has no shell tool",
        )
    return {name: server for name, server in servers.items() if name not in dropped}
