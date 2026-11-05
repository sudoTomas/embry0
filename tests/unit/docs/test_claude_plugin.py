"""The Claude Code plugin in plugins/embry0 and its marketplace stay in step with the code they drive.

The plugin is JSON and Markdown, so nothing fails when it drifts: a renamed
option leaves the MCP server with an empty header, a moved route sends the
install skill's key request to a 404, and a setup skill copied once falls
behind the prompt. These tests pin each of those, and run the install skill's
four scripts for real: the network check, the one that writes ``.env``, the
ready check and the one that mints the plugin's API key and hands it to the
plugin's options (against a local stand-in for the deployment and a stand-in
``claude``). The connect skill's terminal recipe runs for real too.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from embry0.api.deps import bust_api_key_cache, bust_user_gate_cache
from embry0.api.v1.api_keys import CreateKeyBody
from embry0.mcp_server import server
from embry0.mcp_server.setup_prompt import SETUP_PROMPT
from tests.unit.api.v1.test_api_keys_api import XRW, _app

ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN = ROOT / "plugins" / "embry0"
SKILLS = PLUGIN / "skills"
PLUGIN_README = PLUGIN / "README.md"
GUIDE = ROOT / "docs" / "claude-code.md"
REPO_SKILL = ROOT / ".claude" / "skills" / "setup-embry0" / "SKILL.md"

GENERATED = [
    "API_KEY",
    "PROXY_ADMIN_TOKEN",
    "ENVIRONMENT_SECRET_KEY",
    "POSTGRES_PASSWORD",
    "MINIO_ROOT_PASSWORD",
    "GITHUB_WEBHOOK_SECRET",
]
EMPTIED = ["GITHUB_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"]
PLUGIN_ID = "embry0@embry0"
# `claude plugin configure` and its --values-stdin, which the install and connect skills use.
MIN_CLAUDE_CODE = "2.1.285"


def _json(path: Path) -> Any:
    return json.loads(path.read_text())


def _skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text()


def _heredocs(text: str) -> list[str]:
    """The Python scripts a skill runs: the bodies of its ``python3 - [args] <<'PY'`` heredocs."""
    return re.findall(r"python3 -(?: (?!<<)\S+)* <<'PY'(?: \|[^\n]*)?\n(.*?)\nPY\n", text, flags=re.S)


def _install_script(marker: str) -> str:
    """The one install-skill script that contains ``marker``."""
    found = [script for script in _heredocs(_skill("install")) if marker in script]
    assert len(found) == 1, f"{len(found)} install scripts contain {marker!r}"
    return found[0]


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)\n```", text, flags=re.S)


def _tool_prefix() -> str:
    """Claude Code names a plugin's MCP tools after the plugin, then the server in its .mcp.json."""
    plugin = _json(PLUGIN / ".claude-plugin" / "plugin.json")["name"]
    (server_name,) = _json(PLUGIN / ".mcp.json")["mcpServers"]
    return f"mcp__plugin_{plugin}_{server_name}__"


def test_the_marketplace_lists_the_plugin_by_its_own_manifest() -> None:
    market = _json(MARKETPLACE)
    plugin = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    assert market["name"] == "embry0"
    entries = {entry["name"]: entry for entry in market["plugins"]}
    assert list(entries) == ["embry0"], f"marketplace plugins: {sorted(entries)}"
    entry = entries["embry0"]
    # Claude Code refuses a relative source that does not start with ./
    assert entry["source"].startswith("./"), entry["source"]
    assert (ROOT / entry["source"]).resolve() == PLUGIN.resolve(), entry["source"]
    assert plugin["name"] == entry["name"]
    assert plugin["version"] == entry["version"], "plugin.json and the marketplace entry disagree on the version"
    assert plugin["description"] == entry["description"]


def test_the_options_are_the_url_and_a_sensitive_key() -> None:
    options = _json(PLUGIN / ".claude-plugin" / "plugin.json")["userConfig"]
    assert sorted(options) == ["api_key", "url"]
    assert options["api_key"].get("sensitive") is True, (
        "the API key must be a sensitive option (masked, secure storage)"
    )
    assert not options["url"].get("sensitive"), "the URL is not a secret"
    assert "https" in options["url"]["description"], "a remote origin must be https: the key is a bearer"
    port = re.search(r"(?m)^PROD_PORT=(\d+)$", (ROOT / ".env.example").read_text())
    assert port, "no PROD_PORT in .env.example"
    assert options["url"]["default"] == f"http://localhost:{port.group(1)}"


