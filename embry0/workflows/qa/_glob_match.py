"""Path-glob matching for conditional acceptance criteria (EMB-39).

Matches repo-relative POSIX paths (as produced by ``git diff --name-only``)
against gitignore-style glob patterns. Hand-rolled because ``fnmatch`` is not
path-aware (``*`` crosses ``/``) and ``PurePath.full_match`` requires 3.13.

Grammar — patterns match the FULL path, anchored at both ends:

- ``*``  — any run of characters within one segment (never crosses ``/``)
- ``?``  — exactly one character within a segment
- ``[...]`` — character class (``[!...]`` negates); may not contain ``/``
- ``**`` — zero or more whole segments; must be its own segment.
  Trailing ``X/**`` requires at least one segment under ``X`` (so
  ``apps/quoting/**`` matches ``apps/quoting/page.tsx`` but not
  ``apps/quoting`` itself), while ``a/**/b`` matches ``a/b``.

Rejected at compile time: empty patterns, leading ``/``, empty segments,
``..`` segments, ``**`` glued to other characters, unterminated classes.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

__all__ = ["GlobPatternError", "compile_glob", "match_any"]


class GlobPatternError(ValueError):
    """A conditional-criteria glob pattern is malformed."""


def _translate_segment(segment: str, *, pattern: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
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
            if cls.startswith("!"):
                cls = "^" + cls[1:]
            out.append("[" + cls + "]")
            i = j + 1
        else:
            out.append(re.escape(ch))
            i += 1
    return "".join(out)


def compile_glob(pattern: str) -> re.Pattern[str]:
    """Compile one glob into an anchored regex. Raises GlobPatternError."""
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

    parts: list[str] = []
    for idx, seg in enumerate(collapsed):
        is_last = idx == len(collapsed) - 1
        if seg == "**":
            # Mid-pattern: zero or more whole segments (each with its '/').
            # Trailing: at least one character, i.e. ≥1 segment under the prefix.
            parts.append(".+" if is_last else "(?:[^/]+/)*")
        else:
            parts.append(_translate_segment(seg, pattern=pattern) + ("" if is_last else "/"))
    return re.compile(r"\A" + "".join(parts) + r"\Z")


def match_any(path: str, compiled: Sequence[re.Pattern[str]]) -> bool:
    """True if ``path`` matches at least one compiled glob."""
    return any(rx.match(path) for rx in compiled)
