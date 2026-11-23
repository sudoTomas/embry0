"""Tests for the conditional-criteria glob matcher (EMB-39), and that it matches in bounded time (#458's review)."""

from __future__ import annotations

import random
import re
import warnings
from collections.abc import Callable

import pytest

from embry0.workflows.qa._glob_match import GlobPatternError, compile_glob, match_any
from tests.fixtures.cpu_cost import cost, same

MATCH_CASES = [
    # (pattern, path, expected)
    # literal
    ("a/b.py", "a/b.py", True),
    ("a/b.py", "a/b.pyx", False),
    ("a/b.py", "x/a/b.py", False),  # anchored — no substring match
    # * stays within a segment
    ("apps/*/index.ts", "apps/quoting/index.ts", True),
    ("apps/*/index.ts", "apps/quoting/deep/index.ts", False),
    ("src/*.py", "src/main.py", True),
    ("src/*.py", "src/pkg/main.py", False),
    # ? one non-/ char
    ("a?c", "abc", True),
    ("a?c", "a/c", False),
    ("a?c", "abbc", False),
    # character classes
    ("file.[ch]", "file.c", True),
    ("file.[ch]", "file.h", True),
    ("file.[ch]", "file.o", False),
    ("file.[!ch]", "file.o", True),
    ("file.[!ch]", "file.c", False),
    # ** zero segments
    ("a/**/b", "a/b", True),
    ("a/**/b", "a/x/b", True),
    ("a/**/b", "a/x/y/z/b", True),
    ("a/**/b", "a/xb", False),
    # trailing /** requires at least one segment under the prefix
    ("apps/quoting/**", "apps/quoting/page.tsx", True),
    ("apps/quoting/**", "apps/quoting/src/deep/mod.ts", True),
    ("apps/quoting/**", "apps/quoting", False),
    ("apps/quoting/**", "apps/quoting-evil/page.tsx", False),
    # leading **
    ("**/pricing.ts", "pricing.ts", True),
    ("**/pricing.ts", "a/b/pricing.ts", True),
    ("**/pricing.ts", "a/b/notpricing.ts", False),
    # bare **
    ("**", "anything/at/all.py", True),
    ("**", "top.py", True),
    # the issue's sketch patterns
    ("platform/api/**/pricing/**", "platform/api/pricing/rate.py", True),
    ("platform/api/**/pricing/**", "platform/api/v2/pricing/rate.py", True),
    ("platform/api/**/pricing/**", "platform/api/v2/billing/rate.py", False),
    ("apps/quoting/**/pricing*/**", "apps/quoting/src/pricing-engine/calc.ts", True),
    ("apps/quoting/**/pricing*/**", "apps/quoting/pricing/calc.ts", True),
    ("apps/quoting/**/pricing*/**", "apps/quoting/src/quotes/calc.ts", False),
    # consecutive ** collapse
    ("a/**/**/b", "a/b", True),
    ("a/**/**/b", "a/x/b", True),
    # a class matches one character of one segment, never the '/' between two
    ("a[!x]b", "a/b", False),
    ("a[!x]b", "ayb", True),
    # [[] is a literal '[', which is how a Next.js route directory is named
    ("app/[[]id]/**", "app/[id]/page.tsx", True),
    ("app/[[]id]/**", "app/i/page.tsx", False),
    ("a[&&]b", "a&b", True),
    # a trailing ** takes the rest of the path, whatever it holds
    ("a/**", "a/x/", True),
    ("a/**", "a/", False),
    ("**", "/x", True),
]


@pytest.mark.parametrize(("pattern", "path", "expected"), MATCH_CASES)
def test_match(pattern: str, path: str, expected: bool) -> None:
    assert match_any(path, [compile_glob(pattern)]) is expected, f"{pattern!r} vs {path!r}"


INVALID_PATTERNS = [
    "",
    "/abs/path",
    "a//b",
    "a/../b",
    "a/**x/b",
    "x**/b",
    "a/[bc",
    "a/[b/c]",
]


@pytest.mark.parametrize("pattern", INVALID_PATTERNS)
def test_invalid_patterns_raise(pattern: str) -> None:
    with pytest.raises(GlobPatternError):
        compile_glob(pattern)


def test_match_any() -> None:
    compiled = [compile_glob("a/**"), compile_glob("b/*.py")]
    assert match_any("a/x/y.ts", compiled)
    assert match_any("b/m.py", compiled)
    assert not match_any("c/m.py", compiled)
    assert not match_any("b/sub/m.py", compiled)


def test_non_string_pattern_raises() -> None:
    with pytest.raises(GlobPatternError):
        compile_glob(None)  # type: ignore[arg-type]