def test_the_privacy_link_is_the_readme_section() -> None:
    manifest = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    url = manifest["privacyPolicyUrl"]
    assert url == f"{manifest['repository']}/blob/main/plugins/embry0/README.md#privacy", url
    assert "\n## Privacy\n" in PLUGIN_README.read_text()


def test_the_mcp_server_is_the_mcp_endpoint_with_the_options() -> None:
    servers = _json(PLUGIN / ".mcp.json")["mcpServers"]
    assert list(servers) == ["embry0"]
    embry0 = servers["embry0"]
    assert embry0["type"] == "http"
    assert embry0["url"] == "${user_config.url}/mcp"
    assert embry0["headers"] == {"Authorization": "Bearer ${user_config.api_key}"}
    for conf in ("frontend/nginx.conf", "frontend/nginx/dashboard-public.conf.template"):
        assert "location /mcp {" in (ROOT / conf).read_text(), f"{conf} no longer serves /mcp"


def test_the_docs_name_the_tools_by_the_prefix_claude_code_gives_them() -> None:
    prefix = _tool_prefix()
    assert f"`{prefix}`" in _skill("connect")
    assert "the plugin name, then the server name" in _skill("connect")
    assert f"`{prefix}<tool>`" in GUIDE.read_text()
    assert f"`{prefix}*`" in REPO_SKILL.read_text()
    for doc in (REPO_SKILL, GUIDE):
        assert f"`/{prefix}setup`" in doc.read_text(), f"{doc}: the plugin's setup prompt"


def test_each_skill_is_named_for_its_folder() -> None:
    names = sorted(path.name for path in SKILLS.iterdir() if path.is_dir())
    assert names == ["connect", "install", "setup"]
    for name in names:
        text = _skill(name)
        assert text.startswith(f"---\nname: {name}\ndescription: "), f"{name}: frontmatter"
        assert re.search(r"(?m)^description: \S.{40,}$", text), f"{name}: no description"


def test_the_setup_skill_carries_the_prompt_procedure_whole() -> None:
    text = _skill("setup")
    assert text.endswith(SETUP_PROMPT), "the setup skill's procedure differs from SETUP_PROMPT: copy it again"


def test_the_checkout_skill_knows_the_plugin() -> None:
    """A session in an embry0 checkout with the plugin on must not report the plugin's tools as missing."""
    text = REPO_SKILL.read_text()
    assert "`mcp__embry0__*`" in text, "the server added by hand"
    assert "/embry0:connect" in text and "/embry0:install" in text
    guide = GUIDE.read_text().split("## In an embry0 checkout", 1)[1]
    assert "/embry0:connect" in guide and "/embry0:install" in guide


@pytest.mark.asyncio
async def test_the_connect_skill_calls_registered_tools() -> None:
    tools = {tool.name for tool in await server.mcp.list_tools()}
    named = set(re.findall(r"call `([a-z_]+)`, then `([a-z_]+)`", _skill("connect"))[0])
    assert named == {"health", "stats"}
    assert named <= tools, f"not registered: {sorted(named - tools)}"


def test_the_connect_skill_reads_an_unset_key_from_the_401() -> None:
    """The server answers 401 without a non-empty bearer, so an unset key shows as a failed connection."""
    http = (ROOT / "embry0" / "mcp_server" / "http.py").read_text()
    assert 'if not auth.lower().startswith("bearer ") or not auth[7:].strip():' in http
    text = _skill("connect")
    assert "lists its tools for any non-empty key" in text
    rows = [line for line in text.splitlines() if line.startswith("| Failed to connect")]
    assert any("HTTP 401" in row and "unset or empty" in row for row in rows), rows
    assert any("ECONNREFUSED" in row for row in rows), rows
    assert any("HTTP 502" in row and "`mcp` service" in row for row in rows), rows
    # A server added by hand at the plugin's URL hides the plugin's: Claude Code skips duplicates.
    assert "| Only a hand-added embry0 server is listed, no `plugin:embry0:embry0` |" in text
    for doc in (PLUGIN_README, GUIDE):
        assert "At the same URL, Claude Code skips the plugin's server" in doc.read_text(), doc


