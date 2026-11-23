"""Sandbox agent runner — entrypoint for `python -m embry0.sandbox.runner`.

Called by the orchestrator via `docker exec`. Parses the --config JSON into
an AgentInvocation and delegates to SdkAgentExecutor (Phase 1). Emits embry0
events to stdout as JSON-per-line.

No other process of the sandbox's user may open this one (spec 2026-11-23,
an agent run's own result): its stdout is the one channel its events and its
result reach the orchestrator by, and the code a job runs is of that user.
The orchestrator starts it on an interpreter that user may run but not read
(``/usr/local/bin/embry0-runner-python``, infra/Dockerfile.sandbox), which the
kernel starts closed from its first instruction; :func:`seal` closes it
again first thing, for an interpreter that is not.
"""

from __future__ import annotations

import ctypes

#: prctl's options that read and set whether other processes of the user may open this one (``man 2 prctl``).
_PR_GET_DUMPABLE = 3
_PR_SET_DUMPABLE = 4


def seal() -> bool:
    """Make this process one no other process of its user may open: its ``/proc/<pid>/fd``, ``mem`` and
    ``environ`` become root's, and nothing can attach to it or take a core of it. What it starts is unaffected:
    an exec opens a process again, and the CLI and its tools talk to the runner over pipes of their own.

    True when the process was open until now, so something of the user could have opened it already: the kernel
    started it open (its interpreter is readable by the user, or ``fs.suid_dumpable`` is 1), or it could not be
    closed at all. False when it started closed."""
    try:
        prctl = ctypes.CDLL(None, use_errno=True).prctl
        was_open = int(prctl(_PR_GET_DUMPABLE, 0, 0, 0, 0)) == 1
        closed = int(prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0)) == 0
    except (OSError, AttributeError):
        return True
    return was_open or not closed


#: Whether the runner was open when it started (:func:`seal`); reported on its final_result. None when imported.
OPEN_AT_START: bool | None = seal() if __name__ == "__main__" else None

import argparse  # noqa: E402 - after the runner is sealed
import asyncio  # noqa: E402
import dataclasses  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from typing import Any, cast  # noqa: E402

import structlog  # noqa: E402
from langchain_core.runnables import RunnableConfig  # noqa: E402

from embry0.agents.executor_factory import select_executor  # noqa: E402
from embry0.agents.invocation import AgentInvocation, ProviderRecord  # noqa: E402
from embry0.safety.policy import default_policy_for_agent, parse_tool_list, tools_for_run  # noqa: E402
from embry0.safety.redaction import redact_event, redact_secrets, secret_env_values  # noqa: E402

logger = structlog.get_logger(__name__)


def _emit(event: dict[str, Any]) -> None:
    """Write a JSON event to stdout and flush.

    Second redaction pass (spec 3.6): the executors scrub their text fields
    before the constructors cap them; this scrubs every string the event
    still carries (tool_input, summaries, error messages) with the secrets
    this process can see, so no line depends on one side alone.
    """
    event.setdefault("timestamp", datetime.now(UTC).isoformat())
    event = redact_event(event, secret_env_values())
    sys.stdout.write(json.dumps(event, default=str) + "\n")
    sys.stdout.flush()


