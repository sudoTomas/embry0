"""Which MCP servers an agent runs with (embry0.agents.mcp_servers).

An agent's servers come from its definition only, and a server that runs a
command only reaches an agent with a shell tool.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog

from embry0.agents.resolver import resolve_agent_invocation
from embry0.execution.agent_runner import AgentOutput
from embry0.orchestration.nodes.agent import run_agent_node
from embry0.orchestration.nodes.triage import parse_triage_response

OPERATOR_SERVER = {"docs": {"type": "stdio", "command": "docs-mcp", "args": ["--read-only"]}}
REQUESTED_SERVER = {"helper": {"type": "stdio", "command": "sh", "args": ["-c", "true"]}}


def _definition(tools: list[str], servers: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "model": "claude-sonnet-4-6",
        "tools": tools,
        "skills": [],
        "system_prompt": "",
        "mcp_servers": servers or {},
        "execution_mode": None,
        "auth_mode": None,
    }


def _resolve(
    definition: dict[str, Any], pipeline_config: dict[str, Any] | None = None, agent_type: str = "developer"
) -> Any:
    return resolve_agent_invocation(
        agent_type=agent_type,
        prompt="do it",
        system_context="",
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        repo_prefs=None,
        job_overrides=None,
        agent_definition=definition,
        pipeline_config=pipeline_config or {},
        max_turns=10,
        timeout_seconds=60,
        credentials={"api_key": "", "oauth_token": "oat-000000000001"},
    )


def _triage_config(**pipeline_config: Any) -> dict[str, Any]:
    """The pipeline_config of a decision as triage_node stores it on state."""
    decision = parse_triage_response(
        json.dumps({"action": "proceed", "confidence": 0.9, "pipeline_config": pipeline_config})
    )
    return dict(decision["pipeline_config"])


# ---- run-time requests --------------------------------------------------------


def test_a_server_named_by_triage_is_not_granted() -> None:
    config = _triage_config(
        mcp_servers={"developer": REQUESTED_SERVER},
        agent_tools={"developer": ["Read", "Bash", "mcp__helper__run"]},
    )
    inv = _resolve(_definition(["Read", "Bash"]), config)
    assert "helper" not in inv.mcp_servers, f"a server triage named reached the agent: {inv.mcp_servers}"
    assert inv.mcp_servers == {}, inv.mcp_servers


def test_triage_cannot_replace_the_operators_servers() -> None:
    config = _triage_config(mcp_servers={"developer": REQUESTED_SERVER})
    inv = _resolve(_definition(["Read", "Bash"], OPERATOR_SERVER), config)
    assert inv.mcp_servers == OPERATOR_SERVER, f"triage replaced the definition's servers: {inv.mcp_servers}"


def test_triage_cannot_remove_the_operators_servers() -> None:
    config = _triage_config(mcp_servers={"developer": {}})
    inv = _resolve(_definition(["Read", "Bash"], OPERATOR_SERVER), config)
    assert inv.mcp_servers == OPERATOR_SERVER, f"an empty request emptied the definition's servers: {inv.mcp_servers}"


def test_a_request_for_another_agent_changes_nothing_here() -> None:
    inv = _resolve(_definition(["Read", "Bash"], OPERATOR_SERVER), {"mcp_servers": {"review": REQUESTED_SERVER}})
    assert inv.mcp_servers == OPERATOR_SERVER, f"the developer was given {inv.mcp_servers}"


def test_an_ignored_request_is_logged_by_name() -> None:
    with structlog.testing.capture_logs() as logs:
        _resolve(_definition(["Read", "Bash"]), {"mcp_servers": {"developer": REQUESTED_SERVER}})
    ignored = [e for e in logs if e["event"] == "mcp_servers_ignored"]
    assert ignored, f"an ignored request must be logged: {[e['event'] for e in logs]}"
    assert ignored[0]["servers"] == ["helper"] and ignored[0]["agent_type"] == "developer", ignored[0]
    assert "sh" not in json.dumps(ignored[0]["servers"]), "the log names the server, never its command line"


async def test_a_requested_server_never_reaches_the_sandbox() -> None:
    """End to end: a decision triage wrote, the agent node, the config shipped to the sandbox."""
    shipped: dict[str, Any] = {}

    class _Runner:
        async def run(self, container: str, config: dict[str, Any], *args: Any, **kwargs: Any) -> AgentOutput:
            shipped.update(config)
            return AgentOutput(agent_type="developer", is_error=False, output="ok")

    state = {
        "sandbox_container_id": "c1",
        "total_cost_usd": 0.0,
        "pipeline_config": _triage_config(mcp_servers={"developer": REQUESTED_SERVER}),
    }
    await run_agent_node(
        state=state,
        agent_runner=_Runner(),
        agent_type="developer",
        prompt="do it",
        agent_definition=_definition(["Read", "Bash"], OPERATOR_SERVER),
        credentials={"api_key": "", "oauth_token": "oat-000000000001"},
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
    )
    assert shipped, "the runner was never called"
    assert shipped["mcp_servers"] == OPERATOR_SERVER, f"the sandbox was sent {shipped['mcp_servers']}"


# ---- a server that runs a command needs a shell tool ---------------------------


@pytest.mark.parametrize(
    "server",
    [
        {"type": "stdio", "command": "docs-mcp"},
        {"command": "docs-mcp"},
        {"url": "http://docs.test/mcp"},
        {"type": "http", "url": "http://docs.test/mcp", "command": "docs-mcp"},
        {"type": "ws", "url": "ws://docs.test/mcp"},
        "docs-mcp",
    ],
    ids=["stdio", "untyped", "untyped-without-command", "http-with-command", "unknown-type", "not-a-mapping"],
)
def test_an_agent_without_a_shell_tool_gets_no_server_that_runs_a_command(server: Any) -> None:
    inv = _resolve(_definition(["Read", "Glob", "Grep"], {"docs": server}), agent_type="triage")
    assert inv.mcp_servers == {}, f"an agent without a shell tool was given {inv.mcp_servers}"


def test_dropping_a_server_never_empties_the_tool_list() -> None:
    """An empty list reads downstream as the agent type's default tools, Bash among them."""
    inv = _resolve(_definition(["mcp__docs__search"], OPERATOR_SERVER), agent_type="docs-reader")
    assert inv.mcp_servers == {}, f"an agent with only the server's tools kept {inv.mcp_servers}"
    assert inv.tools == ["mcp__docs__search"], f"the tool list became {inv.tools}"


