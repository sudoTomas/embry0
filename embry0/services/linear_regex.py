"""Python regular expressions matched in time linear in the text, for patterns an operator stores.

Python's ``re`` matches by backtracking: how long one search takes depends on
the pattern, and for some patterns, such as a quantifier inside a quantifier
(``(a+)+$``), it doubles with every character of the text. A pattern stored
as configuration, such as a watched stack's signature mask (#172), runs on
every line of every tick, so how long it takes must not depend on what the
pattern is.

:func:`compile_linear` reads a pattern with Python's own parser, so it means
what it means to ``re``, and writes it out for RE2 (``google-re2``), whose
matching takes time linear in the text whatever the pattern. Each
single-character test (a literal, a class such as ``[a-z]`` or ``\\w``, ``.``,
a letter under ``(?i)``) is written out as the exact set of characters ``re``
matches it against, read from ``re`` itself, so ``\\d``, ``\\w``, ``\\s`` and
``(?i)`` keep their Unicode meaning. ``^``, ``$``, ``\\A`` and ``\\Z`` keep
theirs, in and out of multi-line mode, and so do the repetitions, greedy and
lazy, and the alternatives.

**One difference.** ``\\b`` and ``\\B`` count ASCII letters, digits and ``_``
as word characters, as RE2 does; ``re`` also counts every other letter and
digit. The two agree everywhere but beside a non-ASCII letter or digit, and
everywhere under ``(?a)``.

**What is refused** (:class:`UnsupportedPattern`), being what RE2 cannot run
with ``re``'s meaning: look-ahead and look-behind, back-references,
conditional groups, atomic groups and possessive quantifiers, a repetition
count above 1000, a pattern that can match an empty string (it would mask
between characters), ``(?a:...)`` or ``(?u:...)`` around part of a pattern,
``$`` outside multi-line mode together with a multi-line ``^`` or ``$``, a
class that tells a lone surrogate from U+FFFD, and a pattern larger than
:data:`MAX_PROGRAM_SIZE`.

A replacement is literal (:meth:`LinearPattern.sub`): a placeholder, never a
template with group references.
"""

from __future__ import annotations

import _sre
import functools
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from re import _constants, _parser  # type: ignore[attr-defined]
from typing import Any, Final

import re2

C = _constants

#: The largest program, in RE2 instructions, a pattern may compile to. A
#: 500-character pattern of literals and ASCII classes compiles to a few
#: hundred; a Unicode class such as ``\\w`` to about 1,300 each time it is
#: repeated (``\\w{8}`` is about 11,000). It bounds the work and memory of one
#: pattern: compiling one takes about a quarter of a millisecond per thousand.
MAX_PROGRAM_SIZE: Final = 100_000
#: The largest repetition count RE2 accepts.
MAX_REPEAT_COUNT: Final = 1000

_MAX_CODE: Final = 0x10FFFF
_SURROGATES: Final = (0xD800, 0xDFFF)
_REPLACEMENT_CHAR: Final = 0xFFFD
_NEWLINE: Final = 0x0A
_SURROGATE_RE: Final = re.compile("[\ud800-\udfff]")
#: Where a stand-in for a newline is looked for: the private-use planes, then the BMP's private-use area.
_SENTINEL_RANGES: Final = ((0xF0000, 0xFFFFD), (0x100000, 0x10FFFD), (0xE000, 0xF8FF))
#: Programs kept for one pattern's texts that hold a newline before their end, each with its own stand-in.
_MAX_VARIANTS: Final = 8

_FLAG_LETTERS: Final = ((C.SRE_FLAG_IGNORECASE, "i"), (C.SRE_FLAG_DOTALL, "s"), (C.SRE_FLAG_ASCII, "a"))
_CATEGORY_ESCAPES: Final = {
    C.CATEGORY_DIGIT: r"\d",
    C.CATEGORY_NOT_DIGIT: r"\D",
    C.CATEGORY_SPACE: r"\s",
    C.CATEGORY_NOT_SPACE: r"\S",
    C.CATEGORY_WORD: r"\w",
    C.CATEGORY_NOT_WORD: r"\W",
}

#: A set of code points: sorted, disjoint, non-adjacent inclusive ranges.
Ranges = tuple[tuple[int, int], ...]


