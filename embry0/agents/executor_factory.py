"""Pure executor selector.

Maps (execution_mode, auth_mode, provider) → AgentExecutor. Phase 1 only
constructs SdkAgentExecutor; cli mode is rejected at the factory to preserve
the "no behavioral regression" guarantee.

EMB-46: grok's default runtime is the Agent SDK pointed at the xai-proxy
(SdkAgentExecutor with the ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN overlay in
executor.py) — native MCP/tools/resume, SuperGrok bearer auth, no Max OAuth.
The EMB-45 DirectXaiExecutor (embry0-owned Messages-API loop, CLI-free) is the
opt-in fallback: it routes only when ``EMBRY0_XAI_DIRECT_EXECUTOR`` is set
alongside a live proxy (``EMBRY0_XAI_PROXY_URL``) — insurance for a CLI
release breaking against xAI's compat endpoint. With the proxy off, grok
falls back to the EMB-36 CLI/console-key path.
"""

from __future__ import annotations

import os

from embry0.agents.executor import AgentExecutor, SdkAgentExecutor
from embry0.agents.invocation import AgentInvocation
from embry0.execution.auth_provider import AuthConfigError
from embry0.safety.error_codes import ErrorCode


def select_executor(invocation: AgentInvocation) -> AgentExecutor:
    """Choose the executor for this invocation.

    Raises AuthConfigError(ERR_INVALID_CONFIG) for anything the current phase
    does not support.
    """
    # EMB-45/EMB-46: direct-xAI fallback — provider-routed grok + live proxy +
    # explicit opt-in. Default grok path is the SDK over the proxy.
    if (
        invocation.provider == "xai"
        and os.environ.get("EMBRY0_XAI_PROXY_URL")
        and os.environ.get("EMBRY0_XAI_DIRECT_EXECUTOR")
    ):
        from embry0.agents.executor_xai import DirectXaiExecutor

        return DirectXaiExecutor()

    mode = invocation.execution_mode
    if mode == "sdk":
        return SdkAgentExecutor()
    if mode == "cli":
        raise AuthConfigError(
            ErrorCode.INVALID_CONFIG,
            "execution_mode='cli' is not yet supported (arrives in Phase 2)",
        )
    raise AuthConfigError(
        ErrorCode.INVALID_CONFIG,
        f"unknown execution_mode: {mode!r} (expected 'sdk' or 'cli')",
    )
