"""Tests for the shared xai-proxy bearer write command (EMB-45)."""

from __future__ import annotations

import inspect
import subprocess

from embry0.sandbox.xai_token import XAI_PROXY_TOKEN_REL, build_xai_token_write_cmd


def test_cmd_shape_carries_no_bearer():
    assert not inspect.signature(build_xai_token_write_cmd).parameters, (
        "the bearer is read from stdin; the builder takes no token argument"
    )
    cmd = build_xai_token_write_cmd()
    assert cmd[0] == "bash" and cmd[1] == "-c"
    script = cmd[2]
    assert "cat >" in script, "the bearer is read from stdin, never put on the command line"
    assert f'chmod 600 "$HOME/{XAI_PROXY_TOKEN_REL}"' in script


def test_cmd_writes_exact_token(tmp_path):
    assert not inspect.signature(build_xai_token_write_cmd).parameters, (
        "the bearer is read from stdin; the builder takes no token argument"
    )
    token = "tok-abc'; echo pwned; '123"
    cmd = build_xai_token_write_cmd()
    subprocess.run(cmd, check=True, input=token.encode(), env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    written = (tmp_path / XAI_PROXY_TOKEN_REL).read_text()
    assert written == token
    mode = (tmp_path / XAI_PROXY_TOKEN_REL).stat().st_mode & 0o777
    assert mode == 0o600
