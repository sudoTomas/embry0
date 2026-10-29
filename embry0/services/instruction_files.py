"""The files that steer a coding agent working in a repository.

One list, read by the context-audit schedule and by the command each audit's
task carries (:mod:`embry0.services.context_audit_command`), so the files the
schedule fingerprints are the files the agent audits. A coding agent loads some of them on its own (``CLAUDE.md`` at any
depth, ``AGENTS.md``, a rules directory) and is told to read the rest (a
handoff note, a skill), so a stale line in any of them reaches the agent as
an instruction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

#: Repository-relative glob patterns. ``**/`` matches any number of
#: directories, none included; ``*`` stays inside one path segment.
INSTRUCTION_FILE_GLOBS: Final[tuple[str, ...]] = (
    "**/CLAUDE.md",
    "**/CLAUDE.local.md",
    "**/AGENTS.md",
    "**/AGENTS.override.md",
    "**/GEMINI.md",
    "**/HANDOFF.md",
    "**/llms.txt",
    "**/.cursorrules",
    "**/.windsurfrules",
    "**/.clinerules",
    ".github/copilot-instructions.md",
    ".github/instructions/**/*.instructions.md",
    "**/.cursor/rules/**/*.mdc",
    "**/.cursor/rules/**/*.md",
    "**/.claude/skills/**/*.md",
    "**/.claude/commands/**/*.md",
    "**/.claude/agents/**/*.md",
    "**/.claude/rules/**/*.md",
    "**/.grok/skills/**/*.md",
    "**/.grok/rules/**/*.md",
    "**/.codex/**/*.md",
    "**/.agents/**/*.md",
)

#: Directories never searched: vendored or generated trees, whose instruction
#: files belong to another project.
SKIPPED_DIRS: Final[frozenset[str]] = frozenset({".git", ".venv", "build", "dist", "node_modules", "vendor", "venv"})


def _compile(glob: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


_PATTERNS: Final = tuple(_compile(glob) for glob in INSTRUCTION_FILE_GLOBS)


def is_instruction_file(path: str) -> bool:
    """Whether a repository-relative POSIX path is a file that steers an agent."""
    if any(part in SKIPPED_DIRS for part in path.split("/")[:-1]):
        return False
    return any(pattern.match(path) for pattern in _PATTERNS)


def instruction_files(paths: Iterable[str]) -> list[str]:
    """The instruction files among ``paths``, sorted."""
    return sorted(path for path in paths if is_instruction_file(path))
