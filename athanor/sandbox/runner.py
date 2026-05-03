"""Sandbox agent runner — entrypoint for `python -m athanor.sandbox.runner`.

Called by the orchestrator via `docker exec`. Parses the --config JSON into
an AgentInvocation and delegates to SdkAgentExecutor (Phase 1). Emits Athanor
events to stdout as JSON-per-line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from langchain_core.runnables import RunnableConfig

from athanor.agents.executor_factory import select_executor
from athanor.agents.invocation import AgentInvocation
from athanor.safety.policy import default_policy_for_agent

logger = structlog.get_logger(__name__)


def _emit(event: dict[str, Any]) -> None:
    """Write a JSON event to stdout and flush."""
    event.setdefault("timestamp", datetime.now(UTC).isoformat())
    sys.stdout.write(json.dumps(event, default=str) + "\n")
    sys.stdout.flush()


def _invocation_from_config(cfg: dict[str, Any]) -> AgentInvocation:
    """Build an AgentInvocation from the --config JSON the orchestrator passes.

    The orchestrator fully resolves the invocation already; this function
    only unpacks it. Backward-compat: a few older fields (`prompt`,
    `system_context`) are still accepted in the flat shape.
    """
    agent_type = cfg.get("agent_type", "agent")
    return AgentInvocation(
        agent_type=agent_type,
        prompt=cfg["prompt"],
        system_prompt=cfg.get("system_prompt", ""),
        system_context=cfg.get("system_context", ""),
        model=cfg.get("model", "claude-sonnet-4-6"),
        tools=cfg.get("tools", []),
        skills=cfg.get("skills", []),
        mcp_servers=cfg.get("mcp_servers", {}),
        max_turns=cfg.get("max_turns", 40),
        timeout_seconds=cfg.get("timeout_seconds", 300),
        execution_mode=cfg.get("execution_mode", "sdk"),
        auth_mode=cfg.get("auth_mode", "oauth"),
        safety_policy=default_policy_for_agent(agent_type),
        channel_config=None,
    )


def _build_run_kwargs(
    config: dict[str, Any],
    *,
    session_blob_path: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Build the kwargs passed to ``run_agent`` based on resume CLI args.

    For ``auth_mode == "api_key"`` the blob is a JSON file containing the
    prior message list, which we deserialize and forward as
    ``resume_messages``. For ``auth_mode == "oauth"`` (claude-max), the
    session bytes are already on disk inside the sandbox at the canonical
    CLI session path; we only forward the ``session_id`` so the executor
    can pass ``--resume <id>`` to the CLI.
    """
    kwargs: dict[str, Any] = {"config": config}
    if session_blob_path and config.get("auth_mode") == "api_key":
        with open(session_blob_path) as f:
            kwargs["resume_messages"] = json.load(f)
    if session_id and config.get("auth_mode") == "oauth":
        kwargs["resume_session_id"] = session_id
    return kwargs


async def run_agent(
    config: dict[str, Any],
    *,
    resume_messages: list[dict[str, Any]] | None = None,
    resume_session_id: str | None = None,
) -> dict[str, Any]:
    # NOTE: ``resume_messages`` and ``resume_session_id`` are accepted here
    # for forward-compat with Task 5/6, which will thread them into
    # ``SdkAgentExecutor.run()``. For now they are inert pass-throughs so
    # the sandbox runner CLI surface is stable from this task forward.
    del resume_messages, resume_session_id
    invocation = _invocation_from_config(config)
    executor = select_executor(invocation)
    # Inject a writer that serializes events to stdout (Athanor's wire format).
    test_cfg = cast(RunnableConfig, {"configurable": {}, "_test_writer": _emit})
    result = await executor.run(invocation, test_cfg)
    return {
        "agent_type": result.agent_type,
        "is_error": result.is_error,
        "error_message": result.error_message,
        "output": result.output,
        "cost_usd": result.cost_usd,
        "duration_ms": result.duration_ms,
        "tools_called": result.tools_called,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Athanor sandbox agent runner")
    parser.add_argument("--config", required=True, help="JSON agent configuration")
    parser.add_argument(
        "--session-blob",
        default=None,
        help="Path to a file containing prior session state (api mode: JSON message list).",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="claude-max session id; the CLI's session file must already be in place.",
    )
    args = parser.parse_args()

    config = json.loads(args.config)
    kwargs = _build_run_kwargs(
        config,
        session_blob_path=args.session_blob,
        session_id=args.session_id,
    )
    result = asyncio.run(run_agent(**kwargs))

    sys.stdout.write(json.dumps({"type": "final_result", **result}, default=str) + "\n")
    sys.stdout.flush()
    sys.exit(1 if result.get("is_error") else 0)


if __name__ == "__main__":
    main()
