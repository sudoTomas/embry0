"""The values a command's variables can hold, for embry0.safety.bash_paths.

A parameter expansion (``$X``, ``${X:-word}``, ``~``) names a path only through
the value it expands to. This module keeps, for each variable, every literal
value the command assigns it, anywhere and in any order: ``X=…``, ``export``,
``declare``, ``local``, ``readonly``, ``typeset``, ``printf -v``, an array's
elements, a ``for`` loop's items, ``${X:=word}``, and the positional
parameters (``set --``, a function's or a shell's arguments). The scanner
reads the command once per round with the values the round before found, until
they stop changing, so a value assigned after a use, or in a loop, is seen too.

An expansion then stands for each value its variable can hold, and for the
value it held before the command (text only known at run time), and, for
``${X:-word}`` and the like, for its word. ``HOME`` holds the user's home, ``PWD``
the directory the shell is in, and ``OLDPWD`` one that is not known; when a
command gives one of them a value only known at run time, a path built on it
can be anywhere.

- A list (``"$@"``, ``"${a[@]}"``, ``$*``) stands for each list of elements
  assigned whole, in order; ``$1``, ``${a[0]}`` and ``$a`` for any element.
  An element assigned alone (``a[1]=x``, ``unset 'a[0]'``, ``a=x`` on an
  array) leaves the order unknown: the list is folded (below).
- ``${!X}`` stands for the variables X's values name, and a nameref
  (``declare -n R=X``) for the ones it names; assigning it assigns them.
- A transformation of a literal value (``${X#p}``, ``${X/p/s}``, ``${X^^}``,
  ``${X@E}``, ``${X:1:2}``, …) is computed (embry0.safety.shell_transforms).
  Of a value only known at run time, or a glob, it is not known.

An unquoted expansion's value is split into words at the blanks, and, when
the command gives ``IFS`` a value, at that value's characters too: each value
``IFS`` can hold is one way to split it. A variable given more values than
are kept (``MAX_VALUES``) holds one wider value instead, which is enough for a
path but not for a command: a command word or a command string built on one
is refused (``BashSyntaxError``), and so is one that can expand to more than
``MAX_WORDS`` words.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Final

from embry0.safety import shell_transforms as transforms
from embry0.safety.shell_syntax import ACT, CWD, EXT, LIT, OLD, RUN, WILD, BashSyntaxError, Expansion, Word
from embry0.safety.shell_work import Exhausted, Work

# A value is its characters, each with its kind: LIT (data), RUN (text only
# known at run time), CWD or OLD (the current or the previous directory).
Value = tuple[tuple[str, int], ...]
Words = list[tuple[list[str], list[int]]]
# A word as it expands: its characters, their kinds, and which came from an unquoted expansion.
_Marked = tuple[list[str], list[int], list[bool]]

UNKNOWN: Final[Value] = ((WILD, RUN),)
# A path that can be anywhere: an absolute path whose rest is not known.
ANYWHERE: Final[Value] = (("/", LIT), (WILD, RUN))
# Between the elements of a list value: every expansion of it splits there.
SEP: Final[str] = "\x1f"
MAX_VALUES: Final[int] = 16
MAX_WORDS: Final[int] = 64
MAX_TEXT: Final[int] = 4096  # characters of one value or word; past it, it can be anywhere
_MAX_DEPTH: Final[int] = 8  # expansions inside expansions' words, read before giving up
_MAX_NAMES: Final[int] = 32  # variables one nameref can stand for
_DEEP: Final[str] = "\x00deep"  # in a list of names: and more, not read
_SPECIAL: Final[frozenset[str]] = frozenset({"HOME", "PWD", "OLDPWD"})
_BLANKS: Final[frozenset[str]] = frozenset(" \t\n")
_DEFAULTS: Final[frozenset[str]] = frozenset({":-", "-", ":=", "=", ":+", "+", ":?", "?"})
_TRANSFORMS: Final[frozenset[str]] = frozenset(
    {
        "#",
        "##",
        "%",
        "%%",
        "/",
        "//",
        "/#",
        "/%",
        "^",
        "^^",
        ",",
        ",,",
        "~",
        "~~",
        "@U",
        "@u",
        "@L",
        "@Q",
        "@E",
        "substr",
    }
)
_GLOB: Final[frozenset[str]] = frozenset("*?[")
_REF: Final[str] = "\x00ref"  # the key of the names a nameref names
_APPENDED: Final[str] = "\x00appended"  # the key of the values an append made
_CASE: Final[str] = "\x00case"  # the key of the case attributes (declare -l, -u, -c) a variable has
_TARGET: Final[re.Pattern[str]] = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[@*])(?:\[(?P<sub>[^]]*)\])?")
_TOO_MANY: Final[str] = "a command word or command string can hold more values than embry0 checks"
_LIMIT: Final[object] = object()  # a pattern not read: the transformation's result can be anywhere


def element_key(name: str) -> str:
    """Where an element's values are kept: ``$1``, ``$2``, … and ``$@`` share ``@``."""
    return "@" if name in ("@", "*") or (name.isdigit() and name != "0") else name


