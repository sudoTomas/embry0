"""Parameter transformations and printf computed as bash 5.2 computes them (embry0.safety.shell_transforms).

Every expected value below is what bash 5.2.21 printed for the same input.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

HOME = "/home/agent"


def _module() -> Any:
    spec = importlib.util.find_spec("embry0.safety.shell_transforms")
    assert spec is not None, "embry0.safety.shell_transforms must compute parameter transformations"
    import embry0.safety.shell_transforms as module

    return module


def _work_module() -> Any:
    spec = importlib.util.find_spec("embry0.safety.shell_work")
    assert spec is not None, "embry0.safety.shell_work must count the work reading a command costs, in fixed units"
    import embry0.safety.shell_work as module

    return module


def _glob(pattern: str) -> Any:
    """The automaton for a pattern written as in a command: quoted parts are literal."""
    from embry0.safety.shell_syntax import parse

    word = parse("x " + pattern, HOME).items[0][0].first.commands[0].words[1]
    return _module().pattern_glob(word.chars, word.kinds)


def _budget() -> Any:
    Work = _work_module().Work

    return Work()


@pytest.mark.parametrize(
    ("op", "text", "pattern", "want"),
    [
        ("#", "abcabc", "*b", "cabc"),
        ("##", "abcabc", "*b", "c"),
        ("%", "abcabc", "b*", "abca"),
        ("%%", "abcabc", "b*", "a"),
        ("#", "/etc/passwdX", "X", "/etc/passwdX"),
        ("%", "/etc/passwdX", "X", "/etc/passwd"),
        ("##", "a/b/c", "*/", "c"),
        ("%", "a/b/c", "/*", "a/b"),
        ("#", "*x", "'*'", "x"),
        ("#", "ab", "[[:alpha:]]", "b"),
        ("#", "ab", "[!a]", "ab"),
        ("%%", "x.tar.gz", ".*", "x"),
    ],
)
def test_a_pattern_is_removed_from_an_end_as_bash_removes_it(op: str, text: str, pattern: str, want: str) -> None:
    got = _module().removed(op, text, _glob(pattern), _budget())
    assert got == want, f"${{x{op}{pattern}}} of {text!r}: bash gives {want!r}, got {got!r}"


@pytest.mark.parametrize(
    ("op", "text", "pattern", "replacement", "want"),
    [
        ("/", "a-b_/b", "/?", "/", "a-b_/"),
        ("//", "/X/XbXb.", "/", "", "XXbXb."),
        ("//", "aXbXc", "X", "/", "a/b/c"),
        ("/", "aXbXc", "X", "/", "a/bXc"),
        ("/#", "ab", "''", "Z", "Zab"),
        ("/%", "ab", "''", "Z", "abZ"),
        ("/#", "Q/passwd", "Q", "/etc", "/etc/passwd"),
        ("/%", "/etc/passQ", "Q", "wd", "/etc/passwd"),
        ("//", "ab", "*", "Z", "Z"),
        ("//", "", "*", "Z", "Z"),
        ("/", "xab", "a*", "-", "x-"),
    ],
)
def test_a_pattern_is_replaced_as_bash_replaces_it(
    op: str, text: str, pattern: str, replacement: str, want: str
) -> None:
    got = _module().replaced(op, text, _glob(pattern), [replacement], _budget())
    assert got == want, f"{op} {pattern!r} -> {replacement!r} in {text!r}: bash gives {want!r}, got {got!r}"


def test_an_unquoted_ampersand_in_a_replacement_is_the_match() -> None:
    m = _module()
    got = m.replaced("/", "ab", _glob("a"), ["<", m.MATCH, ">"], _budget())
    assert got == "<a>b", f"bash 5.2 puts the match where & is: {got!r}"


@pytest.mark.parametrize(
    ("op", "text", "want"),
    [
        ("@U", "/etc/x", "/ETC/X"),
        ("@L", "/ETC/PASSWD", "/etc/passwd"),
        ("@u", "abc", "Abc"),
        ("@E", "\\x2fetc\\x2fpasswd", "/etc/passwd"),
        ("@E", "a\\0b", "a"),
        ("@Q", "a'b", "'a'\\''b'"),
        ("@Q", "'", "\\'"),
        ("@Q", "a\nb", "$'a\\nb'"),
    ],
)
def test_an_at_transformation_is_computed(op: str, text: str, want: str) -> None:
    got = _module().at_transformed(op, text)
    assert got == want, f"${{x{op}}} of {text!r}: bash gives {want!r}, got {got!r}"


@pytest.mark.parametrize(
    ("arith", "text", "want"),
    [
        ("2", "XX/etc/passwd", "/etc/passwd"),
        ("2:11", "XX/etc/passwdYY", "/etc/passwd"),
        ("2:-2", "XX/etc/passwdYY", "/etc/passwd"),
        (" -13:11", "/etc/passwdYY", "/etc/passwd"),
        ("(-2)", "abc", "bc"),
        ("10:-1", "abc", ""),
        ("1:-3", "abc", None),
        ("$n", "abc", None),
    ],
)
def test_a_substring_is_computed_from_literal_offsets(arith: str, text: str, want: str | None) -> None:
    got = _module().substring(text, arith)
    assert got == want, f"${{x:{arith}}} of {text!r}: bash gives {want!r}, got {got!r}"


@pytest.mark.parametrize(
    ("format_", "args", "want"),
    [
        ("%s/%s", ["/etc", "passwd"], "/etc/passwd"),
        ("%b", ["\\x2fetc\\x2fpasswd"], "/etc/passwd"),
        ("\\x2fetc\\x2fpasswd", [], "/etc/passwd"),
        ("\\c\\101", [], "\\cA"),
        ("%b", ["\\0101"], "A"),
        ("%b", ["a\\cb"], "a"),
        ("%.11s", ["/etc/passwdXYZ"], "/etc/passwd"),
        ("%c%s", ["/x", "etc/passwd"], "/etc/passwd"),
        ("x%sy", ["a", "b"], "xayxby"),
        ("%s%d", ["/etc/passwd", "x"], "/etc/passwd0"),
        (
            "%5.3d|%-5d|%05d|%+d|% d|%#o|%#x|%x|%u",
            ["7"] * 5 + ["8", "0", "-1", "-1"],
            "  007|7    |00007|+7| 7|010|0|ffffffffffffffff|18446744073709551615",
        ),
        ("%d|%d|%d|%x", ["12abc", "abc", "", "0x1zz"], "12|0|0|1"),
        ("a%zb", [], "a"),
        ("%c%s", [], ""),
        ("a%c/etc/passwd", [], "a"),
        ("%q", ["a b"], "a\\ b"),
        ("%*s", ["077", "x"], " " * 62 + "x"),
    ],
)
def test_printf_gives_the_value_bash_gives(format_: str, args: list[str], want: str) -> None:
    got = _module().printf(format_, args)
    assert got == want, f"printf -v v {format_!r} {args}: bash gives {want!r}, got {got!r}"


def test_a_date_in_printf_is_only_known_at_run_time() -> None:
    from embry0.safety.shell_syntax import WILD

    got = _module().printf("%(/etc/%Y)T", ["-1"])
    assert got == "/etc/" + WILD, f"%(…)T is its format, its directives only known at run time: {got!r}"


def test_a_printf_past_its_limit_is_not_computed() -> None:
    m = _module()
    with pytest.raises(m.Exhausted):
        m.printf("%99999999s", ["x"])


def test_matching_costs_steps_linear_in_the_text() -> None:
    """A pattern with many stars, which a backtracking matcher takes exponential time on, costs one
    automaton step per character and per scan: the steps are counted, not timed."""
    m = _module()
    glob = _glob("*a*a*a*a*a*a*a*a*a*a*b")
    text = "a" * 4096
    work = _budget()
    for op in ("#", "##", "%", "%%"):
        assert m.removed(op, text, glob, work) == text
    assert m.replaced("//", text, glob, ["x"], work) == text
    steps = work.spent["transform"]
    assert steps <= 5 * (len(text) + 1), f"five scans of a {len(text)}-character text took {steps} steps"
    # #, ## and // read the whole text (a star keeps the automaton alive; % and %% stop at once, the
    # reversed pattern starting with b): every step is counted.
    assert steps >= 3 * len(text), f"three whole scans of a {len(text)}-character text counted only {steps} steps"


def test_a_budget_bounds_the_steps_of_one_command() -> None:
    m = _module()
    Work = _work_module().Work

    work = Work({"transform": 10_000})
    with pytest.raises(m.Exhausted):
        for _ in range(100):
            m.replaced("//", "ab" * 200, _glob("a"), ["b"], work)
    assert work.spent["transform"] <= 10_000 + 401, work.spent


def test_a_pattern_past_its_length_limit_is_not_read() -> None:
    m = _module()
    with pytest.raises(m.Exhausted):
        _glob("?" * (m.MAX_PATTERN + 1))