def test_the_privacy_section_names_what_install_downloads() -> None:
    privacy = PLUGIN_README.read_text().split("\n## Privacy\n", 1)[1]
    assert "MCP server talks only to the embry0 deployment" in privacy
    assert "downloads embry0's source, its Python packages and its base images" in privacy


def test_no_skill_takes_a_secret_in_the_chat() -> None:
    assert "never ask the person to type one here" in _skill("install")
    assert "Never ask for the key in this chat" in _skill("connect")
    assert "Never ask for a secret in this chat" in _skill("setup")
    for name in ("install", "connect", "setup"):
        assert "transcript" in _skill(name), f"{name} does not say why"


def test_no_plugin_command_puts_the_key_on_a_command_line() -> None:
    """A key in a command's arguments shows in ps and /proc to every local user while it runs.

    Only shell builtins may expand it: ``read`` and ``unset`` name it, and ``printf`` (a bash
    builtin, no new process) writes it into a pipe.
    """
    texts = [*(p.read_text() for p in SKILLS.glob("*/SKILL.md")), PLUGIN_README.read_text()]
    texts.append(GUIDE.read_text().split("## Connect without the plugin", 1)[0])
    expansions = 0
    for block in (b for text in texts for b in _bash_blocks(text)):
        for line in block.splitlines():
            for match in re.finditer(r"\$\{?EMBRY0_API_KEY\b", line):
                expansions += 1
                assert line[: match.start()].endswith("printf '%s' \""), f"the key is an argument: {line}"
    assert expansions >= 1, "the terminal recipe no longer reads the key: update this test"


