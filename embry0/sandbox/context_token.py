"""Per-sandbox context-proxy bearer delivery (RAV-1140).

The in-sandbox logs MCP server authenticates to the context-proxy with the
per-sandbox bearer, read from a 0600 file in the agent's home — the token never
rides the sandbox env. init_node delivers it (watcher-flagged jobs only) with
the same docker-exec command shape as the xai token; the in-sandbox server
reads it back via the same relative path. Line-for-line sibling of
:mod:`embry0.sandbox.xai_token`.
"""

from __future__ import annotations

import os
from pathlib import Path

# Home-relative so writer (root docker exec) and reader (agent user) agree
# regardless of the container's default user.
CONTEXT_PROXY_TOKEN_REL = ".embry0/context_proxy_token"


def read_context_proxy_token() -> str:
    """Read the per-sandbox proxy bearer (env override wins, for tests)."""
    env = os.environ.get("EMBRY0_CONTEXT_PROXY_TOKEN", "")
    if env:
        return env
    path = os.environ.get("EMBRY0_CONTEXT_PROXY_TOKEN_PATH") or os.path.expanduser(f"~/{CONTEXT_PROXY_TOKEN_REL}")
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def build_context_token_write_cmd() -> list[str]:
    """Argv for a ``docker exec -i`` that writes the bearer it reads on stdin to a 0600 home file.

    The bearer is never on the command line: run it with ``stdin_data``.
    """
    return [
        "bash",
        "-c",
        'mkdir -p "$HOME/.embry0" && '
        f'cat > "$HOME/{CONTEXT_PROXY_TOKEN_REL}" && '
        f'chmod 600 "$HOME/{CONTEXT_PROXY_TOKEN_REL}"',
    ]