def list_key(name: str) -> str:
    """Where a list's values are kept: ``${a[@]}`` in ``a[@]``, the positional parameters in ``@[@]``."""
    return element_key(name) + "[@]"


class Variables:
    """The values each variable can hold: ``known`` from the round before, ``assigned`` in this one.

    ``folded`` names the variables (and lists) whose values the round before
    folded into a wider one, or whose order it lost: what they hold is no
    longer listed.
    """

    def __init__(
        self,
        home: str,
        known: Mapping[str, frozenset[Value]],
        folded: frozenset[str] = frozenset(),
        work: Work | None = None,
    ) -> None:
        self.home: Value = tuple((c, LIT) for c in home)
        self.known = known
        self.folded = folded
        self.work = work if work is not None else Work()
        self.assigned: dict[str, set[Value]] = {}
        self.folding: set[str] = set()  # the variables and lists collected() folds
        self._placed: dict[tuple[Value, int], Value] = {}
        self._scalars: set[str] = set()  # names given a value whole (X=…), which on an array is its element 0

    # -- assigning -------------------------------------------------------------------

    def assign(self, name: str, values: Iterable[Value], *, append: bool = False) -> None:
        """``name`` (and what a nameref ``name`` names) can hold each of ``values`` (``append``: after its own)."""
        values = list(values)
        if any(len(v) > MAX_TEXT for v in values):
            # A value longer than is kept: it can be anywhere, and is not run.
            values = [ANYWHERE if len(v) > MAX_TEXT else v for v in values]
            self.folding.update(t for t in self.names(name) if t != _DEEP)
        for target in self.names(name):
            if target == _DEEP:
                continue
            for ((letter, _),) in self.known.get(target + _CASE, ()):
                values = [*values, *(_cased(letter, v) for v in values)]
            found = self.assigned.setdefault(target, set())
            if append:
                # After each value it can hold. A value an append made may be appended to again (in a
                # loop): that one is widened, so the values stop growing.
                appended = self.known.get(target + _APPENDED, frozenset())
                made = {
                    _widened(old, new) if old in appended else old + new
                    for old in [*self.values(target), UNKNOWN]
                    for new in values
                }
                found.update(made)
                self.assigned.setdefault(target + _APPENDED, set()).update(made)
            else:
                found.update(values)

    def assign_word(self, name: str, word: Word, *, append: bool = False) -> None:
        """``name=word``: every value the word can expand to, unsplit."""
        values = [_value(c, k) for c, k in self.expand(word.chars, word.kinds)]
        if name + _REF in self.known:
            self.nameref(name, values)  # a nameref's first value names its variable
        self.assign_scalar(name, values, append=append)

    def assign_scalar(self, name: str, values: Iterable[Value], *, append: bool = False) -> None:
        """A value given whole (``X=…``, ``printf -v``, a ``for`` loop's item): on an array, its element 0."""
        self._scalars.add(element_key(name))
        self.assign(name, values, append=append)

    def assign_list(self, name: str, lists: Iterable[Value], *, append: bool = False) -> None:
        """``name`` holds each of ``lists`` (elements between ``SEP``) as a whole (``append``: after its own)."""
        lists = list(lists)
        made = lists
        if append:
            # After each list it can hold, or alone (an array holds none before the command). A list an
            # append made appended to again (in a loop) has no known length: its order is lost.
            before: list[Value] = [()]
            appended = self.known.get(list_key(name) + _APPENDED, frozenset())
            for old in self.known.get(list_key(name), ()):
                if old in appended:
                    self.fold(name)
                else:
                    before.append(old)
            made = [old + ((SEP, LIT),) + new if old and new else old + new for old in before for new in lists]
        for target in self.names(name):
            if target != _DEEP:
                self.assigned.setdefault(list_key(target), set()).update(made)
                if append:
                    self.assigned.setdefault(list_key(target) + _APPENDED, set()).update(made)

    def case(self, name: str, letters: str) -> None:
        """``declare -l``, ``-u`` or ``-c``: what ``name`` is given is lowered, raised or capitalized."""
        self.assigned.setdefault(element_key(name) + _CASE, set()).update(((c, LIT),) for c in letters)

    def fold(self, name: str) -> None:
        """``name``'s list is no longer known in order (an element assigned or removed alone)."""
        self.folding.update(list_key(target) for target in self.names(name) if target != _DEEP)

    def shifted(self, name: str) -> None:
        """``shift``: the list can be any of its ends."""
        for value in self.known.get(list_key(name), ()):
            text = "".join(c for c, _ in value)
            starts = [i + 1 for i, c in enumerate(text) if c == SEP]
            self.assign_list(name, [value[i:] for i in starts] + [()])

    def nameref(self, name: str, targets: Iterable[Value]) -> None:
        """``declare -n name=target``: ``name`` stands for the variable ``target`` names."""
        self.assigned.setdefault(name + _REF, set()).update(targets)

    def collected(self) -> dict[str, frozenset[Value]]:
        """The values known for the next round: the known ones and the assigned ones, capped.

        The names of those folded into one wider value are left in ``folding``,
        and so is the list of a name also given a value whole. A variable folded
        into one wider value stays folded: what it is given later widens that value.
        """
        out: dict[str, frozenset[Value]] = {}
        for name in set(self.known) | set(self.assigned):
            values = set(self.known.get(name, ())) | self.assigned.get(name, set())
            sticky = name in self.folded and "\x00" not in name and not name.endswith("[@]")
            if len(values) > MAX_VALUES or (sticky and len(values) > 1):
                out[name] = frozenset({generalized(values)})
                self.folding.add(name)
            else:
                out[name] = frozenset(values)
        for name in self._scalars:
            if out.get(list_key(name)):
                self.folding.add(list_key(name))
        return out

    def names(self, name: str) -> list[str]:
        """``name``, and the variables it names if it is a nameref (and those they name). Past
        ``_MAX_NAMES`` the list ends in ``_DEEP``: a value that can be anywhere."""
        out = [element_key(name)]
        for key in out:
            for target in self.known.get(key + _REF, ()):
                text = "".join(c for c, k in target if k in (LIT, ACT))
                m = _TARGET.fullmatch(text)
                if m is None or len(text) != len(target):
                    continue
                found = element_key(m.group("name"))
                if found not in out:
                    if len(out) >= _MAX_NAMES:
                        return [*out, _DEEP]
                    out.append(found)
        return out

    def values(self, name: str | None) -> list[Value]:
        """What ``name`` can hold, besides the value it held before the command."""
        if name is None:
            return []
        values: list[Value] = []
        for key in self.names(name):
            values.extend(v for v in (self.known.get(key, ()) if key != _DEEP else (ANYWHERE,)) if v not in values)
        if name in _SPECIAL:
            # A value only known at run time makes a path built on these anywhere.
            values = [ANYWHERE if value[:1] == UNKNOWN else value for value in values]
        return values

    def lists(self, name: str) -> list[Value]:
        """What the list ``name`` can hold: each list assigned whole, or, if its order is not known, each element."""
        found: list[Value] = []
        for key in self.names(name):
            if key == _DEEP:
                found.append(ANYWHERE)
                continue
            lists = self.known.get(list_key(key), frozenset())
            if not lists or list_key(key) in self.folded:
                found.extend(self.known.get(key, ()))
            found.extend(lists)
        return list(dict.fromkeys(found))

    def before(self, name: str | None) -> Value:
        """The value ``name`` held before the command."""
        if name == "HOME":
            return self.home
        if name == "PWD":
            return ((WILD, CWD),)
        if name == "OLDPWD":
            return ((WILD, OLD),)
        return UNKNOWN

    # -- expanding ---------------------------------------------------------------------

    def expand(self, chars: list[str], kinds: list[int], *, depth: int = 0, code: bool = False) -> Words:
        """The words ``chars`` can expand to, each expansion replaced by each value it can have (unsplit).

        ``code``: the word is run as a command or as a command string, so no
        value may be left out: a folded variable or too many words refuse it.
        """
        return [(c, k) for c, k, _ in self._expand(chars, kinds, depth=depth, code=code)]

    def expand_split(self, chars: list[str], kinds: list[int], *, code: bool = False) -> list[Words]:
        """Each way the word can expand, as the words a list, or an unquoted expansion's blanks or ``IFS``,
        split it into."""
        ways: list[Words] = []
        expanded = self._expand(chars, kinds, code=code)
        for separators in self.separators(expanded):
            for c, k, m in expanded:
                split = _split(c, k, m, separators)
                if split not in ways:
                    ways.append(split)
        if code and len(ways) > MAX_WORDS:
            raise BashSyntaxError(_TOO_MANY)
        return ways

    def separators(self, expanded: list[_Marked]) -> list[frozenset[str]]:
        """The sets of characters splitting ``expanded`` may split at: the blanks, and each value ``IFS``
        can hold (only where an unquoted expansion put text)."""
        if not any(m for _, _, marks in expanded for m in marks):
            return [_BLANKS]
        return self.ifs()

    def ifs(self) -> list[frozenset[str]]:
        """The sets of characters word splitting may split at: the blanks, and each value ``IFS`` can hold.

        A value ``IFS`` only gets at run time is not known, and not listed.
        """
        if "IFS" in self.folded:
            raise BashSyntaxError("the command gives IFS more values than embry0 checks")
        out = [_BLANKS]
        for value in self.values("IFS"):
            if any(k not in (LIT, ACT) for _, k in value):
                continue
            separators = frozenset(c for c, _ in value)
            if separators not in out:
                out.append(separators)
        return out

    def _expand(self, chars: list[str], kinds: list[int], *, depth: int = 0, code: bool = False) -> list[_Marked]:
        """``expand``, each character marked when an unquoted expansion put it there. A word longer than
        ``MAX_TEXT`` can be anywhere (in a command word, it is refused)."""
        results: list[_Marked] = [([], [], [])]
        whole: list[bool] = [False]  # the result is ANYWHERE, past MAX_TEXT: nothing more is added
        for c, k in zip(chars, kinds):
            if isinstance(c, Expansion):
                fragments = self.fragments(c, depth, code=code)
                split = not c.quoted
            else:
                fragments = [((c, k),)]
                split = False
            grown: list[_Marked] = []
            done: list[bool] = []
            for (rc, rk, rm), full in zip(results, whole):
                if full:
                    grown.append((rc, rk, rm))
                    done.append(True)
                    continue
                for frag in fragments:
                    if len(rc) + len(frag) > MAX_TEXT:
                        if code:
                            raise BashSyntaxError(_TOO_MANY)
                        chars_, kinds_ = _word(ANYWHERE)
                        grown.append((chars_, kinds_, [False] * len(chars_)))
                        done.append(True)
                        continue
                    grown.append((rc + [f[0] for f in frag], rk + [f[1] for f in frag], rm + [split] * len(frag)))
                    done.append(False)
            results, whole = grown, done
            self.work.spend("words", len(results))
            if len(results) > MAX_WORDS:
                if code:
                    raise BashSyntaxError(_TOO_MANY)
                chars_, kinds_ = _word(ANYWHERE)
                return [(chars_, kinds_, [False] * len(chars_))]
        return results

    def fragments(self, expansion: Expansion, depth: int, *, code: bool = False) -> list[Value]:
        """The values one expansion can stand for, with the kinds they take in the word."""
        name, op = expansion.name, expansion.op
        data = LIT if expansion.quoted else ACT

        def placed(value: Value) -> Value:
            key = (value, data)
            found = self._placed.get(key)
            if found is None:
                found = self._placed[key] = tuple((c, data if k == LIT else k) for c, k in value)
            return found

        if name is None or (op not in _DEFAULTS and op not in _TRANSFORMS and op):
            # ${#X}, ${X@P}, a process substitution, …: not known; on HOME or PWD, anywhere.
            return [ANYWHERE] if name in _SPECIAL else [UNKNOWN]
        if expansion.indirect:
            return self.indirect(expansion, depth, code=code)
        keys = [list_key(n) if expansion.whole else n for n in self.names(name)]
        if code and any(key in self.folded or key == _DEEP for key in keys):
            raise BashSyntaxError(_TOO_MANY)
        values = [self.before(name), *(self.lists(name) if expansion.whole else self.values(name))]
        if op in _TRANSFORMS:
            out: list[Value] = []
            if any(key in self.folded or key == _DEEP for key in keys):
                out.append(ANYWHERE)  # values folded into a wider one: what is made of them can be anywhere
            for value in values:
                out.extend(v for v in self.transformed(value, expansion, depth, code=code) if v not in out)
            return [placed(v) for v in out]
        value_of = [placed(v) for v in values]
        if not op:
            return value_of
        word: list[Value] = []
        if expansion.word is not None and depth >= _MAX_DEPTH:
            # Words nested deeper than are read: the word can be anything.
            if code:
                raise BashSyntaxError(_TOO_MANY)
            word.append(ANYWHERE)
        elif expansion.word is not None:
            for chars, kinds in self.expand(expansion.word.chars, expansion.word.kinds, depth=depth + 1, code=code):
                word.append(tuple((c, LIT if expansion.quoted and k == ACT else k) for c, k in zip(chars, kinds)))
        if op in (":+", "+"):
            return [*word, ()]
        # :- - := = :? ?: the value, or the word (the word can be the value whichever it is).
        return [*value_of, *word]

    def indirect(self, expansion: Expansion, depth: int, *, code: bool) -> list[Value]:
        """``${!X…}``: the expansion of each variable X's values name (and of one only known at run time)."""
        assert expansion.name is not None
        # The variable X named before the command is not known: the expansion of one that holds nothing known.
        unnamed = Expansion(
            "", expansion.op, expansion.word, quoted=expansion.quoted, repl=expansion.repl, arith=expansion.arith
        )
        if depth >= _MAX_DEPTH:
            if code:
                raise BashSyntaxError(_TOO_MANY)
            return [ANYWHERE]
        out: list[Value] = self.fragments(unnamed, depth + 1, code=code)
        if code and element_key(expansion.name) in self.folded:
            raise BashSyntaxError(_TOO_MANY)
        for value in self.values(expansion.name):
            text = "".join(c for c, k in value if k in (LIT, ACT))
            m = _TARGET.fullmatch(text)
            if m is None or len(text) != len(value):
                continue  # not a name: the same as one not known, above
            sub = m.group("sub")
            target = Expansion(
                m.group("name"),
                expansion.op,
                expansion.word,
                quoted=expansion.quoted,
                whole=m.group("name") in ("@", "*") or sub in ("@", "*"),
                repl=expansion.repl,
                arith=expansion.arith,
            )
            out.extend(v for v in self.fragments(target, depth + 1, code=code) if v not in out)
        return out

    def transformed(self, value: Value, expansion: Expansion, depth: int, *, code: bool) -> list[Value]:
        """``value`` as a transformation turns it: each list element alone, every pattern the words can be."""
        op = expansion.op
        if op == "substr" and expansion.whole:
            # A slice of a list: whole elements, whatever they hold.
            parts = _elements(value)
            positional = element_key(expansion.name or "") == "@"
            picked = transforms.sliced([str(i) for i in range(len(parts))], expansion.arith, positional=positional)
            if picked is None:
                return [UNKNOWN]
            joined: Value = ()
            for n, i in enumerate(picked):
                joined = joined + ((SEP, LIT),) + parts[int(i)] if n else parts[int(i)]
            return [joined]
        unknown = [ANYWHERE] if expansion.name in _SPECIAL or value == ANYWHERE else [UNKNOWN]
        if any(k in (CWD, OLD) for _, k in value):
            return [ANYWHERE]  # the shell's directory, cut or replaced: it can be anywhere
        if any(k == RUN or (k == ACT and c in _GLOB) for c, k in value):
            return unknown  # text only known at run time, or the files a glob matches (anywhere: anywhere)
        text = "".join(c for c, _ in value)
        elements = text.split(SEP) if text or not expansion.whole else []
        if op == "substr":
            cut = [transforms.substring(e, expansion.arith) for e in elements]
            return unknown if None in cut else [_listed([t for t in cut if t is not None])]
        if op in ("@U", "@u", "@L", "@Q", "@E"):
            return [_listed([transforms.at_transformed(op, e) for e in elements])]
        try:
            patterns = self.patterns(expansion.word, depth, anchored=op == "/", code=code)
            replacements = self.replacements(expansion.repl, depth, code=code) if op.startswith("/") else [[]]
        except Exhausted as exc:
            if exc.kind != "transform":
                raise
            if code:
                raise BashSyntaxError(_TOO_MANY) from None
            return [ANYWHERE]
        out: list[Value] = []
        for anchor, glob in patterns:
            for replacement in replacements:
                if glob is None or replacement is None:
                    out.extend(v for v in unknown if v not in out)
                    continue
                try:
                    if glob is _LIMIT:
                        raise Exhausted("transform")
                    texts = [self.one(op + anchor, e, glob, replacement) for e in elements]
                except Exhausted as exc:
                    # A pattern or a text past what is computed: the result can be anywhere.
                    if exc.kind != "transform":
                        raise
                    if code:
                        raise BashSyntaxError(_TOO_MANY) from None
                    if ANYWHERE not in out:
                        out.append(ANYWHERE)
                    continue
                listed = _listed(texts)
                if listed not in out:
                    out.append(listed)
        return out

    def one(
        self, op: str, text: str, glob: transforms.Glob | bool | object, replacement: transforms.Replacement
    ) -> str:
        """One element transformed (``glob`` True: no pattern given)."""
        if glob is True:
            return transforms.case_changed(op, text, None) if op[0] in "^,~" else text
        assert isinstance(glob, transforms.Glob)
        if op in ("#", "##", "%", "%%"):
            return transforms.removed(op, text, glob, self.work)
        if op.startswith("/"):
            return transforms.replaced(op, text, glob, replacement, self.work)
        return transforms.case_changed(op, text, glob)

    def patterns(
        self, word: Word | None, depth: int, *, anchored: bool, code: bool
    ) -> list[tuple[str, transforms.Glob | bool | object | None]]:
        """The patterns a transformation's word can be, each with its anchor (``anchored``: an unquoted
        leading ``#`` or ``%``): True for none, None for one only known at run time, ``_LIMIT`` for one
        not read.

        An extglob (``@(…)``) is not read: with the option off it is literal
        text, with it on a pattern, so the result can be anywhere. Nor is a
        pattern of more than ``MAX_PATTERN`` elements.
        """
        if word is None or not word.chars:
            return [("", True)]
        if depth >= _MAX_DEPTH:
            raise Exhausted("transform")
        out: list[tuple[str, transforms.Glob | bool | object | None]] = []
        for chars, kinds in self.expand(word.chars, word.kinds, depth=depth + 1, code=code):
            anchor = ""
            if anchored and chars and kinds[0] == ACT and chars[0] in "#%":
                anchor, chars, kinds = chars[0], chars[1:], kinds[1:]
            if not chars and not anchor:
                out.append(("", True))  # an empty pattern matches nothing
                continue
            if EXT in kinds:
                out.append(("", _LIMIT))
                continue
            try:
                out.append((anchor, transforms.pattern_glob(chars, kinds)))
            except Exhausted as exc:
                if exc.kind != "transform":
                    raise
                out.append(("", _LIMIT))
        return out

    def replacements(self, word: Word | None, depth: int, *, code: bool) -> list[transforms.Replacement | None]:
        """The texts a replacement can be (None: only known at run time); an unquoted ``&`` may be the
        match (bash 5.2) or itself."""
        if word is None or not word.chars:
            return [[]]
        if depth >= _MAX_DEPTH:
            raise Exhausted("transform")
        out: list[transforms.Replacement | None] = []
        for chars, kinds in self.expand(word.chars, word.kinds, depth=depth + 1, code=code):
            if any(k not in (LIT, ACT) for k in kinds):
                out.append(None)
                continue
            out.append(["".join(chars)])
            if any(c == "&" and k == ACT for c, k in zip(chars, kinds)):
                out.append([transforms.MATCH if c == "&" and k == ACT else c for c, k in zip(chars, kinds)])
        return out

    def defaults_assigned(self, word: Word) -> None:
        """``${X:=word}`` anywhere in ``word`` assigns X its word."""
        for c in word.chars:
            if isinstance(c, Expansion) and c.op in (":=", "=") and c.name and c.word is not None:
                if not c.indirect:
                    self.assign_word(c.name, c.word)
                self.defaults_assigned(c.word)


