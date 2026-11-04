"""The guide to connecting Claude Code (docs/claude-code.md) keeps the API key off every command line (#376).

While a command runs, every local user can read its arguments (``ps``, ``/proc/<pid>/cmdline``), so no documented
command may take the key as one. A static check pins the exact lines that may touch the key, its file, its folder or
the helper, with a refusing case for each form an independent review built to get past earlier versions; and the
guide's recipes (the HTTP and stdio connections and the credential upload) run as written against stand-in
``claude``, ``curl`` and ``embry0-mcp``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs" / "claude-code.md"
REPO_SKILL = ROOT / ".claude" / "skills" / "setup-embry0" / "SKILL.md"
PLUGIN = ROOT / "plugins" / "embry0"


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)\n```", text, flags=re.S)


def _code(text: str) -> list[str]:
    """Every bash block of a page and every inline code span outside them."""
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    return [*_bash_blocks(text), *re.findall(r"`([^`\n]+)`", prose)]


def _guide_recipe(marker: str) -> str:
    """The one bash block of the guide that contains ``marker``."""
    found = [block for block in _bash_blocks(GUIDE.read_text()) if marker in block]
    assert len(found) == 1, f"{len(found)} guide recipes contain {marker!r}"
    return found[0]


def _calls(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


_ADD_JSON = (
    'claude mcp add-json --scope {scope} embry0 "$(jq -n --arg url "$EMBRY0_URL/mcp" --arg helper "$HELPER" '
    '\'{{type: "http", url: $url, headersHelper: $helper}}\')"'
)
_KEY_LINES = {
    "read -rs EMBRY0_API_KEY",
    "unset EMBRY0_API_KEY",
    # The plugin's connect recipe: the key from read into the plugin's sensitive option through stdin.
    "printf '%s' \"$EMBRY0_API_KEY\" | jq -Rs --arg url \"https://embry0.example.com\" '{url: $url, api_key: .}' | "
    "claude plugin configure embry0@embry0 --values-stdin",
    "mkdir -p ~/.config/embry0",
    "chmod 700 ~/.config/embry0",
    '[ -n "$EMBRY0_API_KEY" ] && rm -f ~/.config/embry0/api-key && '
    "(umask 077; printf '%s' \"$EMBRY0_API_KEY\" > ~/.config/embry0/api-key)",
    'HELPER="jq -Rs \'{Authorization: (\\"Bearer \\" + .)}\' ~/.config/embry0/api-key"',
    _ADD_JSON.format(scope="user"),
    _ADD_JSON.format(scope="local"),  # the project-only variant the guide describes in prose
    'claude mcp add --scope user embry0 --env EMBRY0_API_URL="$EMBRY0_URL" -- '
    "sh -c 'EMBRY0_API_KEY=$(cat ~/.config/embry0/api-key); export EMBRY0_API_KEY; exec embry0-mcp'",
    # The credential upload: the secret from read into a pipe through printf, the key's header through a pipe.
    "read -rs EMBRY0_SECRET",
    "unset EMBRY0_SECRET",
    (
        "printf '%s' \"$EMBRY0_SECRET\" | "
        'jq -Rs \'{kind: "forge_token", provider: "github", label: "acme-bot", secret: .}\' | '
        'curl -sS -X POST "$EMBRY0_URL/api/v1/credentials" '
        "-H @<(jq -Rr '\"Authorization: Bearer \" + .' ~/.config/embry0/api-key) "
        '-H "Content-Type: application/json" -H "X-Requested-With: XMLHttpRequest" --data-binary @-'
    ),
    "~/.config/embry0/api-key",  # the path alone, in prose
    "EMBRY0_API_KEY",  # the variable's name alone, in prose
}
_KEY_MENTION = re.compile(r"EMBRY0_API_KEY|EMBRY0_SECRET|api-key|HELPER|\.config/embry0")


def _logical_lines(code: str) -> list[str]:
    """A block's lines, with a line that ends in a backslash or a pipe joined to the next one."""
    lines: list[str] = []
    current = ""
    for line in code.splitlines():
        current += line
        stripped = current.rstrip()
        if stripped.endswith("\\"):
            current = stripped[:-1] + " "
        elif stripped.endswith("|"):
            current = stripped + " "
        else:
            lines.append(current)
            current = ""
    if current:
        lines.append(current)
    return lines


def _key_reaches_an_argument(line: str) -> str | None:
    """Why a logical line is not one of the documented lines that may touch the key, or None."""
    if not _KEY_MENTION.search(line) or " ".join(line.split()) in _KEY_LINES:
        return None
    return f"a line that touches the key is not one of the reviewed lines: {line}"


