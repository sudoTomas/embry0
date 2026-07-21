"""Pure executor selector.

Maps (execution_mode, auth_mode, provider) → AgentExecutor. Phase 1 only
constructs SdkAgentExecutor; cli mode is rejected at the factory to preserve
the "no behavioral regression" guarantee.

EMB-45: xAI (grok) agents route to DirectXaiExecutor — an embry0-owned
Messages-API tool loop that talks to api.x.ai via the xai-proxy — but only
when the proxy is actually available (``EMBRY0_XAI_PROXY_URL`` is injected into
the sandbox env solely when ``xai_proxy_enabled`` launched the proxy). When the
proxy is off, grok falls back to the SdkAgentExecutor CLI/console-key path
(EMB-36), so enabling the proxy is the single opt-in switch.
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
    # EMB-45: direct-xAI path — provider-routed grok + the proxy is live.
    if invocation.provider == "xai" and os.environ.get("EMBRY0_XAI_PROXY_URL"):
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