class UnsupportedPattern(ValueError):
    """A pattern ``re`` reads that cannot be matched in linear time with ``re``'s meaning; the message says why."""


# ---- sets of code points ------------------------------------------------------------------------------------------


def _normalized(ranges: Iterable[tuple[int, int]]) -> Ranges:
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1] + 1:
            if hi > out[-1][1]:
                out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return tuple(out)


def _complement(ranges: Ranges) -> Ranges:
    out: list[tuple[int, int]] = []
    start = 0
    for lo, hi in ranges:
        if lo > start:
            out.append((start, lo - 1))
        start = hi + 1
    if start <= _MAX_CODE:
        out.append((start, _MAX_CODE))
    return tuple(out)


def _union(*sets: Ranges) -> Ranges:
    return _normalized(r for ranges in sets for r in ranges)


def _subtract(ranges: Ranges, removed: Ranges) -> Ranges:
    return _complement(_union(_complement(ranges), removed))


def _intersect(a: Ranges, b: Ranges) -> Ranges:
    return _complement(_union(_complement(a), _complement(b)))


def _contains(ranges: Ranges, code: int) -> bool:
    return any(lo <= code <= hi for lo, hi in ranges)


@functools.cache
def _categories() -> dict[tuple[Any, bool], Ranges]:
    """What ``re`` matches ``\\d``, ``\\D``, ``\\s``, ``\\S``, ``\\w`` and ``\\W`` against, with and without ``(?a)``,
    read from ``re`` itself over every code point. Computed once, in about a tenth of a second."""
    every = "".join(map(chr, range(_MAX_CODE + 1)))
    found: dict[tuple[Any, bool], Ranges] = {}
    for code, escape in _CATEGORY_ESCAPES.items():
        for ascii_only in (False, True):
            pattern = ("(?a)" if ascii_only else "") + "(?:" + escape + ")+"
            found[code, ascii_only] = tuple((m.start(), m.end() - 1) for m in re.finditer(pattern, every))
    return found


@functools.cache
def _case_related() -> tuple[str, tuple[int, ...], Ranges]:
    """Every code point whose match under ``(?i)`` can differ from its match without: the cased ones and their
    lower case (``re``'s extra equivalences, such as ``i`` and dotless ``ı``, are between cased ones). Any other code
    point a test under ``(?i)`` matches exactly when the same test without it does (``re`` compares it, or its lower
    case, which is itself, with the same set), so only these are asked of ``re``. As a string of them, their code
    points in order, and as ranges."""
    related: set[int] = set()
    for code in range(_MAX_CODE + 1):
        if _sre.unicode_iscased(code):
            related.add(code)
            related.add(_sre.unicode_tolower(code))
    codes = tuple(sorted(related))
    return "".join(map(chr, codes)), codes, _normalized((c, c) for c in codes)


# ---- one single-character test ------------------------------------------------------------------------------------


def _python_atom(op: Any, av: Any, flags: int) -> str:
    """A single-character test written back as ``re`` source under ``flags``, to ask ``re`` what it matches."""
    letters = "".join(letter for flag, letter in _FLAG_LETTERS if flags & flag)
    prefix = f"(?{letters})" if letters else ""

    def char(code: int) -> str:
        return f"\\U{code:08x}"

    if op is C.LITERAL:
        return prefix + char(av)
    if op is C.NOT_LITERAL:
        return prefix + "[^" + char(av) + "]"
    parts: list[str] = []
    for item_op, item_av in av:
        if item_op is C.NEGATE:
            parts.insert(0, "^")
        elif item_op is C.LITERAL:
            parts.append(char(item_av))
        elif item_op is C.RANGE:
            parts.append(char(item_av[0]) + "-" + char(item_av[1]))
        else:
            parts.append(_CATEGORY_ESCAPES[item_av])
    return prefix + "[" + "".join(parts) + "]"


