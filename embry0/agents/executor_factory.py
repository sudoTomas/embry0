"""Pure executor selector.

Maps (execution_mode, auth_mode) → AgentExecutor. Phase 1 only constructs
SdkAgentExecutor; cli mode is rejected at the factory to preserve the
"no behavioral regression" guarantee. Phase 2 lifts the cli gate.
"""

from __future__ import annotations

from embry0.agents.executor import AgentExecutor, SdkAgentExecutor
from embry0.agents.invocation import AgentInvocation
from embry0.execution.auth_provider import AuthConfigError
from embry0.safety.error_codes import ErrorCode


def select_executor(invocation: AgentInvocation) -> AgentExecutor:
    """Choose the executor for this invocation.

    Raises AuthConfigError(ERR_INVALID_CONFIG) for anything the current phase
    does not support.
    """
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
