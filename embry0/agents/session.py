"""Agent session state + transcript-replay rendering.

Goal: when the orchestrator re-enters an agent (ask_user answer, review
retry, QA-failure loop), continue the SAME conversation instead of
re-launching with a stitched-prompt restart.

The primary mechanism is the CLI's file-based resume (EMB-35): the
executor captures ``session_id`` + the CLI's session JSONL in any auth
mode, AgentRunner extracts the bytes before the sandbox dies, and the
next run stages them back to the canonical path and passes
``--session-id`` (see ``AgentRunner._stage_resume_session`` and
``SdkAgentExecutor.run(resume_session_id=...)``).

``render_transcript_block`` is the bounded text-replay FALLBACK for
sessions that only carry a messages list (legacy rows, missing or
oversized blob) — lossier and re-billed as input, so it is used only
when file-based resume is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# Byte budget for the replay-fallback transcript. Roughly 8k tokens —
# enough to carry the shape of a prior conversation without re-billing
# a whole session's worth of input on every retry.
TRANSCRIPT_MAX_BYTES = 30_000

# Session JSONLs grow with every turn (they embed full tool results).
# Past this size, staging + CLI reload cost more than a fresh start
# guided by the messages-replay fallback — blob resume is skipped and
# staging falls through (see AgentRunner._stage_resume_session).
MAX_RESUME_BLOB_BYTES = 5 * 1024 * 1024


@dataclass
class AgentSession:
    job_id: str
    agent_type: str
    mode: Literal["anthropic_api", "claude_max"]
    messages: list[dict[str, Any]] | None = None
    session_id: str | None = None
    session_blob: bytes | None = None


def session_resumable(session: AgentSession | None) -> bool:
    """True when the session will actually resume at staging time.

    Mirrors ``AgentRunner._stage_resume_session`` exactly — workflow nodes
    use this to decide between a delta-only prompt (resume engages) and a
    full prompt rebuild (fresh conversation). The two MUST stay in sync:
    a node that sends a delta prompt into a fresh conversation strands the
    agent without context.
    """
    if session is None:
        return False
    if session.session_id and session.session_blob and len(session.session_blob) <= MAX_RESUME_BLOB_BYTES:
        return True
    return bool(session.messages)


def merged_resume_messages(
    resume_session: AgentSession | None,
    new_messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Combine prior + newly-captured messages for persistence.

    A RESUMED run's captured messages hold only the delta conversation
    (the delta prompt + new assistant turns) — persisting them alone would
    shrink the replay-fallback transcript on every resume generation.
    Prepend the prior messages when resume actually engaged; a fresh run
    (resume not engaged) captured the complete conversation already.
    """
    if session_resumable(resume_session) and resume_session and resume_session.messages and new_messages:
        return list(resume_session.messages) + list(new_messages)
    return new_messages


def render_transcript_block(
    messages: list[dict[str, Any]],
    max_bytes: int = TRANSCRIPT_MAX_BYTES,
) -> str:
    """Render a prior [{role, content}] list as a bounded prompt preamble.

    Keeps the MOST RECENT messages: entries are accumulated newest-first
    until the byte budget is spent, then emitted in chronological order
    with a truncation marker when anything was dropped. An over-budget
    single message is tail-truncated rather than dropped so the latest
    context always survives.
    """
    kept: list[str] = []
    used = 0
    truncated = False
    for msg in reversed(messages):
        role = str(msg.get("role", "unknown")).upper()
        content = str(msg.get("content", ""))
        line = f"[{role}]: {content}"
        size = len(line.encode("utf-8"))
        if used + size > max_bytes:
            remaining = max_bytes - used
            if not kept and remaining > 200:
                # Nothing kept yet — take the tail of this message rather
                # than returning an empty transcript.
                encoded = line.encode("utf-8")[-remaining:]
                kept.append("…" + encoded.decode("utf-8", errors="ignore"))
            truncated = True
            break
        kept.append(line)
        used += size
    kept.reverse()
    marker = "(older turns truncated)\n" if truncated else ""
    body = "\n".join(kept)
    return (
        "## Prior conversation transcript\n"
        "Your previous session could not be resumed directly; this is a text "
        "replay of the conversation so far. Continue from this state — do not "
        "redo completed work.\n"
        f"{marker}{body}\n"
        "## End of transcript"
    )
