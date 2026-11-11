"""Path-glob matching for conditional acceptance criteria (EMB-39), test targets, merge denylists and areas.

Matches repo-relative POSIX paths (as produced by ``git diff --name-only``)
against gitignore-style glob patterns. Hand-rolled because ``fnmatch`` is not
path-aware (``*`` crosses ``/``) and ``PurePath.full_match`` requires 3.13.

Grammar — patterns match the FULL path, anchored at both ends:

- ``*``  — any run of characters within one segment (never crosses ``/``)
- ``?``  — exactly one character within a segment
- ``[...]`` — character class (``[!...]`` negates); may not contain ``/``,
  and matches one character of one segment. ``[[]`` is a literal ``[``.
- ``**`` — zero or more whole segments; must be its own segment.
  Trailing ``X/**`` requires at least one segment under ``X`` (so
  ``apps/web/**`` matches ``apps/web/page.tsx`` but not
  ``apps/web`` itself), while ``a/**/b`` matches ``a/b``.

Rejected at compile time: empty patterns, leading ``/``, empty segments,
``..`` segments, ``**`` glued to other characters, unterminated classes.

Matching runs in bounded time: a path is matched segment by
segment, a set of pattern positions advanced per segment, and each segment
character by character the same way, so no pattern backtracks. The cost is
at most the pattern's length times the path's, whatever ``**`` and ``*`` it
holds; a regex of the same glob can take seconds on a long path.

A glob is written with :func:`compile_glob` and read back with
:func:`read_glob`. The release before translated a glob to a regex, and read
a class ending in a backslash (``[\\]``) as one class with whatever followed
it. This grammar refuses such a glob, so none is written any more; one
stored before is read as that release read it, through its regex, so a
stored setup keeps working after the upgrade.
"""

from __future__ import annotations

import functools
import re
import warnings
from collections.abc import Sequence
from typing import Final

import structlog

__all__ = ["Glob", "GlobPatternError", "check_glob", "compile_glob", "match_any", "read_glob"]

logger = structlog.get_logger(__name__)


class GlobPatternError(ValueError):
    """A conditional-criteria glob pattern is malformed."""


#: A token of one segment: a literal character, ``?``, ``*``, or a character class (a one-character regex).
_Token = tuple[str, "str | re.Pattern[str]"]


def _segment_tokens(segment: str, *, pattern: str) -> list[_Token]:
    out: list[_Token] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch in "*?":
            out.append((ch, ch))
            i += 1
        elif ch == "[":
            j = i + 1
            if j < len(segment) and segment[j] in "!^":
                j += 1
            if j < len(segment) and segment[j] == "]":
                j += 1  # a leading ']' is a literal member of the class
            while j < len(segment) and segment[j] != "]":
                j += 1
            if j >= len(segment):
                raise GlobPatternError(f"unterminated character class in glob {pattern!r}")
            cls = segment[i + 1 : j]
            if "/" in cls:
                raise GlobPatternError(f"character class may not contain '/' in glob {pattern!r}")
            if (len(cls) - len(cls.rstrip("\\"))) % 2:
                raise GlobPatternError(
                    f"a character class ends in a backslash in glob {pattern!r}: a backslash in a class is written \\\\"
                )
            if cls.startswith("!"):
                cls = "^" + cls[1:]
            # '[' and a doubled '&', '~' or '|' are literal members, escaped so Python never reads them as a nested
            # set or a set operation; ``[[]`` is how a glob names a literal '['.
            cls = re.sub(r"(?<!\\)([\[&~|])", r"\\\1", cls)
            try:
                out.append(("[", re.compile("[" + cls + "]")))
            except re.error as exc:
                raise GlobPatternError(f"character class cannot be read in glob {pattern!r}") from exc
            i = j + 1
        else:
            out.append(("c", ch))
            i += 1
    return out


class _Segment:
    """One segment of a glob that is not ``**``: matched character by character, never backtracking."""

    __slots__ = ("_literal", "_tokens")

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        literal = all(kind == "c" for kind, _ in tokens)
        self._literal = "".join(str(value) for _, value in tokens) if literal else None

    def _closure(self, states: set[int]) -> set[int]:
        out: set[int] = set()
        stack = list(states)
        while stack:
            i = stack.pop()
            if i in out:
                continue
            out.add(i)
            if i < len(self._tokens) and self._tokens[i][0] == "*":
                stack.append(i + 1)
        return out

    def matches(self, text: str) -> bool:
        if self._literal is not None:
            return text == self._literal
        end = len(self._tokens)
        states = self._closure({0})
        for ch in text:
            following: set[int] = set()
            for i in states:
                if i == end:
                    continue
                kind, value = self._tokens[i]
                if kind == "*":
                    following.add(i)
                elif kind == "?" or (kind == "c" and value == ch):
                    following.add(i + 1)
                elif kind == "[" and isinstance(value, re.Pattern) and value.fullmatch(ch):
                    following.add(i + 1)
            if not following:
                return False
            states = self._closure(following)
        return end in states


#: ``**`` anywhere but last: zero or more whole segments, none of them empty.
_ANY_SEGMENTS: Final = "**"
#: ``**`` last: all the rest of the path, which must hold at least one character.
_REST: Final = "**$"

_Part = _Segment | str


