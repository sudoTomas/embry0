"""The paths a shell word can name, and whether one of them can match a deny rule.

A word in a shell command names one path, or, when it holds a glob or a value
only known at run time, every path a pattern allows. :class:`PathRef` is that
pattern over absolute paths, made of parts:

- :class:`Lit`: literal text;
- :class:`Star` and :class:`One`: a glob's ``*``, and its ``?`` or ``[…]``.
  They match within one path segment, as bash's pathname expansion does with
  its defaults (no ``globstar``): never a ``/``, and, when the part starts a
  segment, never a leading ``.`` (no ``dotglob``; a ``.`` in a bracket is not
  explicit enough, ``[.]x`` does not match ``.x``);
- :class:`AnyName`: an extglob pattern (``@(…)``, ``!(…)``, …), read as any
  name in its segment, a hidden one included;
- :class:`Wild`: text only known at run time (a variable, a substitution's
  output), any text at all, ``/`` included; also a glob segment a glob option
  the command may have set lets match anything.

A path below a directory that is not known is ``Wild`` + ``/`` + the path.

:func:`could_match` says whether some path a ``PathRef`` allows also matches
a deny rule's glob, read as :func:`embry0.safety.policy._glob_to_regex` reads
it (``*`` within a segment, ``**`` across segments, a trailing ``/**`` the
directory too). It intersects the two patterns exactly, so a glob that cannot
reach a denied file (``*.py`` against ``settings*.json``) is not refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Final


@dataclass(frozen=True)
class CharSet:
    """One character: from ``chars`` (any when None), and not in ``excluded``."""

    chars: frozenset[str] | None = None
    excluded: frozenset[str] = frozenset()

    def __and__(self, other: CharSet) -> CharSet:
        if self.chars is None:
            chars = other.chars
        elif other.chars is None:
            chars = self.chars
        else:
            chars = self.chars & other.chars
        return CharSet(chars, self.excluded | other.excluded)

    def nonempty(self) -> bool:
        # The alphabet is unbounded, so only a finite positive set can run out.
        return self.chars is None or bool(self.chars - self.excluded)


ANY_CHAR: Final[CharSet] = CharSet()
NOT_SLASH: Final[CharSet] = CharSet(None, frozenset("/"))
_NO_DOT: Final[CharSet] = CharSet(None, frozenset("."))


@dataclass(frozen=True)
class Lit:
    text: str


@dataclass(frozen=True)
class Star:
    """A glob's ``*``: any characters but ``/``; with ``guard``, not starting with ``.``."""

    guard: bool = False


@dataclass(frozen=True)
class One:
    """A glob's ``?`` or ``[…]``: one character from ``chars``; with ``guard``, not ``.``."""

    chars: CharSet = NOT_SLASH
    guard: bool = False


@dataclass(frozen=True)
class AnyName:
    """An extglob pattern: any characters but ``/``, a leading ``.`` included."""


@dataclass(frozen=True)
class Wild:
    """Text only known at run time: any text, ``/`` included.

    ``glob``: the segment is a glob that a glob option the command may have set
    (``shopt``, ``GLOBIGNORE``) lets match anything, and these are its parts as
    the default options read it (embry0.safety.command_checks reads them).
    """

    glob: tuple[Part, ...] | None = None


Part = Lit | Star | One | AnyName | Wild


def _render(part: Part) -> str:
    if isinstance(part, Lit):
        return re.sub(r"([*?\[\\…])", r"\\\1", part.text)
    if isinstance(part, Star):
        return "*"
    if isinstance(part, One):
        if part.chars.chars is None:
            return "?"
        return "[" + "".join(sorted(part.chars.chars)) + "]"
    if isinstance(part, AnyName):
        return "@(*)"
    return "…"


@dataclass(frozen=True)
class PathRef:
    """The absolute paths a word names: one path, or the ones a pattern allows."""

    parts: tuple[Part, ...]

    @classmethod
    def exact(cls, path: str) -> PathRef:
        return cls((Lit(path),))

    @property
    def is_exact(self) -> bool:
        return all(isinstance(p, Lit) for p in self.parts)

    @property
    def path(self) -> str:
        """The path, or the pattern shown as a glob (``…`` for a run-time value, ``\\`` escaping literals)."""
        if self.is_exact:
            return "".join(p.text for p in self.parts if isinstance(p, Lit))
        return "".join(_render(p) for p in self.parts)

    @property
    def literal_prefix(self) -> str:
        out: list[str] = []
        for part in self.parts:
            if not isinstance(part, Lit):
                break
            out.append(part.text)
        return "".join(out)

    def below(self) -> PathRef:
        """Every path under this one (what a recursive operation reaches)."""
        last = self.parts[-1] if self.parts else None
        if isinstance(last, Lit) and last.text.endswith("/"):
            return PathRef((*self.parts, Wild()))
        if isinstance(last, Lit):
            return PathRef((*self.parts[:-1], Lit(last.text + "/"), Wild()))
        return PathRef((*self.parts, Lit("/"), Wild()))