def _isoformat_or_none(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _invocation_from_config(cfg: dict[str, Any]) -> AgentInvocation:
    """Build an AgentInvocation from the --config JSON the orchestrator passes.

    The orchestrator fully resolves the invocation already; this function
    only unpacks it. Backward-compat: a few older fields (`prompt`,
    `system_context`) are still accepted in the flat shape.
    """
    agent_type = cfg.get("agent_type", "agent")
    # EMB-37: sync the Ring-3 policy allowlist with the invocation's real
    # tools. evaluate_policy default-denies tool names outside
    # policy.allowed_tools, and the per-agent-type default map has no entry
    # for MCP-heavy agents like "qa" — without this sync every
    # mcp__playwright__* call would be denied. Missing cfg tools are the agent
    # type's defaults; an empty list is no tools, never the defaults.
    tools = tools_for_run(agent_type, parse_tool_list(cfg.get("tools")))
    policy = dataclasses.replace(default_policy_for_agent(agent_type), allowed_tools=tools)
    return AgentInvocation(
        agent_type=agent_type,
        prompt=cfg["prompt"],
        system_prompt=cfg.get("system_prompt", ""),
        system_context=cfg.get("system_context", ""),
        model=cfg.get("model", "claude-sonnet-5"),
        tools=tools,
        skills=cfg.get("skills", []),
        mcp_servers=cfg.get("mcp_servers", {}),
        max_turns=cfg.get("max_turns", 40),
        timeout_seconds=cfg.get("timeout_seconds", 600),
        execution_mode=cfg.get("execution_mode", "sdk"),
        auth_mode=cfg.get("auth_mode", "oauth"),
        safety_policy=policy,
        channel_config=None,
        provider=cfg.get("provider"),
        run_id=str(cfg.get("run_id") or ""),
        node=str(cfg.get("node") or ""),
        skill_mounts=tuple(cfg.get("skill_mounts") or ()),
        skills_hidden=cfg.get("skills_hidden") is True,
        # #58: a config without the key reads the workspace (an orchestrator that predates it); one
        # with it reads the workspace only on an exact True, so a value of another type keeps it out.
        workspace_config="workspace_config" not in cfg or cfg["workspace_config"] is True,
        pricing_usd_per_mtok=_rates_from_config(cfg.get("pricing_usd_per_mtok")),
        provider_record=ProviderRecord.from_config(cfg.get("provider_record")),
    )


def _rates_from_config(value: Any) -> tuple[float, float] | None:
    """The run's (input, output) $/Mtok the orchestrator sent for a catalog model; None when absent or not a pair."""
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    try:
        rate_in, rate_out = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    return (rate_in, rate_out) if rate_in >= 0 and rate_out >= 0 else None


def _build_run_kwargs(
    config: dict[str, Any],
    *,
    session_blob_path: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Build the kwargs passed to ``run_agent`` based on resume CLI args.

    Mode-agnostic (EMB-35): the CLI's session file works under either auth
    mode, so ``--session-id`` always forwards as ``resume_session_id`` (the
    session bytes are already staged at the canonical CLI path by
    AgentRunner), and ``--session-blob`` — a JSON file with the prior
    message list — always forwards as ``resume_messages`` (the text-replay
    fallback). The staging side passes at most one of the two.
    """
    kwargs: dict[str, Any] = {"config": config}
    if session_blob_path:
        with open(session_blob_path) as f:
            kwargs["resume_messages"] = json.load(f)
    if session_id:
        kwargs["resume_session_id"] = session_id
    return kwargs


async def run_agent(
    config: dict[str, Any],
    *,
    resume_messages: list[dict[str, Any]] | None = None,
    resume_session_id: str | None = None,
) -> dict[str, Any]:
    invocation = _invocation_from_config(config)
    executor = select_executor(invocation)
    # Inject a writer that serializes events to stdout (embry0's wire format).
    test_cfg = cast(RunnableConfig, {"configurable": {}, "_test_writer": _emit})
    result = await executor.run(
        invocation,
        test_cfg,
        resume_session_id=resume_session_id,
        resume_messages=resume_messages,
    )
    # Plan C Task 5: forward post-run conversation state so the orchestrator
    # can persist it via AgentSessionsRepository (Task 6). For now the
    # executor leaves these as None — Task 6 wires the SDK-side extraction
    # behind tests that populate them via mocks.
    return {
        "agent_type": result.agent_type,
        "is_error": result.is_error,
        "error_message": result.error_message,
        "output": result.output,
        "cost_usd": result.cost_usd,
        "duration_ms": result.duration_ms,
        "tools_called": result.tools_called,
        # #470: the orchestrator's AgentRunner reads this back; without it
        # every run's denials stopped at the sandbox boundary.
        "policy_denials": getattr(result, "policy_denials", None) or {},
        "input_tokens": getattr(result, "input_tokens", 0),
        "output_tokens": getattr(result, "output_tokens", 0),
        "cache_read_tokens": getattr(result, "cache_read_tokens", 0),
        "cache_creation_tokens": getattr(result, "cache_creation_tokens", 0),
        "error_code": getattr(result, "error_code", None),
        # #466: ISO-8601 with offset; the orchestrator's AgentRunner reads it back.
        "provider_resets_at": _isoformat_or_none(getattr(result, "provider_resets_at", None)),
        "messages": getattr(result, "messages", None),
        "session_id": getattr(result, "session_id", None),
        "session_blob_path": getattr(result, "session_blob_path", None),
    }


#: The key of the config the orchestrator puts its per-run result nonce under (``--result-nonce``).
RESULT_NONCE_KEY = "result_nonce"


def _read_config(argv: list[str] | None = None) -> tuple[dict[str, Any], str | None, str | None]:
    """Parse the runner CLI into ``(config, session_blob_path, session_id)``.

    The config arrives on stdin (``--config-stdin``, #304): as one argv string
    it is capped by the kernel at MAX_ARG_STRLEN (128 KiB), which a review
    prompt carrying a PR diff exceeds — the exec then fails with E2BIG before
    this process exists. ``--config <json>`` stays for an orchestrator that
    predates the stdin transport.
    """
    parser = argparse.ArgumentParser(description="embry0 sandbox agent runner")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", default=None, help="JSON agent configuration (legacy; size-capped by argv)")
    source.add_argument("--config-stdin", action="store_true", help="Read the JSON agent configuration from stdin")
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
    parser.add_argument(
        "--result-nonce",
        action="store_true",
        help=f"The config carries {RESULT_NONCE_KEY}: write it on the final_result, which the orchestrator takes by it",
    )
    args = parser.parse_args(argv)

    config = json.loads(sys.stdin.read() if args.config_stdin else args.config)
    return config, args.session_blob, args.session_id


_DIAGNOSTIC_LIMIT = 2000


def _one_line(text: str) -> str:
    return " ".join(text.split())


def error_diagnostic(result: dict[str, Any]) -> str:
    """A redacted one-line diagnosis of an is_error run.

    The runner exits 1 with this on stderr and on the final_result payload.
    stdout already carried the result, but the orchestrator used to log only
    the empty stderr, so a subtype-only SDK failure had nothing to diagnose.
    ``error_message`` and ``output`` on the payload stay unredacted — the job
    row needs them — and this copy is what may leave the process in a log.
    """
    secrets = secret_env_values()
    message = _one_line(redact_secrets(str(result.get("error_message") or ""), secrets))[:_DIAGNOSTIC_LIMIT]
    result_text = _one_line(redact_secrets(str(result.get("output") or ""), secrets))[:_DIAGNOSTIC_LIMIT]
    code = result.get("error_code") or "none"
    parts = [f"error_code={code}", f"error_message={message}"]
    if result_text:
        parts.append(f"result={result_text}")
    return " ".join(parts)


def main() -> None:
    config, session_blob, session_id = _read_config()
    # Kept apart, never in the invocation: it is the orchestrator's mark of this run's own result, and goes out
    # only on the final_result line, last.
    nonce = config.pop(RESULT_NONCE_KEY, None)
    kwargs = _build_run_kwargs(
        config,
        session_blob_path=session_blob,
        session_id=session_id,
    )
    result = asyncio.run(run_agent(**kwargs))
    if result.get("is_error"):
        diagnostic = error_diagnostic(result)
        result = {**result, "diagnostic": diagnostic}
        sys.stderr.write(diagnostic + "\n")
        sys.stderr.flush()

    marked: dict[str, Any] = {"type": "final_result", **result}
    if isinstance(OPEN_AT_START, bool):
        marked["open_at_start"] = OPEN_AT_START
    if isinstance(nonce, str):
        marked["nonce"] = nonce
    sys.stdout.write(json.dumps(marked, default=str) + "\n")
    sys.stdout.flush()
    sys.exit(1 if result.get("is_error") else 0)


if __name__ == "__main__":
    main()
