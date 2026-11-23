"""Path patterns matched in bounded time (:mod:`embry0.bounded_patterns`): the same answers as Python's regex.

The glob grammars built on them are tested in ``tests/unit/services/test_bounded_globs.py``.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable

import pytest

from embry0.bounded_patterns import ANY, NOT_SLASH, Memory, Node, Pattern, either, lit, opt, plus, seq, star
from tests.fixtures.cpu_cost import cost, same

#: Every test here runs with the process's memory capped (tests/fixtures/memory_cap.py): a matcher that grows
#: without bound, as one whose size check stopped checking, fails at once with MemoryError instead of taking the
#: host's memory first.
pytestmark = pytest.mark.memory_capped


# ---- the engine against Python's regex ---------------------------------------------------------------------


def _random_tree(rng: random.Random, depth: int = 0) -> tuple[Node, str]:
    """A random pattern and the regex it stands for."""
    choice = rng.random()
    if depth > 2 or choice < 0.45:
        leaf = rng.choice(["a", "b", "/", "not_slash", "any"])
        if leaf == "not_slash":
            return NOT_SLASH, "[^/]"
        if leaf == "any":
            return ANY, "."
        return lit(leaf), re.escape(leaf)
    parts = [_random_tree(rng, depth + 1) for _ in range(rng.randint(1, 3))]
    inner = "".join(r for _, r in parts)
    nodes = [n for n, _ in parts]
    kind = rng.choice(["seq", "star", "plus", "opt"])
    if kind == "seq":
        return seq(*nodes), inner
    wrap = {"star": star, "plus": plus, "opt": opt}[kind]
    return wrap(*nodes), f"(?:{inner}){ {'star': '*', 'plus': '+', 'opt': '?'}[kind] }".replace(" ", "")


def test_a_pattern_matches_what_its_regex_matches() -> None:
    rng = random.Random(475)
    for _ in range(1500):
        node, regex = _random_tree(rng)
        pattern, reference = Pattern(node, prefer_regex=False, use_re2=False), re.compile(regex)
        for _ in range(12):
            text = "".join(rng.choice("ab/\n") for _ in range(rng.randint(0, 6)))
            assert pattern.fullmatch(text) is bool(reference.fullmatch(text)), f"{regex!r} vs {text!r}"


def test_the_automaton_and_the_regex_fast_path_answer_alike_and_the_memo_starts_again_when_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pattern with at most one unbounded part is matched by Python's regex; every other by RE2 or the
    automaton, whose memo of the sets of states it walked starts again once full, and answers as before."""
    from embry0 import bounded_patterns

    monkeypatch.setattr(bounded_patterns, "_PATTERN_BUDGET", 120)
    rng = random.Random(4754)
    for _ in range(400):
        node, regex = _random_tree(rng)
        fast, slow = Pattern(node), Pattern(node, prefer_regex=False, use_re2=False)
        for _ in range(12):
            text = "".join(rng.choice("ab/\n") for _ in range(rng.randint(0, 8)))
            assert fast.fullmatch(text) is slow.fullmatch(text) is bool(re.fullmatch(regex, text)), (regex, text)
    assert Pattern(star(NOT_SLASH), lit(".py"))._regex is not None, "one unbounded part takes the fast path"