def _documented_lines() -> list[str]:
    plugin = [*(p.read_text() for p in (PLUGIN / "skills").glob("*/SKILL.md")), (PLUGIN / "README.md").read_text()]
    texts = [*plugin, GUIDE.read_text(), REPO_SKILL.read_text()]
    return [line for text in texts for code in _code(text) for line in _logical_lines(code)]


def test_no_documented_command_puts_the_key_on_a_command_line() -> None:
    """Every plugin skill, the plugin README, the whole guide and the checkout's setup skill, inline code as well as
    blocks (#376)."""
    lines = _documented_lines()
    for line in lines:
        reason = _key_reaches_an_argument(line)
        assert reason is None, reason
    # The key file's line reads it twice (once to see it was read, once to write it), the plugin's connect once.
    assert sum(len(re.findall(r"\$\{?EMBRY0_API_KEY\b", line)) for line in lines) >= 3, (
        "a recipe no longer reads the key"
    )


def test_every_reviewed_key_line_is_still_documented() -> None:
    """A listed line no page uses any more is an opening nobody reviews: remove it with the recipe."""
    used = {" ".join(line.split()) for line in _documented_lines()}
    prose_only = {_ADD_JSON.format(scope="local"), "EMBRY0_API_KEY"}  # described in prose, not written as a block
    assert sorted(_KEY_LINES - used - prose_only) == []


@pytest.mark.parametrize(
    "code",
    [
        'curl -H "Authorization: Bearer $(printf \'%s\' "$EMBRY0_API_KEY")" https://x',
        'curl -H "Authorization: Bearer $(cat ~/.config/embry0/api-key)" https://x',
        'curl -H "Authorization: Bearer `cat ~/.config/embry0/api-key`" https://x',
        'claude mcp add embry0 --header "Authorization: Bearer ${EMBRY0_API_KEY}"',
        "claude mcp add embry0 --env EMBRY0_API_KEY=$(cat ~/.config/embry0/api-key) -- embry0-mcp",
        "env EMBRY0_API_KEY=$(cat ~/.config/embry0/api-key) embry0-mcp",
        "env -i 'PATH=/usr/bin' EMBRY0_API_KEY=$(cat ~/.config/embry0/api-key) embry0-mcp",
        'curl -H "$(jq -Rr \'("Authorization: Bearer " + .)\' ~/.config/embry0/api-key)" https://x',
        "printf '%s' \"$EMBRY0_API_KEY\" | xargs -I{} curl -H 'Authorization: Bearer {}' https://x",
        "printf '%s' \"$EMBRY0_API_KEY\" | jq -Rs . |\n  xargs -I{} curl -H 'Authorization: Bearer {}' https://x",
        "printf '%s' \"$EMBRY0_API_KEY\" | jq -Rs '\"curl -H Bearer:\" + .' | sh",
        "cat ~/.config/embry0/api-key |\n  xargs -I{} curl -H 'Authorization: Bearer {}' https://x",
        "xargs -a ~/.config/embry0/api-key -I{} curl -H 'Authorization: Bearer {}' https://x",
        'read -r K < ~/.config/embry0/api-key; curl -H "Authorization: Bearer $K" https://x',
        'K=$(cat ~/.config/embry0/api-key); curl -H "Authorization: Bearer $K" https://x',
        'curl -H "Authorization: Bearer $(cat \\\n  ~/.config/embry0/api-key)" https://x',
        "sh -c 'curl -H \"Authorization: Bearer $(cat ~/.config/embry0/api-key)\" https://x'",
        "bash -c 'curl -H \"Authorization: Bearer $(cat ~/.config/embry0/api-key)\" https://x'",
        "sh -ec 'curl -H \"Authorization: Bearer $(cat ~/.config/embry0/api-key)\" https://x'",
        "curl -d \"$(printf '%s' \"$EMBRY0_API_KEY\" | jq -Rs '{api_key: .}')\" https://x",
        'printf \'%s\' "$EMBRY0_API_KEY" | jq -Rs -r . | while read -r k; do curl -H "Bearer $k" https://x; done',
        "printf '%s' \"$EMBRY0_API_KEY\" | jq -Rs -r . | awk '{system(\"curl -H Bearer:\" $0)}'",
        "printf '%s' \"$EMBRY0_API_KEY\" | jq -Rs -r '\"curl -H Bearer:\" + .' | /bin/sh",
        'curl -H "$(sh -c "$HELPER" | jq -r \'"Authorization: " + .Authorization\')" https://x',
        "env printf '%s' \"$EMBRY0_API_KEY\" > ~/.config/embry0/api-key",
        "(umask 077; exec printf '%s' \"$EMBRY0_API_KEY\" > ~/.config/embry0/api-key)",
        'env [ -n "$EMBRY0_API_KEY" ] && echo set',
        "printf '%s' \"$EMBRY0_API_KEY\" | jq -Rs --arg k \"$(cat)\" -n '$k'",
        'curl -H "Authorization: Bearer $(cat ~/.config/embry0/a*)" https://x',
    ],
    ids=[
        "printf inside $()",
        "the key file inside $()",
        "the key file in backticks",
        "a plain expansion",
        "an option's value",
        "an env argument",
        "env -i with a quoted word",
        "parentheses inside $()",
        "printf into xargs",
        "jq into xargs on the next line",
        "jq into a shell",
        "the key file into xargs on the next line",
        "xargs -a the key file",
        "read from the key file",
        "another variable",
        "a substitution across lines",
        "an sh -c script",
        "a bash -c script",
        "an sh -ec script",
        "an allowed form inside $()",
        "a while loop after jq",
        "awk system after jq",
        "a pipe into /bin/sh",
        "the helper run by hand",
        "env printf",
        "exec printf",
        "env [",
        "jq reading the key into its arguments",
        "a glob on the key's folder",
    ],
)
def test_the_command_line_check_refuses_each_form(code: str) -> None:
    reasons = [r for r in map(_key_reaches_an_argument, _logical_lines(code)) if r]
    assert reasons, f"the check lets this through: {code}"


