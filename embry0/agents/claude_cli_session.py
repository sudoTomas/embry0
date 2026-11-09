"""Discovery of Claude CLI session files.

The Claude CLI persists per-session conversation state as a JSONL file.
Its on-disk location has changed once (sessions/ → projects/<sanitized-cwd>/)
and is likely to move again with future CLI versions. This module is the
single source of truth for "where does the CLI keep session files" — both
when reading them out (for AgentSession capture) and when writing them
back in (for resume staging).

If the CLI moves the file again, change ONLY this module. The executor
and runner consume `find_session_file` / `canonical_session_path_for` and
don't carry a path string of their own.

See docs/contracts/claude-cli-session-file-layout.md for the full
on-disk format spec, verified on claude-code 2.1.316 (#288; first on 2.1.292, #501).
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def sanitize_cwd_for_session_dir(cwd: str) -> str:
    """Convert a working-directory path to the form the Claude CLI uses
    as the projects/<dir>/ name. Pinned to claude-code 2.1.92 behavior:
    leading slash kept as a leading dash, internal slashes replaced with
    dashes."""
    return cwd.replace("/", "-")


def canonical_session_path_for(
    *,
    home_dir: Path,
    session_id: str,
    project_cwd: str | None = None,
) -> Path:
    """The canonical path we WRITE to when staging a session blob in for
    resume. Matches where the current CLI version will look first.

    If project_cwd is provided, returns the projects/<sanitized-cwd>/ path
    (current CLI). If unknown, falls back to legacy sessions/ layout."""
    claude_root = home_dir / ".claude"
    if project_cwd:
        return claude_root / "projects" / sanitize_cwd_for_session_dir(project_cwd) / f"{session_id}.jsonl"
    return claude_root / "sessions" / f"{session_id}.jsonl"


def find_session_file(
    *,
    home_dir: Path,
    session_id: str,
    project_cwd: str | None = None,
) -> Path | None:
    """Find a Claude CLI session file by id. Returns the discovered path
    or None if not found.

    Search priority:
    1. ~/.claude/projects/<sanitized-cwd>/<session_id>.jsonl  (current CLI, when cwd known)
    2. ~/.claude/projects/*/<session_id>.jsonl                (current CLI, glob fallback)
    3. ~/.claude/sessions/<session_id>.jsonl                  (legacy CLI)
    """
    claude_root = home_dir / ".claude"

    if project_cwd:
        candidate = claude_root / "projects" / sanitize_cwd_for_session_dir(project_cwd) / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate

    projects_dir = claude_root / "projects"
    if projects_dir.is_dir():
        matches = sorted(projects_dir.glob(f"*/{session_id}.jsonl"))
        if matches:
            if len(matches) > 1:
                logger.warning(
                    "multiple_session_files_found_picking_first",
                    session_id=session_id,
                    matches=[str(p) for p in matches],
                )
            return matches[0]

    legacy = claude_root / "sessions" / f"{session_id}.jsonl"
    if legacy.exists():
        return legacy

    logger.info(
        "session_file_not_found",
        session_id=session_id,
        home_dir=str(home_dir),
        project_cwd=project_cwd,
    )
    return None


def restored_cost_usd(session_file: Path) -> float:
    """The session cost the CLI restores when it resumes ``session_file``.

    Since claude-code 2.1.277 a headless run saves its running totals at exit
    as a ``{"type": "cost-state", "totalCostUSD": ...}`` line, and a resume
    starts from the last one, so the resumed run's ``total_cost_usd`` includes
    every earlier run of the session. 0.0 when the file has no such line (an
    older CLI) or cannot be read.
    """
    restored = 0.0
    try:
        with session_file.open(encoding="utf-8") as lines:
            for line in lines:
                if '"cost-state"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except (ValueError, RecursionError):  # see embry0.json_text
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "cost-state":
                    continue
                total = entry.get("totalCostUSD")
                if isinstance(total, int | float) and not isinstance(total, bool):
                    restored = float(total)
    except OSError as exc:
        logger.warning("session_cost_state_unreadable", path=str(session_file), error=str(exc))
        return 0.0
    return restored