def _fake_claude(tmp_path: Path, fail: str | None = None) -> tuple[dict[str, str], Path]:
    """A ``claude`` on PATH that records each call (args, stdin, cwd).

    ``fail``: ``check`` or ``save`` exits 1 on that call; ``hang`` never answers the save.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "claude-calls.jsonl"
    fake = bin_dir / "claude"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys, time\n"
        "args = sys.argv[1:]\n"
        "save = '--values-stdin' in args\n"
        "stdin = sys.stdin.read() if save else None\n"
        f"open({str(calls)!r}, 'a').write(json.dumps({{'args': args, 'stdin': stdin, 'cwd': os.getcwd()}}) + '\\n')\n"
        f"fail = {fail!r}\n"
        "if save and fail == 'hang':\n"
        "    time.sleep(30)\n"
        "if args[:2] != ['plugin', 'configure'] or fail == ('save' if save else 'check'):\n"
        "    print('No installed plugin has the id', file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )
    fake.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, calls


def _calls(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("key", ["canary-key", ""], ids=["a key", "an empty answer"])
def test_the_connect_recipe_hands_the_key_to_the_options_through_stdin(tmp_path: Path, key: str) -> None:
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed")
    (recipe,) = [b for b in _bash_blocks(_skill("connect")) if "--values-stdin" in b]
    assert recipe.splitlines()[0] == "read -rs EMBRY0_API_KEY"
    env, calls = _fake_claude(tmp_path)
    script = "\n".join(recipe.splitlines()[1:])  # the key comes from the environment, not a terminal
    run = subprocess.run(
        ["bash", "-c", script],
        env={**env, "EMBRY0_API_KEY": key},
        capture_output=True,
        text=True,
        check=False,
    )
    # An empty answer (Enter alone) must reach Claude Code as "", not stop jq with an error.
    assert run.returncode == 0, run.stderr
    (call,) = _calls(calls)
    assert call["args"] == ["plugin", "configure", PLUGIN_ID, "--values-stdin"]
    assert json.loads(call["stdin"]) == {"url": "https://embry0.example.com", "api_key": key}
    if key:
        assert key not in run.stdout + run.stderr


def test_the_install_scripts_compile() -> None:
    scripts = _heredocs(_skill("install"))
    assert len(scripts) == 4, f"expected the network, .env, ready and key scripts, found {len(scripts)}"
    for script in scripts:
        compile(script, "install-skill", "exec")


def test_the_env_script_writes_the_secrets_and_never_prints_one(tmp_path: Path) -> None:
    env_script = _install_script(".env.example")
    (tmp_path / ".env.example").write_text((ROOT / ".env.example").read_text())
    run = subprocess.run([sys.executable, "-c", env_script], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    env = dict(re.findall(r"(?m)^([A-Z_]+)=(.*)$", (tmp_path / ".env").read_text()))
    for key in GENERATED:
        assert re.fullmatch(r"[0-9a-f]{64}", env[key]), f"{key} not generated"
        assert env[key] not in run.stdout + run.stderr, f"{key}'s value was printed"
    assert len({env[key] for key in GENERATED}) == len(GENERATED), "two secrets share a value"
    for key in EMPTIED:
        assert env[key] == "", f"{key} keeps the example value"
    assert stat.S_IMODE(os.stat(tmp_path / ".env").st_mode) == 0o600
    again = subprocess.run(
        [sys.executable, "-c", env_script], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert again.returncode != 0 and ".env exists" in again.stderr, "a second run must not overwrite .env"


def test_the_env_script_writes_the_port_the_person_picked(tmp_path: Path) -> None:
    env_script = _install_script(".env.example")
    (tmp_path / ".env.example").write_text((ROOT / ".env.example").read_text())
    bad = subprocess.run([sys.executable, "-c", env_script, "80a"], cwd=tmp_path, capture_output=True, text=True)
    assert bad.returncode != 0 and "not a port number" in bad.stderr
    assert not (tmp_path / ".env").exists(), "a bad port must stop the script before .env exists"
    run = subprocess.run([sys.executable, "-c", env_script, "8300"], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert re.findall(r"(?m)^PROD_PORT=.*$", (tmp_path / ".env").read_text()) == ["PROD_PORT=8300"]


class _Deployment(BaseHTTPRequestHandler):
    """A stand-in for the deployment: records each request, mints numbered keys, lists and revokes them, and answers
    the ready check from ``ready``."""

    requests: list[dict[str, Any]] = []
    ready: list[str] = []
    keys: list[dict[str, Any]] = []

    def _reply(self, code: int, payload: dict[str, Any]) -> None:
        reply = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def _record(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        seen = {"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body}
        _Deployment.requests.append(seen)
        return seen

    def do_GET(self) -> None:  # noqa: N802 (http.server's name)
        self._record()
        if self.path == "/api/v1/apikeys":
            self._reply(200, {"keys": [{k: v for k, v in key.items() if k != "key"} for key in _Deployment.keys]})
            return
        status = _Deployment.ready.pop(0) if _Deployment.ready else "ok"
        self._reply(200, {"status": status, "checks": {"database": "ok" if status == "ok" else "not_connected"}})

    def do_POST(self) -> None:  # noqa: N802
        seen = self._record()
        n = len(_Deployment.keys) + 1
        key = {
            "id": f"key-{n}",
            "name": seen["body"]["name"],
            "scope": "admin",
            "revoked_at": None,
            "key": f"minted-key-{n}",
        }
        _Deployment.keys.append(key)
        self._reply(201, key)

    def do_DELETE(self) -> None:  # noqa: N802
        self._record()
        key = next(k for k in _Deployment.keys if self.path == f"/api/v1/apikeys/{k['id']}")
        key["revoked_at"] = "2026-01-01T00:00:00+00:00"
        self._reply(200, {k: v for k, v in key.items() if k != "key"})

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def deployment() -> Iterator[int]:
    """The stand-in's port, with its records cleared."""
    _Deployment.requests = []
    _Deployment.ready = []
    _Deployment.keys = []
    httpd = HTTPServer(("127.0.0.1", 0), _Deployment)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


# An existing .env the person keeps: compose reads `export` lines, quoted values and a ` #` comment after an
# unquoted value, so the scripts must too.
_KEPT_ENV = "export API_KEY=\"master-secret\"\nPROD_PORT='127.0.0.1:{port}'\n"
_COMMENTED_ENV = "API_KEY=master-secret # the master key\nPROD_PORT=127.0.0.1:{port}   # the dashboard\n"
KEY_NAME = re.compile(r"claude-code-[A-Za-z0-9.-]+")


def _methods() -> list[tuple[str, str]]:
    return [(r["method"], r["path"]) for r in _Deployment.requests]