def _cased(letter: str, value: Value) -> Value:
    """A value as a case attribute changes it: ``l`` lowers it, ``u`` raises it, ``c`` capitalizes it."""
    change = {"l": str.lower, "u": str.upper, "c": str.capitalize}[letter]
    text = change("".join(c if k in (LIT, ACT) else "\x00" for c, k in value))
    return tuple((t if k in (LIT, ACT) else c, k) for t, (c, k) in zip(text, value))


def _widened(old: Value, new: Value) -> Value:
    """``old`` (made by an append) with ``new`` appended, as one value that stands for any number of
    appends: ``old`` then anything (anywhere, if that can climb with ``..``)."""
    if old[-1:] == UNKNOWN:
        return old
    if old[:1] == UNKNOWN:
        return old + UNKNOWN
    return generalized({old + new})


def _elements(value: Value) -> list[Value]:
    """A list value's elements (an empty value: none)."""
    if not value:
        return []
    parts: list[Value] = [()]
    for c, k in value:
        if c == SEP:
            parts.append(())
        else:
            parts[-1] = parts[-1] + ((c, k),)
    return parts


def _listed(texts: list[str]) -> Value:
    """Elements as one value: their text, between ``SEP``."""
    return tuple((c, LIT) for c in SEP.join(texts))


