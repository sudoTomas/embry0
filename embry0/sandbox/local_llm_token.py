"""Per-sandbox llm-proxy bearer delivery.

The chat-completions executor authenticates to the llm-proxy with the
per-sandbox bearer, read from a 0600 file in the agent's home, the way the xAI
proxy's bearer is delivered: the bearer exists only after enrollment, when the
container's env can no longer change, and it never rides the env. The sandbox
manager writes it at create and again at every re-enrollment.
"""

from __future__ import annotations

import os
from pathlib import Path

# Home-relative so writer (root docker exec) and reader (agent user) agree.
LOCAL_LLM_PROXY_TOKEN_REL = ".embry0/local_llm_proxy_token"

#: Where a sandbox reaches the llm-proxy (sandbox-restricted's name for it). A chat-completions provider's root
#: there is ``<root>/p/<provider>/v1`` (ADR 0033); the custom provider's is also ``<root>/v1``.
LLM_PROXY_URL = "http://llm-proxy:9108"

# Sandbox env, under the reserved EMBRY0_LLM_ prefix so a repository cannot set
# it. The flag says the custom provider's base URL is the llm-proxy, so
# the executor authenticates with the bearer file instead of a key. (A custom
# model's context window rides the run's provider record, from the catalogue.)
LOCAL_LLM_PROXY_FLAG = "EMBRY0_LLM_CUSTOM_VIA_PROXY"


def build_local_llm_token_write_cmd() -> list[str]:
    """Argv for a ``docker exec -i`` that writes the bearer it reads on stdin to a 0600 home file.

    The bearer is never on the command line: run it with ``stdin_data``.
    """
    return [
        "bash",
        "-c",
        'mkdir -p "$HOME/.embry0" && '
        f'cat > "$HOME/{LOCAL_LLM_PROXY_TOKEN_REL}" && '
        f'chmod 600 "$HOME/{LOCAL_LLM_PROXY_TOKEN_REL}"',
    ]


def read_local_llm_proxy_token() -> str:
    """The per-sandbox bearer for the llm-proxy, or '' when there is none."""
    path = os.environ.get("EMBRY0_LLM_CUSTOM_PROXY_TOKEN_PATH") or os.path.expanduser(f"~/{LOCAL_LLM_PROXY_TOKEN_REL}")
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
