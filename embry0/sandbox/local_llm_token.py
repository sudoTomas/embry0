"""Per-sandbox local-llm-proxy bearer delivery.

The chat-completions executor authenticates to the local-llm-proxy with the
per-sandbox bearer, read from a 0600 file in the agent's home, the way the xAI
proxy's bearer is delivered: the bearer exists only after enrollment, when the
container's env can no longer change, and it never rides the env. The sandbox
manager writes it at create and again at every re-enrollment.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

# Home-relative so writer (root docker exec) and reader (agent user) agree.
LOCAL_LLM_PROXY_TOKEN_REL = ".embry0/local_llm_proxy_token"

# Sandbox env, both under the reserved EMBRY0_LLM_ prefix so a repository
# cannot set them. The flag says the custom provider's base URL is the
# local-llm-proxy, so the executor authenticates with the bearer file instead
# of a key; the window is the model's context in tokens, when the operator set one.
LOCAL_LLM_PROXY_FLAG = "EMBRY0_LLM_CUSTOM_VIA_PROXY"
LOCAL_LLM_CONTEXT_ENV = "EMBRY0_LLM_CUSTOM_CONTEXT_TOKENS"


def build_local_llm_token_write_cmd(sandbox_token: str) -> list[str]:
    """Argv for a ``docker exec`` that writes the bearer to a 0600 home file."""
    return [
        "bash",
        "-c",
        'mkdir -p "$HOME/.embry0" && '
        f'printf %s {shlex.quote(sandbox_token)} > "$HOME/{LOCAL_LLM_PROXY_TOKEN_REL}" && '
        f'chmod 600 "$HOME/{LOCAL_LLM_PROXY_TOKEN_REL}"',
    ]


def read_local_llm_proxy_token() -> str:
    """The per-sandbox bearer for the local-llm-proxy, or '' when there is none."""
    path = os.environ.get("EMBRY0_LLM_CUSTOM_PROXY_TOKEN_PATH") or os.path.expanduser(f"~/{LOCAL_LLM_PROXY_TOKEN_REL}")
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