def test_every_pattern_together_remembers_no_more_than_the_budget_and_gives_it_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patterns are kept (up to thousands): what all of them remember together is bounded, a pattern that cannot
    remember more walks on and still answers right, and a pattern let go gives its share back."""
    import gc

    from embry0 import bounded_patterns

    monkeypatch.setattr(bounded_patterns._SHARED, "limit", 900)
    monkeypatch.setattr(bounded_patterns, "_PATTERN_BUDGET", 500)
    gc.collect()
    # Counted from nothing, so what other tests' patterns hold cannot crowd this one out of the budget.
    monkeypatch.setattr(bounded_patterns._SHARED, "spent", 0)
    before = 0
    rng = random.Random(4757)
    patterns = [
        (
            Pattern(star(NOT_SLASH), lit("a"), *[NOT_SLASH] * k, star(NOT_SLASH), use_re2=False),
            re.compile(rf"[^/]*a[^/]{{{k}}}[^/]*"),
        )
        for k in range(8, 14)
    ]
    for _ in range(60):
        text = "".join(rng.choice("ab") for _ in range(40))
        for pattern, reference in patterns:
            assert pattern.fullmatch(text) is bool(reference.fullmatch(text)), (reference.pattern, text)
            assert bounded_patterns._SHARED.spent - before <= 5_000, (
                f"the patterns remember {bounded_patterns._SHARED.spent - before}"
            )
    del patterns, pattern
    gc.collect()
    assert bounded_patterns._SHARED.spent == before, "a pattern let go kept its share of the budget"


def test_a_run_s_copy_remembers_in_the_run_s_memory_and_counts_the_same_on_every_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A copy of a pattern for one run (a scoring pass) remembers within that run's memory, never the memory
    every pattern shares; it counts work only for the steps it works out, so a text read again from memory
    counts none; and two runs count the same for the same texts, however much the shared pattern remembered,
    whether the shared memory was full, and however small the run's memory."""
    import gc

    from embry0 import bounded_patterns
    from embry0.bounded_patterns import Memory

    pattern = Pattern(star(NOT_SLASH), lit("a"), *[NOT_SLASH] * 10, star(NOT_SLASH), lit("b"), use_re2=False)
    reference = re.compile(r"[^/]*a[^/]{10}[^/]*b")
    assert pattern.engine == "automaton", "the pattern must be the automaton's for the test to mean anything"
    rng = random.Random(4761)
    texts = ["".join(rng.choice("ab") for _ in range(40)) for _ in range(200)]

    def run(memory: Memory) -> tuple[Pattern, list[int]]:
        copy = pattern.run_copy(memory)
        shared = bounded_patterns._SHARED.spent
        counted = []
        for text in texts:
            found, work = copy.fullmatch_counted(text)
            assert found is bool(reference.fullmatch(text)), text
            assert memory.spent <= memory.limit, f"the run's copy remembers {memory.spent}"
            counted.append(work)
        assert bounded_patterns._SHARED.spent == shared, "the run's copy took from the memory every pattern shares"
        return copy, counted

    memory = Memory(100_000)
    copy, first = run(memory)
    assert copy.fullmatch_counted(texts[0])[1] == 0, "a text read again from memory counted work"
    assert memory.spent > 0
    for text in texts[:50]:
        pattern.fullmatch(text)  # the shared pattern remembers...
    monkeypatch.setattr(bounded_patterns._SHARED, "spent", bounded_patterns._SHARED.limit)  # ...and is full
    assert run(Memory(100_000))[1] == first, "a second run counted other work"
    small = run(Memory(300))[1]
    assert run(Memory(300))[1] == small and sum(small) > sum(first), "a small memory counts more, the same each run"
    del copy
    gc.collect()
    assert memory.spent == 0, "a copy let go kept its share of the run's memory"


# ---- where a try ends, for the check that routes to Python's regex ------------------------------------------


def test_a_rest_every_text_matches_ends_a_try() -> None:
    """'[^/]*(?:/.*)?' after the last literal, as an ignore line ending in '*' has: every text without a newline
    matches it, so a try that reaches it is done, and the parts before it cannot split a text two ways."""
    pattern = Pattern(star(NOT_SLASH), lit("a"), star(NOT_SLASH), opt(lit("/"), star(ANY)))
    assert pattern.uses_regex, "a rest every text matches did not end the try"
    assert not pattern.fullmatch("b" * 20_000 + "/b") and pattern.fullmatch("b" * 20_000 + "a/b\n") is False


