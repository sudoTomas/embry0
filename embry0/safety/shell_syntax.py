"""A shell command parsed the way bash parses it: its words, its commands and how they nest.

``embry0.safety.bash_paths`` finds the paths a command names from what this
module makes of it.

Words lose their quotes (``'…'``, ``"…"``, ``$'…'``, ``$"…"``) and
backslashes, ``\\``-newline is joined, and ``~``, ``~user`` and ``$HOME`` are
expanded (``~+``, ``$PWD``, ``~-`` and ``$OLDPWD`` are kept as the directory
they stand for). Every command substitution (``$(…)``, backticks, ``<(…)``,
``>(…)``, in or out of quotes, in an arithmetic expression, in ``${…}`` and in
an unquoted here-document's body) is parsed as a script of its own, and its
end is found by parsing it, as bash finds it: a ``case`` pattern's ``)`` or a
here-document's body inside it does not end it. Arithmetic (``$((…))``,
``((…))``, ``$[…]``) is told from a subshell the way bash tells them apart,
and a ``<<`` inside it is a shift, not a here-document. Here-documents are read
by their real delimiter. A ``$'…'`` string ends at a NUL, as in bash.

Commands are parsed into lists, ``&&``/``||`` chains, pipelines, simple
commands, subshells, ``{ …; }`` groups, ``if``, ``while``/``until``,
``for``/``select``, ``case``, ``[[ … ]]``, ``(( … ))``, ``coproc`` and
function definitions, with the redirections each carries.

Text only known at run time (any other variable, a substitution's output) is
kept as one :data:`WILD` character of kind :data:`RUN`. NUL can stand for it
because no word can hold a NUL: bash ends a string there, and a command with a
NUL in it is refused. A command this module cannot read raises
:class:`BashSyntaxError`; bash would refuse it too, or it uses a form this
module does not know.
"""

from __future__ import annotations

import pwd
import re
from dataclasses import dataclass, field
from typing import Final

# Stands for text only known at run time: a variable, a substitution.
WILD: Final[str] = "\x00"
# How deeply substitutions and compound commands may nest before a command is refused.
MAX_NESTING: Final[int] = 40

# The kind of each character of a word.
LIT: Final[int] = 0  # quoted, or produced by an expansion: a character, nothing more
ACT: Final[int] = 1  # unquoted: glob and brace characters apply
RUN: Final[int] = 2  # text only known at run time (the character is WILD)
EXT: Final[int] = 3  # an extglob pattern, ?(…) *(…) +(…) @(…) !(…) (the character is WILD)
CWD: Final[int] = 4  # the shell's current directory: ~+ or $PWD at the start of a word (WILD)
OLD: Final[int] = 5  # the previous directory: ~- or $OLDPWD at the start of a word (WILD)


class BashSyntaxError(ValueError):
    """A command this module cannot read as the shell would."""


class Expansion(str):
    """A parameter expansion, kept in a word as the WILD character it stands for.

    ``name`` is the parameter (None for one this module cannot name: a length
    ``${#x}``, a process substitution); ``indirect`` for ``${!x}``, whose
    parameter is the one ``x`` names; ``whole`` for a list (``$@``, ``$*``,
    ``${a[@]}``, ``${a[*]}``). ``op`` is ``""`` for its value; one of ``:-``
    ``-`` ``:=`` ``=`` ``:+`` ``+`` ``:?`` ``?`` with ``word`` as its word; a
    transformation of the value: ``#`` ``##`` ``%`` ``%%`` (``word`` the
    pattern), ``/`` ``//`` (``word`` the pattern, whose leading ``#`` or ``%``
    anchors it once expanded, ``repl`` the replacement), ``^`` ``^^`` ``,``
    ``,,`` ``~`` ``~~`` (``word`` the pattern, if any), ``@U`` ``@u`` ``@L``
    ``@Q`` ``@E``, ``@P`` (expanded as a prompt: its command substitutions
    run), or ``substr`` (``arith`` the offset and length); or ``"other"`` for
    any other. ``quoted``: inside
    double quotes (or a tilde's), so its value is neither split nor globbed.
    A tilde is ``HOME``, ``~+`` is ``PWD`` and ``~-`` is ``OLDPWD``.
    """

    name: str | None
    op: str
    word: Word | None
    quoted: bool
    indirect: bool
    whole: bool
    repl: Word | None
    arith: str

    def __new__(
        cls,
        name: str | None,
        op: str = "",
        word: Word | None = None,
        *,
        quoted: bool = False,
        indirect: bool = False,
        whole: bool = False,
        repl: Word | None = None,
        arith: str = "",
    ) -> Expansion:
        self = super().__new__(cls, WILD)
        self.name = name
        self.op = op
        self.word = word
        self.quoted = quoted
        self.indirect = indirect
        self.whole = whole
        self.repl = repl
        self.arith = arith
        return self


@dataclass
class Word:
    chars: list[str] = field(default_factory=list)
    kinds: list[int] = field(default_factory=list)
    subs: list[Script] = field(default_factory=list)  # command substitutions inside the word
    maybe_subs: list[str] = field(default_factory=list)  # !(…) at a word's start: a subshell, or a pattern
    array: list[Word] = field(default_factory=list)  # NAME=(…): the elements
    quoted: bool = False
    start: int = 0
    end: int = 0
    raw: str = ""

    @property
    def text(self) -> str:
        return "".join(self.chars)

    @property
    def plain(self) -> bool:
        """An unquoted word with no expansion: what a reserved word, an alias or a function name must be."""
        return not self.quoted and all(k == ACT for k in self.kinds) and self.raw == self.text

    def add(self, text: str, kind: int) -> None:
        self.chars.extend(text)
        self.kinds.extend([kind] * len(text))

    def mark(self, kind: int, expansion: Expansion | None = None) -> None:
        self.chars.append(expansion if expansion is not None else WILD)
        self.kinds.append(kind)


@dataclass
class Heredoc:
    strip_tabs: bool
    delimiter: str
    quoted: bool
    body: str = ""
    subs: list[Script] = field(default_factory=list)