class Glob:
    """A compiled glob: :meth:`matches` says whether a whole path matches it, in time bounded by their lengths."""

    __slots__ = ("_parts", "pattern")

    def __init__(self, pattern: str, parts: list[_Part]) -> None:
        self.pattern = pattern
        self._parts = parts

    def __repr__(self) -> str:
        return f"Glob({self.pattern!r})"

    def _closure(self, states: set[int]) -> set[int]:
        out: set[int] = set()
        stack = list(states)
        while stack:
            i = stack.pop()
            if i in out:
                continue
            out.add(i)
            if i < len(self._parts) and self._parts[i] == _ANY_SEGMENTS:
                stack.append(i + 1)
        return out

    def matches(self, path: str) -> bool:
        """Whether all of ``path`` matches. A set of pattern positions moves on one segment at a time."""
        segments = path.split("/")
        end = len(self._parts)
        rest = end - 1 if end and self._parts[-1] == _REST else None
        states = self._closure({0})
        for k, segment in enumerate(segments):
            if rest in states and (segment or k < len(segments) - 1):
                return True  # a trailing ** takes what is left, which is not empty
            following: set[int] = set()
            for i in states:
                if i in (end, rest):
                    continue
                part = self._parts[i]
                if part == _ANY_SEGMENTS:
                    if segment:
                        following.add(i)
                elif isinstance(part, _Segment) and part.matches(segment):
                    following.add(i + 1)
            if not following:
                return False
            states = self._closure(following)
        return end in states


def compile_glob(pattern: str) -> Glob:
    """Compile one glob. Raises GlobPatternError."""
    if not isinstance(pattern, str) or not pattern:
        raise GlobPatternError("glob pattern must be a non-empty string")
    if pattern.startswith("/"):
        raise GlobPatternError(f"glob must be repo-relative (no leading '/'): {pattern!r}")
    segments = pattern.split("/")
    if any(s == "" for s in segments):
        raise GlobPatternError(f"empty path segment in glob {pattern!r}")
    if any(s == ".." for s in segments):
        raise GlobPatternError(f"'..' segments are not allowed in glob {pattern!r}")

    collapsed: list[str] = []
    for seg in segments:
        if "**" in seg and seg != "**":
            raise GlobPatternError(f"'**' must be its own path segment in glob {pattern!r}")
        if seg == "**" and collapsed and collapsed[-1] == "**":
            continue  # a/**/**/b ≡ a/**/b
        collapsed.append(seg)

    parts: list[_Part] = []
    for idx, seg in enumerate(collapsed):
        if seg == "**":
            # Mid-pattern: zero or more whole segments. Trailing: the rest of the path, at least one character.
            parts.append(_REST if idx == len(collapsed) - 1 else _ANY_SEGMENTS)
        else:
            parts.append(_Segment(_segment_tokens(seg, pattern=pattern)))
    return Glob(pattern, parts)


class _StoredGlob(Glob):
    """A stored glob this grammar refuses and the release before read: matched by that release's own regex."""

    __slots__ = ("_regex",)

    def __init__(self, pattern: str, regex: re.Pattern[str]) -> None:
        super().__init__(pattern, [])
        self._regex = regex

    def matches(self, path: str) -> bool:
        return self._regex.match(path) is not None


def _translated_segment(segment: str) -> str | None:
    """One segment as the release before translated it, or None where that raised."""
    out: list[str] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            j = i + 1
            if j < len(segment) and segment[j] in "!^":
                j += 1
            if j < len(segment) and segment[j] == "]":
                j += 1
            while j < len(segment) and segment[j] != "]":
                j += 1
            cls = segment[i + 1 : j]
            if j >= len(segment) or "/" in cls:
                return None
            out.append("[" + ("^" + cls[1:] if cls.startswith("!") else cls) + "]")
            i = j
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def _regex_before(pattern: str) -> re.Pattern[str] | None:
    """``pattern`` as the release before compiled it, an anchored regex, or None where it refused it."""
    segments = pattern.split("/")
    if pattern.startswith("/") or "" in segments or ".." in segments:
        return None
    collapsed: list[str] = []
    for seg in segments:
        if "**" in seg and seg != "**":
            return None
        if not (seg == "**" and collapsed and collapsed[-1] == "**"):
            collapsed.append(seg)
    parts: list[str] = []
    for idx, seg in enumerate(collapsed):
        last = idx == len(collapsed) - 1
        if seg == "**":
            parts.append(".+" if last else "(?:[^/]+/)*")
            continue
        translated = _translated_segment(seg)
        if translated is None:
            return None
        parts.append(translated + ("" if last else "/"))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            return re.compile(r"\A" + "".join(parts) + r"\Z")
    except re.error:
        return None


@functools.lru_cache(maxsize=4096)
def read_glob(pattern: str) -> Glob:
    """A stored glob, read: :func:`compile_glob`'s, or, for one this grammar refuses that the release before read,
    that release's reading (logged once). Raises GlobPatternError when neither reads it.

    Only a glob stored before this release can be read the old way, since a
    write takes :func:`compile_glob`; it is matched by that release's regex,
    with that release's cost.
    """
    try:
        return compile_glob(pattern)
    except GlobPatternError:
        before = _regex_before(pattern) if isinstance(pattern, str) and pattern else None
        if before is None:
            raise
        logger.warning("glob_read_as_before", glob=pattern[:200])
        return _StoredGlob(pattern, before)


def check_glob(pattern: str, *, stored: bool) -> None:
    """Raise GlobPatternError unless ``pattern`` is a glob: one this grammar writes, or, ``stored``, one it reads."""
    if stored:
        read_glob(pattern)
    else:
        compile_glob(pattern)


def match_any(path: str, compiled: Sequence[Glob]) -> bool:
    """True if ``path`` matches at least one compiled glob."""
    return any(glob.matches(path) for glob in compiled)
