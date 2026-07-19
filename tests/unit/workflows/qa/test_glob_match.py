"""Tests for the conditional-criteria glob matcher (EMB-39)."""

from __future__ import annotations

import pytest

from embry0.workflows.qa._glob_match import GlobPatternError, compile_glob, match_any

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
]


@pytest.mark.parametrize(("pattern", "path", "expected"), MATCH_CASES)
def test_match(pattern: str, path: str, expected: bool) -> None:
    rx = compile_glob(pattern)
    assert bool(rx.match(path)) is expected, f"{pattern!r} vs {path!r}"


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
