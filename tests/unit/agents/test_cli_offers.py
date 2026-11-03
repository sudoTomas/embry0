"""What the CLI offers an agent, measured on the CLI the SDK bundles.

MCP servers: build_sdk_options hands the CLI the invocation's servers, which
the resolver took from the agent's definition (embry0.agents.mcp_servers).
The CLI also starts, with no tool call and whatever the agent's tools, the
servers named by the workspace's ``.mcp.json`` and by a plugin (a mounted
skill source). Each server here touches a file when it starts.

Tools: an agent whose tool list is empty has no tools. Without ``--tools ""``
the CLI offers every tool it has.

The tests run the bundled CLI with build_sdk_options' flags in a project,
against a stand-in for the Messages API on localhost.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from embry0.agents.config_builder import build_sdk_options
from embry0.agents.invocation import AgentInvocation
from embry0.safety.policy import default_policy_for_agent

_MODEL = "claude-sonnet-4-6"


def _bundled_cli() -> Path:
    import claude_agent_sdk

    return Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not os.access(_bundled_cli(), os.X_OK),
    reason="needs Linux and the CLI bundled with claude_agent_sdk",
)


class _Api(BaseHTTPRequestHandler):
    """Answers every Messages API request with the text "ok"."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        usage = {"input_tokens": 1, "output_tokens": 1}
        message = {"id": "msg_x", "type": "message", "role": "assistant", "model": body.get("model", _MODEL)}
        if not body.get("stream"):
            data = json.dumps(
                {**message, "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": usage}
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        events: list[tuple[str, dict[str, Any]]] = [
            ("message_start", {"message": {**message, "content": [], "stop_reason": None, "usage": usage}}),
            ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "ok"}}),
            ("content_block_stop", {"index": 0}),
            ("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}),
            ("message_stop", {}),
        ]
        for name, data in events:
            chunk = f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n".encode()
            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


@pytest.fixture
def api_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _server(marker: Path) -> dict[str, Any]:
    """A stdio server that touches ``marker`` when it starts."""
    return {"type": "stdio", "command": "sh", "args": ["-c", f"touch {marker}; exec sleep 5"]}


def _setup(root: Path) -> tuple[Path, Path, AgentInvocation]:
    """A project whose .mcp.json names a server, a plugin that names one, and an invocation with one of its own."""
    project, home, plugin = root / "project", root / "home", root / "plugin"
    (project / ".claude").mkdir(parents=True)
    home.mkdir()
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"repo": _server(root / "workspace")}}))
    (project / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"deny": []}}))
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "probe", "version": "0.0.1"}))
    (plugin / "skills" / "hello").mkdir(parents=True)
    (plugin / "skills" / "hello" / "SKILL.md").write_text(
        "---\nname: hello\ndescription: Says hello.\n---\nSay hello.\n"
    )
    (plugin / ".mcp.json").write_text(json.dumps({"mcpServers": {"bundled": _server(root / "plugin-server")}}))
    invocation = AgentInvocation(
        agent_type="triage",
        prompt="probe",
        system_prompt="",
        system_context="",
        model=_MODEL,
        tools=["Read", "Glob", "Grep"],
        skills=["probe:hello"],
        mcp_servers={"given": _server(root / "given")},
        max_turns=2,
        timeout_seconds=60,
        execution_mode="sdk",
        auth_mode="api_key",
        safety_policy=default_policy_for_agent("triage"),
        channel_config=None,
        skill_mounts=({"source_id": "s1", "plugin_name": "probe", "container_path": str(plugin)},),
    )
    return project, home, invocation


