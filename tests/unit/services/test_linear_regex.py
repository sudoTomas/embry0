"""Signature masks are matched with a linear-time engine that keeps Python's meaning.

A stack's masks (#172) are regular expressions an operator stores, applied to
every line of every tick. ``embry0.services.linear_regex`` reads each one with
Python's own parser and matches it with RE2, so the time one line takes is
linear in the line whatever the pattern, and the masked line is the one
Python's ``re`` would give. These tests pin both halves: the answer is ``re``'s
(compared on generated patterns and texts, the one documented difference
aside), and what cannot be matched that way is refused, with a reason.
"""

from __future__ import annotations

import importlib
import random
import re
import time
from types import ModuleType
from typing import Any

import pytest


def _engine() -> ModuleType:
    try:
        return importlib.import_module("embry0.services.linear_regex")
    except ModuleNotFoundError as exc:
        pytest.fail(f"embry0 has no linear-time engine for a stack's masks: {exc}")


def _python_sub(pattern: str, text: str) -> str:
    return re.sub(pattern, lambda _m: "<m>", text)


# ---- the same answer as re ----------------------------------------------------

#: Patterns of the kind a stack's masks are written in, and a few that use
#: every construct the engine translates.
REALISTIC = [
    r"\brec_[a-z0-9]{7}\b",
    r"rec-\d{6}-[a-z0-9]{2}",
    r"tkt-\d{4}-[a-z]{2}",
    r"(?i)order[ _-]?\d{5,}",
    r"[A-Z]{3}-\d{4,8}",
    r"session=[^;\s]+",
    r"\d+(?:\.\d+)?ms",
    r"(?:GET|POST|PUT) /api/v1/\S+",
    r"(?m)^\s*at \S+$",
    r"\w+@\w+\.\w+",
    r"user_\d+?",
    r"\s+$",
    r"^\S+\s",
    r"[^\x00-\x7f]+",
    r"(?s)<<.*?>>",
    r"(?x) id \s* = \s* \d+",
    r"\$\{[A-Z_]+\}",
    r"[\u0400-\u04ff]+",
    r"(?i)straße",
    r"(?i)[ks]elvin",
    r"\d{2,}",
    r"(?:ab|a)(?:bc|c)",
    r"x(?:a|)+y",
]

LINES = [
    "WARN invoice sync failed for record rec_7hq2kx9: upstream timeout",
    "ERROR order 12345 failed; ORDER_99999 retried in 250ms",
    "GET /api/v1/jobs/abc?x=1 took 12.5ms session=deadbeef; path=/",
    "    at com.example.Service.run(Service.java:42)\n    at com.example.Main.main(Main.java:7)",
    "Straße STRASSE strasse Kelvin \u212aelvin \u017felvin selvin",
    "ид клиента: Иван 42, tkt-2026-ab done\n",
    "日本語のログ rec_7hq2kx9 と ٣٤٥ 数字 user_7 xay xy xaay",
    "<<multi\nline>> ${HOME_DIR} id = 17 ab abc ac",
    "trailing space   \nnext line\n",
    "a\ud800b rec_0000000 \ufffd",
    "",
]


@pytest.mark.parametrize("pattern", REALISTIC)
def test_a_mask_masks_a_line_as_python_re_does(pattern: str) -> None:
    compiled = _engine().compile_linear(pattern)
    for line in LINES:
        expected = _python_sub(pattern, line)
        assert compiled.sub("<m>", line) == expected, f"{pattern!r} on {line!r}: re gives {expected!r}"


