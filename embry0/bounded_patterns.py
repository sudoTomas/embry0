"""Path patterns matched in bounded time: a small automaton the glob translators build instead of a regex.

A glob translated to a regular expression (``[^/]*`` for ``*``, ``.*`` for
``**``, ``(?:[^/]+/)*`` for ``**/``) can backtrack: a pattern of several
such parts against a long path that nearly matches tries every way to split
the path between them, and a dozen of them take seconds or hours. The glob
translators here build the same pattern as a tree of parts instead, and
:class:`Pattern` matches it as a Thompson automaton: a set of positions in
the pattern, moved on one character at a time, so the cost is at most the
pattern's size times the text's, whatever it holds.

The parts are the ones the translators need: a literal character, one
character but ``/`` (``[^/]``), one character but a newline (``.``, which a
regex without ``DOTALL`` reads so), and sequences of parts that repeat any
number of times (``*``), at least once (``+``) or may be left out (``?``).
A pattern matches the whole text, as a regex anchored with ``\\A`` and
``\\Z``.

**When Python's regex is used instead.** Most patterns cannot backtrack
much, and Python's regex matches them many times faster. :func:`_backtracks`
decides it from the automaton, with the measures of ambiguity that bound a
backtracking matcher's work: a pattern goes to the regex only when no state
has two different loops on one word (exponential ambiguity), no two states
loop on one word with a way from the first to the second on it (polynomial
ambiguity: two ``*`` that can take the same characters, as ``*a*b`` or
``**/*/**/*/``), and it holds at most :data:`_MAX_BRANCHES` parts that
repeat or may be left out, so the finite number of ways it can split a text
stays small too. A part that matches every rest of a text (a trailing
``**``) ends every try at once, so it counts for nothing; such a pattern is
then matched by the regex only on a text without a newline, where that holds.

**RE2, for most of the rest.** A pattern that can backtrack goes to RE2
(``google-re2``) when its DFA is small: :meth:`Pattern._small_dfa` builds
every set of states the pattern can be in, one character of each kind at a
time, and RE2 takes the pattern when there are at most :data:`_DFA_STATES`
of them. RE2 then reads a text in one pass of a DFA it holds, in C, in at
most :data:`_RE2_MAX_MEM` per pattern. The pattern is written out for RE2
with every character but an ASCII letter or digit as its code point, so no
escape reads differently, and RE2 matches the whole text as Python's
``fullmatch`` does. A text RE2 cannot read (a lone surrogate, which UTF-8
cannot hold) goes to the automaton. A pattern whose DFA grows with the text,
as ``*a`` and sixty ``?`` before a ``*``, stays with the automaton: RE2 would
run out of memory on it and read on with its NFA, at 0.1 to 0.5 ms a
260-character text, beyond any budget a caller keeps; the automaton counts
its work (:meth:`Pattern.fullmatch_counted`), which a caller can budget.

**Memory.** The automaton remembers the sets of states it walked, and where
each character moves each set, so a path like ones it has seen is a lookup
per character. A character is remembered by its kind: a literal of the
pattern, a slash, a newline, or any other, since no test tells those apart;
so what a pattern remembers depends on the pattern, not on how many
characters its texts hold. What it remembers is bounded by size, per
pattern (:data:`_PATTERN_BUDGET`) and across every pattern
(:class:`Memory`, :data:`_BUDGET`): past either, a pattern forgets what it
remembered, and one that keeps outgrowing its share walks without
remembering, in time bounded as above and memory of its own size. A run of
matches, as a scoring pass, may match with copies of its own that remember
within a memory of that run's (:meth:`Pattern.run_copy`), and count the
work they do (:meth:`Pattern.fullmatch_counted`).

This module imports nothing of embry0 (the standard library and
google-re2 only), so the settings sections, which import before most of
embry0, may use it.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Final

try:
    # google-re2's binding, which its wrapper drives: a match through it is one call, without the wrapper's generator
    # and its bookkeeping of character offsets, which cost more than the match. Checked at import (_re2_works).
    from re2 import _re2 as _re2_binding
except ImportError:  # pragma: no cover - google-re2 is a dependency; without it the automaton matches
    _re2_binding = None

__all__ = [
    "ANY",
    "NOT_SLASH",
    "Memory",
    "Node",
    "Pattern",
    "any_re2_fullmatch",
    "either",
    "lit",
    "opt",
    "plus",
    "seq",
    "star",
]

#: Whether one character may stand where a part is.
Test = Callable[[str], bool]


@dataclass(frozen=True)
class _Char:
    test: Test
    regex: str
    #: The character, for a literal; None for a class.
    char: str | None = None


@dataclass(frozen=True)
class _Seq:
    parts: tuple[Node, ...]


@dataclass(frozen=True)
class _Repeat:
    part: Node
    least: int  # 0 or 1
    most_one: bool  # "?": at most once


@dataclass(frozen=True)
class _Either:
    parts: tuple[Node, ...]


Node = _Char | _Seq | _Repeat | _Either

#: One character but ``/``: ``[^/]``.
NOT_SLASH: Final[Node] = _Char(lambda c: c != "/", "[^/]")
#: One character but a newline: a regex's ``.`` without ``DOTALL``.
ANY: Final[Node] = _Char(lambda c: c != "\n", ".")


def _is(want: str) -> Test:
    return lambda c: c == want


def lit(text: str) -> Node:
    """``text``, character for character."""
    return _Seq(tuple(_Char(_is(ch), re.escape(ch), ch) for ch in text))


def seq(*parts: Node) -> Node:
    return _Seq(tuple(parts))


def star(*parts: Node) -> Node:
    """``parts`` any number of times, none included."""
    return _Repeat(seq(*parts), 0, False)


def plus(*parts: Node) -> Node:
    """``parts`` once or more."""
    return _Repeat(seq(*parts), 1, False)


def opt(*parts: Node) -> Node:
    """``parts`` once, or not at all."""
    return _Repeat(seq(*parts), 0, True)


def either(*parts: Node) -> Node:
    """One of ``parts``: a text matches when it matches any of them."""
    return _Either(tuple(parts))


def _regex(node: Node) -> str:
    if isinstance(node, _Char):
        return node.regex
    if isinstance(node, _Seq):
        return "".join(_regex(part) for part in node.parts)
    if isinstance(node, _Either):
        return "(?:" + "|".join(_regex(part) for part in node.parts) + ")"
    quantifier = "?" if node.most_one else "+" if node.least else "*"
    return f"(?:{_regex(node.part)}){quantifier}"


def _re2_source(node: Node) -> str:
    """``node`` as RE2 reads it: every character but an ASCII letter or digit written as its code point, so no
    escape means anything but the character, and ``.`` written as the class it is in Python's regex."""
    if isinstance(node, _Char):
        if node.char is None:
            return "[^\\n]" if node.regex == "." else node.regex  # "[^/]": RE2's, as Python's, takes a newline
        return node.char if node.char.isascii() and node.char.isalnum() else f"\\x{{{ord(node.char):X}}}"
    if isinstance(node, _Seq):
        return "".join(_re2_source(part) for part in node.parts) if node.parts else "(?:)"
    if isinstance(node, _Either):
        return "(?:" + "|".join(_re2_source(part) for part in node.parts) + ")"
    quantifier = "?" if node.most_one else "+" if node.least else "*"
    return f"(?:{_re2_source(node.part)}){quantifier}"


