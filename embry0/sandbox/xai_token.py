"""Per-sandbox xai-proxy bearer delivery (EMB-45).

The DirectXaiExecutor authenticates to the xai-proxy with the per-sandbox bearer,
read from a 0600 file in the agent's home — the token never rides the sandbox env.
Every orchestrator-side path that preps a sandbox for a potential grok agent
(issue_to_pr init node, QA sub-task prep) delivers it with the same command; the
in-sandbox executor reads it back via the same relative path.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

# Home-relative so writer (root docker exec) and reader (agent user) agree
# regardless of the container's default user.
XAI_PROXY_TOKEN_REL = ".embry0/xai_proxy_token"


def read_xai_proxy_token() -> str:
    """Read the per-sandbox proxy bearer (env override wins, for tests).

    Shared by both grok paths: the SDK executor puts it on the CLI subprocess
    as ``ANTHROPIC_AUTH_TOKEN`` (EMB-46) and the DirectXaiExecutor hands it to
    its anthropic client (EMB-45 Phase B).
    """
    env = os.environ.get("EMBRY0_XAI_PROXY_TOKEN", "")
    if env:
        return env
    path = os.environ.get("EMBRY0_XAI_PROXY_TOKEN_PATH") or os.path.expanduser(f"~/{XAI_PROXY_TOKEN_REL}")
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def build_xai_token_write_cmd(sandbox_token: str) -> list[str]:
    """Argv for a ``docker exec`` that writes the bearer to a 0600 home file."""
    return [
        "bash",
        "-c",
        'mkdir -p "$HOME/.embry0" && '
        f'printf %s {shlex.quote(sandbox_token)} > "$HOME/{XAI_PROXY_TOKEN_REL}" && '
        f'chmod 600 "$HOME/{XAI_PROXY_TOKEN_REL}"',
    ]