#: What the generated patterns are built from: literals that fold in odd ways
#: under (?i) (KELVIN SIGN, LONG S, dotted and dotless i, sharp s), digits and
#: letters outside ASCII, every class escape, anchors and quantifiers.
_ATOMS = [
    *"abKkSsiIx1 _",
    "\u212a",
    "\u017f",
    "\u0130",
    "\u0131",
    "\u00df",
    "\u1e9e",
    "\u0660",
    "\u00e9",
    r"\n",
    r"\d",
    r"\D",
    r"\w",
    r"\W",
    r"\s",
    r"\S",
    ".",
    "[ab]",
    "[^a]",
    "[a-c]",
    "[Kk]",
    "[Ss]",
    r"[^\w]",
    r"[\d\s]",
    "[A-Z]",
]
_ANCHORS = ["^", "$", r"\A", r"\Z", r"\b", r"\B"]
_QUANTIFIERS = ["*", "+", "?", "{2}", "{1,3}", "{0,2}", "*?", "+?", "??", "{1,2}?"]
_FLAGS = ["", "(?i)", "(?m)", "(?s)", "(?a)", "(?im)", "(?is)", "(?ia)", "(?ms)"]
_TEXT = [*"abKkSsiIx1 _AB\n", "\u212a", "\u017f", "\u0130", "\u0131", "\u00df", "\u1e9e", "\u0660", "\u00e9", "\u2028"]


def _generated(rng: random.Random, depth: int = 0) -> str:
    roll = rng.random()
    if depth > 3 or roll < 0.35:
        return rng.choice(_ATOMS)
    if roll < 0.45:
        return rng.choice(_ANCHORS)
    if roll < 0.6:
        return _generated(rng, depth + 1) + _generated(rng, depth + 1)
    if roll < 0.7:
        return "(?:" + _generated(rng, depth + 1) + "|" + _generated(rng, depth + 1) + ")"
    if roll < 0.85:
        return "(?:" + _generated(rng, depth + 1) + ")" + rng.choice(_QUANTIFIERS)
    if roll < 0.92:
        return "(" + _generated(rng, depth + 1) + ")"
    return rng.choice(["(?i:", "(?-i:", "(?m:", "(?s:"]) + _generated(rng, depth + 1) + ")"


def _beside_a_non_ascii_word_character(pattern: str, text: str) -> bool:
    """The one documented difference: \\b and \\B beside a letter or digit outside ASCII."""
    return ("\\b" in pattern or "\\B" in pattern) and any(not c.isascii() and c.isalnum() for c in text)


def test_generated_patterns_mask_as_python_re_does() -> None:
    """3,000 seeded patterns, 20 texts each: every pattern the engine accepts gives re's answer."""
    engine = _engine()
    rng = random.Random(172)
    compared = refused = 0
    differences = []
    for _ in range(3000):
        pattern = rng.choice(_FLAGS) + _generated(rng)
        try:
            python = re.compile(pattern)
        except re.error:
            continue
        try:
            compiled = engine.compile_linear(pattern)
        except engine.UnsupportedPattern:
            refused += 1
            continue
        for _ in range(20):
            text = "".join(rng.choice(_TEXT) for _ in range(rng.randint(0, 10)))
            expected = python.sub(lambda _m: "<m>", text)
            got = compiled.sub("<m>", text)
            compared += 1
            if got != expected and not _beside_a_non_ascii_word_character(pattern, text):
                differences.append((pattern, text, expected, got))
    assert compared > 30_000, f"only {compared} comparisons: the generator is measuring nothing"
    assert refused < compared / 20, f"{refused} patterns refused: the comparison skips too much"
    assert differences == [], f"{len(differences)} answers differ from re, for example {differences[:5]}"


def test_a_word_boundary_counts_ascii_word_characters_only() -> None:
    """The documented difference, pinned: beside é, \\b differs from re's; under (?a) it is re's."""
    engine = _engine()
    assert engine.compile_linear(r"\bx\b").sub("<m>", "éx x") == "é<m> <m>"
    assert re.sub(r"\bx\b", "<m>", "éx x") == "éx <m>", "re counts é as a word character"
    assert engine.compile_linear(r"(?a)\bx\b").sub("<m>", "éx x") == re.sub(r"(?a)\bx\b", "<m>", "éx x")


def test_a_capital_and_its_lower_case_never_fold_beyond_python_re() -> None:
    """RE2 reads [Kk] as k under case folding, and merged into an alternation that folding
    reaches KELVIN SIGN; re's [Kk] does not."""
    # The groups keep re from merging the alternatives into one class, as it merges x|[Kk].
    pattern = r"(x)|[Kk]|(y)|[Ss]"
    text = "K\u212ak S\u017fs xy"
    assert (
        _engine().compile_linear(pattern).sub("<m>", text)
        == re.sub(pattern, "<m>", text)
        == ("<m>\u212a<m> <m>\u017f<m> <m><m>")
    )