def _nodes(node: Node) -> Iterator[Node]:
    yield node
    if isinstance(node, _Seq | _Either):
        for part in node.parts:
            yield from _nodes(part)
    elif isinstance(node, _Repeat):
        yield from _nodes(node.part)


#: The most parts that repeat or may be left out a pattern may hold and still go to Python's regex: each can
#: double the ways a backtracking matcher tries to split a text.
_MAX_BRANCHES: Final = 6
#: The most character states the ambiguity check reads; a larger pattern is matched by the automaton.
_MAX_ANALYSED: Final = 40
#: The most product states the ambiguity check visits before it gives up and keeps the automaton.
_MAX_VISITS: Final = 200_000

#: The most sets of states a pattern's DFA may have, and the most work (states moved, :meth:`Pattern._small_dfa`)
#: finding them may take, for RE2 to match it: its DFA then fits RE2's memory, and RE2 reads each text in one pass
#: of it. A common pattern's has tens; one built to make a DFA grow has more than any bound.
_DFA_STATES: Final = 1_000
_DFA_WORK: Final = 100_000
#: What one RE2 program may hold (its DFA is built as it reads, so a small one holds little), and its largest size.
_RE2_MAX_MEM: Final = 2 << 20
_RE2_MAX_PROGRAM: Final = 10_000

