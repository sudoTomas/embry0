"""Parameter transformations computed on literal text, as bash 5.2 computes them.

``${X#pattern}``, ``${X/pattern/string}``, ``${X^^}``, ``${X@E}``,
``${X:offset:length}`` and the like turn a value into another. When the value
and the words are literal, the result is too, and embry0.safety.shell_variables
computes it with these functions. A pattern is matched by an automaton over
its elements, one step per character, so a transformation costs time linear in
its text for each scan; the steps are counted as the command's ``transform``
work (embry0.safety.shell_work), and past its limit ``Exhausted`` is raised
(the caller then treats the result as anywhere).

A pattern is given as characters and their kinds (embry0.safety.shell_syntax):
an unquoted ``*``, ``?`` or ``[…]`` is a glob (``*`` matches ``/`` too, as in
any parameter pattern); anything quoted is itself.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

from embry0.safety.shell_syntax import ACT, LIT, WILD, decode_ansi_c
from embry0.safety.shell_work import Exhausted, Work

# A replacement: its pieces, each literal text or MATCH (an unquoted ``&``, the matched text).
MATCH: Final[object] = object()
Replacement = list[str | object]

_CLASSES: Final[dict[str, Callable[[str], bool]]] = {
    "alnum": str.isalnum,
    "alpha": str.isalpha,
    "blank": lambda c: c in " \t",
    "cntrl": lambda c: ord(c) < 32 or ord(c) == 127,
    "digit": lambda c: c in "0123456789",
    "graph": lambda c: c.isprintable() and not c.isspace(),
    "lower": str.islower,
    "print": str.isprintable,
    "punct": lambda c: c.isprintable() and not c.isalnum() and not c.isspace(),
    "space": str.isspace,
    "upper": str.isupper,
    "word": lambda c: c.isalnum() or c == "_",
    "xdigit": lambda c: c in "0123456789abcdefABCDEF",
}
_INT: Final[str] = r"\s*(?:-?\d+|\(\s*-?\d+\s*\))\s*"
_SUBSTR: Final[re.Pattern[str]] = re.compile(rf"(?P<offset>{_INT})(?::(?P<length>{_INT}))?")
_MAX_OFFSET: Final[int] = 1 << 20
MAX_PATTERN: Final[int] = 1024  # elements; a longer pattern is not matched


class Glob:
    """A parameter pattern as an automaton: state i has matched its first i elements.

    An element is a ``*`` or a test of one character. The states are the bits
    of an int, so a step is a few operations on it.
    """

    def __init__(self, elements: list[Callable[[str], bool] | None]) -> None:
        collapsed: list[Callable[[str], bool] | None] = []
        for element in elements:
            if element is None and collapsed and collapsed[-1] is None:
                continue  # ** is *
            collapsed.append(element)
        self.elements = collapsed
        self.stars = sum(1 << i for i, e in enumerate(collapsed) if e is None)
        self.accept = 1 << len(collapsed)
        self._masks: dict[str, int] = {}

    def reversed(self) -> Glob:
        return Glob(self.elements[::-1])

    def closure(self, states: int) -> int:
        return states | ((states & self.stars) << 1)

    def mask(self, c: str) -> int:
        found = self._masks.get(c)
        if found is None:
            found = sum(1 << i for i, e in enumerate(self.elements) if e is not None and e(c))
            self._masks[c] = found
        return found

    def step(self, states: int, c: str) -> int:
        return self.closure(((states & ~self.stars & self.mask(c)) << 1) | (states & self.stars))

    def ends(self, text: str, start: int, work: Work) -> list[int]:
        """Every end e where the pattern matches ``text[start:e]`` exactly."""
        work.check("transform")
        states = self.closure(1)
        found = [start] if states & self.accept else []
        p = start
        while p < len(text):
            states = self.step(states, text[p])
            p += 1
            if not states:
                break
            if states & self.accept:
                found.append(p)
        work.spend("transform", p - start + 1)
        return found

    def starts(self, text: str, work: Work) -> list[bool]:
        """For each position, whether a match of at least one character starts there."""
        work.check("transform")
        backwards = self.reversed()
        out = [False] * (len(text) + 1)
        states = 0
        for p in range(len(text) - 1, -1, -1):
            states = backwards.step(states | backwards.closure(1), text[p])
            out[p] = bool(states & backwards.accept)
        work.spend("transform", len(text) + 1)
        return out

    def one(self, c: str) -> bool:
        return bool(self.step(self.closure(1), c) & self.accept)


def pattern_glob(chars: list[str], kinds: list[int]) -> Glob | None:
    """The automaton a parameter pattern is, or None when it holds text only known at run time.

    A pattern longer than ``MAX_PATTERN`` raises ``Exhausted``.
    """
    elements: list[Callable[[str], bool] | None] = []
    i = 0
    while i < len(chars):
        c, k = chars[i], kinds[i]
        if k == LIT:
            elements.append(c.__eq__)
        elif k != ACT:
            return None  # run-time text, an extglob, a directory
        elif c == "*":
            elements.append(None)
        elif c == "?":
            elements.append(_ANY)
        elif c == "[" and (bracket := _bracket(chars, kinds, i)) is not None:
            test, i = bracket
            elements.append(test)
            continue
        else:
            elements.append(c.__eq__)
        i += 1
    if len(elements) > MAX_PATTERN:
        raise Exhausted("transform")
    return Glob(elements)


def _any(c: str) -> bool:
    return True


_ANY: Final[Callable[[str], bool]] = _any


def _bracket(chars: list[str], kinds: list[int], start: int) -> tuple[Callable[[str], bool], int] | None:
    """``[…]`` from ``chars[start]``: a test of one character, and the index after it; None if not closed."""
    i = start + 1
    negate = i < len(chars) and kinds[i] == ACT and chars[i] in "!^"
    if negate:
        i += 1
    tests: list[Callable[[str], bool]] = []
    first = True
    while i < len(chars):
        c, k = chars[i], kinds[i]
        if c == "]" and k == ACT and not first:
            return (lambda x: not any(t(x) for t in tests)) if negate else (lambda x: any(t(x) for t in tests)), i + 1
        first = False
        if c == "[" and k == ACT and i + 1 < len(chars) and chars[i + 1] == ":":
            end = "".join(chars[i:]).find(":]")
            if end > 0 and "".join(chars[i + 2 : i + end]) in _CLASSES:
                tests.append(_CLASSES["".join(chars[i + 2 : i + end])])
                i += end + 2
                continue
        if i + 2 < len(chars) and chars[i + 1] == "-" and not (chars[i + 2] == "]" and kinds[i + 2] == ACT):
            tests.append(_in_range(c, chars[i + 2]))
            i += 3
            continue
        tests.append(c.__eq__)
        i += 1
    return None


def _in_range(low: str, high: str) -> Callable[[str], bool]:
    return lambda c: low <= c <= high


def removed(op: str, text: str, glob: Glob, work: Work) -> str:
    """``${X#p}`` ``${X##p}`` ``${X%p}`` ``${X%%p}``: the shortest or longest match removed from an end."""
    if op in ("#", "##"):
        ends = glob.ends(text, 0, work)
        return text[(min(ends) if op == "#" else max(ends)) :] if ends else text
    lengths = glob.reversed().ends(text[::-1], 0, work)
    if not lengths:
        return text
    return text[: len(text) - (min(lengths) if op == "%" else max(lengths))]


def replaced(op: str, text: str, glob: Glob, replacement: Replacement, work: Work) -> str:
    """``${X/p/s}`` ``${X//p/s}`` ``${X/#p/s}`` ``${X/%p/s}``: the longest match(es) of p replaced."""

    def put(match: str) -> str:
        return "".join(match if piece is MATCH else str(piece) for piece in replacement)

    n = len(text)
    if op == "/#":
        ends = glob.ends(text, 0, work)
        return put(text[: max(ends)]) + text[max(ends) :] if ends else text
    if op == "/%":
        lengths = glob.reversed().ends(text[::-1], 0, work)
        return text[: n - max(lengths)] + put(text[n - max(lengths) :]) if lengths else text
    if n == 0:
        return put("") if glob.closure(1) & glob.accept else text  # only an empty value has an empty match
    starts = glob.starts(text, work)
    out: list[str] = []
    i = 0
    while i < n:
        if not starts[i]:
            out.append(text[i])
            i += 1
            continue
        end = max(e for e in glob.ends(text, i, work) if e > i)
        out.append(put(text[i:end]))
        i = end
        if op == "/":
            break
    out.append(text[i:])
    return "".join(out)


def case_changed(op: str, text: str, glob: Glob | None) -> str:
    """``${X^}`` ``${X^^}`` ``${X,}`` ``${X,,}`` ``${X~}`` ``${X~~}``: the case of the (first) matching characters."""
    change = {"^": str.upper, ",": str.lower, "~": str.swapcase}[op[0]]
    out = list(text)
    for i, c in enumerate(out if len(op) == 2 else out[:1]):
        if glob is None or glob.one(c):
            out[i] = change(c)
    return "".join(out)


def at_transformed(op: str, text: str) -> str:
    """``${X@U}`` ``${X@u}`` ``${X@L}`` ``${X@Q}`` ``${X@E}``."""
    if op == "@U":
        return text.upper()
    if op == "@u":
        return text[:1].upper() + text[1:]
    if op == "@L":
        return text.lower()
    if op == "@E":
        return decode_ansi_c(text)
    if all(c.isprintable() for c in text):
        return "\\'" if text == "'" else "'" + text.replace("'", "'\\''") + "'"
    return _ansi_c_quoted(text)


def _ansi_c_quoted(text: str) -> str:
    """``$'…'``: how bash quotes text with a control character in it."""
    return "$'" + "".join(_C_ESCAPES.get(c) or (c if c.isprintable() else f"\\{ord(c) & 0xFF:03o}") for c in text) + "'"


_C_ESCAPES: Final[dict[str, str]] = {
    "\a": "\\a",
    "\b": "\\b",
    "\x1b": "\\E",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\v": "\\v",
    "'": "\\'",
    "\\": "\\\\",
}


def substring(text: str, arith: str) -> str | None:
    """``${X:offset}`` and ``${X:offset:length}`` with literal integers; None for any other arithmetic."""
    bounds = _bounds(len(text), arith, negative_length=True)
    if bounds is None:
        return None
    start, end = bounds
    return text[start:end]


def sliced(elements: list[str], arith: str, *, positional: bool) -> list[str] | None:
    """``${@:offset:length}`` and ``${a[@]:offset:length}``: the elements in that range (``$0`` is
    positional offset 0, and not listed: None)."""
    m = _SUBSTR.fullmatch(arith)
    if m is None:
        return None
    offset = _int(m.group("offset"))
    if positional:
        if offset == 0:
            return None
        if offset > 0:
            offset -= 1
    bounds = _bounds(len(elements), arith, negative_length=False, offset=offset)
    if bounds is None:
        return None
    start, end = bounds
    return elements[start:end]


def _bounds(n: int, arith: str, *, negative_length: bool, offset: int | None = None) -> tuple[int, int] | None:
    m = _SUBSTR.fullmatch(arith)
    if m is None:
        return None
    start = _int(m.group("offset")) if offset is None else offset
    if abs(start) > _MAX_OFFSET:
        return None
    if start < 0:
        start += n
        if start < 0:
            return (0, 0)
    if start >= n:
        return n, n  # past the end: nothing, whatever the length
    if m.group("length") is None:
        return start, n
    length = _int(m.group("length"))
    if length < 0:
        if not negative_length:
            return None
        end = n + length
        return (start, end) if end >= start else None  # bash: "substring expression < 0", no value
    return start, min(n, start + length)


def _int(text: str) -> int:
    return int(text.strip().strip("()").strip())


_CONVERSION: Final[re.Pattern[str]] = re.compile(
    r"%(?P<flags>[-+ #0]*)(?P<width>\d+|\*)?(?:\.(?P<precision>\d*|\*))?(?P<conversion>[a-zA-Z%])"
)
_MAX_PRINTF: Final[int] = 1 << 16
_TIME: Final[re.Pattern[str]] = re.compile(
    r"%(?P<flags>[-+ #0]*)(?P<width>\d+|\*)?(?:\.(?P<precision>\d*|\*))?\((?P<format>[^)]*)\)T"
)


def printf(format_: str, args: list[str], *, opaque: frozenset[str] = frozenset()) -> str | None:
    """What ``printf -v NAME FORMAT ARGS`` gives NAME, a float's digits (only known at run time) as
    WILD; None for a ``*`` this module does not read; ``Exhausted`` past ``_MAX_PRINTF``.

    A character in ``opaque`` stands for text only known at run time: ``%s`` passes it through, and
    any other conversion of an argument holding one gives WILD.
    """
    out: list[str] = []
    rest = list(args)
    while True:
        used = False
        i = 0
        while i < len(format_):
            t = _TIME.match(format_, i)
            if t is not None:
                # %(…)T: the date as the strftime format says; its directives are only known at run time.
                if t.group("width") == "*" or t.group("precision") == "*":
                    return None
                if (rest.pop(0) if rest else None) is not None:
                    used = True
                out.append(re.sub(r"%.", WILD, t.group("format")))
                i = t.end()
                continue
            m = _CONVERSION.match(format_, i)
            if m is None:
                if format_[i] == "%":
                    return "".join(out)  # bash: an invalid format character ends the output
                end = format_.find("%", i)
                end = len(format_) if end < 0 else end
                text, stop = _printf_escapes(format_[i:end], b=False)
                out.append(text)
                if stop:
                    return "".join(out)
                i = end
                continue
            i = m.end()
            conversion = m.group("conversion")
            if conversion == "%":
                out.append("%")
                continue
            flags = m.group("flags")
            width = m.group("width")
            precision = m.group("precision")
            if width == "*":
                width, used = str(_number(rest.pop(0) if rest else "0")), True
            if precision == "*":
                precision, used = str(_number(rest.pop(0) if rest else "0")), True
            arg = rest.pop(0) if rest else None
            used = used or arg is not None
            piece: str | None
            if arg is not None and opaque & set(arg) and (conversion not in "s" or precision is not None):
                # An opaque character (text only known at run time) cut, quoted or read as a number.
                piece, stop = WILD, False
            else:
                piece, stop = _converted(conversion, flags, width, precision, arg)
            if piece is None:
                return None
            out.append(piece)
            if stop:
                return "".join(out)
            if sum(map(len, out)) > _MAX_PRINTF:
                raise Exhausted("transform")
        if not used or not rest:
            return "".join(out)


def _converted(
    conversion: str, flags: str, width: str | None, precision: str | None, arg: str | None
) -> tuple[str | None, bool]:
    try:
        w = int(width) if width else 0
        p = None if precision is None else int(precision or "0")
    except ValueError:
        return None, False
    if w < 0:
        flags, w = flags + "-", -w
    if w > _MAX_PRINTF or (p is not None and abs(p) > _MAX_PRINTF):
        raise Exhausted("transform")
    if p is not None and p < 0:
        p = None
    stop = False
    if conversion in "sbqc":
        text = arg or ""
        if conversion == "b":
            text, stop = _printf_escapes(text, b=True)
        elif conversion == "q":
            text = _backslash_quoted(text)
        elif conversion == "c":
            if not text:
                return "", True  # the character is NUL: the variable's value ends there
            text = text[:1]
        if p is not None and conversion in "sb":
            text = text[:p]
        return (text.ljust(w) if "-" in flags else text.rjust(w)), stop
    if conversion in "diouxX":
        return _c_integer(conversion, flags, w, p, _number(arg)), False
    if conversion in "eEfFgGaA":
        return WILD, False  # a number: only its digits are left to run time
    return "", True  # bash: an invalid format character ends the output


def _c_integer(conversion: str, flags: str, width: int, precision: int | None, n: int) -> str:
    """An integer as C's printf formats it (bash's integers are 64-bit)."""
    if conversion in "ouxX" and n < 0:
        n &= (1 << 64) - 1
    digits = {"o": "%o", "x": "%x", "X": "%X"}.get(conversion, "%d") % abs(n)
    if precision is not None:
        digits = "" if precision == 0 and n == 0 else digits.rjust(precision, "0")
    prefix = ""
    if "#" in flags and conversion == "o" and not digits.startswith("0"):
        digits = "0" + digits
    elif "#" in flags and conversion in "xX" and n != 0:
        prefix = "0" + conversion
    if conversion in "di":
        prefix = "-" if n < 0 else ("+" if "+" in flags else (" " if " " in flags else ""))
    if "-" in flags:
        return (prefix + digits).ljust(width)
    if "0" in flags and precision is None:
        return prefix + digits.rjust(width - len(prefix), "0")
    return (prefix + digits).rjust(width)


def _number(arg: str | None) -> int:
    """An integer argument as bash reads it: its leading number (0x… hex, 0… octal, 'c a character), or 0."""
    text = (arg or "").lstrip()
    if text[:1] in ("'", '"'):
        return ord(text[1]) if len(text) > 1 else 0
    m = re.match(r"([-+]?)(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)", text)
    if m is None:
        return 0
    body = m.group(2)
    value = int(body, 16) if body[:2].lower() == "0x" else int(body, 8) if body.startswith("0") else int(body)
    value = -value if m.group(1) == "-" else value
    return max(-(1 << 63), min(value, (1 << 64) - 1))


_SIMPLE_ESCAPES: Final[dict[str, str]] = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
}