def test_a_lone_surrogate_in_a_line_is_kept_and_read_as_python_re_reads_it() -> None:
    compiled = _engine().compile_linear(r"a.b")
    line = "a\ud800b and a\ud800c"
    assert compiled.sub("<m>", line) == re.sub(r"a.b", "<m>", line) == "<m> and a\ud800c"


def test_dollar_outside_multi_line_mode_is_the_end_of_a_text_of_several_lines() -> None:
    """A proposal's body, which attribution normalizes too, holds newlines."""
    for pattern in (r"\w+$", r"\s$", r"[^\n]$"):
        compiled = _engine().compile_linear(pattern)
        # The last text holds the private-use character the engine would read its other newlines as first.
        for text in ("one\ntwo\nthree", "one\ntwo\nthree\n", "one\n", "one", "x\nab\U000f0000\n"):
            assert compiled.sub("<m>", text) == re.sub(pattern, "<m>", text), (pattern, text)


# ---- linear time ----------------------------------------------------------------

#: Patterns Python's re takes exponential (or high polynomial) time on, given a
#: line of a's that ends in a character they do not expect.
SLOW_ON_PYTHON_RE = [
    r"(a+)+$",
    r"(a|aa)+$",
    r"(a|a)*b",
    r"(.*a){20}",
    r"(\w+\s?)+$",
    r"^(([a-z])+.)+[A-Z]([a-z])+$",
    r"(x+x+)+y",
]


@pytest.mark.parametrize("pattern", SLOW_ON_PYTHON_RE)
def test_a_pattern_python_re_backtracks_on_takes_linear_time(pattern: str) -> None:
    compiled = _engine().compile_linear(pattern)
    short = "a" * 12 + "!"
    assert compiled.sub("<m>", short) == _python_sub(pattern, short), "on a line re still answers, the same answer"
    for length in (40, 100_000):
        line = "a" * length + "!"
        started = time.perf_counter()
        compiled.sub("<m>", line)
        took = time.perf_counter() - started
        assert took < 0.5, f"{pattern!r} on a line of {length + 1} characters took {took:.2f}s"


# ---- what is refused ------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "why"),
    [
        (r"rec_(?=\d)", "look-ahead"),
        (r"(?<!x)rec_\d+", "look-ahead and look-behind"),
        (r"(a)\1", "back-reference"),
        (r"(?P<x>a)(?P=x)", "back-reference"),
        (r"(a)?(?(1)b|c)", "conditional"),
        (r"(?>a+)b", "atomic"),
        (r"a++b", "possessive"),
        (r"a{1001}", "above 1000"),
        (r"\w{80}", "too large"),
        (r"x*\b", "empty string"),
        (r"(?a:\w+)x", "(?a:...)"),
        (r"(?m:^x)|y$", "multi-line"),
        ("[\ud800]x", "surrogate"),
    ],
)
def test_what_cannot_be_matched_in_linear_time_with_pythons_meaning_is_refused(pattern: str, why: str) -> None:
    engine = _engine()
    re.compile(pattern)  # re reads every one of them
    with pytest.raises(engine.UnsupportedPattern) as refused:
        engine.compile_linear(pattern)
    assert why in str(refused.value), str(refused.value)


def test_a_pattern_is_compiled_once() -> None:
    """A stack's masks are read on every tick."""
    engine = _engine()
    assert engine.compile_linear(r"rec_\d{4}") is engine.compile_linear(r"rec_\d{4}")


def test_a_pattern_re_does_not_read_raises_re_error() -> None:
    with pytest.raises(re.error):
        _engine().compile_linear("rec_[")


def _sizes(engine: Any) -> list[int]:
    return [engine.compile_linear(p)._program.programsize for p in (r"\w{8}", r"[A-Za-z0-9_]{64}", REALISTIC[0])]


def test_the_masks_a_person_writes_fit_the_program_bound() -> None:
    engine = _engine()
    sizes = _sizes(engine)
    assert max(sizes) < engine.MAX_PROGRAM_SIZE, sizes