#: What the automaton may remember, in states stored, across every pattern and per pattern.
_BUDGET: Final = 4_000_000
_PATTERN_BUDGET: Final = 250_000
#: How often a pattern may outgrow its share and start again before it walks without remembering.
_MAX_RESETS: Final = 3
#: What remembering one set of states, and one step, costs besides its states.
_SET_COST: Final = 16
_STEP_COST: Final = 4


class Memory:
    """What the patterns that share it may remember together, in states stored (:data:`_SET_COST`,
    :data:`_STEP_COST`)."""

    __slots__ = ("_lock", "limit", "spent")

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.spent = 0
        self._lock = threading.Lock()

    def take(self, cost: int) -> bool:
        """Take ``cost``; False, taking nothing, when it would pass :attr:`limit`."""
        with self._lock:
            if self.spent + cost > self.limit:
                return False
            self.spent += cost
            return True

    def give(self, cost: int) -> None:
        with self._lock:
            self.spent -= cost


#: The memory every pattern shares but the copies a run keeps of its own (:meth:`Pattern.run_copy`).
_SHARED: Final = Memory(_BUDGET)


def _re2_program(source: str) -> Any | None:
    """RE2's program for ``source``, or None when RE2 is missing or refuses it, or it is too large."""
    if _re2_binding is None:
        return None
    options = _re2_binding.RE2.Options()
    options.max_mem = _RE2_MAX_MEM
    options.never_capture = True
    options.log_errors = False
    try:
        program = _re2_binding.RE2(source.encode(), options)
    except Exception:  # noqa: BLE001 - whatever RE2 refuses, the automaton matches
        return None
    if not program.ok() or program.ProgramSize() > _RE2_MAX_PROGRAM:
        return None
    return program


def _re2_works() -> bool:
    """Whether the binding answers as this module reads it: a program, a match of a whole text, and none."""
    try:
        program = _re2_program("a[^/]\\x{E9}")
        if program is None:
            return False
        both = _re2_binding.RE2.Anchor.ANCHOR_BOTH
        text = "aµé".encode()
        found = program.Match(both, text, 0, len(text))
        missed = program.Match(both, b"a/\xc3\xa9", 0, 4)
        return bool(found[0] == (0, len(text)) and missed[0] == _RE2_NO_MATCH)
    except Exception:  # noqa: BLE001
        return False


#: What RE2's binding answers for a group that did not match.
_RE2_NO_MATCH: Final = (-1, -1)
if _re2_binding is not None and not _re2_works():  # pragma: no cover - a google-re2 whose binding changed
    _re2_binding = None
_RE2_BOTH: Final = _re2_binding.RE2.Anchor.ANCHOR_BOTH if _re2_binding is not None else None