# ---- the parsed command ------------------------------------------------------------


@dataclass
class Redirected:
    redirects: list[tuple[str, Word]] = field(default_factory=list)
    heredocs: list[Heredoc] = field(default_factory=list)
    herestrings: list[Word] = field(default_factory=list)


@dataclass
class Simple(Redirected):
    words: list[Word] = field(default_factory=list)


@dataclass
class Subshell(Redirected):
    body: Script = field(default_factory=lambda: Script())


@dataclass
class Group(Redirected):
    body: Script = field(default_factory=lambda: Script())


@dataclass
class If(Redirected):
    branches: list[tuple[Script, Script]] = field(default_factory=list)
    orelse: Script | None = None


@dataclass
class Loop(Redirected):
    """``while``/``until`` (``condition``), ``for``/``select`` (``items``), or ``for ((…))`` (``arith``)."""

    condition: Script | None = None
    until: bool = False
    var: str = ""  # for/select: the name each item is assigned to
    items: list[Word] | None = None
    arith: Arith | None = None
    body: Script = field(default_factory=lambda: Script())


@dataclass
class Case(Redirected):
    subject: Word = field(default_factory=Word)
    items: list[tuple[list[Word], Script]] = field(default_factory=list)


@dataclass
class Arith(Redirected):
    """``(( … ))``: its substitutions run."""

    text: str = ""
    subs: list[Script] = field(default_factory=list)


@dataclass
class Cond(Redirected):
    """``[[ … ]]``: its words are tests, not paths; their substitutions run."""

    words: list[Word] = field(default_factory=list)


@dataclass
class FunctionDef(Redirected):
    name: str = ""
    body: Command | None = None


@dataclass
class Coproc(Redirected):
    body: Command | None = None


Command = Simple | Subshell | Group | If | Loop | Case | Arith | Cond | FunctionDef | Coproc


@dataclass
class Pipeline:
    commands: list[Command] = field(default_factory=list)
    negated: bool = False


@dataclass
class AndOr:
    first: Pipeline = field(default_factory=Pipeline)
    rest: list[tuple[str, Pipeline]] = field(default_factory=list)


@dataclass
class Script:
    """A list of ``&&``/``||`` chains, each ended by ``;``, a newline or ``&`` (run in the background)."""

    items: list[tuple[AndOr, bool]] = field(default_factory=list)  # (chain, background)


def _describe(token: Token) -> str:
    kind, value, _ = token
    if kind == "word" and isinstance(value, Word):
        return repr(value.raw)
    if kind == "arith":
        return "'(('"
    if kind == "heredoc":
        return "'<<'"
    if kind == "eof":
        return "the end of the command"
    return repr(value)


def parse(src: str, home: str) -> Script:
    """Parse ``src`` as bash would; ``home`` is what ``~`` and ``$HOME`` expand to."""
    if "\x00" in src:
        raise BashSyntaxError("the command holds a NUL character, which no shell can be given")
    parser = _Parser(src, 0, home, depth=0)
    script = parser.script(stop_ops=frozenset())
    if parser.peek()[0] != "eof":
        raise BashSyntaxError(f"unexpected {_describe(parser.peek())}")
    return script


# The prompt escapes whose text no expansion reads: bash quotes what they give (a user, a host, a
# directory, a date) or gives a control character. One space stands for each.
_PROMPT_TEXT_ESCAPES: Final[str] = "aedtT@AswWuhHjlvV#!nr[]"


def _decoded_prompt(text: str) -> str:
    """``text`` after bash replaces its prompt escapes (decode_prompt_string in bash's parse.y)."""
    out: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c != "\\" or i + 1 >= len(text):
            out.append(c)
            i += 1
            continue
        e = text[i + 1]
        if e in "01234567":
            # bash reads the next three characters as an octal number, or fewer at the end of the text;
            # with anything else among them it is a backslash, and the digits stay text.
            digits = text[i + 1 : i + 4]
            if all(d in "01234567" for d in digits):
                code = int(digits, 8) % 256  # a char: \444 is $ as \044 is
                if code:
                    out.append(chr(code))
                i += 1 + len(digits)
            else:
                out.append("\\")
                i += 1
            continue
        if e == "\\":
            out.append("\\")
        elif e == "$":
            out.append("\\$")  # $ for a user that is not root, written so no expansion reads it
        elif e == "D" and text.startswith("{", i + 2) and text.find("}", i + 3) != -1:
            out.append(" ")  # \D{format}: a date
            i = text.find("}", i + 3) + 1
            continue
        elif e in _PROMPT_TEXT_ESCAPES:
            out.append(" ")
        else:
            out.append("\\" + e)
        i += 2
    return "".join(out)


def parse_prompt(text: str, home: str) -> Script:
    """``text`` as bash expands a prompt (``PS0``-``PS4``, ``${X@P}``): the command ``: <prompt>``.

    bash first replaces the prompt's backslash escapes: ``\\NNN`` is the character with that octal
    code, so it can make a ``$``; ``\\\\`` is one backslash; the others give text no expansion reads.
    It then expands the result as the inside of a double-quoted string, except that a ``"`` in it is
    a character, and a backquoted command in it is read as outside quotes.
    """
    decoded = _decoded_prompt(text)
    if "\x00" in decoded:
        raise BashSyntaxError("the prompt holds a NUL character")
    lexer = _Lexer(decoded, 0, home, 1)
    word = Word(start=0, quoted=True)
    word.end = lexer.double(word, 0, prompt=True)
    word.raw = decoded
    colon = Word(chars=[":"], kinds=[ACT], end=1, raw=":")
    return Script(items=[(AndOr(first=Pipeline(commands=[Simple(words=[colon, word])])), False)])


# ---- reading words ------------------------------------------------------------------------