def _plain_set(op: Any, av: Any, flags: int) -> Ranges:
    """What a single-character test matches without ``(?i)``."""
    if op is C.LITERAL:
        return ((av, av),)
    if op is C.NOT_LITERAL:
        return _complement(((av, av),))
    if op is C.ANY:
        return ((0, _MAX_CODE),) if flags & C.SRE_FLAG_DOTALL else _complement(((_NEWLINE, _NEWLINE),))
    negate = False
    parts: list[Ranges] = []
    for item_op, item_av in av:
        if item_op is C.NEGATE:
            negate = True
        elif item_op is C.LITERAL:
            parts.append(((item_av, item_av),))
        elif item_op is C.RANGE:
            parts.append(((item_av[0], item_av[1]),))
        elif item_op is C.CATEGORY:
            parts.append(_categories()[item_av, bool(flags & C.SRE_FLAG_ASCII)])
        else:  # pragma: no cover - the parser writes no other item into a class
            raise UnsupportedPattern(f"{str(item_op).lower()} in a class is not supported")
    found = _union(*parts)
    return _complement(found) if negate else found


@functools.lru_cache(maxsize=4096)
def _char_set(op: Any, av: Any, flags: int) -> Ranges:
    """The exact code points ``re`` matches a single-character test against under ``flags``."""
    plain = _plain_set(op, av, flags)
    if not flags & C.SRE_FLAG_IGNORECASE or op is C.ANY:
        return plain
    text, codes, related = _case_related()
    asked = re.compile(_python_atom(op, av, flags))
    matched = _normalized((codes[m.start()], codes[m.start()]) for m in asked.finditer(text))
    return _union(_subtract(plain, related), matched)


# ---- writing RE2 --------------------------------------------------------------------------------------------------


@dataclass
class _Shape:
    """What writing a pattern found that decides how a text is handed to RE2."""

    line_end: bool = False  # ``$`` outside multi-line mode
    line_anchors: bool = False  # ``^`` or ``$`` in multi-line mode
    sets: list[Ranges] = field(default_factory=list)


def _nullable(items: Iterable[tuple[Any, Any]]) -> bool:
    """Whether a sequence can match an empty string somewhere, taking every assertion as met."""
    return all(_item_nullable(op, av) for op, av in items)


def _item_nullable(op: Any, av: Any) -> bool:
    if op in (C.LITERAL, C.NOT_LITERAL, C.ANY, C.IN):
        return False
    if op is C.BRANCH:
        return any(_nullable(alternative) for alternative in av[1])
    if op is C.SUBPATTERN:
        return _nullable(av[3])
    if op in (C.MAX_REPEAT, C.MIN_REPEAT):
        return av[0] == 0 or _nullable(av[2])
    return True  # an assertion


def _class(ranges: Ranges, sentinel: int | None) -> str:
    """``ranges`` as an RE2 class.

    A lone surrogate cannot be written as UTF-8, so a text's are read as
    U+FFFD and no class names one (the caller checks each class treats the two
    alike). ``sentinel``, when set, stands for a newline in the text, so a
    class matches it exactly when it matches a newline.
    """
    if sentinel is not None:
        ranges = _subtract(ranges, ((sentinel, sentinel),))
        if _contains(ranges, _NEWLINE):
            ranges = _union(ranges, ((sentinel, sentinel),))
    ranges = _subtract(ranges, (_SURROGATES,))
    if not ranges:
        return r"[^\x{0}-\x{10ffff}]"
    if len(ranges) == 2 and all(lo == hi for lo, hi in ranges) and ranges[1][0] - ranges[0][0] == 32:
        # RE2 reads a class of exactly an ASCII capital and its lower case, [Kk], as the letter under case
        # folding, and merged into an alternation that folding is Unicode's: [Kk] would match U+212A KELVIN
        # SIGN, [Ss] U+017F LONG S. Two alternatives are never read that way.
        return f"(?:\\x{{{ranges[0][0]:x}}}|\\x{{{ranges[1][0]:x}}})"
    body = "".join(f"\\x{{{lo:x}}}" if lo == hi else f"\\x{{{lo:x}}}-\\x{{{hi:x}}}" for lo, hi in ranges)
    return "[" + body + "]"


def _combine_flags(flags: int, add: int, delete: int) -> int:
    """A group's flags, as ``re`` combines them."""
    if add & _parser.TYPE_FLAGS:
        flags &= ~_parser.TYPE_FLAGS
    return (flags | add) & ~delete


def _write(items: Iterable[tuple[Any, Any]], flags: int, shape: _Shape, sentinel: int | None) -> str:
    return "".join(_write_item(op, av, flags, shape, sentinel) for op, av in items)


