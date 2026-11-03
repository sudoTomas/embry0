"""Tests for the context-proxy bearer write/read helpers (RAV-1140)."""

from __future__ import annotations

import inspect
import subprocess

from embry0.sandbox.context_token import (
    CONTEXT_PROXY_TOKEN_REL,
    build_context_token_write_cmd,
    read_context_proxy_token,
)


def test_cmd_shape_carries_no_bearer():
    assert not inspect.signature(build_context_token_write_cmd).parameters, (
        "the bearer is read from stdin; the builder takes no token argument"
    )
    cmd = build_context_token_write_cmd()
    assert cmd[0] == "bash" and cmd[1] == "-c"
    script = cmd[2]
    assert "cat >" in script, "the bearer is read from stdin, never put on the command line"
    assert f'chmod 600 "$HOME/{CONTEXT_PROXY_TOKEN_REL}"' in script


def test_cmd_writes_exact_token(tmp_path):
    assert not inspect.signature(build_context_token_write_cmd).parameters, (
        "the bearer is read from stdin; the builder takes no token argument"
    )
    token = "tok-abc'; echo pwned; '123"
    cmd = build_context_token_write_cmd()
    subprocess.run(cmd, check=True, input=token.encode(), env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    written = (tmp_path / CONTEXT_PROXY_TOKEN_REL).read_text()
    assert written == token
    mode = (tmp_path / CONTEXT_PROXY_TOKEN_REL).stat().st_mode & 0o777
    assert mode == 0o600


def test_read_env_override_wins(monkeypatch):
    monkeypatch.setenv("EMBRY0_CONTEXT_PROXY_TOKEN", "env-token")
    assert read_context_proxy_token() == "env-token"


def test_read_from_path_override(monkeypatch, tmp_path):
    monkeypatch.delenv("EMBRY0_CONTEXT_PROXY_TOKEN", raising=False)
    token_file = tmp_path / "tok"
    token_file.write_text("  file-token\n")
    monkeypatch.setenv("EMBRY0_CONTEXT_PROXY_TOKEN_PATH", str(token_file))
    assert read_context_proxy_token() == "file-token"


def test_read_missing_file_is_empty(monkeypatch, tmp_path):
    monkeypatch.delenv("EMBRY0_CONTEXT_PROXY_TOKEN", raising=False)
    monkeypatch.setenv("EMBRY0_CONTEXT_PROXY_TOKEN_PATH", str(tmp_path / "absent"))
    assert read_context_proxy_token() == ""