def test_the_guide_blocks_carry_no_comments() -> None:
    """Pasted into zsh without interactivecomments, a ``#`` after a command is more arguments (``rm -f x # y z``)."""
    for block in _bash_blocks(GUIDE.read_text()):
        for line in block.splitlines():
            assert not re.search(r"(^|\s)#", line), f"a comment in a guide block: {line}"


GUIDE_URL = "https://embry0.example.com"


@pytest.fixture
def stand_ins(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    """``claude``, ``curl`` and ``embry0-mcp`` on PATH that record each call, in a home of their own.

    The home's path has a space in it. A call records its arguments, the key in its environment
    (only ``embry0-mcp`` may have it), and for curl the header files it is given (``-H @file``)
    and its stdin.
    """
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed")
    bin_dir, home = tmp_path / "bin", tmp_path / "a home"
    bin_dir.mkdir()
    home.mkdir()
    calls = tmp_path / "calls.jsonl"
    body = (
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "name, args = os.path.basename(sys.argv[0]), sys.argv[1:]\n"
        "call = {'name': name, 'args': args, 'key': os.environ.get('EMBRY0_API_KEY')}\n"
        "if name == 'curl':\n"
        "    call['headers'] = [open(b[1:]).read() for a, b in zip(args, args[1:]) if a == '-H' and b[:1] == '@']\n"
        "    call['stdin'] = sys.stdin.read()\n"
        f"open({str(calls)!r}, 'a').write(json.dumps(call) + '\\n')\n"
    )
    for name in ("claude", "curl", "embry0-mcp"):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "EMBRY0_API_KEY"}
    return {**env, "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}", "HOME": str(home)}, calls, home