@pytest.mark.parametrize(
    ("rest", "text"),
    [
        ([star(NOT_SLASH)], "a" * 20_000 + "/"),  # a slash fails it
        ([star(NOT_SLASH), opt(lit("/"), star(NOT_SLASH), lit("/"), star(ANY))], "a" * 20_000 + "/b"),  # a directory
    ],
    ids=["a slash fails it", "one more directory fails it"],
)
def test_a_rest_that_some_text_fails_does_not_end_a_try(rest: list[Node], text: str) -> None:
    """A rest that a text can fail sends a backtracking matcher back into the parts before it, and the two '*'
    there split a text of n characters n² ways: the pattern stays with the automaton, in linear time. Measured
    against a pass of re over the text, on this thread's CPU clock (tests/fixtures/cpu_cost.py), not with the wall
    clock, which times the host."""
    pattern = Pattern(star(NOT_SLASH), lit("a"), *rest)
    assert not pattern.uses_regex, "a rest some text fails ended the try: Python's regex takes the pattern"
    assert not pattern.fullmatch(text)

    def fresh() -> Callable[[], object]:
        built = Pattern(star(NOT_SLASH), lit("a"), *rest)
        return lambda: built.fullmatch(text)

    measured = cost(fresh, [text])
    assert measured < REST_COST, f"the match cost {measured:.0f} passes of re over the text, at most {REST_COST:.0f}"


#: How many passes of re over the text the match above may cost: it measures under 1 (RE2 reads it), Python's regex
#: on the same pattern and the same 20,000 characters 570 to 840, growing with the text
#: (:func:`test_the_bound_tells_python_s_regex_from_the_pattern`).
REST_COST = 100.0


def test_the_bound_tells_python_s_regex_from_the_pattern() -> None:
    """The bound can fail: Python's regex, on the first case's pattern and text, splits its 20,000 characters n² ways."""
    regex = re.compile(r"[^/]*a[^/]*\Z")
    text = "a" * 20_000 + "/"
    measured = cost(same(lambda: regex.match(text)), [text], runs=1)
    assert measured > REST_COST, f"Python's regex cost {measured:.0f} passes of re over the text, within the bound"


# ---- RE2, for a pattern that can backtrack and whose DFA is small ---------------------------------------------

#: Literals RE2 and Python's regex write differently, or not at all, beside plain ones; a lone surrogate, which RE2
#: takes as a code point UTF-8 never holds, so it matches no text RE2 reads.
_LITERALS = [
    "a",
    "b",
    "/",
    ".",
    "\\",
    "$",
    "^",
    "[",
    "]",
    "(",
    "{",
    "|",
    "*",
    "?",
    "+",
    "-",
    " ",
    "\n",
    "\t",
    "é",
    "\U0001f600",
    "\ud800",
]
#: Texts made of them, a lone surrogate (which UTF-8 cannot hold), and U+FFFD, which RE2 might read one as.
_TEXT = [*_LITERALS, "x", "\ud800", "\ufffd"]


def _rich_tree(rng: random.Random, depth: int = 0) -> tuple[Node, str]:
    """A random pattern over :data:`_LITERALS` and the two classes, and the regex it stands for in Python's ``re``."""
    if depth > 2 or rng.random() < 0.45:
        leaf = rng.choice([*_LITERALS, "not_slash", "any"])
        if leaf == "not_slash":
            return NOT_SLASH, "[^/]"
        if leaf == "any":
            return ANY, "."
        return lit(leaf), re.escape(leaf)
    parts = [_rich_tree(rng, depth + 1) for _ in range(rng.randint(1, 3))]
    nodes, inner = [n for n, _ in parts], "".join(r for _, r in parts)
    kind = rng.choice(["seq", "star", "plus", "opt", "either"])
    if kind == "seq":
        return seq(*nodes), inner
    if kind == "either":
        return either(*nodes), "(?:" + "|".join(r for _, r in parts) + ")"
    wrap = {"star": star, "plus": plus, "opt": opt}[kind]
    return wrap(*nodes), "(?:" + inner + ")" + {"star": "*", "plus": "+", "opt": "?"}[kind]


