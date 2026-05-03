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
on-disk format spec, current as of claude-code 2.1.92.
"""

from __future__ import annotations

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
        matches = list(projects_dir.glob(f"*/{session_id}.jsonl"))
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