def test_a_dropped_server_is_logged() -> None:
    with structlog.testing.capture_logs() as logs:
        _resolve(_definition(["Read", "mcp__docs__search"], OPERATOR_SERVER))
    dropped = [e for e in logs if e["event"] == "mcp_servers_dropped"]
    assert dropped, f"a dropped server must be logged: {[e['event'] for e in logs]}"
    assert dropped[0]["servers"] == ["docs"] and dropped[0]["tools"] == ["mcp__docs__search"], dropped[0]


def test_an_agent_without_a_shell_tool_keeps_a_remote_server() -> None:
    remote = {"docs": {"type": "http", "url": "http://docs.test/mcp"}}
    inv = _resolve(_definition(["Read", "mcp__docs__search"], remote))
    assert inv.mcp_servers == remote, f"a server that runs no command was dropped: {inv.mcp_servers}"
    assert "mcp__docs__search" in inv.tools, inv.tools


@pytest.mark.parametrize("shell", ["Bash", "Monitor"])
def test_an_operators_server_works_for_an_agent_with_a_shell_tool(shell: str) -> None:
    inv = _resolve(_definition(["Read", shell, "mcp__docs__search"], OPERATOR_SERVER))
    assert inv.mcp_servers == OPERATOR_SERVER, f"{shell}: the definition's server was dropped"
    assert "mcp__docs__search" in inv.tools


def test_a_shell_tool_triage_removes_takes_the_command_server_with_it() -> None:
    config = _triage_config(agent_tools={"developer": ["Read", "mcp__docs__search"]})
    inv = _resolve(_definition(["Read", "Bash", "mcp__docs__search"], OPERATOR_SERVER), config)
    assert inv.mcp_servers == {}, "the server must follow the tools the agent runs with, not its definition's"