def test_re2_is_there_and_takes_a_pattern_that_can_backtrack() -> None:
    """google-re2 is a dependency: its binding answers as the module reads it (checked at import), and a pattern
    that can backtrack, with a small DFA, is RE2's; without this the RE2 tests below would test the automaton."""
    from embry0 import bounded_patterns

    assert bounded_patterns._re2_binding is not None and bounded_patterns._re2_works()
    assert Pattern(star(NOT_SLASH), lit("a"), star(NOT_SLASH), lit("b")).engine == "re2"
    assert bounded_patterns._re2_program("a{1001}") is None, "RE2 refuses a count past 1,000: the automaton matches"


def test_re2_answers_as_python_s_regex_does(monkeypatch: pytest.MonkeyPatch) -> None:
    """Random patterns of every literal RE2 and Python's regex might write differently (a backslash, '$', '^',
    brackets, a newline, a tab, a letter of two bytes and one of four), forced to RE2, against texts of them,
    a lone surrogate and U+FFFD: every answer is Python's regex's."""
    from embry0 import bounded_patterns

    # RE2 takes every pattern it can express: bounds far past these small patterns' DFAs, and finite.
    monkeypatch.setattr(bounded_patterns, "_DFA_STATES", 100_000)
    monkeypatch.setattr(bounded_patterns, "_DFA_WORK", 10_000_000)
    rng = random.Random(4768)
    taken = 0
    for _ in range(1500):
        node, regex = _rich_tree(rng)
        pattern, reference = Pattern(node, prefer_regex=False), re.compile(regex, re.DOTALL * 0)
        taken += pattern.engine == "re2"
        for _ in range(12):
            text = "".join(rng.choice(_TEXT) for _ in range(rng.randint(0, 7)))
            assert pattern.fullmatch(text) is bool(reference.fullmatch(text)), f"{regex!r} vs {text!r}"
            assert pattern.fullmatch_counted(text)[0] is bool(reference.fullmatch(text)), f"{regex!r} vs {text!r}"
    assert taken == 1500, f"RE2 took {taken} of 1,500 patterns"


def test_a_pattern_whose_dfa_grows_stays_with_the_automaton() -> None:
    """RE2 runs out of memory on a pattern whose DFA grows with the text and reads on without it, 0.1 to 0.5 ms
    a 260-character name, beyond any budget; such a pattern is the automaton's, which a pass budgets."""
    grows = Pattern(star(NOT_SLASH), lit("a"), *[NOT_SLASH] * 60, star(NOT_SLASH), opt(lit("/"), star(ANY)))
    assert grows.engine == "automaton", "a pattern whose DFA grows went to RE2"
    small = Pattern(*[opt(star(ANY), lit("/")), star(NOT_SLASH), lit("/")] * 14, lit("a"))
    assert small.engine == "re2", "a pattern of fourteen '**/*/', whose DFA is small, did not go to RE2"