def _run_recipe(
    recipe: str, env: dict[str, str], typed: str = "", cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Guide blocks, run by bash as written under the common umask 022, with ``typed`` on stdin (the key at ``read``)."""
    run = subprocess.run(
        ["bash", "-c", "umask 022\n" + recipe],
        env=env,
        input=typed,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return run


def _key_file(home: Path) -> Path:
    return home / ".config" / "embry0" / "api-key"


@pytest.mark.parametrize("earlier", [False, True], ids=["a new home", "an earlier folder and key file"])
def test_the_manual_connection_keeps_the_key_in_a_private_file(
    stand_ins: tuple[dict[str, str], Path, Path], earlier: bool
) -> None:
    env, calls, home = stand_ins
    key_file = _key_file(home)
    if earlier:  # an earlier run, or a folder and file made by hand with the default umask
        key_file.parent.mkdir(parents=True)
        key_file.parent.chmod(0o755)
        key_file.write_text("an older key")
        key_file.chmod(0o644)
    key = "canary-manual-key"
    # With allexport on (set -a, some profiles turn it on), read exports the key: then only the unset keeps it
    # out of claude's environment.
    recipe = "set -a\n" + _guide_recipe("read -rs EMBRY0_API_KEY") + "\n" + _guide_recipe("headersHelper")
    run = _run_recipe(recipe, env, typed=key + "\n")
    assert key not in run.stdout + run.stderr

    assert key_file.read_text() == key
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600, "the key file is readable by others"
    assert stat.S_IMODE(key_file.parent.stat().st_mode) == 0o700, "the key's folder is open to others"
    removed, add, listed = _calls(calls)
    for call in (removed, add, listed):
        assert call["name"] == "claude"
        assert key not in json.dumps(call["args"]), f"the key is an argument: {call['args']}"
        assert call["key"] is None, "claude runs with the key in its environment"
    # An entry an earlier connection left would keep its old header: it goes first.
    assert removed["args"] == ["mcp", "remove", "--scope", "user", "embry0"]
    assert listed["args"] == ["mcp", "list"]
    assert add["args"][:5] == ["mcp", "add-json", "--scope", "user", "embry0"]
    config = json.loads(add["args"][5])
    assert {k: v for k, v in config.items() if k != "headersHelper"} == {"type": "http", "url": f"{GUIDE_URL}/mcp"}
    # Claude Code runs the helper through a shell each time it connects; it prints the header.
    helper = subprocess.run(["sh", "-c", config["headersHelper"]], env=env, capture_output=True, text=True, check=True)
    assert json.loads(helper.stdout) == {"Authorization": f"Bearer {key}"}


def test_the_second_block_alone_keeps_a_saved_key(stand_ins: tuple[dict[str, str], Path, Path]) -> None:
    """Run in a shell where the first block did not run, the key block must not empty the saved file."""
    env, _, home = stand_ins
    _key_file(home).parent.mkdir(parents=True, mode=0o700)
    _key_file(home).write_text("the saved key")
    _key_file(home).chmod(0o600)
    _run_recipe(_guide_recipe("headersHelper"), {**env, "EMBRY0_URL": GUIDE_URL})
    assert _key_file(home).read_text() == "the saved key"
    assert stat.S_IMODE(_key_file(home).stat().st_mode) == 0o600


def test_the_stdio_connection_reads_the_key_from_the_file_when_it_starts(
    stand_ins: tuple[dict[str, str], Path, Path],
) -> None:
    env, calls, home = stand_ins
    key = "canary-stdio-key"
    _key_file(home).parent.mkdir(parents=True, mode=0o700)
    _key_file(home).write_text(key)
    _run_recipe(_guide_recipe("embry0-mcp"), {**env, "EMBRY0_URL": GUIDE_URL})
    (add,) = _calls(calls)
    assert key not in json.dumps(add["args"])
    sep = add["args"].index("--")
    assert add["args"][:sep] == ["mcp", "add", "--scope", "user", "embry0", "--env", f"EMBRY0_API_URL={GUIDE_URL}"]
    command = add["args"][sep + 1 :]
    assert command[:2] == ["sh", "-c"] and len(command) == 3
    calls.unlink()
    # Claude Code starts the server as that command, with no shell of its own around it.
    subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    (server,) = _calls(calls)
    assert server["name"] == "embry0-mcp"
    assert server["args"] == []
    assert server["key"] == key, "the server did not get the key from the file"


def test_the_credential_upload_sends_the_key_and_the_secret_through_files(
    stand_ins: tuple[dict[str, str], Path, Path], tmp_path: Path
) -> None:
    """The secret is typed at read and goes through a pipe: no file holds it, no argument carries it (#599 review)."""
    env, calls, home = stand_ins
    key, secret = "canary-upload-key", "canary-forge-secret"
    _key_file(home).parent.mkdir(parents=True, mode=0o700)
    _key_file(home).write_text(key)
    upload = _guide_recipe("/api/v1/credentials")
    assert "read " not in upload, "the secret is read in a block of its own: a read in a pasted block swallows a line"
    recipe = _guide_recipe("read -rs EMBRY0_SECRET") + "\n" + upload
    work = tmp_path / "work"
    work.mkdir()
    run = _run_recipe(recipe, {**env, "EMBRY0_URL": GUIDE_URL}, typed=secret + "\n", cwd=work)
    assert key not in run.stdout + run.stderr and secret not in run.stdout + run.stderr
    assert list(work.iterdir()) == [], "the upload left a file behind"
    (curl,) = _calls(calls)
    assert curl["name"] == "curl"
    assert key not in json.dumps(curl["args"]) and secret not in json.dumps(curl["args"])
    assert curl["key"] is None
    assert f"{GUIDE_URL}/api/v1/credentials" in curl["args"]
    assert f"Authorization: Bearer {key}\n" in curl["headers"]
    assert json.loads(curl["stdin"]) == {
        "kind": "forge_token",
        "provider": "github",
        "label": "acme-bot",
        "secret": secret,
    }


def test_the_guide_tells_a_plugin_user_to_write_the_key_file_before_the_upload() -> None:
    """The credential upload reads the key file the manual connection writes; a plugin user has none until told."""
    upload = GUIDE.read_text().split("## Secrets stay out of the chat", 1)[-1]
    assert "/api/v1/credentials" in upload and "~/.config/embry0/api-key" in _guide_recipe("/api/v1/credentials")
    assert re.search(r"With the plugin, write that file first", upload), "the upload's note for a plugin user is gone"