def _write_item(op: Any, av: Any, flags: int, shape: _Shape, sentinel: int | None) -> str:  # noqa: PLR0911
    if op in (C.LITERAL, C.NOT_LITERAL, C.ANY, C.IN):
        ranges = _char_set(op, tuple(av) if isinstance(av, list) else av, flags)
        shape.sets.append(ranges)
        return _class(ranges, sentinel)
    if op is C.BRANCH:
        return "(?:" + "|".join(_write(alternative, flags, shape, sentinel) for alternative in av[1]) + ")"
    if op is C.SUBPATTERN:
        _group, add, delete, body = av
        if (add | delete) & _parser.TYPE_FLAGS:
            # re's search reads such a group's \w, \d and \s with the pattern's flags at some positions and the
            # group's at others; a flag for the whole pattern has one meaning.
            raise UnsupportedPattern(
                "(?a:...) or (?u:...) around part of a pattern is not supported; start it with (?a)"
            )
        return "(?:" + _write(body, _combine_flags(flags, add, delete), shape, sentinel) + ")"
    if op in (C.MAX_REPEAT, C.MIN_REPEAT):
        return _write_repeat(op, av, flags, shape, sentinel)
    if op is C.AT:
        return _write_at(av, flags, shape)
    if op in (C.ASSERT, C.ASSERT_NOT):
        raise UnsupportedPattern("look-ahead and look-behind ((?=, (?!, (?<=, (?<!) are not supported")
    if op is C.GROUPREF:
        raise UnsupportedPattern("a back-reference (\\1, (?P=name)) is not supported")
    if op is C.GROUPREF_EXISTS:
        raise UnsupportedPattern("a conditional group ((?(1)...)) is not supported")
    if op in (C.ATOMIC_GROUP, C.POSSESSIVE_REPEAT):
        raise UnsupportedPattern("atomic groups and possessive quantifiers ((?>, *+, ++, ?+) are not supported")
    raise UnsupportedPattern(f"{str(op).lower()} is not supported")  # pragma: no cover - re writes no other op


def _write_repeat(op: Any, av: Any, flags: int, shape: _Shape, sentinel: int | None) -> str:
    low, high, body = av
    unbounded = high == C.MAXREPEAT
    if low > MAX_REPEAT_COUNT or (not unbounded and high > MAX_REPEAT_COUNT):
        raise UnsupportedPattern(f"a repetition count above {MAX_REPEAT_COUNT} is not supported")
    if unbounded:
        quantifier = "*" if low == 0 else "+" if low == 1 else f"{{{low},}}"
    elif (low, high) == (0, 1):
        quantifier = "?"
    else:
        quantifier = f"{{{low}}}" if low == high else f"{{{low},{high}}}"
    lazy = "?" if op is C.MIN_REPEAT else ""
    return "(?:" + _write(body, flags, shape, sentinel) + ")" + quantifier + lazy


def _write_at(av: Any, flags: int, shape: _Shape) -> str:
    multiline = bool(flags & C.SRE_FLAG_MULTILINE)
    if av is C.AT_BEGINNING_STRING:
        return r"\A"
    if av is C.AT_END_STRING:
        return r"\z"
    if av is C.AT_BOUNDARY:
        return r"\b"
    if av is C.AT_NON_BOUNDARY:
        return r"\B"
    if av is C.AT_BEGINNING:
        shape.line_anchors |= multiline
        return "(?m:^)" if multiline else r"\A"
    # ``$``. Outside multi-line mode it is the end, or before a newline that ends the text: RE2's multi-line ``$``
    # once the text holds no other newline (LinearPattern._subject).
    shape.line_anchors |= multiline
    shape.line_end |= not multiline
    return "(?m:$)"


def _options() -> Any:
    options = re2.Options()
    options.never_capture = True
    options.log_errors = False
    return options


def _translate(parsed: Any, sentinel: int | None) -> tuple[Any, _Shape]:
    """RE2's program for ``parsed``, ``sentinel`` standing for a newline when set, and what writing it found."""
    shape = _Shape()
    source = _write(parsed, parsed.state.flags, shape, sentinel)
    try:
        program = re2.compile(source, _options())
    except re2.error as exc:
        raise UnsupportedPattern(f"it is too large to match: {exc}") from None
    if program.programsize > MAX_PROGRAM_SIZE:
        raise UnsupportedPattern(
            f"it is too large to match ({program.programsize} instructions, at most {MAX_PROGRAM_SIZE}); "
            "a Unicode class such as \\w costs about 1,300 each time it is repeated, [A-Za-z0-9_] a few"
        )
    return program, shape