# ---- matching --------------------------------------------------------------------

# A token is (characters, repeats, guard): it consumes one character from
# ``characters``, or any number when ``repeats``; ``guard`` is a zero-width
# token that keeps the next character consumed from being ".".
_Token = tuple[CharSet, bool, bool]
_GUARD: Final[_Token] = (ANY_CHAR, False, True)


def _ref_tokens(ref: PathRef) -> tuple[_Token, ...]:
    tokens: list[_Token] = []
    for part in ref.parts:
        if isinstance(part, Lit):
            tokens.extend((CharSet(frozenset(c)), False, False) for c in part.text)
        elif isinstance(part, Star):
            if part.guard:
                tokens.append(_GUARD)
            tokens.append((NOT_SLASH, True, False))
        elif isinstance(part, One):
            if part.guard:
                tokens.append(_GUARD)
            tokens.append((part.chars & NOT_SLASH, False, False))
        elif isinstance(part, AnyName):
            tokens.append((NOT_SLASH, True, False))
        else:
            tokens.append((ANY_CHAR, True, False))
    return tuple(tokens)


@lru_cache(maxsize=256)
def _rule_tokens(rule_glob: str) -> tuple[tuple[_Token, ...], ...]:
    """The token sequences a rule glob matches, one per alternative."""

    def tokens(glob: str) -> list[_Token]:
        out: list[_Token] = []
        i = 0
        while i < len(glob):
            if glob.startswith("**", i):
                out.append((ANY_CHAR, True, False))
                i += 2
            elif glob[i] == "*":
                out.append((NOT_SLASH, True, False))
                i += 1
            else:
                out.append((CharSet(frozenset(glob[i])), False, False))
                i += 1
        return out

    if glob_ends_with_tree(rule_glob):
        base = tokens(rule_glob[: -len("/**")])
        alternatives = [base, [*base, (CharSet(frozenset("/")), False, False), (ANY_CHAR, True, False)]]
    else:
        alternatives = [tokens(rule_glob)]
    # A path that ends in a newline is refused as the regex matcher refuses it.
    newline = (CharSet(frozenset("\n")), False, False)
    return tuple(tuple(a) for a in alternatives) + tuple((*a, newline) for a in alternatives)


def glob_ends_with_tree(rule_glob: str) -> bool:
    return rule_glob.endswith("/**")


def _rule_literal_prefix(rule_glob: str) -> str:
    return rule_glob.split("*", 1)[0]


def _intersects(a: tuple[_Token, ...], b: tuple[_Token, ...]) -> bool:
    """Whether some string matches both token sequences."""
    start = (0, 0, False, False)
    seen = {start}
    stack = [start]
    while stack:
        i, j, ga, gb = stack.pop()
        if i == len(a) and j == len(b):
            return True
        moves: list[tuple[int, int, bool, bool]] = []
        # zero-width moves: a guard, or a repeating token that matches nothing
        if i < len(a):
            chars, repeats, guard = a[i]
            if guard:
                moves.append((i + 1, j, True, gb))
            elif repeats:
                moves.append((i + 1, j, ga, gb))
        if j < len(b):
            chars, repeats, guard = b[j]
            if guard:
                moves.append((i, j + 1, ga, True))
            elif repeats:
                moves.append((i, j + 1, ga, gb))
        # consume one character on both sides
        if i < len(a) and j < len(b) and not a[i][2] and not b[j][2]:
            chars = a[i][0] & b[j][0]
            if ga or gb:
                chars = chars & _NO_DOT
            if chars.nonempty():
                moves.append((i if a[i][1] else i + 1, j if b[j][1] else j + 1, False, False))
        for state in moves:
            if state not in seen:
                seen.add(state)
                stack.append(state)
    return False


def could_match(ref: PathRef, rule_glob: str) -> bool:
    """Whether a path ``ref`` allows can match ``rule_glob`` (an absolute permission-rule glob)."""
    prefix, rule_prefix = ref.literal_prefix, _rule_literal_prefix(rule_glob)
    if not (prefix.startswith(rule_prefix) or rule_prefix.startswith(prefix)):
        return False
    tokens = _ref_tokens(ref)
    return any(_intersects(tokens, alternative) for alternative in _rule_tokens(rule_glob))
