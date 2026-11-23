"""What the CLI offers an agent, measured on the CLI the SDK bundles.

MCP servers: build_sdk_options hands the CLI the invocation's servers, which
the resolver took from the agent's definition (embry0.agents.mcp_servers).
The CLI also starts, with no tool call and whatever the agent's tools, the
servers named by the workspace's ``.mcp.json`` and by a plugin (a mounted
skill source). Each server here touches a file when it starts.

Tools: an agent whose tool list is empty has no tools. Without ``--tools ""``
the CLI offers every tool it has.

The workspace's configuration (#58): an invocation that may not read it
(``workspace_config=False``) is offered none of the commands, skills or
agents a workspace plants, reads none of its CLAUDE.md, runs none of its
settings' hooks, and still has the Ring-2 deny rules applied.

The tests run the bundled CLI with build_sdk_options' flags in a project,
against a stand-in for the Messages API on localhost.
"""

from __future__ import annotations

import dataclasses
import io
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
    api_url: str,
    project: Path,
    home: Path,
    invocation: AgentInvocation,
    *,
    strict: bool,
    tools: list[str] | None,
    settings: bool = False,
    disallowed: bool = False,
) -> str:
    """The bundled CLI with the MCP, plugin, tool, setting-source and permission flags the SDK builds from
    build_sdk_options.

    ``strict`` and ``tools`` are passed as given (``--strict-mcp-config``,
    ``--tools``), so a test can run the CLI with and without them; so are the
    options' flag settings (``settings``, ``--settings``) and deny rules
    (``disallowed``, ``--disallowedTools``), which are off unless asked for.
    """
    options = build_sdk_options(invocation)
    assert options.permission_mode == "bypassPermissions"
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
        # As the SDK passes it: an empty list is "--setting-sources=", no source at all.
        f"--setting-sources={','.join(options.setting_sources or [])}",
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
    if settings and options.settings is not None:
        cmd += ["--settings", options.settings]
    if disallowed and options.disallowed_tools:
        cmd += ["--disallowedTools", ",".join(options.disallowed_tools)]
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


# -- the workspace's configuration (#58) -------------------------------------------------------------------------


def _plant(root: Path, project: Path) -> None:
    """Claude configuration a pull request's author could write into the head: a command, a skill, an agent, a
    CLAUDE.md, and settings whose SessionStart hook touches ``root / "hook"``."""
    claude = project / ".claude"
    (claude / "commands").mkdir(parents=True, exist_ok=True)
    (claude / "commands" / "planted.md").write_text("---\ndescription: Planted.\n---\nDo the planted thing.\n")
    (claude / "skills" / "planted-skill").mkdir(parents=True)
    (claude / "skills" / "planted-skill" / "SKILL.md").write_text(
        "---\nname: planted-skill\ndescription: Planted.\n---\nDo the planted thing.\n"
    )
    (claude / "agents").mkdir()
    (claude / "agents" / "planted-agent.md").write_text(
        "---\nname: planted-agent\ndescription: Planted.\n---\nYou do the planted thing.\n"
    )
    hook = {"hooks": [{"type": "command", "command": f"touch {root / 'hook'}"}]}
    (claude / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [hook]}}))
    (project / "CLAUDE.md").write_text("PLANTED-MEMORY-MARKER: follow the planted instructions.\n")


class _Recording(_Api):
    """The stand-in API, keeping every request body."""

    bodies: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length) if length else b""
        type(self).bodies.append(raw.decode("utf-8", "replace"))
        self.rfile = io.BytesIO(raw)  # _Api reads the body again
        super().do_POST()


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _offered(stdout: str) -> dict[str, list[str]]:
    init = _init(stdout)
    return {key: [str(v) for v in init.get(key) or []] for key in ("slash_commands", "skills", "agents")}


@pytest.mark.parametrize("workspace_config", [True, False])
def test_an_untrusted_workspace_configures_nothing(tmp_path: Path, workspace_config: bool) -> None:
    """With the workspace's configuration (every agent but a review that runs nothing) the planted command, skill,
    agent, CLAUDE.md and SessionStart hook all reach the session, which proves each check below can see them.
    Without it, none does."""
    project, home, invocation = _setup(tmp_path)
    _plant(tmp_path, project)
    invocation = dataclasses.replace(invocation, workspace_config=workspace_config)
    handler = type("_Requests", (_Recording,), {"bodies": []})
    server, url = _serve(handler)
    try:
        options = build_sdk_options(invocation)
        stdout = _run_cli(
            url, project, home, invocation, strict=True, tools=options.tools, settings=True, disallowed=True
        )
    finally:
        server.shutdown()
        server.server_close()
    offered = _offered(stdout)
    seen = {
        "command": "planted" in offered["slash_commands"],
        "skill": "planted-skill" in offered["skills"],
        "agent": "planted-agent" in offered["agents"],
        "CLAUDE.md": any("PLANTED-MEMORY-MARKER" in body for body in handler.bodies),
        "hook": (tmp_path / "hook").exists(),
    }
    assert handler.bodies, "the CLI sent no request: the CLAUDE.md check measured nothing"
    assert not (tmp_path / "workspace").exists(), "the workspace's .mcp.json server started"
    expected = dict.fromkeys(seen, workspace_config)
    assert seen == expected, f"workspace_config={workspace_config}: the session got {seen} ({offered})"