# ---- what embry0 itself grants keeps working -----------------------------------


@pytest.mark.parametrize("backend_name", ["playwright", "vibium"])
def test_the_qa_agent_keeps_its_browser_server(backend_name: str) -> None:
    from embry0.workflows.qa.agent_seed import QA_AGENT_SEED
    from embry0.workflows.qa.browser_backends import backend_for, with_browser_backend

    definition = with_browser_backend(dict(QA_AGENT_SEED), backend_for(backend_name))
    inv = _resolve(definition, agent_type="qa")
    assert inv.mcp_servers and inv.mcp_servers == definition["mcp_servers"], f"the QA agent got {inv.mcp_servers}"
    assert inv.tools == definition["tools"], f"the QA agent lost tools: {set(definition['tools']) - set(inv.tools)}"


def test_the_log_tool_grant_keeps_its_server() -> None:
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
    from embry0.workflows.issue_to_pr.log_tool import CONTEXT_LOGS_TOOL, CONTEXT_MCP_SERVER, grant_log_tool

    definition = grant_log_tool(dict(BUILTIN_SEED["analysis"]))
    inv = _resolve(definition, agent_type="analysis")
    assert inv.mcp_servers == CONTEXT_MCP_SERVER, f"the log tool's server was dropped: {inv.mcp_servers}"
    assert CONTEXT_LOGS_TOOL in inv.tools, inv.tools


# ---- the one server kept for an agent with no shell tool: the log tool's -----------


@pytest.mark.parametrize("backend_name", ["playwright", "vibium"])
def test_an_agent_with_browser_tools_and_no_shell_gets_no_browser_server(backend_name: str) -> None:
    """A browser server is a Node program that loads code from places the agent can write: no exemption."""
    from embry0.workflows.qa.agent_seed import QA_AGENT_SEED
    from embry0.workflows.qa.browser_backends import backend_for, with_browser_backend

    backend = backend_for(backend_name)
    definition = with_browser_backend(dict(QA_AGENT_SEED), backend)
    narrowed = _triage_config(agent_tools={"qa": ["Read", "Glob", "Write", *backend.browser_tools()]})
    with structlog.testing.capture_logs() as logs:
        inv = _resolve(definition, narrowed, agent_type="qa")
    assert "Bash" not in inv.tools, inv.tools
    assert inv.mcp_servers == {}, f"an agent with no shell tool kept its browser server: {inv.mcp_servers}"
    dropped = [e for e in logs if e["event"] == "mcp_servers_dropped"]
    assert dropped and dropped[0]["log_level"] == "warning", f"the drop must be a warning: {dropped}"
    assert dropped[0]["agent_type"] == "qa" and dropped[0]["servers"] == [backend.mcp_server_name], dropped[0]


def test_an_agent_without_bash_keeps_the_log_tools_server() -> None:
    from embry0.workflows.issue_to_pr.log_tool import CONTEXT_MCP_SERVER, grant_log_tool

    inv = _resolve(grant_log_tool(_definition(["Read", "Grep"])), agent_type="analysis")
    assert inv.mcp_servers == CONTEXT_MCP_SERVER, f"the log tool's server was dropped: {inv.mcp_servers}"


@pytest.mark.parametrize(
    "server",
    [
        {"type": "stdio", "command": "sh", "args": ["--headless", "--browser", "chromium"]},
        {"type": "stdio", "command": "playwright-mcp", "args": ["--headless", "--browser", "chromium", "--extra"]},
        {
            "type": "stdio",
            "command": "playwright-mcp",
            "args": ["--headless", "--browser", "chromium"],
            "env": {"NODE_OPTIONS": "--require ./x.js"},
        },
    ],
    ids=["other-command", "other-args", "extra-key"],
)
def test_a_server_named_like_a_browser_server_needs_a_shell_tool(server: dict[str, Any]) -> None:
    inv = _resolve(_definition(["Read"], {"playwright": server}), agent_type="qa")
    assert inv.mcp_servers == {}, f"a server named like embry0's but not its own reached the agent: {inv.mcp_servers}"
