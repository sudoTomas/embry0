"""The shell commands a skill runs when it is invoked, and the skills a session loaded.

Claude Code runs the commands in a skill's (or a command's) Markdown before
the skill's text reaches the model: an inline ``!`command``` and a fenced block
opened with ```` ```! ````. The CLI runs them itself, so no tool call reaches
embry0's ``PreToolUse`` hook for them. ``gate_skill_call``
(embry0.safety.policy) checks every command a skill runs as a Bash call.

The CLI runs the text it loaded, not the file as it is when the skill is
invoked: it reads every skill when the session starts and keeps that text. So
:meth:`SkillSnapshot.take` reads every skill file the session can load before
the session starts: the workspace's ``.claude/skills/<name>/SKILL.md`` and
``.claude/commands/**/<name>.md``, and a mounted skill source's
``skills/<name>/SKILL.md`` and ``commands/**/<name>.md`` (also named
``<plugin>:<name>``). Each must resolve inside its own directory. The SDK
executor keeps the CLI from loading a skill again during the session (its
``ConfigChange`` hook blocks every reload) and calls :meth:`SkillSnapshot.settle`
once the CLI has loaded them: a skill whose file, or any directory from
``.claude`` (or the skill source) down to it, changed between the two (by
inode, mode, size, mtime or ctime, which no process can set back) may have
been loaded in another form, and is refused. A directory renamed away and
back, or a symlink flipped and restored, changes its parent's mtime and its
own ctime.

A skill is checked as it was in the snapshot and as its file is now; both
must pass. A skill the snapshot does not hold is refused: one written during
the session, one found in a subdirectory (the CLI loads those from
``<dir>/.claude/skills`` when the agent touches a file there, at a moment
embry0 cannot pin), a ``<dir>:<name>`` variant of one, and the CLI's own. So is
a file larger than 1 MiB, not UTF-8, or whose frontmatter does not close:
embry0 cannot say what it runs.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

# The tools that invoke a skill or a command.
SKILL_TOOLS: Final[frozenset[str]] = frozenset({"Skill", "SlashCommand"})

_MAX_SKILL_BYTES: Final[int] = 1024 * 1024
_NAME: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(?::[A-Za-z0-9_][A-Za-z0-9_.-]*)*")
# The forms the CLI runs (its own patterns): a fenced block opened with ```!,
# and !`…` at the start of a line or after whitespace.
_BLOCK: Final[re.Pattern[str]] = re.compile(r"```!\s*\n?([\s\S]*?)\n?```")
_INLINE: Final[re.Pattern[str]] = re.compile(r"(?:^|(?<=\s))!`([^`]+)`", re.MULTILINE)
_CODE_SPAN: Final[re.Pattern[str]] = re.compile(r"`[^`\n]+`")
# $ARGUMENTS, $ARGUMENTS[N] and $N, and their ${…} forms: the CLI fills in the
# first ones; the braced ones are read as filled in too, in case a CLI does.
_ARGUMENT_PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"\$\{?ARGUMENTS(?:\[\d+\])?\}?|\$\{?\d+\}?")


class SkillError(ValueError):
    """A skill whose commands cannot be checked."""


@dataclass(frozen=True)
class SkillCall:
    name: str
    arguments: str


def skill_call(tool_name: str, tool_input: Mapping[str, Any]) -> SkillCall:
    """The skill a ``Skill`` or ``SlashCommand`` call invokes, and the arguments it passes."""
    if tool_name == "SlashCommand":
        command = str(tool_input.get("command") or "").strip()
        name, _, arguments = command.partition(" ")
    else:
        name = str(tool_input.get("skill") or "")
        arguments = str(tool_input.get("args") or "")
    name = name.strip().removeprefix("/")
    if not _NAME.fullmatch(name):
        raise SkillError(f"skill name {name!r} is not one embry0 can look up")
    return SkillCall(name=name, arguments=arguments)


def _inside(path: str, base: str) -> bool:
    return os.path.commonpath([path, base]) == base


_MAX_SKILL_FILES: Final[int] = 2000
_MAX_COMMAND_DEPTH: Final[int] = 4


_Identity = tuple[str, tuple[int, ...] | None]


def _lidentity(path: str) -> _Identity:
    """A path's identity by lstat (a symlink's own), or None when it is not there."""
    try:
        st = os.lstat(path)
    except OSError:
        return (path, None)
    return (path, (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns))


def _chain(base: str, path: str) -> tuple[_Identity, ...]:
    """The identity of every directory from ``base`` down to ``path``, and of ``path``: as written, and resolved.

    Renaming a directory, or replacing one or a symlink and putting it back,
    changes the parent's mtime and the moved entry's ctime, and no process can
    set a ctime back; so an unchanged chain means the same file was there.
    """

    def components(top: str, leaf: str) -> list[str]:
        relative = os.path.relpath(leaf, top)
        parts = [] if relative == "." else relative.split(os.sep)
        return [top] + [os.path.join(top, *parts[: i + 1]) for i in range(len(parts))]

    written = components(base, path)
    resolved = components(os.path.realpath(base), os.path.realpath(path))
    return tuple(_lidentity(p) for p in dict.fromkeys(written + resolved))


@dataclass(frozen=True)
class SkillFile:
    """One file the CLI may load for a skill, as it was when the session started."""

    root: str  # the directory it must resolve inside
    path: str  # where the CLI reads it
    real: str  # where it resolved
    chain: tuple[_Identity, ...]  # every directory from root down, and the file (_chain)
    text: str | None  # None when it could not be read
    error: str = ""


def _frontmatter_name(text: str | None) -> str:
    for line in (_frontmatter(text or "") or "").splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "name" and not line[:1].isspace():
            return value.strip().strip("'\"")
    return ""


def _load(root: str, path: str) -> SkillFile:
    real_root = os.path.realpath(root)
    real = os.path.realpath(path)
    chain = _chain(root, path)
    if not _inside(real, real_root):
        return SkillFile(root, path, real, chain, None, f"{path} resolves outside {root}")
    try:
        text = read_skill(Path(real))
    except SkillError as exc:
        return SkillFile(root, path, real, chain, None, str(exc))
    return SkillFile(root, path, real, chain, text)


class SkillSnapshot:
    """The skill files a session can load, read before it starts."""

    def __init__(
        self, files: Mapping[str, list[SkillFile]], *, skill_dirs: Mapping[str, str], hidden: bool = False
    ) -> None:
        self.files = {name: list(found) for name, found in files.items()}
        self.skill_dirs = dict(skill_dirs)
        self.changed: set[str] = set()
        self.settled = False
        # The session loads no skill (AgentInvocation.skills_hidden), so none may run.
        self.hidden = hidden

    @classmethod
    def take(cls, workspace: str, skill_dirs: Mapping[str, str]) -> SkillSnapshot:
        """Read every skill the CLI can load from the workspace and the mounted skill sources."""
        files: dict[str, list[SkillFile]] = {}
        count = 0

        def add(names: list[str], found: SkillFile) -> None:
            nonlocal count
            count += 1
            if count > _MAX_SKILL_FILES:
                raise SkillError(f"more than {_MAX_SKILL_FILES} skill files, more than embry0 reads")
            extra = _frontmatter_name(found.text)
            for name in dict.fromkeys([*names, *([extra] if extra else [])]):
                files.setdefault(name, []).append(found)

        sources = [("", os.path.join(workspace, ".claude"))] + [
            (plugin, directory) for plugin, directory in skill_dirs.items()
        ]
        for plugin, base in sources:
            prefix = f"{plugin}:" if plugin else ""
            skills = os.path.join(base, "skills")
            if os.path.isdir(skills):
                for entry in sorted(os.listdir(skills)):
                    folder = os.path.join(skills, entry)
                    if not os.path.isdir(folder):
                        continue
                    for leaf in sorted(os.listdir(folder)):
                        if leaf.lower() == "skill.md":
                            found = _load(base, os.path.join(folder, leaf))
                            add([prefix + entry, entry] if plugin else [entry], found)
            commands = os.path.join(base, "commands")
            if os.path.isdir(commands):
                for directory, subdirs, names in os.walk(commands):
                    relative = os.path.relpath(directory, commands)
                    parts = [] if relative == "." else relative.split(os.sep)
                    if len(parts) >= _MAX_COMMAND_DEPTH:
                        subdirs[:] = []
                    for leaf in sorted(names):
                        if not leaf.endswith(".md"):
                            continue
                        stem = leaf[: -len(".md")]
                        found = _load(base, os.path.join(directory, leaf))
                        names_of = [stem, ":".join([*parts, stem])]
                        if plugin:
                            names_of += [prefix + n for n in names_of]
                        add(names_of, found)
        return cls(files, skill_dirs=skill_dirs)

    def settle(self) -> None:
        """Once the CLI has loaded its skills: a skill whose file, or any directory above it, changed since the
        snapshot may have loaded in another form."""
        if self.settled:
            return
        self.settled = True
        for name, found in self.files.items():
            for skill in found:
                if _chain(skill.root, skill.path) != skill.chain:
                    self.changed.add(name)

    def texts(self, name: str) -> list[str]:
        """The texts the CLI may run for skill ``name``: as loaded, and as its file is now.

        Raises SkillError when the snapshot has no such skill, when it changed
        while the CLI loaded it, or when a file cannot be read.
        """
        if self.hidden:
            raise SkillError(f"skill {name!r}: this agent's run loads no skill")
        found = self.files.get(name)
        if not found:
            raise SkillError(
                f"skill {name!r} was not in the workspace's .claude/skills or .claude/commands, or a mounted "
                "skill source, when the session started, so the commands it runs cannot be checked"
            )
        if name in self.changed:
            raise SkillError(f"skill {name!r} changed while the session loaded it, so what it runs is not known")
        texts: list[str] = []
        for skill in found:
            if skill.text is None:
                raise SkillError(f"skill {name!r}: {skill.error}")
            texts.append(skill.text)
            if not os.path.lexists(skill.path):
                continue
            now = _load(skill.root, skill.path)
            if now.text is None:
                raise SkillError(f"skill {name!r}: {now.error}")
            if now.text != skill.text:
                texts.append(now.text)
        return texts


def read_skill(path: Path) -> str:
    """A skill file's text; SkillError when it is too large, not UTF-8, or its frontmatter does not close."""
    try:
        if path.stat().st_size > _MAX_SKILL_BYTES:
            raise SkillError(f"skill file {path} is larger than {_MAX_SKILL_BYTES} bytes")
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise SkillError(f"skill file {path} is not UTF-8") from None
    except OSError as exc:
        raise SkillError(f"skill file {path} cannot be read: {exc.strerror}") from None
    if text.startswith("---") and _frontmatter(text) is None:
        raise SkillError(f"skill file {path} has frontmatter that does not close")
    return text