def _same_text(a: tuple[str, int], b: tuple[str, int]) -> bool:
    """Whether two characters of a value read alike: the same character, quoted or not, but for a glob's."""
    return a == b or (a[0] == b[0] and a[0] not in "*?[" and {a[1], b[1]} <= {LIT, ACT})


def generalized(values: set[Value]) -> Value:
    """One value that stands for all of ``values``: their common start, then text only known at run time.

    Sound only when no value climbs with ``..`` or holds a directory or run-time
    text (but at its end, as an earlier round's generalized value does);
    otherwise it can be anywhere. A relative common start stays relative
    (``./start…``), so it is resolved against the directory, not read as unknown.
    """
    starts: list[Value] = []
    absolute = relative = False
    for value in values:
        open_ = value[-1:] == UNKNOWN
        body = value[:-1] if open_ else value
        text = "".join(c for c, _ in body)
        if any(k not in (LIT, ACT) for _, k in body) or ".." in text.split("/"):
            return ANYWHERE
        if text.startswith("/"):
            absolute = True
        else:
            relative = True
            if text.startswith("./"):
                body = body[2:]
        starts.append(body)
    if absolute and relative:
        return ANYWHERE
    common = 0
    shortest = min(starts, key=len)
    while common < len(shortest) and all(_same_text(v[common], shortest[common]) for v in starts):
        common += 1
    start = shortest[:common]
    return (start if absolute else (((".", LIT), ("/", LIT)) + start)) + UNKNOWN


def _value(chars: list[str], kinds: list[int]) -> Value:
    return tuple((c, k if k in (RUN, CWD, OLD) else LIT) for c, k in zip(chars, kinds))


def _word(value: Value) -> tuple[list[str], list[int]]:
    return [c for c, _ in value], [k for _, k in value]


def _split(chars: list[str], kinds: list[int], marks: list[bool], separators: frozenset[str]) -> Words:
    """Split a word between a list's elements, and at the separators an unquoted expansion put in it,
    as bash does (empty words left out)."""
    words: Words = []
    current: tuple[list[str], list[int]] = ([], [])
    for c, k, m in zip(chars, kinds, marks):
        if c == SEP or (m and k == ACT and c in separators):
            if current[0]:
                words.append(current)
            current = ([], [])
            continue
        current[0].append(c)
        current[1].append(k)
    if current[0]:
        words.append(current)
    return words