def test_re2_refuses_a_program_past_its_size(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0 import bounded_patterns

    assert bounded_patterns._re2_program("abcdefgh") is not None
    monkeypatch.setattr(bounded_patterns, "_RE2_MAX_PROGRAM", 5)
    assert bounded_patterns._re2_program("abcdefgh") is None, "a program past its size went to RE2"


@pytest.mark.parametrize(
    ("bound", "other", "steps_most"), [("_DFA_STATES", 3_000_000, 4_000), ("_DFA_WORK", 30_000, 3_500)]
)
def test_the_dfa_check_stops_at_either_bound(
    monkeypatch: pytest.MonkeyPatch, bound: str, other: int, steps_most: int
) -> None:
    """Each bound alone stops the check on a pattern whose DFA grows with the text, in few steps; the other one,
    raised (but kept finite, so a check without the first still ends), would let it take many more."""
    from embry0 import bounded_patterns

    monkeypatch.setattr(bounded_patterns, "_DFA_WORK" if bound == "_DFA_STATES" else "_DFA_STATES", other)
    steps = 0
    closure = Pattern._closure

    def counted(self: Pattern, states: set[int]) -> frozenset[int]:
        nonlocal steps
        steps += 1
        return closure(self, states)

    monkeypatch.setattr(Pattern, "_closure", counted)
    grows = Pattern(star(NOT_SLASH), lit("a"), *[NOT_SLASH] * 60, star(NOT_SLASH), opt(lit("/"), star(ANY)))
    assert grows.engine == "automaton" and steps <= steps_most, (grows.engine, steps)


def test_python_s_regex_and_re2_count_no_work() -> None:
    """Only the automaton's work is counted: Python's regex and RE2 read a text in linear time."""
    by_regex = Pattern(star(NOT_SLASH), lit(".py"))
    by_re2 = Pattern(star(NOT_SLASH), lit("a"), star(NOT_SLASH), lit("b"))
    assert (by_regex.engine, by_re2.engine) == ("re", "re2")
    for pattern, text in ((by_regex, "a.py"), (by_regex, "a.js"), (by_re2, "xab"), (by_re2, "xba")):
        found, work = pattern.run_copy(Memory(10_000)).fullmatch_counted(text)
        assert (found, work) == (text in ("a.py", "xab"), 0), (pattern.engine, text, found, work)


def test_a_text_re2_cannot_read_is_read_by_the_automaton() -> None:
    """A lone surrogate, which UTF-8 cannot hold, is read by the automaton, which counts its work."""
    pattern = Pattern(star(NOT_SLASH), lit("a"), star(NOT_SLASH), lit("b"))
    assert pattern.engine == "re2"
    for text, expected in (("x\ud800ab", True), ("\ud800a/b", False), ("xab", True)):
        assert pattern.fullmatch(text) is expected, text
        found, work = pattern.run_copy(Memory(10_000)).fullmatch_counted(text)
        assert found is expected and (work > 0) is ("\ud800" in text), (text, found, work)


def test_the_automaton_s_work_is_bounded_by_its_pattern_not_by_the_characters_of_the_texts() -> None:
    """The automaton remembers a character by its kind (a literal of the pattern, a slash, a newline, any other),
    so texts of thousands of distinct characters cost a pattern no more steps to work out."""
    pattern = Pattern(
        opt(star(ANY), lit("/")),
        lit("a"),
        lit("/"),
        opt(star(ANY), lit("/")),
        lit("b"),
        opt(lit("/"), star(ANY)),
        prefer_regex=False,
        use_re2=False,
    )  # '**/a/**/b' as an ignore line
    rng = random.Random(4766)
    alphabet = [chr(c) for c in range(0x4E00, 0x4E00 + 3000)]  # 3,000 CJK characters
    copy, work = pattern.run_copy(Memory(500_000)), 0
    for _ in range(3000):
        work += copy.fullmatch_counted("/".join("".join(rng.choice(alphabet) for _ in range(6)) for _ in range(5)))[1]
    assert work < 2_000, f"the pattern worked out {work} state steps"


def test_re2_s_reading_of_a_dfa_that_crowds_its_memory_counts_work() -> None:
    """RE2 reads a byte of a small DFA at about the cost of a lookup and counts none; past 256 sets it builds and
    forgets states as it reads, and each byte counts a unit for each 256 sets past 256, so a budget covers it."""
    from embry0 import bounded_patterns

    built = "*a??b*????a?*??*b?*b??aa?*a*q"  # as an ignore line: 521 sets
    crowding = Pattern(
        opt(star(ANY), lit("/")),
        *[star(NOT_SLASH) if c == "*" else NOT_SLASH if c == "?" else lit(c) for c in built],
        opt(lit("/"), star(ANY)),
    )
    small = Pattern(star(NOT_SLASH), lit("a"), star(NOT_SLASH), lit("b"))
    assert (crowding.engine, small.engine) == ("re2", "re2")
    assert crowding.dfa_sets > bounded_patterns._RE2_FREE_SETS >= small.dfa_sets
    assert crowding.re2_counts_work and not small.re2_counts_work
    text = "ab" * 100 + "é"  # 202 bytes in UTF-8
    excess = crowding.dfa_sets - bounded_patterns._RE2_FREE_SETS
    copy = crowding.run_copy(Memory(10_000))
    assert copy.fullmatch_counted(text) == (False, 202 * excess // bounded_patterns._RE2_SETS_PER_UNIT)
    assert small.run_copy(Memory(10_000)).fullmatch_counted(text) == (False, 0)


def test_a_test_here_is_held_to_its_memory_cap() -> None:
    """The cap is on (the module's marker): an allocation past its margin fails at once."""
    import resource

    from tests.fixtures.memory_cap import DEFAULT_MARGIN, mapped_bytes

    if mapped_bytes() is None:
        pytest.skip("no /proc here: the marker caps nothing")

    assert resource.getrlimit(resource.RLIMIT_AS)[0] != resource.RLIM_INFINITY, "no cap is set"
    with pytest.raises(MemoryError):
        bytearray(DEFAULT_MARGIN + (256 << 20))


def test_the_memory_cap_holds_for_its_block_and_is_lifted_after() -> None:
    import resource

    from tests.fixtures.memory_cap import capped_memory, mapped_bytes

    if mapped_bytes() is None:
        pytest.skip("no /proc here: the marker caps nothing")
    before = resource.getrlimit(resource.RLIMIT_AS)
    with capped_memory(16 << 20) as cap:
        assert cap is not None and resource.getrlimit(resource.RLIMIT_AS)[0] == cap
        with pytest.raises(MemoryError):
            bytearray(64 << 20)
    assert resource.getrlimit(resource.RLIMIT_AS) == before, "the cap outlived its block"
    assert len(bytearray(64 << 20)) == 64 << 20


def test_a_module_s_cap_is_measured_from_one_base() -> None:
    """The base is read once for a module, so what one test leaves mapped never raises the next test's cap."""
    from tests.fixtures.memory_cap import mapped_bytes, module_base

    if mapped_bytes() is None:
        pytest.skip("no /proc here: the marker caps nothing")
    first = module_base("tests.example_module")
    kept = bytearray(64 << 20)
    assert first is not None and module_base("tests.example_module") == first, "the base moved with the process"
    del kept


def test_a_test_begun_after_others_grew_the_process_still_gets_its_margin() -> None:
    """The eighth review's case: under xdist another module's test can leave the process 400 MiB larger between
    two capped tests. The cap starts from what the process maps when the test starts, so the test still has its
    margin, and no allocation it was given room for fails."""
    from tests.fixtures.memory_cap import capped_memory, mapped_bytes

    now = mapped_bytes()
    if now is None:
        pytest.skip("no /proc here: the marker caps nothing")
    with capped_memory(64 << 20, base=now - (400 << 20)) as cap:  # the module's base, from before the growth
        assert cap is not None and cap >= now + (64 << 20), f"the cap is {cap >> 20} MiB, the process maps {now >> 20}"
        assert len(bytearray(32 << 20)) == 32 << 20


def test_where_nothing_can_be_capped_the_marker_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without resource (not a Unix) or /proc (not Linux) the cap is a no-op, never an error."""
    import resource

    from tests.fixtures import memory_cap

    before = resource.getrlimit(resource.RLIMIT_AS)
    for name, value in (("resource", None), ("mapped_bytes", lambda: None)):
        with monkeypatch.context() as patched:
            patched.setattr(memory_cap, name, value)
            with memory_cap.capped_memory() as cap:
                assert cap is None and resource.getrlimit(resource.RLIMIT_AS) == before, name
            if name == "mapped_bytes":
                assert memory_cap.module_base("tests.nowhere") is None