def any_re2_fullmatch(programs: Sequence[Any], utf8: bytes) -> bool:
    """Whether one of ``programs`` (:attr:`Pattern.re2`) matches all of ``utf8``, a text encoded once for them all:
    a caller matching many patterns against one text saves an encoding and a call for each."""
    size = len(utf8)
    for program in programs:
        if program.Match(_RE2_BOTH, utf8, 0, size)[0] != _RE2_NO_MATCH:
            return True
    return False


class Pattern:
    """A pattern compiled to an automaton. :meth:`fullmatch` says whether all of a text matches it, in time
    bounded by the pattern's size times the text's length, and memory bounded by size (the module docstring).

    A pattern that cannot backtrack much (:meth:`_backtracks`) is matched by
    Python's regex, which is fastest; any other by RE2 when its DFA is small
    (:meth:`_small_dfa`) and RE2 takes it, and else by the automaton, as a DFA
    built as it is walked. ``prefer_regex=False`` leaves Python's regex out,
    ``use_re2=False`` RE2.
    """

    __slots__ = (
        "__weakref__",
        "_accept",
        "_chars",
        "_codes",
        "_cost",
        "_epsilon",
        "_ids",
        "_lock",
        "_memory",
        "_re2",
        "_regex",
        "_remember",
        "_resets",
        "_sets",
        "_start",
        "_start_states",
        "_step",
        "_tests",
        "_to",
        "_universal_tail",
    )

    def __init__(self, *parts: Node, prefer_regex: bool = True, use_re2: bool = True) -> None:
        tree = seq(*parts)
        # State i is a character state when _tests[i] is set (it moves to _to[i] on a character the test takes),
        # else an epsilon state, which moves to every state of _epsilon[i] without reading.
        self._tests: list[Test | None] = []
        self._chars: list[str | None] = []
        self._to: list[int] = []
        self._epsilon: list[list[int]] = []
        start, ends = self._build(tree)
        self._accept = self._state(None)
        self._patch(ends, self._accept)
        # What the memo keys a character by: a literal of the pattern, a slash and a newline each by its own code;
        # every other character by 0, since no test the pattern holds tells those apart.
        tellable = {c for c in self._chars if c is not None} | {"/", "\n"}
        self._codes = {c: code for code, c in enumerate(sorted(tellable), 1)}
        self._start_states = self._closure({start})
        self._start_remembering(_SHARED)
        self._universal_tail = False
        backtracks = self._backtracks(tree)
        self._regex = re.compile(_regex(tree)) if prefer_regex and not backtracks else None
        self._re2 = _re2_program(_re2_source(tree)) if self._regex is None and use_re2 and self._small_dfa() else None

    def __del__(self) -> None:
        # A pattern let go gives its share of the budget back (at shutdown the module may be gone already).
        try:
            if self._cost:
                self._memory.give(self._cost)
        except Exception:  # noqa: BLE001, S110
            pass

    def run_copy(self, memory: Memory) -> Pattern:
        """This pattern for one run of matches (a scoring pass, say): the same automaton, remembering from
        nothing within ``memory``, which the run's copies share, instead of the memory every pattern shares.
        What a copy remembers then depends only on the texts the run gives it, in their order, so the work it
        counts (:meth:`fullmatch_counted`) does too."""
        copy = object.__new__(Pattern)
        for name in ("_accept", "_chars", "_codes", "_epsilon", "_re2", "_regex", "_start_states", "_tests", "_to"):
            setattr(copy, name, getattr(self, name))  # built once, never changed: shared
        copy._universal_tail = self._universal_tail
        copy._start_remembering(memory)
        return copy

    def _start_remembering(self, memory: Memory) -> None:
        self._memory = memory
        self._lock = threading.Lock()
        self._cost = 0
        self._resets = 0
        self._remember = True
        self._reset()

    # ---- building -------------------------------------------------------------------------------------

    def _state(self, test: Test | None, char: str | None = None) -> int:
        self._tests.append(test)
        self._chars.append(char)
        self._to.append(-1)
        self._epsilon.append([])
        return len(self._tests) - 1

    def _patch(self, ends: Sequence[int], target: int) -> None:
        for end in ends:
            if self._tests[end] is not None:
                self._to[end] = target
            else:
                self._epsilon[end].append(target)

    def _build(self, node: Node) -> tuple[int, list[int]]:
        """``node`` as a fragment: its first state, and the states whose way out is still open."""
        if isinstance(node, _Char):
            state = self._state(node.test, node.char)
            return state, [state]
        if isinstance(node, _Either):
            fork = self._state(None)
            ends = []
            for part in node.parts:
                first, part_ends = self._build(part)
                self._epsilon[fork].append(first)
                ends += part_ends
            return fork, ends
        if isinstance(node, _Seq):
            if not node.parts:
                state = self._state(None)
                return state, [state]
            start, ends = self._build(node.parts[0])
            for part in node.parts[1:]:
                first, next_ends = self._build(part)
                self._patch(ends, first)
                ends = next_ends
            return start, ends
        split = self._state(None)
        first, ends = self._build(node.part)
        self._epsilon[split].append(first)
        if node.most_one:
            return split, [*ends, split]
        self._patch(ends, split)
        if node.least:
            return first, [split]
        return split, [split]

    def _closure(self, states: set[int]) -> frozenset[int]:
        out: set[int] = set()
        stack = list(states)
        while stack:
            state = stack.pop()
            if state in out:
                continue
            out.add(state)
            if self._tests[state] is None:
                stack.extend(self._epsilon[state])
        return frozenset(out)

    # ---- whether Python's regex may match it --------------------------------------------------------------

    def _backtracks(self, tree: Node) -> bool:
        """Whether a backtracking matcher could spend more than a small multiple of a text's length on this
        pattern: too many parts that repeat or may be left out, or an ambiguity that grows with the text."""
        branches = sum(
            1 if isinstance(n, _Repeat) else len(n.parts) - 1 for n in _nodes(tree) if isinstance(n, _Repeat | _Either)
        )
        if not branches:
            return False
        if branches > _MAX_BRANCHES:
            return True
        chars = [i for i, test in enumerate(self._tests) if test is not None]
        if len(chars) > _MAX_ANALYSED:
            return True
        literal = {c for c in self._chars if c is not None}
        other = next(c for c in map(chr, range(1, 0x110000)) if c not in literal and c not in "/\n")
        alphabet = sorted(literal | {"/", other} - {"\n"})
        # A place every text without a newline matches from at once ends a try. With one, the regex is used only
        # on a text without a newline, and the check reads such texts; without one, it reads newlines too.
        universal = self._universal(chars, alphabet)
        tails = self._tails(chars, alphabet)
        if universal or tails:
            self._universal_tail = True
        else:
            alphabet.append("\n")

        def ends(states: frozenset[int]) -> bool:
            """Whether every rest of the text matches from ``states`` at once: the try ends."""
            return self._accept in states and bool(states & universal or self._tail_set(states, tails, alphabet))

        moves: dict[int, dict[str, frozenset[int]]] = {}
        for s in chars:
            test = self._tests[s]
            assert test is not None
            moves[s] = {}
            for c in alphabet:
                if not test(c):
                    continue
                after = self._closure({self._to[s]})
                if ends(after):
                    moves[s][c] = frozenset()
                else:
                    moves[s][c] = frozenset(t for t in after if self._tests[t] is not None)
        start = frozenset(t for t in self._start_states if self._tests[t] is not None)
        if ends(self._start_states):
            return False
        return self._exponential(start, moves, alphabet) or self._polynomial(start, moves, alphabet)

    def _universal(self, chars: list[int], alphabet: list[str]) -> frozenset[int]:
        """The character states from which every text without a newline matches (as a trailing ``**``)."""
        found = set(chars)
        changed = True
        while changed:
            changed = False
            for s in list(found):
                test = self._tests[s]
                assert test is not None
                after = self._closure({self._to[s]})
                if not all(test(c) for c in alphabet) or self._accept not in after or not after & found:
                    found.discard(s)
                    changed = True
        return frozenset(found)

    def _small_dfa(self) -> bool:
        """Whether the pattern's DFA has at most :data:`_DFA_STATES` sets of states, all found within
        :data:`_DFA_WORK` states moved: every set reachable from the start on one character of each kind (a literal
        of the pattern, a slash, a newline, any other), as RE2's DFA would build them."""
        if _re2_binding is None:
            return False
        kinds = sorted(self._codes)
        kinds.append(next(c for c in map(chr, range(1, 0x110000)) if c not in self._codes))
        seen = {self._start_states}
        waiting = [self._start_states]
        work = 0
        while waiting:
            states = waiting.pop()
            for ch in kinds:
                work += len(states)
                if work > _DFA_WORK:
                    return False
                following = self._closure(
                    {self._to[s] for s in states if (test := self._tests[s]) is not None and test(ch)}
                )
                if following and following not in seen:
                    if len(seen) >= _DFA_STATES:
                        return False
                    seen.add(following)
                    waiting.append(following)
        return True

    def _tails(self, chars: list[int], alphabet: list[str]) -> frozenset[int]:
        """The character states of a tail every text without a newline matches, whichever way it is read (as
        ``[^/]*(?:/.*)?``, which a gitignore line ends with): from each, the states the next character may move
        to accept the end, take every character, and are such states. Whichever of them a backtracking matcher
        reads a character with, it is still in the tail, so it never goes back and is done at the end."""
        found = set(chars)
        changed = True
        while changed:
            changed = False
            for s in list(found):
                after = self._closure({self._to[s]})
                if self._accept not in after or not self._tail_set(after, found, alphabet):
                    found.discard(s)
                    changed = True
        return frozenset(found)

    def _tail_set(self, states: frozenset[int], tails: set[int] | frozenset[int], alphabet: list[str]) -> bool:
        """Whether the character states of ``states`` are all of ``tails`` and take every character of
        ``alphabet`` but a newline."""
        moving = [t for t in states if self._tests[t] is not None]
        if not moving or not all(t in tails for t in moving):
            return False
        tests = [test for t in moving if (test := self._tests[t]) is not None]
        return all(any(test(c) for test in tests) for c in alphabet if c != "\n")

    @staticmethod
    def _reachable_pairs(
        start: frozenset[int], moves: dict[int, dict[str, frozenset[int]]], alphabet: list[str]
    ) -> dict[tuple[int, int], set[tuple[int, int]]] | None:
        """The graph of pairs of states that read the same text, from the start; None past the visit bound."""
        graph: dict[tuple[int, int], set[tuple[int, int]]] = {}
        queue = deque((a, b) for a in start for b in start)
        while queue:
            pair = queue.popleft()
            if pair in graph:
                continue
            if len(graph) > _MAX_VISITS:
                return None
            a, b = pair
            nxt = {(x, y) for c in alphabet for x in moves[a].get(c, ()) for y in moves[b].get(c, ())}
            graph[pair] = nxt
            queue.extend(n for n in nxt if n not in graph)
        return graph

    def _exponential(
        self, start: frozenset[int], moves: dict[int, dict[str, frozenset[int]]], alphabet: list[str]
    ) -> bool:
        """Whether a state has two different loops on one word: a pair of states walks off the diagonal and
        comes back to it in one strongly connected part of the pair graph."""
        graph = self._reachable_pairs(start, moves, alphabet)
        if graph is None:
            return True
        for component in _components(graph):
            if any(a == b for a, b in component) and any(a != b for a, b in component):
                return True
        return False

    def _polynomial(
        self, start: frozenset[int], moves: dict[int, dict[str, frozenset[int]]], alphabet: list[str]
    ) -> bool:
        """Whether two states ``p`` and ``q`` loop on one word ``w`` with ``p`` reaching ``q`` on it too: the
        triple ``(p, p, q)`` reaches ``(p, q, q)``. Only states on a loop are tried."""
        single = {s: {t for c in alphabet for t in moves[s].get(c, ())} for s in moves}
        looping = {s for component in _components(single) for s in component if _loops(component, single)}
        visits = 0
        for p in looping:
            for q in looping:
                if p == q:
                    continue
                seen = {(p, p, q)}
                queue = deque([(p, p, q)])
                while queue:
                    a, b, c = queue.popleft()
                    visits += 1
                    if visits > _MAX_VISITS:
                        return True
                    for ch in alphabet:
                        for x in moves[a].get(ch, ()):
                            for y in moves[b].get(ch, ()):
                                for z in moves[c].get(ch, ()):
                                    if (x, y, z) == (p, q, q):
                                        return True
                                    if (x, y, z) not in seen:
                                        seen.add((x, y, z))
                                        queue.append((x, y, z))
        return False

    # ---- remembering --------------------------------------------------------------------------------------

    def _reset(self) -> None:
        if self._cost:
            self._memory.give(self._cost)
        self._cost = 0
        self._ids: dict[frozenset[int], int] = {}
        self._sets: list[frozenset[int]] = []
        self._step: dict[int, int] = {}
        self._start = self._intern(self._start_states, charge=False)

    def _intern(self, states: frozenset[int], *, charge: bool = True) -> int:
        """The id of ``states``, remembered; its cost counted against this pattern's share when ``charge``."""
        known = self._ids.get(states)
        if known is None:
            known = len(self._sets)
            self._sets.append(states)
            self._ids[states] = known
            if charge:
                self._cost += len(states) + _SET_COST
        return known

    def _advance(self, current: frozenset[int], ch: str, code: int) -> int:
        """The id of the set ``current`` moves to on ``ch``, worked out and remembered while the budgets allow."""
        following = self._closure({self._to[s] for s in current if (test := self._tests[s]) is not None and test(ch)})
        with self._lock:
            cost = _STEP_COST + sum(len(new) + _SET_COST for new in {current, following} if new not in self._ids)
            if not self._remember:
                raise _Forget(following)
            if self._cost + cost > _PATTERN_BUDGET:
                # This pattern outgrows its share: it starts again, and after a few times walks without remembering.
                self._resets += 1
                self._reset()
                self._remember = self._resets <= _MAX_RESETS
                raise _Forget(following)
            if not self._memory.take(cost):
                # The patterns sharing its memory hold all of it: this one gives its share back and walks on.
                self._reset()
                raise _Forget(following)
            source = self._intern(current)
            target = self._intern(following)
            self._step[(source << 21) | code] = target
            self._cost += _STEP_COST  # the sets were counted as they were remembered: the cost taken, in all
        return target

    def _walk(self, states: frozenset[int], text: str) -> tuple[bool, int]:
        """Match ``text`` from ``states`` without remembering: a set of states moved on per character; with the
        work, the states moved on."""
        work = 0
        for ch in text:
            work += len(states)
            states = self._closure({self._to[s] for s in states if (test := self._tests[s]) is not None and test(ch)})
            if not states:
                return False, work
        return self._accept in states, work

    @property
    def uses_regex(self) -> bool:
        """Whether Python's regex matches this pattern (on a text without a newline at least)."""
        return self._regex is not None

    @property
    def regex(self) -> re.Pattern[str] | None:
        """Python's regex for this pattern, when it takes it; it answers for a text holding a newline only when
        :attr:`regex_reads_newlines`."""
        return self._regex

    @property
    def regex_reads_newlines(self) -> bool:
        """Whether :attr:`regex` answers for a text holding a newline too (else the automaton does)."""
        return not self._universal_tail

    @property
    def re2(self) -> Any | None:
        """RE2's program for this pattern, when it takes it (:func:`any_re2_fullmatch`)."""
        return self._re2

    @property
    def engine(self) -> str:
        """What matches this pattern: ``"re"`` (Python's regex), ``"re2"`` or ``"automaton"``."""
        return "re" if self._regex is not None else "re2" if self._re2 is not None else "automaton"

    def _re2_fullmatch(self, text: str) -> bool | None:
        """RE2's answer, or None for a text it cannot read (a lone surrogate, which UTF-8 cannot hold)."""
        program = self._re2
        try:
            encoded = text.encode()
        except UnicodeEncodeError:
            return None
        return program is not None and bool(program.Match(_RE2_BOTH, encoded, 0, len(encoded))[0] != _RE2_NO_MATCH)

    def fullmatch(self, text: str) -> bool:
        """Whether all of ``text`` matches."""
        if self._regex is not None and not (self._universal_tail and "\n" in text):
            return self._regex.fullmatch(text) is not None
        if self._re2 is not None and (found := self._re2_fullmatch(text)) is not None:
            return found
        return self.fullmatch_counted(text)[0]

    def fullmatch_counted(self, text: str) -> tuple[bool, int]:
        """Whether all of ``text`` matches, and the work it took the automaton: for each character it read by
        working out where its states move (a step it had not remembered, or a walk without remembering), the
        number of states. A remembered step is a lookup and counts none, nor does Python's regex, which takes
        only a pattern that cannot backtrack and so reads a text in linear time, nor RE2, which takes only a
        pattern whose DFA it holds and so reads a text in one pass. A pattern's own copy (:meth:`run_copy`)
        counts the same for the same texts in the same order, on every run."""
        if self._regex is not None and not (self._universal_tail and "\n" in text):
            return self._regex.fullmatch(text) is not None, 0
        if self._re2 is not None and (found := self._re2_fullmatch(text)) is not None:
            return found, 0
        if not self._remember:
            return self._walk(self._start_states, text)
        codes = self._codes
        step = self._step
        sets = self._sets
        current = self._start
        work = 0
        for index, ch in enumerate(text):
            code = codes.get(ch, 0)
            following = step.get((current << 21) | code)
            if following is None:
                work += len(sets[current])
                try:
                    following = self._advance(sets[current], ch, code)
                except _Forget as forgot:
                    found, rest = self._walk(forgot.states, text[index + 1 :])
                    return found, work + rest
                step, sets = self._step, self._sets
            current = following
            if not sets[current]:
                return False, work
        return self._accept in sets[current], work


class _Forget(Exception):  # noqa: N818 - a signal, not an error
    """The step could not be remembered: the walk goes on without the memory, from ``states``."""

    def __init__(self, states: frozenset[int]) -> None:
        super().__init__()
        self.states = states


def _components(graph: dict) -> list[set]:  # type: ignore[type-arg]
    """The strongly connected parts of ``graph`` (a node to the nodes it moves to), by Tarjan's algorithm, kept
    iterative so a large graph needs no deep recursion."""
    index: dict = {}  # type: ignore[type-arg]
    low: dict = {}  # type: ignore[type-arg]
    on_stack: set = set()  # type: ignore[type-arg]
    stack: list = []  # type: ignore[type-arg]
    out: list[set] = []  # type: ignore[type-arg]
    counter = 0
    for root in graph:
        if root in index:
            continue
        work = [(root, iter(graph.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(graph.get(child, ()))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = set()
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.add(member)
                    if member == node:
                        break
                out.append(component)
    return out


def _loops(component: set, graph: dict) -> bool:  # type: ignore[type-arg]
    """Whether ``component`` holds a loop: more than one node, or one that moves to itself."""
    if len(component) > 1:
        return True
    (only,) = component
    return only in graph.get(only, ())