def _run_cli(
    api_url: str, project: Path, home: Path, invocation: AgentInvocation, *, strict: bool, tools: list[str] | None
) -> str:
    """The bundled CLI with the MCP, plugin, tool and permission flags the SDK builds from build_sdk_options.

    ``strict`` and ``tools`` are passed as given (``--strict-mcp-config``,
    ``--tools``), so a test can run the CLI with and without them.
    """
    options = build_sdk_options(invocation)
    assert options.permission_mode == "bypassPermissions" and options.setting_sources == ["project"]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "ANTHROPIC_BASE_URL": api_url,
        "ANTHROPIC_API_KEY": "sk-ant-api03-mcp-sources-not-a-key",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        # The stand-in servers never answer the handshake: give up on them soon.
        "MCP_TIMEOUT": "2000",
        **(options.env or {}),
    }
    cmd = [
        str(_bundled_cli()),
        "-p",
        "probe",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        _MODEL,
        "--max-turns",
        "2",
        "--permission-mode",
        str(options.permission_mode),
        "--setting-sources",
        ",".join(options.setting_sources or []),
        "--mcp-config",
        json.dumps({"mcpServers": options.mcp_servers}),
    ]
    if options.allowed_tools:  # the SDK passes no flag for an empty list
        cmd += ["--allowedTools", ",".join(options.allowed_tools)]
    for plugin in options.plugins:
        cmd += ["--plugin-dir", str(plugin["path"])]
    if strict:
        cmd.append("--strict-mcp-config")
    if tools is not None:
        cmd += ["--tools", ",".join(tools)]
    out = subprocess.run(
        cmd, cwd=project, env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL
    )
    assert out.returncode == 0, f"the CLI failed: {out.stderr[-2000:]}"
    return out.stdout


def _init(stdout: str) -> dict[str, Any]:
    """The CLI's init event: what the session starts with."""
    for line in stdout.splitlines():
        event = json.loads(line) if line.startswith("{") else {}
        if event.get("type") == "system" and event.get("subtype") == "init":
            return dict(event)
    raise AssertionError(f"the CLI reported no init event: {stdout[-1000:]}")


def _skills(stdout: str) -> list[str]:
    return [str(s) for s in _init(stdout).get("skills") or []]


def test_only_the_invocations_server_starts(api_url: str, tmp_path: Path) -> None:
    project, home, invocation = _setup(tmp_path)
    options = build_sdk_options(invocation)
    stdout = _run_cli(api_url, project, home, invocation, strict=bool(options.strict_mcp_config), tools=options.tools)

    assert (tmp_path / "given").exists(), "the invocation's own server must start"
    assert not (tmp_path / "workspace").exists(), "a server the workspace's .mcp.json names started"
    assert not (tmp_path / "plugin-server").exists(), "a server a plugin names started"
    assert "probe:hello" in _skills(stdout), "the plugin's skills must still load"


def test_the_cli_starts_the_other_servers_unless_told_not_to(api_url: str, tmp_path: Path) -> None:
    """Why the flag is needed, and proof that the check above can see a server start."""
    project, home, invocation = _setup(tmp_path)
    _run_cli(api_url, project, home, invocation, strict=False, tools=None)

    assert (tmp_path / "workspace").exists(), "the CLI no longer starts a workspace's .mcp.json server by itself"
    assert (tmp_path / "plugin-server").exists(), "the CLI no longer starts a plugin's server by itself"


def test_an_agent_with_no_tools_is_offered_none(api_url: str, tmp_path: Path) -> None:
    project, home, invocation = _setup(tmp_path)
    toolless = dataclasses.replace(invocation, tools=[], mcp_servers={}, skills=[], skill_mounts=())
    options = build_sdk_options(toolless)
    offered = _init(_run_cli(api_url, project, home, toolless, strict=True, tools=options.tools)).get("tools")
    assert offered == [], f"an agent with no tools was offered {offered}"


def test_the_cli_offers_its_tools_unless_told_not_to(api_url: str, tmp_path: Path) -> None:
    """Why the flag is needed, and proof that the check above can see a tool offered."""
    project, home, invocation = _setup(tmp_path)
    toolless = dataclasses.replace(invocation, tools=[], mcp_servers={}, skills=[], skill_mounts=())
    offered = _init(_run_cli(api_url, project, home, toolless, strict=True, tools=None)).get("tools") or []
    assert "Bash" in offered, f"the CLI no longer offers Bash by itself: {offered}"
