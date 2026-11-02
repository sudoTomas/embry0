"""Brace expansion as bash does it, counted before it is listed, for embry0.safety.bash_paths.

A word's ``{a,b}`` lists and ``{x..y[..step]}`` sequences make the words bash
passes on. They are counted first, without listing a sequence, so a word like
``{1..100000000}`` costs nothing; past :data:`MAX_BRACE_ALTERNATIVES` words a
sequence (which makes no ``/`` and no leading ``.``) is read as a glob ``*``,
and a word with any other brace in it as one that can name any path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from embry0.safety.shell_syntax import ACT, LIT, RUN, WILD

MAX_BRACE_ALTERNATIVES: Final[int] = 64
MAX_BRACE_NESTING: Final[int] = 32

_SEQUENCE: Final[re.Pattern[str]] = re.compile(
    r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?|([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?"
)


@dataclass
class _Region:
    """A ``{…}`` brace expansion expands: its bounds, and its comma-separated parts or its sequence."""

    start: int
    end: int
    parts: list[tuple[int, int]] = field(default_factory=list)  # bounds of each part, for a list
    sequence: re.Match[str] | None = None

    def count(self, chars: list[str], kinds: list[int]) -> int:
        if self.sequence is not None:
            return len(_sequence_values(self.sequence, limit=MAX_BRACE_ALTERNATIVES + 1))
        total = 0
        for lo, hi in self.parts:
            total += _brace_count(chars[lo:hi], kinds[lo:hi])
            if total > MAX_BRACE_ALTERNATIVES:
                break
        return total


def _regions(chars: list[str], kinds: list[int]) -> list[_Region]:
    """The brace expansions of a word that are not inside another one, left to right."""
    pairs: list[tuple[int, int]] = []
    stack: list[int] = []
    for i, (c, k) in enumerate(zip(chars, kinds)):
        if k != ACT:
            continue
        if c == "{":
            stack.append(i)
        elif c == "}" and stack:
            pairs.append((stack.pop(), i))
    pairs.sort()
    regions: list[_Region] = []
    covered = -1
    for start, end in pairs:
        if start < covered:
            continue
        region = _region(chars, kinds, start, end)
        if region is not None:
            regions.append(region)
            covered = end
    return regions


def _region(chars: list[str], kinds: list[int], start: int, end: int) -> _Region | None:
    depth = 0
    bounds = [start]
    for i in range(start + 1, end):
        c, k = chars[i], kinds[i]
        if k != ACT:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "," and depth == 0:
            bounds.append(i)
    if len(bounds) > 1:
        bounds.append(end)
        return _Region(start, end, parts=[(a + 1, b) for a, b in zip(bounds, bounds[1:])])
    if all(k == ACT for k in kinds[start + 1 : end]):
        m = _SEQUENCE.fullmatch("".join(chars[start + 1 : end]))
        if m:
            return _Region(start, end, sequence=m)
    return None


def _sequence_values(m: re.Match[str], *, limit: int) -> list[str]:
    step = abs(int(m.group(3) or m.group(6) or "1")) or 1
    if m.group(1) is not None:
        lo, hi = int(m.group(1)), int(m.group(2))
        width = (
            max(len(m.group(1)), len(m.group(2)))
            if m.group(1).lstrip("-").startswith("0") or m.group(2).lstrip("-").startswith("0")
            else 0
        )
        values = range(lo, hi + 1, step) if hi >= lo else range(lo, hi - 1, -step)
        return [str(v).zfill(width) for v in values[:limit]]
    lo, hi = ord(m.group(4)), ord(m.group(5))
    letters = range(lo, hi + 1, step) if hi >= lo else range(lo, hi - 1, -step)
    return [chr(v) for v in letters[:limit]]


def _brace_count(chars: list[str], kinds: list[int]) -> int:
    """How many words brace expansion makes of a word, counted up to just past the cap."""
    total = 1
    for region in _regions(chars, kinds):
        total *= region.count(chars, kinds)
        if total > MAX_BRACE_ALTERNATIVES:
            return MAX_BRACE_ALTERNATIVES + 1
    return total


def brace_alternatives(chars: list[str], kinds: list[int]) -> list[tuple[list[str], list[int]]]:
    """The words a brace expansion makes; past the cap, an over-approximation of them.

    Past the cap, a sequence (``{1..1000}``, which makes no ``/`` and no
    leading ``.``) is read as a glob ``*``, and a word with any other brace in
    it is read as one that can name any path.
    """
    if "{" not in chars:
        return [(chars, kinds)]
    depth = max_depth = 0
    for c, k in zip(chars, kinds):
        if k == ACT and c == "{":
            depth += 1
            max_depth = max(max_depth, depth)
        elif k == ACT and c == "}":
            depth = max(depth - 1, 0)
    if max_depth > MAX_BRACE_NESTING:
        return [(["/", WILD], [LIT, RUN])]
    regions = _regions(chars, kinds)
    if not regions:
        return [(chars, kinds)]
    if len(regions) <= MAX_BRACE_ALTERNATIVES and _brace_count(chars, kinds) <= MAX_BRACE_ALTERNATIVES:
        return _expand(chars, kinds, regions)
    # Too many to list: sequences become a glob; any other brace makes a word that can name anything.
    if any(r.sequence is None for r in regions):
        return [(["/", WILD], [LIT, RUN])]
    out_chars: list[str] = []
    out_kinds: list[int] = []
    pos = 0
    for region in regions:
        out_chars += chars[pos : region.start] + ["*"]
        out_kinds += kinds[pos : region.start] + [ACT]
        pos = region.end + 1
    out_chars += chars[pos:]
    out_kinds += kinds[pos:]
    return [(out_chars, out_kinds)]


def _expand(chars: list[str], kinds: list[int], regions: list[_Region]) -> list[tuple[list[str], list[int]]]:
    results: list[tuple[list[str], list[int]]] = [([], [])]
    pos = 0
    for region in regions:
        literal = (chars[pos : region.start], kinds[pos : region.start])
        options: list[tuple[list[str], list[int]]] = []
        if region.sequence is not None:
            options = [
                (list(v), [LIT] * len(v)) for v in _sequence_values(region.sequence, limit=MAX_BRACE_ALTERNATIVES + 1)
            ]
        else:
            for lo, hi in region.parts:
                options += brace_alternatives(chars[lo:hi], kinds[lo:hi])
        results = [(rc + literal[0] + oc, rk + literal[1] + ok) for rc, rk in results for oc, ok in options]
        pos = region.end + 1
    return [(rc + chars[pos:], rk + kinds[pos:]) for rc, rk in results]