_OPERATORS: Final[tuple[str, ...]] = (
    ";;&",
    "<<<",
    "<<-",
    "&>>",
    ";;",
    ";&",
    "<<",
    "&>",
    ">>",
    ">|",
    "<>",
    "<&",
    ">&",
    "&&",
    "||",
    "|&",
    "<(",
    ">(",
    "|",
    "&",
    ";",
    "(",
    ")",
    "<",
    ">",
)
_REDIRECTS: Final[frozenset[str]] = frozenset({"<", ">", ">>", ">|", "<>", "&>", "&>>", "<&", ">&", "<<<"})
_WORD_END: Final[frozenset[str]] = frozenset(" \t\n|&;()<>")
_NAME: Final[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ARRAY_ASSIGNMENT: Final[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=")
_FD_NAME: Final[re.Pattern[str]] = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
# A parameter in ${…}: an indirect !, a name, a positional or special parameter, a subscript.
_PARAMETER: Final[re.Pattern[str]] = re.compile(
    r"(?P<bang>!)?(?P<name>[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[@*#?$!-])(?P<sub>\[[^]]*\])?"
)
_DEFAULT_OPERATORS: Final[tuple[str, ...]] = (":-", ":=", ":+", ":?", "-", "=", "+", "?")
_PATTERN_OPERATORS: Final[tuple[str, ...]] = ("##", "#", "%%", "%")
_CASE_OPERATORS: Final[tuple[str, ...]] = ("^^", "^", ",,", ",", "~~", "~")
_EXTGLOB_OPENERS: Final[frozenset[str]] = frozenset("?*+@!")


def _user_home(user: str) -> str:
    try:
        return pwd.getpwnam(user).pw_dir
    except KeyError:
        raise BashSyntaxError(f"cannot resolve ~{user}: no such user") from None


def _skip_single(s: str, j: int) -> int:
    k = s.find("'", j + 1)
    if k < 0:
        raise BashSyntaxError("unterminated single quote")
    return k + 1


def _skip_double(s: str, j: int, home: str, depth: int) -> int:
    """``s[j]`` opens a double-quoted string; the index after it closes."""
    j += 1
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j + 1
        if c == "$" and s.startswith("$(", j):
            _, j = _dollar_paren(s, j, home, depth)
            continue
        if c == "$" and s.startswith("${", j):
            _, j = _braced(s, j + 1, home, depth)
            continue
        if c == "`":
            _, j = _backtick(s, j, in_double=True)
            continue
        j += 1
    raise BashSyntaxError("unterminated double quote")


def _paren_end(s: str, i: int, home: str, depth: int) -> int:
    """``s[i]`` is ``(``: the index after the ``)`` that closes it, counting parentheses (quotes skipped)."""
    level = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            j = _skip_single(s, j)
            continue
        if c == '"':
            j = _skip_double(s, j, home, depth)
            continue
        if c == "`":
            _, j = _backtick(s, j, in_double=False)
            continue
        if c == "(":
            level += 1
        elif c == ")":
            level -= 1
            if level == 0:
                return j + 1
        j += 1
    raise BashSyntaxError("unbalanced (")


def _arith_end(s: str, i: int, home: str, depth: int) -> int | None:
    """``s[i:i+2]`` is ``((``: the index after the closing ``))`` when bash reads it as arithmetic, else None.

    bash reads ``((…))`` as arithmetic only when the inner ``(`` closes right
    before the outer one; ``$((cmd) )`` and ``((a); (b))`` are subshells.
    """
    try:
        inner_end = _paren_end(s, i + 1, home, depth)
    except BashSyntaxError:
        return None
    if inner_end < len(s) and s[inner_end] == ")":
        return inner_end + 1
    return None


def _backtick(s: str, i: int, *, in_double: bool) -> tuple[str, int]:
    """``s[i]`` opens a backtick substitution; its command, and the index after it.

    In it a backslash escapes ``$``, a backtick and a backslash, and also ``"``
    when the backticks are inside double quotes.
    """
    escapable = '`\\$"' if in_double else "`\\$"
    out: list[str] = []
    j = i + 1
    while j < len(s):
        c = s[j]
        if c == "\\" and j + 1 < len(s) and s[j + 1] in escapable:
            out.append(s[j + 1])
            j += 2
            continue
        if c == "\\" and s.startswith("\\\n", j):
            j += 2
            continue
        if c == "`":
            return "".join(out), j + 1
        out.append(c)
        j += 1
    raise BashSyntaxError("unterminated backtick")


def _comsub(s: str, i: int, home: str, depth: int) -> tuple[Script, int]:
    """``s[i]`` starts the command of a ``$(…)``, ``<(…)`` or ``>(…)``: parse it to its ``)``."""
    parser = _Parser(s, i, home, depth=depth + 1)
    script = parser.script(stop_ops=frozenset({")"}))
    kind, value, pos = parser.next()
    if kind != "op" or value != ")":
        raise BashSyntaxError("unterminated command substitution")
    if parser.lexer.pending or parser.buffer:
        raise BashSyntaxError("a here-document is still open where its command substitution ends")
    return script, pos + 1


def _dollar_paren(s: str, i: int, home: str, depth: int) -> tuple[list[Script], int]:
    """``s[i:i+2]`` is ``$(``: the scripts it runs, and the index after it."""
    if s.startswith("$((", i):
        end = _arith_end(s, i + 1, home, depth)
        if end is not None:
            return _substitutions(s[i + 3 : end - 2], home, depth), end
    script, end = _comsub(s, i + 2, home, depth)
    return [script], end


def _braced(s: str, i: int, home: str, depth: int) -> tuple[str, int]:
    """``s[i]`` is the ``{`` of ``${``: its contents, and the index after the closing ``}``."""
    level = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            j = _skip_single(s, j)
            continue
        if c == '"':
            j = _skip_double(s, j, home, depth)
            continue
        if c == "`":
            _, j = _backtick(s, j, in_double=False)
            continue
        if s.startswith("$(", j):
            _, j = _dollar_paren(s, j, home, depth)
            continue
        if c == "{":
            level += 1
        elif c == "}":
            level -= 1
            if level == 0:
                return s[i + 1 : j], j + 1
        j += 1
    raise BashSyntaxError("unbalanced ${")


def _substitutions(text: str, home: str, depth: int) -> list[Script]:
    """The command substitutions in ``text``: an unquoted here-document's body, ``${…}``, arithmetic.

    Quotes do not protect one there, so none is skipped.
    """
    if depth > MAX_NESTING:
        raise BashSyntaxError("commands nested too deep to read")
    subs: list[Script] = []
    j = 0
    while j < len(text):
        if text[j] == "\\":
            j += 2
            continue
        if text.startswith("$(", j):
            found, j = _dollar_paren(text, j, home, depth)
            subs.extend(found)
            continue
        if text.startswith("${", j):
            inner, j = _braced(text, j + 1, home, depth)
            subs.extend(_substitutions(inner, home, depth + 1))
            continue
        if text.startswith("$[", j):
            inner, j = _bracketed(text, j + 1)
            subs.extend(_substitutions(inner, home, depth + 1))
            continue
        if text[j] == "`":
            inner, j = _backtick(text, j, in_double=False)
            subs.append(_Parser(inner, 0, home, depth=depth + 1).whole())
            continue
        j += 1
    return subs


def _bracketed(s: str, i: int) -> tuple[str, int]:
    """``s[i]`` is the ``[`` of ``$[``: its contents, and the index after the closing ``]``."""
    level = 0
    for j in range(i, len(s)):
        if s[j] == "[":
            level += 1
        elif s[j] == "]":
            level -= 1
            if level == 0:
                return s[i + 1 : j], j + 1
    raise BashSyntaxError("unbalanced $[")


_ANSI_ESCAPES: Final[dict[str, str]] = {
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
    "'": "'",
    '"': '"',
    "?": "?",
}


def _ansi_c(s: str, i: int) -> tuple[str, int]:
    """``$'…'`` from ``s[i]`` (just after the quote): its value, and the index after the closing quote.

    The value ends at the first NUL an escape makes (``\\0``, ``\\x00``,
    ``\\u0000``, ``\\c@``): bash keeps strings as C strings.
    """
    value, end, _ = _ansi_c_ended(s, i)
    return value, end


def _ansi_c_ended(s: str, i: int) -> tuple[str, int, bool]:
    """``_ansi_c``, and whether a NUL ended the value."""
    out: list[str] = []
    ended = False
    j = i
    while j < len(s):
        c = s[j]
        if c == "'":
            return "".join(out), j + 1, ended
        if c != "\\" or j + 1 >= len(s):
            value, j = c, j + 1
        else:
            e = s[j + 1]
            if e in _ANSI_ESCAPES:
                value, j = _ANSI_ESCAPES[e], j + 2
            elif e in "01234567":
                m = re.match(r"[0-7]{1,3}", s[j + 1 :])
                assert m is not None
                value, j = chr(int(m.group(), 8) & 0xFF), j + 1 + len(m.group())
            elif e == "x" and (m := re.match(r"[0-9A-Fa-f]{1,2}", s[j + 2 :])):
                value, j = chr(int(m.group(), 16)), j + 2 + len(m.group())
            elif e == "u" and (m := re.match(r"[0-9A-Fa-f]{1,4}", s[j + 2 :])):
                value, j = chr(int(m.group(), 16)), j + 2 + len(m.group())
            elif e == "U" and (m := re.match(r"[0-9A-Fa-f]{1,8}", s[j + 2 :])):
                value, j = chr(min(int(m.group(), 16), 0x10FFFF)), j + 2 + len(m.group())
            elif e == "c" and j + 2 < len(s):
                value, j = chr(ord(s[j + 2]) & 0x1F), j + 3
            else:
                value, j = "\\" + e, j + 2
        if value == "\x00":
            ended = True
        if not ended:
            out.append(value)
    raise BashSyntaxError("unterminated $'")


def decode_ansi_c(text: str) -> str:
    """``text`` with its backslash escapes decoded as ``$'…'`` decodes them (``${X@E}``)."""
    out: list[str] = []
    i = 0
    while True:
        # A quote ends $'…'; in a value it is itself, so each run between quotes is decoded alone.
        end = i
        while end < len(text) and text[end] != "'":
            end += 2 if text[end] == "\\" else 1
        run = text[i:end]
        if end > len(text):  # a backslash at the very end is itself
            end, run = len(text), text[i:-1]
        decoded, _, ended = _ansi_c_ended(run + "'", 0)
        out.append(decoded + ("\\" if run != text[i:end] and not ended else ""))
        if ended or end >= len(text):
            return "".join(out)
        out.append("'")
        i = end + 1


def _delimiter(raw: str) -> tuple[str, bool]:
    """A here-document's delimiter word as bash reads it: quotes removed, nothing expanded."""
    out: list[str] = []
    quoted = False
    j = 0
    while j < len(raw):
        c = raw[j]
        if c == "\\" and j + 1 < len(raw):
            out.append(raw[j + 1])
            quoted = True
            j += 2
        elif c == "$" and j + 1 < len(raw) and raw[j + 1] in "'\"":
            j += 1  # $'…' and $"…" are quoted forms of their text
        elif c == "'":
            k = _skip_single(raw, j)
            out.append(raw[j + 1 : k - 1])
            quoted = True
            j = k
        elif c == '"':
            k = raw.find('"', j + 1)
            if k < 0:
                raise BashSyntaxError("unterminated double quote")
            out.append(raw[j + 1 : k])
            quoted = True
            j = k + 1
        else:
            out.append(c)
            j += 1
    return "".join(out), quoted


Token = tuple[str, object, int]  # (kind, value, position): kind is word, op, heredoc, arith or eof


class _Lexer:
    def __init__(self, src: str, pos: int, home: str, depth: int) -> None:
        self.s = src
        self.i = pos
        self.home = home
        self.depth = depth
        self.pending: list[Heredoc] = []

    def token(self) -> Token:
        s = self.s
        while True:
            if self.i >= len(s):
                self._bodies()
                return ("eof", None, self.i)
            c = s[self.i]
            if c in " \t":
                self.i += 1
                continue
            if s.startswith("\\\n", self.i):
                self.i += 2
                continue
            pos = self.i
            if c == "\n":
                self.i += 1
                self._bodies()
                return ("op", "\n", pos)
            if c == "#":
                end = s.find("\n", self.i)
                self.i = len(s) if end < 0 else end
                continue
            if s.startswith("((", self.i):
                closes = _arith_end(s, self.i, self.home, self.depth)
                if closes is not None:
                    text = s[self.i + 2 : closes - 2]
                    self.i = closes
                    return ("arith", Arith(text=text, subs=_substitutions(text, self.home, self.depth + 1)), pos)
            op = next((o for o in _OPERATORS if s.startswith(o, self.i)), None)
            if op in ("<(", ">("):
                script, end = _comsub(s, self.i + 2, self.home, self.depth)
                word = Word(start=pos, end=end, raw=s[pos:end])
                word.mark(RUN, Expansion(None, "procsub"))
                word.subs.append(script)
                self.i = end
                return ("word", word, pos)
            if op in ("<<", "<<-"):
                self.i += len(op)
                while self.i < len(s) and s[self.i] in " \t":
                    self.i += 1
                target = self.word()
                if not target.raw:
                    raise BashSyntaxError("a here-document needs a delimiter")
                delimiter, quoted = _delimiter(target.raw)
                heredoc = Heredoc(strip_tabs=op == "<<-", delimiter=delimiter, quoted=quoted)
                self.pending.append(heredoc)
                return ("heredoc", heredoc, pos)
            if op is not None:
                self.i += len(op)
                return ("op", op, pos)
            return ("word", self.word(), pos)

    def _bodies(self) -> None:
        """Read the bodies of the here-documents opened on the line that just ended."""
        s = self.s
        for heredoc in self.pending:
            lines: list[str] = []
            while self.i < len(s):
                end = s.find("\n", self.i)
                line = s[self.i :] if end < 0 else s[self.i : end]
                self.i = len(s) if end < 0 else end + 1
                if not heredoc.quoted:
                    # An unquoted body joins a line that ends in an odd number of backslashes with the next.
                    while (len(line) - len(line.rstrip("\\"))) % 2 == 1 and self.i < len(s):
                        end = s.find("\n", self.i)
                        line = line[:-1] + (s[self.i :] if end < 0 else s[self.i : end])
                        self.i = len(s) if end < 0 else end + 1
                if (line.lstrip("\t") if heredoc.strip_tabs else line) == heredoc.delimiter:
                    break
                lines.append(line)
            heredoc.body = "\n".join(lines)
            if not heredoc.quoted:
                # An unquoted delimiter expands the body: its substitutions run.
                heredoc.subs = _substitutions(heredoc.body, self.home, self.depth + 1)
        self.pending = []

    def word(self, ends: frozenset[str] = _WORD_END) -> Word:
        s = self.s
        word = Word(start=self.i)
        while self.i < len(s):
            c = s[self.i]
            if c in "<>" and s.startswith("(", self.i + 1) and word.chars:
                # A process substitution inside a word, as in X=<(cmd): the file it names is only known at run time.
                script, self.i = _comsub(s, self.i + 2, self.home, self.depth)
                word.subs.append(script)
                word.mark(RUN, Expansion(None, "procsub"))
                continue
            if c in ends:
                break
            if c == "\\":
                if s.startswith("\\\n", self.i):
                    self.i += 2
                elif self.i + 1 < len(s):
                    word.add(s[self.i + 1], LIT)
                    word.quoted = True
                    self.i += 2
                else:
                    word.add("\\", LIT)
                    self.i += 1
                continue
            if c == "'":
                end = _skip_single(s, self.i)
                word.add(s[self.i + 1 : end - 1], LIT)
                word.quoted = True
                self.i = end
                continue
            if s.startswith("$'", self.i):
                text, self.i = _ansi_c(s, self.i + 2)
                word.add(text, LIT)
                word.quoted = True
                continue
            if s.startswith('$"', self.i):
                word.quoted = True
                self.i = self.double(word, self.i + 2)
                continue
            if c == '"':
                word.quoted = True
                self.i = self.double(word, self.i + 1)
                continue
            if c == "$":
                self.i = self.dollar(word, self.i, kind=ACT)
                continue
            if c == "`":
                inner, self.i = _backtick(s, self.i, in_double=False)
                word.subs.append(_Parser(inner, 0, self.home, depth=self.depth + 1).whole())
                word.mark(RUN)
                continue
            if c == "~" and not word.chars and not word.quoted:
                self.i = self.tilde(word, self.i)
                continue
            if c in _EXTGLOB_OPENERS and s.startswith("(", self.i + 1):
                end = _paren_end(s, self.i + 1, self.home, self.depth)
                if c == "!" and not word.chars:
                    # With extglob off, as in Claude Code's shell, !(…) is a negated subshell.
                    word.maybe_subs.append(s[self.i + 2 : end - 1])
                word.mark(EXT)
                self.i = end
                continue
            word.add(c, ACT)
            self.i += 1
        if s.startswith("(", self.i) and _ARRAY_ASSIGNMENT.fullmatch(s[word.start : self.i]):
            self._array(word)
        word.end = self.i
        word.raw = s[word.start : self.i]
        return word

    def _array(self, word: Word) -> None:
        """``NAME=(…)``: the array's elements are values; their substitutions run."""
        inner = _Lexer(self.s, self.i + 1, self.home, self.depth + 1)
        while True:
            kind, value, _ = inner.token()
            if kind == "op" and value == ")":
                break
            if kind == "op" and value == "\n":
                continue
            if kind != "word":
                raise BashSyntaxError("unexpected text in an array assignment")
            assert isinstance(value, Word)
            word.subs.extend(value.subs)
            word.array.append(value)
        self.i = inner.i
        word.mark(RUN)

    def double(self, word: Word, i: int, *, prompt: bool = False) -> int:
        """Read a double-quoted string's contents from ``i``; the index after its closing quote.

        ``prompt``: the text is a prompt bash expands (``parse_prompt``). It runs to the end of the
        text, a ``"`` in it is a character, and a backquoted command in it is read as outside quotes.
        """
        s = self.s
        while i < len(s):
            c = s[i]
            if c == '"' and not prompt:
                return i + 1
            if c == "\\" and i + 1 < len(s) and s[i + 1] in '$`"\\\n':
                if s[i + 1] != "\n":
                    word.add(s[i + 1], LIT)
                i += 2
                continue
            if c == "$":
                i = self.dollar(word, i, kind=LIT)
                continue
            if c == "`":
                inner, i = _backtick(s, i, in_double=not prompt)
                word.subs.append(_Parser(inner, 0, self.home, depth=self.depth + 1).whole())
                word.mark(RUN)
                continue
            word.add(c, LIT)
            i += 1
        if prompt:
            return i
        raise BashSyntaxError("unterminated double quote")

    def dollar(self, word: Word, i: int, *, kind: int) -> int:
        """Read the expansion at ``s[i] == "$"``; the index after it. ``kind``: LIT inside quotes."""
        s = self.s
        if s.startswith("$(", i):
            scripts, end = _dollar_paren(s, i, self.home, self.depth)
            word.subs.extend(scripts)
            word.mark(RUN)
            return end
        if s.startswith("${", i):
            inner, end = _braced(s, i + 1, self.home, self.depth)
            word.subs.extend(_substitutions(inner, self.home, self.depth + 1))
            expansion = self.parameter(inner, quoted=kind == LIT)
            if expansion.name in ("PWD", "OLDPWD") and not expansion.op and not word.chars:
                word.mark(CWD if expansion.name == "PWD" else OLD, expansion)
            else:
                word.mark(RUN, expansion)
            return end
        if s.startswith("$[", i):
            inner, end = _bracketed(s, i + 1)
            word.subs.extend(_substitutions(inner, self.home, self.depth + 1))
            word.mark(RUN)
            return end
        m = _NAME.match(s, i + 1)
        if m:
            name = m.group()
            expansion = Expansion(name, quoted=kind == LIT)
            if name in ("PWD", "OLDPWD") and not word.chars:
                word.mark(CWD if name == "PWD" else OLD, expansion)
            else:
                word.mark(RUN, expansion)
            return m.end()
        if i + 1 < len(s) and s[i + 1] in "0123456789@*#?$!-":
            word.mark(RUN, Expansion(s[i + 1], quoted=kind == LIT, whole=s[i + 1] in "@*"))
            return i + 2
        word.add("$", kind)
        return i + 1

    def parameter(self, inner: str, *, quoted: bool) -> Expansion:
        """What ``${inner}`` expands: its parameter, and its operator and words if it has them."""
        if inner.startswith("#") and len(inner) > 1:
            return Expansion(None, "other", quoted=quoted)  # ${#x}: a length
        m = _PARAMETER.match(inner)
        if not m:
            return Expansion(None, "other", quoted=quoted)
        name, sub, indirect = m.group("name"), m.group("sub"), bool(m.group("bang"))
        rest = inner[m.end() :]
        if indirect and (sub in ("[@]", "[*]") or rest in ("@", "*")):
            return Expansion(None, "other", quoted=quoted)  # ${!a[@]} ${!prefix*}: keys or names
        whole = (name in ("@", "*") and not sub) or sub in ("[@]", "[*]")

        def lexed(text: str, ends: frozenset[str] = frozenset()) -> tuple[Word, int]:
            lexer = _Lexer(text, 0, self.home, self.depth + 1)
            return lexer.word(ends=ends), lexer.i

        def made(op: str = "", word: Word | None = None, repl: Word | None = None, arith: str = "") -> Expansion:
            return Expansion(name, op, word, quoted=quoted, indirect=indirect, whole=whole, repl=repl, arith=arith)

        if not rest:
            return made()
        # The words are one word each, their blanks kept (an unquoted one splits it later).
        op = next((o for o in _DEFAULT_OPERATORS if rest.startswith(o)), None)
        if op is not None:
            return made(op, lexed(rest[len(op) :])[0])
        op = next((o for o in _PATTERN_OPERATORS if rest.startswith(o)), None)
        if op is not None:
            return made(op, lexed(rest[len(op) :])[0])
        if rest.startswith("/"):
            # ${X/p/s}, ${X//p/s}: a pattern that starts with / keeps it (bash looks for the / that ends
            # the pattern after it). An anchor (# or %) is read from the pattern once it is expanded.
            op = "//" if rest.startswith("//") else "/"
            body = rest[len(op) :]
            lead = body.startswith("/")
            pattern, end = lexed(body[1:] if lead else body, frozenset("/"))
            if lead:
                pattern.chars.insert(0, "/")
                pattern.kinds.insert(0, ACT)
                end += 1
            repl = lexed(body[end + 1 :])[0] if end < len(body) else None
            return made(op, pattern, repl)
        op = next((o for o in _CASE_OPERATORS if rest.startswith(o)), None)
        if op is not None:
            return made(op, lexed(rest[len(op) :])[0] if rest[len(op) :] else None)
        if rest in ("@U", "@u", "@L", "@Q", "@E", "@P"):
            return made(rest)
        if rest.startswith(":"):
            return made("substr", arith=rest[1:])
        return made("other")

    def tilde(self, word: Word, i: int) -> int:
        """Expand ``~``, ``~user``, ``~+`` or ``~-`` at the start of a word; the index after it."""
        s = self.s
        j = i + 1
        while j < len(s) and s[j] not in _WORD_END and s[j] != "/":
            if s[j] in "'\"\\$`":
                word.add("~", ACT)  # a quoted tilde prefix is no expansion
                return i + 1
            j += 1
        user = s[i + 1 : j]
        if user == "+":
            word.mark(CWD, Expansion("PWD", quoted=True))
        elif user == "-":
            word.mark(OLD, Expansion("OLDPWD", quoted=True))
        elif not user:
            word.mark(RUN, Expansion("HOME", quoted=True))
        else:
            word.add(_user_home(user), LIT)
        return j


# ---- parsing commands ------------------------------------------------------------------

_LIST_ENDS: Final[frozenset[str]] = frozenset({")", ";;", ";&", ";;&"})
_COMPOUND_STARTS: Final[frozenset[str]] = frozenset({"{", "if", "while", "until", "for", "select", "case", "[["})


class _Parser:
    def __init__(self, src: str, pos: int, home: str, *, depth: int) -> None:
        if depth > MAX_NESTING:
            raise BashSyntaxError("commands nested too deep to read")
        self.lexer = _Lexer(src, pos, home, depth)
        self.depth = depth
        self.buffer: list[Token] = []

    def whole(self) -> Script:
        """Parse to the end of the source."""
        script = self.script(stop_ops=frozenset())
        if self.peek()[0] != "eof":
            raise BashSyntaxError(f"unexpected {_describe(self.peek())}")
        return script

    def peek(self, k: int = 0) -> Token:
        while len(self.buffer) <= k:
            self.buffer.append(self.lexer.token())
        return self.buffer[k]

    def next(self) -> Token:
        token = self.peek()
        self.buffer.pop(0)
        return token

    def _reserved(self, token: Token, *texts: str) -> bool:
        kind, value, _ = token
        return kind == "word" and isinstance(value, Word) and value.plain and value.text in texts

    def _op(self, token: Token, *ops: str) -> bool:
        return token[0] == "op" and token[1] in ops

    def _expect(self, text: str) -> None:
        token = self.next()
        if not self._reserved(token, text):
            raise BashSyntaxError(f"expected {text!r}, found {_describe(token)}")

    def _expect_op(self, op: str) -> None:
        token = self.next()
        if not self._op(token, op):
            raise BashSyntaxError(f"expected {op!r}, found {_describe(token)}")

    def _newlines(self) -> None:
        while self._op(self.peek(), "\n"):
            self.next()

    def script(self, *, stop_ops: frozenset[str], stop_words: frozenset[str] = frozenset()) -> Script:
        script = Script()
        while True:
            self._newlines()
            token = self.peek()
            if token[0] == "eof" or self._op(token, *(stop_ops | _LIST_ENDS)) or self._reserved(token, *stop_words):
                return script
            chain = self.and_or()
            token = self.peek()
            if self._op(token, ";", "&", "\n"):
                self.next()
                script.items.append((chain, token[1] == "&"))
                continue
            script.items.append((chain, False))
            if not (
                token[0] == "eof" or self._op(token, *(stop_ops | _LIST_ENDS)) or self._reserved(token, *stop_words)
            ):
                raise BashSyntaxError(f"unexpected {_describe(token)}")
            return script

    def and_or(self) -> AndOr:
        chain = AndOr(first=self.pipeline())
        while self._op(self.peek(), "&&", "||"):
            op = str(self.next()[1])
            self._newlines()
            chain.rest.append((op, self.pipeline()))
        return chain

    def pipeline(self) -> Pipeline:
        pipeline = Pipeline()
        while True:
            if self._reserved(self.peek(), "time"):
                self.next()
                if self._reserved(self.peek(), "-p"):
                    self.next()
                continue
            if self._reserved(self.peek(), "!"):
                self.next()
                pipeline.negated = not pipeline.negated
                continue
            break
        pipeline.commands.append(self.command())
        while self._op(self.peek(), "|", "|&"):
            self.next()
            self._newlines()
            pipeline.commands.append(self.command())
        return pipeline

    def command(self) -> Command:
        token = self.peek()
        kind, value, _ = token
        node: Command
        if kind == "arith":
            self.next()
            assert isinstance(value, Arith)
            node = value
        elif self._op(token, "("):
            self.next()
            node = Subshell(body=self._nested(stop_ops=frozenset({")"})))
            self._expect_op(")")
        elif kind == "word" and isinstance(value, Word) and value.plain:
            text = value.text
            if text == "{":
                self.next()
                node = Group(body=self._nested(stop_ops=frozenset(), stop_words=frozenset({"}"})))
                self._expect("}")
            elif text == "if":
                node = self._if()
            elif text in ("while", "until"):
                self.next()
                condition = self._nested(stop_ops=frozenset(), stop_words=frozenset({"do"}))
                node = Loop(condition=condition, until=text == "until", body=self._do_group())
            elif text in ("for", "select"):
                node = self._for()
            elif text == "case":
                node = self._case()
            elif text == "[[":
                node = self._cond()
            elif text == "function":
                self.next()
                name = self.next()
                if name[0] != "word":
                    raise BashSyntaxError("function needs a name")
                assert isinstance(name[1], Word)
                if self._op(self.peek(), "(") and self._op(self.peek(1), ")"):
                    self.next()
                    self.next()
                self._newlines()
                node = FunctionDef(name=name[1].text, body=self.command())
                return node
            elif text == "coproc":
                self.next()
                follow = self.peek()
                if (
                    follow[0] == "word"
                    and isinstance(follow[1], Word)
                    and follow[1].plain
                    and follow[1].text not in _COMPOUND_STARTS
                    and (self._reserved(self.peek(1), *_COMPOUND_STARTS) or self._op(self.peek(1), "("))
                ):
                    self.next()  # the coprocess's name
                node = Coproc(body=self.command())
                return node
            elif self._op(self.peek(1), "(") and self._op(self.peek(2), ")"):
                self.next()
                self.next()
                self.next()
                self._newlines()
                return FunctionDef(name=text, body=self.command())
            else:
                return self._simple()
        else:
            return self._simple()
        self._redirects(node)
        return node

    def _nested(self, *, stop_ops: frozenset[str], stop_words: frozenset[str] = frozenset()) -> Script:
        if self.depth + 1 > MAX_NESTING:
            raise BashSyntaxError("commands nested too deep to read")
        self.depth += 1
        try:
            return self.script(stop_ops=stop_ops, stop_words=stop_words)
        finally:
            self.depth -= 1

    def _do_group(self) -> Script:
        self._newlines()
        if self._reserved(self.peek(), "{"):
            self.next()
            body = self._nested(stop_ops=frozenset(), stop_words=frozenset({"}"}))
            self._expect("}")
            return body
        self._expect("do")
        body = self._nested(stop_ops=frozenset(), stop_words=frozenset({"done"}))
        self._expect("done")
        return body

    def _if(self) -> If:
        self.next()
        node = If()
        condition = self._nested(stop_ops=frozenset(), stop_words=frozenset({"then"}))
        self._expect("then")
        node.branches.append(
            (condition, self._nested(stop_ops=frozenset(), stop_words=frozenset({"elif", "else", "fi"})))
        )
        while self._reserved(self.peek(), "elif"):
            self.next()
            condition = self._nested(stop_ops=frozenset(), stop_words=frozenset({"then"}))
            self._expect("then")
            body = self._nested(stop_ops=frozenset(), stop_words=frozenset({"elif", "else", "fi"}))
            node.branches.append((condition, body))
        if self._reserved(self.peek(), "else"):
            self.next()
            node.orelse = self._nested(stop_ops=frozenset(), stop_words=frozenset({"fi"}))
        self._expect("fi")
        return node

    def _for(self) -> Loop:
        self.next()
        token = self.peek()
        if token[0] == "arith":
            self.next()
            assert isinstance(token[1], Arith)
            if self._op(self.peek(), ";"):
                self.next()
            return Loop(arith=token[1], body=self._do_group())
        name = self.next()
        if name[0] != "word":
            raise BashSyntaxError("for needs a name")
        self._newlines()
        items: list[Word] | None = None
        if self._reserved(self.peek(), "in"):
            self.next()
            items = []
            while self.peek()[0] == "word":
                word = self.next()[1]
                assert isinstance(word, Word)
                items.append(word)
            if not self._op(self.peek(), ";", "\n"):
                raise BashSyntaxError(f"unexpected {_describe(self.peek())} in a for loop")
            self.next()
        elif self._op(self.peek(), ";"):
            self.next()
        assert isinstance(name[1], Word)
        return Loop(var=name[1].text, items=items, body=self._do_group())

    def _case(self) -> Case:
        self.next()
        subject = self.next()
        if subject[0] != "word":
            raise BashSyntaxError("case needs a word")
        assert isinstance(subject[1], Word)
        node = Case(subject=subject[1])
        self._newlines()
        self._expect("in")
        self._newlines()
        while not self._reserved(self.peek(), "esac"):
            if self.peek()[0] == "eof":
                raise BashSyntaxError("case without esac")
            if self._op(self.peek(), "("):
                self.next()
            patterns: list[Word] = []
            while True:
                token = self.next()
                if token[0] != "word":
                    raise BashSyntaxError(f"unexpected {_describe(token)} in a case pattern")
                assert isinstance(token[1], Word)
                patterns.append(token[1])
                if self._op(self.peek(), "|"):
                    self.next()
                    continue
                break
            self._expect_op(")")
            body = self._nested(stop_ops=frozenset(), stop_words=frozenset({"esac"}))
            node.items.append((patterns, body))
            if self._op(self.peek(), ";;", ";&", ";;&"):
                self.next()
            self._newlines()
        self.next()
        return node

    def _cond(self) -> Cond:
        self.next()
        node = Cond()
        while True:
            kind, value, _ = self.next()
            if kind == "eof":
                raise BashSyntaxError("[[ without ]]")
            if kind == "heredoc":
                raise BashSyntaxError("a here-document inside [[ ]]")
            if kind == "arith":
                assert isinstance(value, Arith)
                node.words.append(Word(subs=list(value.subs)))
                continue
            if kind == "word":
                assert isinstance(value, Word)
                if value.plain and value.text == "]]":
                    return node
                node.words.append(value)

    def _fd_before_redirect(self) -> bool:
        """Whether the next token is a file descriptor's number or {name} written against a redirection, as in 2>&1."""
        kind, value, _ = self.peek()
        if kind != "word" or not isinstance(value, Word):
            return False
        if not (value.raw.isdigit() or _FD_NAME.fullmatch(value.raw)):
            return False
        after_kind, after, pos = self.peek(1)
        return pos == value.end and (after_kind == "heredoc" or (after_kind == "op" and after in _REDIRECTS))

    def _redirects(self, node: Redirected) -> None:
        while True:
            if self._fd_before_redirect():
                self.next()  # the redirection's file descriptor, as after a compound command: ( … ) 2>&1
                continue
            kind, value, _ = self.peek()
            if kind == "heredoc":
                self.next()
                assert isinstance(value, Heredoc)
                node.heredocs.append(value)
            elif kind == "op" and value in _REDIRECTS:
                self.next()
                target = self.next()
                if target[0] != "word":
                    raise BashSyntaxError(f"{value!r} needs a word after it")
                assert isinstance(target[1], Word)
                if value == "<<<":
                    node.herestrings.append(target[1])
                else:
                    node.redirects.append((str(value), target[1]))
            else:
                return

    def _simple(self) -> Simple:
        node = Simple()
        while True:
            kind, value, pos = self.peek()
            if kind == "word":
                self.next()
                assert isinstance(value, Word)
                node.words.append(value)
            elif kind == "heredoc" or (kind == "op" and value in _REDIRECTS):
                if node.words and node.words[-1].end == pos:
                    fd = node.words[-1].raw
                    if fd.isdigit() or _FD_NAME.fullmatch(fd):
                        node.words.pop()  # a file descriptor, as in 2>file or {fd}>file
                self._redirects(node)
            else:
                break
        if not (node.words or node.redirects or node.heredocs or node.herestrings):
            raise BashSyntaxError(f"expected a command, found {_describe(self.peek())}")
        return node
