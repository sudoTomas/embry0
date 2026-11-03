"""The files that steer a coding agent working in a repository.

A coding agent loads some of them on its own (``CLAUDE.md`` at any depth,
``AGENTS.md``, a rules directory) and is told to read the rest (a handoff
note, a skill), so a stale line in any of them reaches the agent as an
instruction.

Which files those are is a :class:`FileRules`: the globs a path must match
and the directories never searched. :data:`DEFAULT_RULES` holds the built-in
lists below; the settings document's ``context_audit`` section replaces
either one, for the deployment, an organization or a repository (#170). One
set of rules is read by the context-audit schedule, which selects and
fingerprints the files with it, and by the command each audit's task
carries (:mod:`embry0.services.context_audit_command`), which lists the same
files with git, so the files the schedule fingerprints are the files the
agent audits.

A glob is written in the grammar both readers agree on (:func:`check_glob`),
and a directory as one name (:func:`check_dir_name`). The settings section
checks what an operator writes with them, so a list that reaches either
reader is one they read alike. The matcher follows git's own rule for a
glob pathspec (:meth:`FileRules.select`): a path is selected when it lies
under the glob as a directory, or when the glob matches it whole. So a glob with no wildcard that names a directory, such as
``.kiro/steering``, selects every tracked file under it, whatever its kind,
on both sides; ``.kiro/steering/*.md`` selects its Markdown alone.

This module imports nothing of embry0, so a settings section may import it.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable
from dataclasses import dataclass
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

#: The longest glob :func:`check_glob` takes, and the most globs one list holds.
MAX_GLOB_LENGTH: Final = 200
MAX_GLOBS: Final = 64
#: The longest directory name :func:`check_dir_name` takes, and the most one list holds.
MAX_DIR_LENGTH: Final = 100
MAX_SKIPPED_DIRS: Final = 32

#: What a glob may hold. ``?``, ``[`` and ``\`` are wildcards or escapes to
#: git that the matcher reads literally, and a quote or a space would end the
#: command's argument, so none of them is taken.
_GLOB_CHARACTERS: Final = re.compile(r"[A-Za-z0-9._+@*/-]+\Z")
#: What a directory name may hold: a glob's characters, without ``*`` and ``/``.
_DIR_CHARACTERS: Final = re.compile(r"[A-Za-z0-9._+@-]+\Z")


def check_glob(glob: str) -> str:
    """``glob`` when the matcher and git's glob pathspec read it alike; else :class:`ValueError`.

    A glob is path segments joined by ``/``, relative to the repository's
    root. A segment is ``**`` (any number of directories, none included), or
    letters, digits, ``.``, ``_``, ``-``, ``+``, ``@`` and ``*`` (any
    characters within that one segment). The last segment is never ``**``:
    a glob with no wildcard that names a directory already selects every
    file under it (:meth:`FileRules.select`). The message never repeats the
    text.
    """
    if not glob:
        raise ValueError("write a glob, such as **/CLAUDE.md")
    if len(glob) > MAX_GLOB_LENGTH:
        raise ValueError(f"a glob is at most {MAX_GLOB_LENGTH} characters")
    if not _GLOB_CHARACTERS.match(glob):
        raise ValueError("a glob holds only letters, digits, '.', '_', '-', '+', '@', '*' and '/'")
    segments = glob.split("/")
    if not all(segments):
        raise ValueError("a glob starts at the repository's root: no leading, trailing or doubled '/'")
    if any(segment in (".", "..") for segment in segments):
        raise ValueError("a glob names no '.' or '..' directory")
    if any("**" in segment and segment != "**" for segment in segments):
        raise ValueError("'**' stands alone between slashes, as in **/CLAUDE.md or docs/**/*.md")
    if any(a == b == "**" for a, b in zip(segments, segments[1:], strict=False)):
        raise ValueError("write '**' once in a row: **/**/ matches what **/ does")
    if segments[-1] == "**":
        raise ValueError("'**' is never last: write docs/**/*.md for the Markdown under docs, or docs for every file")
    return glob


def check_dir_name(name: str) -> str:
    """``name`` when it is one directory name; else :class:`ValueError`, whose message never repeats it."""
    if len(name) > MAX_DIR_LENGTH:
        raise ValueError(f"a directory name is at most {MAX_DIR_LENGTH} characters")
    if not _DIR_CHARACTERS.match(name) or name in (".", ".."):
        raise ValueError(
            "a directory is one name of letters, digits, '.', '_', '-', '+' and '@', "
            "such as node_modules: no '/', no wildcard, not '.' or '..'"
        )
    return name


@functools.lru_cache(maxsize=1024)
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


@dataclass(frozen=True)
class FileRules:
    """Which files of a repository are instruction files: the globs a path matches and the directories
    never searched. With no argument, the built-in lists (:data:`DEFAULT_RULES`)."""

    globs: tuple[str, ...] = INSTRUCTION_FILE_GLOBS
    skipped_dirs: frozenset[str] = SKIPPED_DIRS

    @classmethod
    def of(cls, globs: Iterable[str] | None, skipped_dirs: Iterable[str] | None) -> FileRules:
        """The rules a written list of each makes, None standing for the built-in list."""
        return cls(
            INSTRUCTION_FILE_GLOBS if globs is None else tuple(globs),
            SKIPPED_DIRS if skipped_dirs is None else frozenset(skipped_dirs),
        )

    def matches(self, path: str) -> bool:
        """Whether a repository-relative POSIX path is a file that steers an agent."""
        return bool(self.select([path]))

    def select(self, paths: Iterable[str]) -> list[str]:
        """The instruction files among ``paths``, sorted: those ``git ls-files`` lists for the command's pathspecs.

        Git selects a path for a glob pathspec when it starts with the
        pattern and a ``/`` (the pattern names a directory the path lies in,
        read literally, wildcards included), or when the pattern matches the
        whole path, ``*`` within one segment; a pattern always matches its own
        text. A path in a skipped directory, at any depth, is left out, as the
        command's excludes leave it.
        """
        compiled = [(f"{glob}/", _compile(glob)) for glob in self.globs]
        return sorted(
            path
            for path in paths
            if not any(part in self.skipped_dirs for part in path.split("/")[:-1])
            and any(path.startswith(under) or pattern.match(path) for under, pattern in compiled)
        )


#: The built-in rules: what a repository reads while no settings document names its own.
DEFAULT_RULES: Final = FileRules()