class LinearPattern:
    """A pattern ``re`` reads, matched by RE2 in time linear in the text (:func:`compile_linear`)."""

    def __init__(self, pattern: str) -> None:
        try:
            parsed = _parser.parse(pattern)
            program, shape = _translate(parsed, None)
        except RecursionError:
            raise UnsupportedPattern("it nests too deeply") from None
        if _nullable(parsed):
            raise UnsupportedPattern("it matches an empty string, so it would mask between characters")
        if shape.line_end and shape.line_anchors:
            raise UnsupportedPattern("$ outside multi-line mode cannot be combined with a multi-line ^ or $")
        for ranges in shape.sets:
            expected: Ranges = (_SURROGATES,) if _contains(ranges, _REPLACEMENT_CHAR) else ()
            if _intersect(ranges, (_SURROGATES,)) != expected:
                raise UnsupportedPattern("a class that tells a lone surrogate from U+FFFD is not supported")
        self.pattern = pattern
        self._parsed = parsed
        self._program = program
        self._line_end = shape.line_end
        self._variants: dict[int, Any] = {}

    def __repr__(self) -> str:
        return f"LinearPattern({self.pattern!r})"

    def _subject(self, text: str) -> tuple[str, Any]:
        """``text`` as RE2 reads it, one character for each of ``text``'s so every position is the same, and the
        program that reads it.

        A lone surrogate cannot be written as UTF-8, so it is read as U+FFFD.
        When the pattern has ``$`` outside multi-line mode and ``text`` holds a
        newline before its last character, those newlines are read as a
        private-use character ``text`` does not hold, which every class that
        matches a newline matches too: RE2's multi-line ``$`` is then true only
        at the end and before a newline that ends the text, as ``re``'s ``$``.
        """
        subject = text
        if not text.isascii() and _SURROGATE_RE.search(text):
            subject = _SURROGATE_RE.sub("�", text)
        if not self._line_end or "\n" not in text[:-1]:
            return subject, self._program
        sentinel = _free_character(subject)
        program = self._variants.get(sentinel)
        if program is None:
            program, _shape = _translate(self._parsed, sentinel)
            if len(self._variants) >= _MAX_VARIANTS:
                self._variants.clear()
            self._variants[sentinel] = program
        return subject[:-1].replace("\n", chr(sentinel)) + subject[-1], program

    def spans(self, text: str) -> list[tuple[int, int]]:
        """Where the pattern matches in ``text``, as ``re.finditer`` finds it: leftmost first, never overlapping,
        never empty."""
        subject, program = self._subject(text)
        return [match.span() for match in program.finditer(subject)]

    def sub(self, replacement: str, text: str) -> str:
        """``text`` with every match replaced by ``replacement`` as written: never read for ``\\1`` or ``\\g<1>``."""
        pieces: list[str] = []
        end = 0
        for start, stop in self.spans(text):
            pieces.append(text[end:start])
            pieces.append(replacement)
            end = stop
        if not pieces:
            return text
        pieces.append(text[end:])
        return "".join(pieces)


def _free_character(text: str) -> int:
    """A private-use code point ``text`` does not hold."""
    present: set[str] | None = None
    for lo, hi in _SENTINEL_RANGES:
        for code in range(lo, hi + 1):
            if present is None:
                if chr(code) not in text:
                    return code
                present = set(text)
            elif chr(code) not in present:
                return code
    raise UnsupportedPattern("the text holds every private-use character")  # pragma: no cover - 137,000 of them


@functools.lru_cache(maxsize=256)
def compile_linear(pattern: str) -> LinearPattern:
    """``pattern`` read as ``re`` reads it, matched in time linear in the text.

    Raises :class:`re.error` for a pattern ``re`` does not read, and
    :class:`UnsupportedPattern` for one it reads that cannot be matched in
    linear time with its meaning (see the module docstring). Compiled once per
    pattern: a stack's masks are read on every tick.
    """
    return LinearPattern(pattern)