def _run_key_script(
    tmp_path: Path, port: int, fail: str | None = None, env_text: str = _KEPT_ENV, script: str | None = None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the key script as the skill says: from the session's directory, with the checkout as an argument."""
    checkout, session = tmp_path / "checkout", tmp_path / "session"
    checkout.mkdir(exist_ok=True)
    session.mkdir(exist_ok=True)
    (checkout / ".env").write_text(env_text.format(port=port))
    env, calls = _fake_claude(tmp_path, fail)
    run = subprocess.run(
        [sys.executable, "-c", script or _install_script("/api/v1/apikeys"), PLUGIN_ID, str(checkout)],
        cwd=session,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return run, calls


@pytest.mark.parametrize("env_text", [_KEPT_ENV, _COMMENTED_ENV], ids=["export and quotes", "inline comments"])
def test_the_key_script_mints_an_admin_key_and_hands_it_to_the_options(
    tmp_path: Path, deployment: int, env_text: str
) -> None:
    run, calls = _run_key_script(tmp_path, deployment, env_text=env_text)
    assert run.returncode == 0, run.stderr
    assert "minted-key" not in run.stdout + run.stderr and "master-secret" not in run.stdout + run.stderr
    check, save = _calls(calls)
    assert check["args"] == ["plugin", "configure", PLUGIN_ID] and check["stdin"] is None, "the id is proved first"
    assert save["args"] == ["plugin", "configure", PLUGIN_ID, "--values-stdin"]
    assert json.loads(save["stdin"]) == {"url": f"http://localhost:{deployment}", "api_key": "minted-key-1"}
    # A plugin installed for a project is found only from that project: never from the checkout.
    assert {check["cwd"], save["cwd"]} == {str(tmp_path / "session")}
    assert _methods() == [("POST", "/api/v1/apikeys"), ("GET", "/api/v1/apikeys")], "nothing to revoke on a first run"
    mint = _Deployment.requests[0]
    assert mint["headers"]["Authorization"] == "Bearer master-secret"
    assert mint["headers"]["X-Requested-With"] == "XMLHttpRequest"
    body = mint["body"]
    assert set(body) <= set(CreateKeyBody.model_fields), (
        f"fields the route ignores: {set(body) - set(CreateKeyBody.model_fields)}"
    )
    assert KEY_NAME.fullmatch(body["name"]), body["name"]
    assert body["scope"] == "admin"
    assert body["expires_days"] == 365, "an admin key that never expires"

    # The real route takes that body from the master key and answers with the id and the key the script reads.
    bust_api_key_cache()
    bust_user_gate_cache()
    app = _app()
    answer = TestClient(app).post("/api/v1/apikeys", json=body, headers={"Authorization": "Bearer master-key", **XRW})
    assert answer.status_code == 201, answer.text
    assert answer.json()["key"] == "emb_plaintext-token"
    assert answer.json()["id"] == "key-abc123def456"
    assert app.state.api_keys_repo.create.await_args.kwargs["expires_at"] is not None
    listed = TestClient(app).get("/api/v1/apikeys", headers={"Authorization": "Bearer master-key", **XRW})
    assert listed.status_code == 200 and {"id", "name", "revoked_at"} <= set(listed.json()["keys"][0]), listed.text


def test_a_second_run_revokes_this_machines_earlier_key(tmp_path: Path, deployment: int) -> None:
    first, _ = _run_key_script(tmp_path, deployment)
    assert first.returncode == 0, first.stderr
    _Deployment.keys.append(
        {"id": "key-other", "name": "claude-code-another-host", "scope": "admin", "revoked_at": None}
    )
    _Deployment.requests = []
    again, _ = _run_key_script(tmp_path, deployment)
    assert again.returncode == 0, again.stderr
    assert _methods() == [("POST", "/api/v1/apikeys"), ("GET", "/api/v1/apikeys"), ("DELETE", "/api/v1/apikeys/key-1")]
    assert "key-1" in again.stdout, "say which earlier key was revoked"
    assert next(k for k in _Deployment.keys if k["id"] == "key-other")["revoked_at"] is None, "another machine's key"


def test_the_key_script_mints_nothing_when_the_plugin_id_is_wrong(tmp_path: Path, deployment: int) -> None:
    run, calls = _run_key_script(tmp_path, deployment, fail="check")
    assert run.returncode != 0
    assert "no key was minted" in run.stderr
    assert _Deployment.requests == [], "a key was minted that nobody can be given"
    assert len(_calls(calls)) == 1


def test_the_key_script_revokes_the_key_the_options_did_not_take(tmp_path: Path, deployment: int) -> None:
    run, _ = _run_key_script(tmp_path, deployment, fail="save")
    assert run.returncode != 0
    assert "revoked" in run.stderr and "key-1" in run.stderr
    assert "minted-key" not in run.stdout + run.stderr
    assert _methods() == [("POST", "/api/v1/apikeys"), ("DELETE", "/api/v1/apikeys/key-1")]
    assert _Deployment.requests[1]["headers"]["X-Requested-With"] == "XMLHttpRequest"


def test_the_key_script_revokes_the_key_when_the_save_hangs(tmp_path: Path, deployment: int) -> None:
    script = _install_script("/api/v1/apikeys")
    assert script.count("timeout=60") == 2, "both claude calls must have a timeout: update this test"
    run, _ = _run_key_script(tmp_path, deployment, fail="hang", script=script.replace("timeout=60", "timeout=2"))
    assert run.returncode != 0
    assert _methods() == [("POST", "/api/v1/apikeys"), ("DELETE", "/api/v1/apikeys/key-1")]
    assert "minted-key" not in run.stdout + run.stderr


def test_the_key_script_refuses_a_port_that_is_not_a_number(tmp_path: Path, deployment: int) -> None:
    run, calls = _run_key_script(tmp_path, deployment, env_text="API_KEY=master-secret\nPROD_PORT=eighty{port}\n")
    assert run.returncode != 0 and "not a port number" in run.stderr
    assert _Deployment.requests == [] and _calls(calls) == []


def test_the_key_script_needs_claude_on_the_path(tmp_path: Path, deployment: int) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".env").write_text(_KEPT_ENV.format(port=deployment))
    run = subprocess.run(
        [sys.executable, "-c", _install_script("/api/v1/apikeys"), PLUGIN_ID, str(checkout)],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run.returncode != 0 and "claude" in run.stderr
    assert _Deployment.requests == []


def test_the_key_and_health_routes_are_where_the_scripts_call() -> None:
    routes = (ROOT / "embry0" / "api" / "v1" / "api_keys.py").read_text()
    assert '@router.post("/apikeys", status_code=201)' in routes
    assert '@router.delete("/apikeys/{key_id}")' in routes
    health = (ROOT / "embry0" / "api" / "v1" / "health.py").read_text()
    assert '@router.get("/health/ready")' in health
    app = (ROOT / "embry0" / "api" / "app.py").read_text()
    assert 'app.include_router(api_keys.router, prefix="/api/v1"' in app
    assert 'app.include_router(health.router, prefix="/api/v1"' in app


def _ready_script() -> str:
    """The ready check, with its wait cut to nothing for the test."""
    script = _install_script("/health/ready")
    for slow, fast in (("range(60)", "range(3)"), ("time.sleep(5)", "time.sleep(0)")):
        assert script.count(slow) == 1, f"the ready check no longer has {slow}: update this test"
        script = script.replace(slow, fast)
    return script


@pytest.mark.parametrize("env_text", [_KEPT_ENV, _COMMENTED_ENV], ids=["export and quotes", "inline comments"])
def test_the_ready_check_waits_for_ok_not_just_an_answer(tmp_path: Path, deployment: int, env_text: str) -> None:
    assert "When it has exited 0, wait for the stack to be ready" in _skill("install"), (
        "the first build outlasts the wait"
    )
    (tmp_path / ".env").write_text(env_text.format(port=deployment))
    _Deployment.ready = ["degraded", "degraded"]
    run = subprocess.run(
        [sys.executable, "-c", _ready_script()], cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False
    )
    assert run.returncode == 0, run.stderr
    assert [r["path"] for r in _Deployment.requests] == ["/api/v1/health/ready"] * 3


def test_the_ready_check_fails_on_a_degraded_stack(tmp_path: Path, deployment: int) -> None:
    (tmp_path / ".env").write_text(_KEPT_ENV.format(port=deployment))
    _Deployment.ready = ["degraded"] * 3
    run = subprocess.run(
        [sys.executable, "-c", _ready_script()], cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False
    )
    assert run.returncode != 0
    assert "not_connected" in run.stderr, run.stderr


def test_the_example_env_has_every_line_the_script_edits() -> None:
    example = (ROOT / ".env.example").read_text()
    for key in GENERATED + EMPTIED:
        assert len(re.findall(rf"(?m)^{key}=", example)) == 1, f"{key}: not exactly one line in .env.example"


def test_the_host_checks_match_the_quick_start() -> None:
    readme = (ROOT / "README.md").read_text().split("## Quick start", 1)[1]
    assert "Python 3.12+, on an x86_64 Linux host" in readme and "about 20 GB of disk" in readme
    text = _skill("install").split("## 2.", 1)[1].split("## 3.", 1)[0]
    assert "Stop unless `uname -sm` prints `Linux x86_64`" in text
    assert "3.12 or newer" in text and "20 GB" in text
    assert "python3 -c 'import venv, ensurepip'" in text, "Debian and Ubuntu need python3-venv"
    assert "ss -ltn" in text, "the dashboard port must be free before the build"
    assert "claude --version" in text and MIN_CLAUDE_CODE in text
    assert "{{.DockerRootDir}}" in text, "the images land on Docker's root directory, not on $HOME"


def test_the_docs_state_the_claude_code_version_the_plugin_needs() -> None:
    for doc in (PLUGIN_README, GUIDE):
        text = doc.read_text()
        assert f"Claude Code {MIN_CLAUDE_CODE} or newer" in text, doc
        assert "older than" not in text, f"{doc}: an older Claude Code cannot run the plugin's commands"


def _fake_docker(tmp_path: Path, subnets: dict[str, str]) -> dict[str, str]:
    """A ``docker`` on PATH that lists ``subnets`` (network name -> subnet) and nothing else."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    nets = [{"Name": name, "IPAM": {"Config": [{"Subnet": subnet}]}} for name, subnet in subnets.items()]
    fake = bin_dir / "docker"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"nets = {nets!r}\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['network', 'ls']:\n"
        "    print('\\n'.join(n['Name'] for n in nets))\n"
        "elif args[:2] == ['network', 'inspect']:\n"
        "    print(json.dumps([n for n in nets if n['Name'] in args[2:]]))\n"
        "else:\n"
        "    sys.exit(2)\n"
    )
    fake.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


@pytest.mark.parametrize(
    ("subnets", "clash"),
    [
        ({"bridge": "172.17.0.0/16", "embry0_frontend": "172.29.0.0/16"}, False),  # the stack's own: not a clash
        ({"bridge": "172.17.0.0/16", "accounts": "172.29.240.0/24"}, True),
    ],
)
def test_the_network_check_finds_a_taken_range_before_the_build(
    tmp_path: Path, subnets: dict[str, str], clash: bool
) -> None:
    net_script = _install_script("docker")
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "docker-compose.yml").write_text((ROOT / "infra" / "docker-compose.yml").read_text())
    env = _fake_docker(tmp_path, subnets)
    run = subprocess.run(
        [sys.executable, "-c", net_script], cwd=tmp_path, env=env, capture_output=True, text=True, check=False
    )
    assert run.returncode == 0, run.stderr
    if clash:
        assert "overlaps 172.29.240.0/24 (network accounts)" in run.stdout, run.stdout
    else:
        assert run.stdout.startswith("free: "), run.stdout


def test_the_range_the_skill_names_is_the_compose_one() -> None:
    compose = (ROOT / "infra" / "docker-compose.yml").read_text()
    subnets = re.findall(r"subnet: ([0-9./]+)", compose)
    assert subnets == ["172.29.0.0/16"], f"the stack's pinned subnets changed: {subnets}"
    assert "every `172.29.0.` in `infra/docker-compose.yml`" in _skill("install")
    assert "and in `infra/docker-compose.rootless.yml`" in _skill("install")
    lines = [line for line in compose.splitlines() if "172.29.0." in line]
    assert len(lines) == 4, f"the skill names four places in the compose file, found {len(lines)}"
    rootless = (ROOT / "infra" / "docker-compose.rootless.yml").read_text()
    assert "172.29.0." in rootless, "the rootless overlay no longer carries the range: drop it from the skill"
