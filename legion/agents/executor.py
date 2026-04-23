"""AgentExecutor — the protocol both SDK and CLI executors implement.

Executors receive a fully-resolved AgentInvocation and a LangGraph
RunnableConfig (whose stream writer they use to emit events).
They return an AgentOutput with the aggregated result.

Concrete implementations:
- SdkAgentExecutor (Phase 1) — wraps claude_agent_sdk.query().
- CliAgentExecutor (Phase 2) — spawns `claude -p` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from legion.agents.invocation import AgentInvocation
from legion.execution.agent_runner import AgentOutput

if TYPE_CHECKING:
    from langchain_core.runnables.config import RunnableConfig


class AgentExecutor(Protocol):
    async def run(
        self,
        invocation: AgentInvocation,
        config: RunnableConfig,
    ) -> AgentOutput: ...


# Placeholder import to keep SdkAgentExecutor referenceable before Task 10.
# Task 10 replaces this with the real implementation.
class SdkAgentExecutor:
    """Stub — filled in by Task 10."""

    async def run(self, invocation, config):  # noqa: ANN001, ANN201
        raise NotImplementedError("SdkAgentExecutor.run lands in Task 10")
