"""Per-sandbox xai-proxy bearer delivery (EMB-45).

The DirectXaiExecutor authenticates to the xai-proxy with the per-sandbox bearer,
read from a 0600 file in the agent's home — the token never rides the sandbox env.
Every orchestrator-side path that preps a sandbox for a potential grok agent
(issue_to_pr init node, QA sub-task prep) delivers it with the same command; the
in-sandbox executor reads it back via the same relative path.
"""

from __future__ import annotations

import shlex

# Home-relative so writer (root docker exec) and reader (agent user) agree
# regardless of the container's default user.
XAI_PROXY_TOKEN_REL = ".embry0/xai_proxy_token"


def build_xai_token_write_cmd(sandbox_token: str) -> list[str]:
    """Argv for a ``docker exec`` that writes the bearer to a 0600 home file."""
    return [
        "bash",
        "-c",
        'mkdir -p "$HOME/.embry0" && '
        f'printf %s {shlex.quote(sandbox_token)} > "$HOME/{XAI_PROXY_TOKEN_REL}" && '
        f'chmod 600 "$HOME/{XAI_PROXY_TOKEN_REL}"',
    ]