def _reference(pattern: str) -> re.Pattern[str]:
    """The regex the matcher was before it matched in bounded time, a class kept off '/': what it must still mean."""
    segments: list[str] = []
    for seg in re.sub(r"(^|/)\*\*(/\*\*)+(?=/|$)", r"\1**", pattern).split("/"):
        if seg == "**":
            segments.append(seg)
            continue
        out = ""
        for token in re.findall(r"\[!?[^]]*\]|.", seg):
            if token == "*":
                out += "[^/]*"
            elif token == "?":
                out += "[^/]"
            elif token.startswith("[") and len(token) > 1:
                out += "(?!/)[" + ("^" + token[2:-1] if token[1] == "!" else token[1:-1]) + "]"
            else:
                out += re.escape(token)
        segments.append(out)
    parts = [
        (".+" if i == len(segments) - 1 else "(?:[^/]+/)*")
        if seg == "**"
        else seg + ("" if i == len(segments) - 1 else "/")
        for i, seg in enumerate(segments)
    ]
    return re.compile(r"\A" + "".join(parts) + r"\Z")


def test_the_matcher_means_what_the_regex_it_replaced_meant() -> None:
    """Random globs against random paths: the same answer as the old regex, on inputs small enough for it."""
    rng = random.Random(458)
    pieces = ["a", "b", "ab", "*", "?", "a*", "*b", "[ab]", "[!a]", "**"]
    names = ["a", "b", "ab", "ba", "aab", "", "x"]
    checked = 0
    for _ in range(3000):
        pattern = "/".join(rng.choice(pieces) for _ in range(rng.randint(1, 4)))
        try:
            glob = compile_glob(pattern)
        except GlobPatternError:
            continue
        reference = _reference(pattern)
        for _ in range(8):
            path = "/".join(rng.choice(names) for _ in range(rng.randint(1, 5)))
            assert match_any(path, [glob]) is bool(reference.match(path)), f"{pattern!r} vs {path!r}"
            checked += 1
    assert checked > 10000, f"only {checked} pairs were compared"


#: Globs a regex matches by backtracking, and a path each fails on after trying every way: seconds, or hours.
PATHOLOGICAL = [
    ("**/*/" * 14 + "a", "/".join(["x"] * 28) + "/b"),
    ("**/*/" * 40 + "a", "/".join(["x"] * 200) + "/b"),
    ("*a" * 12 + "b", "a" * 400),
    ("**/" + "*a" * 12 + "b/**", "/".join(["a" * 60] * 30)),
]


@pytest.mark.parametrize(
    ("pattern", "path"),
    PATHOLOGICAL,
    ids=["14 **-and-* pairs, 28 directories", "40 pairs, 200 directories", "12 stars, one name", "12 stars under **"],
)
def test_a_glob_is_matched_in_bounded_time(pattern: str, path: str) -> None:
    """Measured against a pass of re over the path, on this thread's CPU clock (tests/fixtures/cpu_cost.py), not
    with the wall clock, which times the host. Each run compiles the glob afresh, untimed."""
    assert match_any(path, [compile_glob(pattern)]) is False

    def fresh() -> Callable[[], object]:
        glob = compile_glob(pattern)
        return lambda: match_any(path, [glob])

    measured = cost(fresh, [path])
    assert measured < GLOB_COST, (
        f"matching {pattern[:24]!r}... against a {len(path)}-character path cost {measured:.0f} passes of re over "
        f"it, at most {GLOB_COST:.0f}"
    )


#: How many passes of re over the path a match may cost: the cases above measure at most about 600 (40 pairs against
#: 200 directories, the matcher's states walked in Python); a regex translation that backtracks, as the one the meter
#: had, 45,000 at 11 pairs against 22 directories, doubling with each pair (:func:`test_the_bound_tells_a_regex_that_backtracks`).
GLOB_COST = 6000.0


def test_the_bound_tells_a_regex_that_backtracks() -> None:
    regex = re.compile("(?:[^/]+/)*[^/]*/" * 11 + "a\\Z")
    path = "/".join(["x"] * 22) + "/b"
    measured = cost(same(lambda: regex.match(path)), [path], runs=1)
    assert measured > GLOB_COST, f"a regex that backtracks cost {measured:.0f} passes of re, within the bound"


@pytest.mark.parametrize("pattern", ["app/[[]id]/**", "a[&&]b", "a[~~]b", "a[||]b", "a[x[]b"])
def test_a_class_naming_a_bracket_or_a_doubled_operator_compiles_without_a_warning(pattern: str) -> None:
    """Python warns that ``[[`` and a doubled ``&``, ``~`` or ``|`` in a class may change meaning in a later
    release: the matcher escapes them, so ``[[]`` stays a literal ``[``."""
    re.purge()  # a class compiled before, by another test, would warn no more
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile_glob(pattern)