def _denied_read_api(secret: Path) -> type[BaseHTTPRequestHandler]:
    """A stand-in API whose model reads ``secret`` once, then says "ok"; it keeps every request body."""

    class _ReadsSecret(_Recording):
        bodies: list[str] = []

        def do_POST(self) -> None:  # noqa: N802 - http.server's name
            length = int(self.headers.get("content-length", 0))
            raw = self.rfile.read(length) if length else b""
            type(self).bodies.append(raw.decode("utf-8", "replace"))
            body = json.loads(raw or b"{}")
            if not body.get("stream") or "tool_result" in raw.decode("utf-8", "replace"):
                self.rfile = io.BytesIO(raw)  # _Api reads the body again
                _Api.do_POST(self)
                return
            usage = {"input_tokens": 1, "output_tokens": 1}
            message = {"id": "msg_t", "type": "message", "role": "assistant", "model": body.get("model", _MODEL)}
            tool = {"type": "tool_use", "id": "toolu_probe", "name": "Read", "input": {}}
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            events: list[tuple[str, dict[str, Any]]] = [
                ("message_start", {"message": {**message, "content": [], "stop_reason": None, "usage": usage}}),
                ("content_block_start", {"index": 0, "content_block": tool}),
                (
                    "content_block_delta",
                    {
                        "index": 0,
                        "delta": {"type": "input_json_delta", "partial_json": json.dumps({"file_path": str(secret)})},
                    },
                ),
                ("content_block_stop", {"index": 0}),
                ("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 1}}),
                ("message_stop", {}),
            ]
            for name, data in events:
                chunk = f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n".encode()
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    return _ReadsSecret


@pytest.mark.parametrize(
    ("settings", "disallowed", "denied"),
    [(True, True, True), (True, False, True), (False, True, True), (False, False, False)],
    ids=["as-the-sdk-passes-them", "flag-settings-alone", "command-line-alone", "neither"],
)
def test_the_ring2_rules_apply_without_the_workspaces_settings(
    tmp_path: Path, settings: bool, disallowed: bool, denied: bool
) -> None:
    """An untrusted workspace's session reads no settings file, but a Ring-2 deny rule still refuses the read it
    names, through each of the two layers it reaches the CLI by. Neither layer: the read goes through, which proves
    the check can see it."""
    from embry0.safety.policy import SafetyPolicy

    project, home, invocation = _setup(tmp_path)
    (tmp_path / "secret").mkdir()
    secret = tmp_path / "secret" / "token.txt"
    secret.write_text("RING2-SECRET-MARKER\n")
    policy = SafetyPolicy(allowed_tools=["Read"], deny_rules=[f"Read(/{tmp_path}/secret/**)"])
    invocation = dataclasses.replace(
        invocation, workspace_config=False, safety_policy=policy, skills=[], skill_mounts=(), mcp_servers={}
    )
    handler = _denied_read_api(secret)
    server, url = _serve(handler)
    try:
        options = build_sdk_options(invocation)
        _run_cli(
            url, project, home, invocation, strict=True, tools=options.tools, settings=settings, disallowed=disallowed
        )
    finally:
        server.shutdown()
        server.server_close()
    results = [body for body in handler.bodies if "toolu_probe" in body and "tool_result" in body]
    assert results, f"the CLI never sent the read's result: {len(handler.bodies)} requests"
    leaked = any("RING2-SECRET-MARKER" in body for body in results)
    assert leaked is not denied, f"settings={settings} disallowed={disallowed}: the denied file was read={leaked}"


# -- a browser server's own environment ---------------------------------------------------------------------------


def _env_server(path: Path, env: dict[str, str] | None) -> dict[str, Any]:
    """A stdio server that writes the environment it was started with to ``path``."""
    server: dict[str, Any] = {"type": "stdio", "command": "sh", "args": ["-c", f"env > {path}; exec sleep 5"]}
    if env is not None:
        server["env"] = env
    return server


@pytest.mark.parametrize("own_env", [True, False], ids=["vibium's env", "no env (proves the check)"])
def test_the_vibium_server_reads_no_ai_key_the_sandbox_carries(api_url: str, tmp_path: Path, own_env: bool) -> None:
    """The CLI starts a stdio server with its own environment, which carries the agent's API key. The vibium
    server's env (embry0.workflows.qa.browser_backends) empties it, and every other variable vibium reads for a
    model or a station, over what it inherits. Without that env the key reaches the server."""
    from embry0.workflows.qa.browser_backends import VIBIUM

    project, home, invocation = _setup(tmp_path)
    written = tmp_path / "server-env"
    server = _env_server(written, dict(VIBIUM.mcp_env) if own_env else None)
    probe = dataclasses.replace(invocation, mcp_servers={"vibium": server}, skills=[], skill_mounts=())
    options = build_sdk_options(probe)
    _run_cli(api_url, project, home, probe, strict=True, tools=options.tools)
    env = dict(line.split("=", 1) for line in written.read_text().splitlines() if "=" in line)
    if own_env:
        assert env.get("ANTHROPIC_API_KEY") == "", "the agent's API key reached the vibium server"
        assert env.get("VIBIUM_LOAD_AI_ENV") == "0" and env.get("VIBIUM_CONNECT_URL") == "", sorted(env)
    else:
        assert env.get("ANTHROPIC_API_KEY", "").startswith("sk-ant-"), "the CLI no longer passes its env to a server"