def _printf_escapes(text: str, *, b: bool) -> tuple[str, bool]:
    """A printf format's backslash escapes decoded (``b``: a ``%b`` argument's, where ``\\c`` stops
    the output and ``\\0NNN`` is octal); whether ``\\c`` stopped it."""
    out: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c != "\\" or i + 1 >= len(text):
            out.append(c)
            i += 1
            continue
        e = text[i + 1]
        if e in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[e])
            i += 2
        elif e in "\"'?":
            out.append("\\" + e if b else e)
            i += 2
        elif b and e == "c":
            return "".join(out), True
        elif b and e == "0":
            m = re.match(r"[0-7]{0,3}", text[i + 2 :])
            assert m is not None
            out.append(chr(int(m.group() or "0", 8) & 0xFF))
            i += 2 + len(m.group())
        elif e in "01234567":
            m = re.match(r"[0-7]{1,3}", text[i + 1 :])
            assert m is not None
            out.append(chr(int(m.group(), 8) & 0xFF))
            i += 1 + len(m.group())
        elif e in "xuU" and (digits := _hex_digits(e, text, i + 2)):
            out.append(chr(min(int(digits, 16), 0x10FFFF)))
            i += 2 + len(digits)
        else:
            out.append("\\" + e)
            i += 2
        if out and out[-1] == "\x00":
            return "".join(out[:-1]), True  # a variable holds no NUL: its value ends there
    return "".join(out), False


_HEX2: Final[re.Pattern[str]] = re.compile(r"[0-9A-Fa-f]{1,2}")
_HEX4: Final[re.Pattern[str]] = re.compile(r"[0-9A-Fa-f]{1,4}")
_HEX8: Final[re.Pattern[str]] = re.compile(r"[0-9A-Fa-f]{1,8}")


def _hex_digits(escape: str, text: str, start: int) -> str:
    """The hex digits ``\\x`` (2), ``\\u`` (4) or ``\\U`` (8) takes from ``text[start:]``."""
    if escape == "x":
        m = _HEX2.match(text, start)
    elif escape == "u":
        m = _HEX4.match(text, start)
    else:
        m = _HEX8.match(text, start)
    return m.group() if m else ""


def _backslash_quoted(text: str) -> str:
    if text == "":
        return "''"
    if all(c.isprintable() for c in text):
        quoted = re.sub(r"([^A-Za-z0-9@%_+=:./~#-])", r"\\\1", text)
        return "\\" + quoted if quoted[0] in "~#" else quoted
    return _ansi_c_quoted(text)
