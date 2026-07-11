"""Per-mode session capture + restore.

Goal: when the orchestrator pauses an agent (ask_user interrupt), grab
enough state to continue the SAME conversation on resume, instead of
re-launching the agent process with a stitched-prompt restart.

anthropic_api mode: state == messages (full history).
claude_max mode: state == session_id (CLI's identifier) + bytes of the
  CLI's per-session file (so we can replay it back to the CLI on resume).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from embry0.agents.claude_cli_session import canonical_session_path_for


@dataclass
class AgentSession:
    job_id: str
    agent_type: str
    mode: Literal["anthropic_api", "claude_max"]
    messages: list[dict[str, Any]] | None = None
    session_id: str | None = None
    session_blob: bytes | None = None


def capture_session_after_run(
    result: dict[str, Any],
    *,
    mode: str,
    job_id: str,
    agent_type: str,
) -> AgentSession:
    if mode == "anthropic_api":
        return AgentSession(
            job_id=job_id,
            agent_type=agent_type,
            mode="anthropic_api",
            messages=result.get("messages"),
        )
    if mode == "claude_max":
        sid = result.get("session_id")
        blob_path = result.get("session_blob_path")
        blob = Path(blob_path).read_bytes() if blob_path and Path(blob_path).exists() else None
        return AgentSession(
            job_id=job_id,
            agent_type=agent_type,
            mode="claude_max",
            session_id=sid,
            session_blob=blob,
        )
    raise ValueError(f"unknown agent mode: {mode!r}")


def restore_session_into_options(
    session: AgentSession,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Mutates `options` in place AND returns it (for chaining)."""
    if session.mode == "anthropic_api":
        if session.messages is not None:
            options["messages"] = session.messages
        return options
    if session.mode == "claude_max":
        if session.session_blob and session.session_id:
            home_dir = Path(options.get("home_dir", "/home/agent"))
            project_cwd = options.get("project_cwd")
            target = canonical_session_path_for(
                home_dir=home_dir,
                session_id=session.session_id,
                project_cwd=project_cwd,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(session.session_blob)
            cli_args = options.setdefault("cli_extra_args", [])
            if "--resume" not in cli_args:
                cli_args.extend(["--resume", session.session_id])
        return options
    raise ValueError(f"unknown session mode: {session.mode!r}")
