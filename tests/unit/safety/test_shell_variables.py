"""The values a command's variables can hold (embry0.safety.shell_variables).

An expansion stands for every value its variable can hold, the value it held
before the command, and its default word. Past a cap the values are folded into
one that can only be wider: their common start, then anything.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

HOME = "/home/agent"


def _module() -> Any:
    spec = importlib.util.find_spec("embry0.safety.shell_variables")
    assert spec is not None, "embry0.safety.shell_variables must say what values a command's variables can hold"
    import embry0.safety.shell_variables as module

    return module


def _syntax() -> Any:
    import embry0.safety.shell_syntax as syntax

    return syntax


def _value(text: str) -> Any:
    return tuple((c, _syntax().LIT) for c in text)


def _texts(variables: Any, source: str) -> list[str]:
    syntax = _syntax()
    word = syntax.parse("x " + source, HOME).items[0][0].first.commands[0].words[1]
    out = []
    for chars, kinds in variables.expand(word.chars, word.kinds):
        out.append(
            "".join("…" if k == syntax.RUN else ("<cwd>" if k == syntax.CWD else c) for c, k in zip(chars, kinds))
        )
    return sorted(out)


@pytest.mark.parametrize(
    ("source", "values"),
    [
        ("${x:-/etc/passwd}", ["/etc/passwd", "…"]),
        ("${x-/etc/passwd}", ["/etc/passwd", "…"]),
        ("${x:=/etc/passwd}", ["/etc/passwd", "…"]),
        ("${x:+/etc/passwd}", ["", "/etc/passwd"]),
        ("${x:?/etc/passwd}", ["/etc/passwd", "…"]),
        ("${x%/}", ["…"]),
        ("$x", ["…"]),
        ("~/a", [f"{HOME}/a"]),
        ("${HOME:-/x}", ["/x", HOME]),
        ("$PWD/a", ["<cwd>/a"]),
    ],
)
def test_an_expansion_stands_for_each_value_it_can_have(source: str, values: list[str]) -> None:
    variables = _module().Variables(HOME, {})
    assert _texts(variables, source) == sorted(values), f"{source}: {_texts(variables, source)}"


def test_an_assigned_value_is_one_the_variable_can_hold() -> None:
    m = _module()
    variables = m.Variables(HOME, {"x": frozenset({_value("/etc/passwd")}), "HOME": frozenset({_value("/etc")})})
    assert _texts(variables, "$x") == ["/etc/passwd", "…"], _texts(variables, "$x")
    assert _texts(variables, "~/p") == sorted(["/etc/p", f"{HOME}/p"]), _texts(variables, "~/p")


def test_home_from_a_run_time_value_can_be_anywhere() -> None:
    m = _module()
    variables = m.Variables(HOME, {"HOME": frozenset({m.UNKNOWN})})
    assert "/…/p" in _texts(variables, "~/p"), _texts(variables, "~/p")


@pytest.mark.parametrize(
    ("values", "folded"),
    [
        ([f"/srv/app/f{i}" for i in range(20)], "/srv/app/f…"),
        ([f"src/m{i}.py" for i in range(20)], "./src/m…"),
        ([*(f"src/m{i}.py" for i in range(20)), "../../etc/passwd"], "/…"),
        ([*(f"/srv/f{i}" for i in range(20)), "rel"], "/…"),
    ],
)
def test_too_many_values_fold_into_a_wider_one(values: list[str], folded: str) -> None:
    m = _module()
    variables = m.Variables(HOME, {})
    variables.assign("x", [_value(v) for v in values])
    got = variables.collected()["x"]
    texts = {"".join("…" if k == _syntax().RUN else c for c, k in v) for v in got}
    assert texts == {folded}, f"{len(values)} values fold into {texts}, want {folded}"


def _words(variables: Any, source: str, *, code: bool = False) -> list[list[str]]:
    """Each way ``source`` expands, as the words it splits into (run-time text as …)."""
    syntax = _syntax()
    word = syntax.parse("x " + source, HOME).items[0][0].first.commands[0].words[1]
    out = []
    for split in variables.expand_split(word.chars, word.kinds, code=code):
        out.append(["".join("…" if k == syntax.RUN else c for c, k in zip(chars, kinds)) for chars, kinds in split])
    return out


def test_ifs_splits_an_unquoted_value_at_its_characters() -> None:
    m = _module()
    variables = m.Variables(HOME, {"x": frozenset({_value("cat,/etc/passwd")}), "IFS": frozenset({_value(",")})})
    ways = _words(variables, "$x")
    assert ["cat", "/etc/passwd"] in ways, f"with IFS=, the value splits at the comma: {ways}"
    assert ["cat,/etc/passwd"] in ways, f"before IFS is assigned it does not: {ways}"
    assert _words(variables, '"$x"') == [["…"], ["cat,/etc/passwd"]], "a quoted value is not split"


def test_ifs_does_not_split_the_words_text_itself() -> None:
    m = _module()
    variables = m.Variables(HOME, {"IFS": frozenset({_value(",")}), "x": frozenset({_value("c,d")})})
    assert _words(variables, "a,b") == [["a,b"]], "bash splits only what an expansion put in the word"
    ways = _words(variables, "a,b$x")
    assert ["a,bc", "d"] in ways and ["a", "bc", "d"] not in ways, (
        f"only the value's comma splits, not the word's own: {ways}"
    )


def test_a_list_keeps_its_order_and_an_element_stands_alone() -> None:
    m = _module()
    variables = m.Variables(
        HOME, {m.list_key("@"): frozenset({_value("cd" + m.SEP + "/")}), "@": frozenset({_value("cd"), _value("/")})}
    )
    ways = _words(variables, '"$@"')
    assert sorted(ways) == sorted([["…"], ["cd", "/"]]), f'"$@" is the list in order, or one not known: {ways}'
    assert sorted(w[0] for w in _words(variables, "$1")) == sorted(["…", "cd", "/"]), "$1 is any element"


def test_a_list_whose_order_is_lost_is_refused_as_a_command() -> None:
    m = _module()
    variables = m.Variables(
        HOME, {m.list_key("a"): frozenset({_value("x" + m.SEP + "/")})}, frozenset({m.list_key("a")})
    )
    with pytest.raises(_syntax().BashSyntaxError):
        _words(variables, '"${a[@]}"', code=True)
    assert _words(variables, '"${a[@]}"'), "as an argument it still expands"


def test_a_nameref_stands_for_the_variable_it_names() -> None:
    m = _module()
    variables = m.Variables(HOME, {"r\x00ref": frozenset({_value("y")}), "y": frozenset({_value("/etc/passwd")})})
    assert "/etc/passwd" in _texts(variables, "$r"), _texts(variables, "$r")
    variables.assign("r", [_value("/x")])
    assert variables.assigned.get("y") == {_value("/x")}, "assigning a nameref assigns the variable it names"


def test_an_indirect_expansion_stands_for_the_variable_its_value_names() -> None:
    m = _module()
    variables = m.Variables(HOME, {"x": frozenset({_value("y")}), "y": frozenset({_value("/etc/passwd")})})
    assert "/etc/passwd" in _texts(variables, "${!x}"), _texts(variables, "${!x}")
    assert "/etc/passwd" in _texts(variables, "${!x%X}"), "and is transformed as the variable would be"


@pytest.mark.parametrize(
    ("source", "value", "want"),
    [
        ("${x%X}", "/etc/passwdX", "/etc/passwd"),
        ("${x//X/e}", "/Xtc/passwd", "/etc/passwd"),
        ("${x,,}", "/ETC/PASSWD", "/etc/passwd"),
        ("${x:2}", "XX/etc/passwd", "/etc/passwd"),
        ("${x@E}", "\\x2fetc\\x2fpasswd", "/etc/passwd"),
        ("${x/pass/&wd}", "/etc/pass", "/etc/passwd"),
    ],
)
def test_a_transformation_of_a_literal_value_is_computed(source: str, value: str, want: str) -> None:
    variables = _module().Variables(HOME, {"x": frozenset({_value(value)})})
    assert want in _texts(variables, source), f"{source} of {value!r}: {_texts(variables, source)}"


def test_a_transformation_of_a_glob_or_a_run_time_value_is_not_known() -> None:
    m = _module()
    variables = m.Variables(HOME, {"f": frozenset({tuple((c, _syntax().ACT) for c in "*.py")})})
    assert _texts(variables, "${f%.py}") == ["…"], "the files a glob matches are only known at run time"


def test_a_value_longer_than_is_kept_can_be_anywhere() -> None:
    m = _module()
    variables = m.Variables(HOME, {})
    variables.assign("x", [_value("a" * (m.MAX_TEXT + 1))])
    assert variables.assigned["x"] == {m.ANYWHERE}, "a value past MAX_TEXT is kept as anywhere"
    assert "x" in variables.folding, "and is not run"