def _frontmatter(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:i])
    return None


def _argument_names(frontmatter: str) -> list[str]:
    """The names a skill's frontmatter declares under ``arguments:``."""
    names: list[str] = []
    lines = frontmatter.splitlines()
    for i, line in enumerate(lines):
        key, sep, value = line.partition(":")
        if not sep or key.strip() != "arguments" or line[:1].isspace():
            continue
        names += re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", value)
        for item in lines[i + 1 :]:
            if item[:1].strip():
                break
            names += re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", item.strip().lstrip("-"))[:1]
    return names


def skill_commands(text: str, arguments: str) -> list[str]:
    """The shell commands ``text`` runs when invoked with ``arguments``.

    Each command as written, and, when there are arguments, also with every
    argument placeholder (``$ARGUMENTS``, ``$ARGUMENTS[N]``, ``$N`` and the
    names the frontmatter declares) replaced by the arguments.
    """
    text = text.replace("\r\n", "\n")
    # The CLI first blanks every inline code span not opened right after a ! or a `.
    blanked = _CODE_SPAN.sub(
        lambda m: (
            m.group() if m.start() > 0 and text[m.start() - 1] in "!`" else "`" + " " * (len(m.group()) - 2) + "`"
        ),
        text,
    )
    found = [m.group(1) for m in _BLOCK.finditer(text)] + [m.group(1) for m in _INLINE.finditer(blanked)]
    commands = [c.strip() for c in found if c.strip()]
    if not arguments:
        return commands
    names = _argument_names(_frontmatter(text) or "")
    named = re.compile(r"\$(?:" + "|".join(re.escape(n) for n in names) + r")\b") if names else None
    filled = []
    for command in commands:
        with_arguments = _ARGUMENT_PLACEHOLDER.sub(lambda _m: arguments, command)
        if named is not None:
            with_arguments = named.sub(lambda _m: arguments, with_arguments)
        if with_arguments != command:
            filled.append(with_arguments)
    return commands + filled
