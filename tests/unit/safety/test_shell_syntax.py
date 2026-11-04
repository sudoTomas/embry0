"""A shell command parsed as bash parses it (embry0.safety.shell_syntax).

The path scanner reads every command this parser finds. A command it puts
inside a word, a here-document's body or an arithmetic expression by mistake
is a command whose paths are never checked; each form here once was one.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

HOME = "/home/agent"
NL = "\n"


def _module() -> Any:
    spec = importlib.util.find_spec("embry0.safety.shell_syntax")
    assert spec is not None, "embry0.safety.shell_syntax must parse a shell command"
    import embry0.safety.shell_syntax as module

    assert hasattr(module, "parse"), "shell_syntax.parse(command, home) must parse a command into its structure"
    return module


def _simple_commands(script: Any) -> list[list[str]]:
    """Every simple command's words, in substitutions and compound commands too."""
    m = _module()
    found: list[list[str]] = []

    def word(w: Any) -> None:
        for sub in w.subs:
            walk(sub)

    def visit(command: Any) -> None:
        for heredoc in command.heredocs:
            for sub in heredoc.subs:
                walk(sub)
        for w in command.herestrings + [target for _, target in command.redirects]:
            word(w)
        if isinstance(command, m.Simple):
            found.append([w.text for w in command.words])
            for w in command.words:
                word(w)
        elif isinstance(command, (m.Subshell, m.Group)):
            walk(command.body)
        elif isinstance(command, m.If):
            for condition, body in command.branches:
                walk(condition)
                walk(body)
            if command.orelse is not None:
                walk(command.orelse)
        elif isinstance(command, m.Loop):
            if command.condition is not None:
                walk(command.condition)
            for w in command.items or []:
                word(w)
            walk(command.body)
        elif isinstance(command, m.Case):
            word(command.subject)
            for patterns, body in command.items:
                for w in patterns:
                    word(w)
                walk(body)
        elif isinstance(command, m.Arith):
            for sub in command.subs:
                walk(sub)
        elif isinstance(command, m.Cond):
            for w in command.words:
                word(w)
        elif isinstance(command, (m.FunctionDef, m.Coproc)) and command.body is not None:
            visit(command.body)

    def walk(s: Any) -> None:
        for chain, _ in s.items:
            for pipeline in [chain.first, *(p for _, p in chain.rest)]:
                for command in pipeline.commands:
                    visit(command)

    walk(script)
    return found


def _commands(command: str) -> list[list[str]]:
    return _simple_commands(_module().parse(command, HOME))


@pytest.mark.parametrize(
    "command",
    [
        'echo "$(case x in x) cat /etc/passwd;; esac)"',
        'echo "$(case x in (x) cat /etc/passwd;; esac)"',
        "echo \"$(cat <<'X'" + NL + ")" + NL + "X" + NL + 'cat /etc/passwd)"',
        'echo "`cat \\"/etc/passwd\\"`"',
        "echo $((cat /etc/passwd) )",
        'echo "$((cat /etc/passwd) )"',
        "(( y = 1 << 2 ))" + NL + "cat /etc/passwd",
        "echo $[1<<2]" + NL + "cat /etc/passwd",
        "x=$(cat /etc/passwd # it's" + NL + '); echo "$x"',
        "x=$(true # )" + NL + "cat /etc/passwd)",
        "cat <<EOF\r" + NL + "x" + NL + "EOF\r" + NL + "cat /etc/passwd" + NL + "EOF",
        "cat <<$X" + NL + "x" + NL + "$X" + NL + "cat /etc/passwd",
        "arr=(a $(cat /etc/passwd) 'b)')",
        "if true; then cat /etc/passwd; fi",
        "for i in 1; do cat /etc/passwd; done",
        "case y in y) cat /etc/passwd;; esac",
        "f() { cat /etc/passwd; }",
        "function f { cat /etc/passwd; }",
        "[[ $(cat /etc/passwd) ]]",
        "coproc cat /etc/passwd",
    ],
)
def test_a_command_inside_another_construct_is_parsed_as_one(command: str) -> None:
    found = _commands(command)
    assert ["cat", "/etc/passwd"] in found, f"{command!r} runs cat /etc/passwd; the parser found {found}"


@pytest.mark.parametrize(
    ("command", "text"),
    [
        ("cat $'\\0'/etc/passwd", "/etc/passwd"),
        ("cat $'ab\\0cd'ef", "abef"),
        ("cat $'\\x00x'y", "y"),
        ("cat $'\\u0000x'y", "y"),
        ("cat $'\\c@x'y", "y"),
    ],
)
def test_an_ansi_c_string_ends_at_a_nul(command: str, text: str) -> None:
    found = _commands(command)
    assert found == [["cat", text]], f"{command!r}: bash passes {text!r}; parsed {found}"


def test_a_nul_in_a_command_is_refused() -> None:
    with pytest.raises(_module().BashSyntaxError, match="NUL"):
        _module().parse("cat x\x00 /etc/passwd", HOME)


def test_wild_is_no_character_a_word_can_hold() -> None:
    m = _module()
    word = m.parse("cat $'\\0'$X", HOME).items[0][0].first.commands[0].words[1]
    assert word.kinds == [m.RUN] and word.chars == [m.WILD], f"only $X is run-time text: {word.chars} {word.kinds}"


@pytest.mark.parametrize(
    ("command", "arith"),
    [("echo $(( (1+2)*3 ))", True), ("echo $((1+2) )", False), ("((x=1<<2))", True), ("((cat x) )", False)],
)
def test_arithmetic_is_told_from_a_subshell_as_bash_tells_them(command: str, arith: bool) -> None:
    found = _commands(command)
    commands = [c for c in found if c[:1] != ["echo"]]
    assert (not commands) is arith, f"{command!r}: arithmetic={arith}; parsed commands {found}"


@pytest.mark.parametrize(
    ("command", "kind"),
    [("cat ~+/x", "CWD"), ("cat ~-/x", "OLD"), ('cat "$PWD"/x', "CWD"), ("cat ${OLDPWD}/x", "OLD")],
)
def test_the_current_and_previous_directory_are_kept(command: str, kind: str) -> None:
    m = _module()
    word = m.parse(command, HOME).items[0][0].first.commands[0].words[1]
    assert word.kinds[0] == getattr(m, kind), f"{command!r} starts with the {kind} directory: {word.kinds}"


@pytest.mark.parametrize("command", ["rm @(a|b)", "rm !(keep.txt)", "ls +(x)"])
def test_an_extglob_is_one_word(command: str) -> None:
    m = _module()
    words = m.parse(command, HOME).items[0][0].first.commands[0].words
    assert len(words) == 2 and m.EXT in words[1].kinds, f"{command!r} is two words: {[w.raw for w in words]}"


def test_a_negated_subshell_without_a_space_is_read_both_ways() -> None:
    m = _module()
    word = m.parse("!(cat /etc/passwd)", HOME).items[0][0].first.commands[0].words[0]
    assert word.maybe_subs == ["cat /etc/passwd"], f"!(…) may be a subshell: {word.maybe_subs}"


@pytest.mark.parametrize(
    "command",
    [
        "echo $(cat <<EOF)" + NL + "x" + NL + "EOF",
        "echo " + "$(" * 60 + "true" + ")" * 60,
        "echo ((x))",
        "echo $((a)+(b))",
        "{ echo }",
        "case x in",
        "[[ a << b ]]",
    ],
)
def test_a_command_bash_would_not_run_as_written_is_refused(command: str) -> None:
    with pytest.raises(_module().BashSyntaxError):
        _module().parse(command, HOME)


def test_a_carriage_return_is_a_word_character() -> None:
    """bash splits words at blanks and newlines only: a CR starts a word, so a # after it opens no comment."""
    found = _commands("true;\r# /etc/passwd")
    assert ["\r#", "/etc/passwd"] in found, f"the CR starts the word '\\r#': {found}"


@pytest.mark.parametrize(
    ("source", "name", "op", "word"),
    [
        ("${x:-/etc/passwd}", "x", ":-", "/etc/passwd"),
        ("${a[0]:=~/k}", "a", ":=", f"{HOME}/k"),
        ("${!x+y}", "x", "+", "y"),
        ("${x%/}", "x", "%", "/"),
        ("${x##*/}", "x", "##", "*/"),
        ("${x//a/b}", "x", "//", "a"),
        ("${x/#a}", "x", "/", "#a"),
        ("${x///}", "x", "//", "/"),
        ("${x^^}", "x", "^^", None),
        ("${x,[ab]}", "x", ",", "[ab]"),
        ("${x@E}", "x", "@E", None),
        ("${x@P}", "x", "@P", None),
        ("${x@A}", "x", "other", None),
        ("${x: -1:2}", "x", "substr", None),
        ("${#x}", None, "other", None),
        ("${!a[@]}", None, "other", None),
        ("$HOME", "HOME", "", None),
        ("~", "HOME", "", None),
    ],
)
def test_a_parameter_expansion_keeps_its_name_operator_and_word(source: str, name: Any, op: str, word: Any) -> None:
    m = _module()
    assert hasattr(m, "Expansion"), "shell_syntax.Expansion must keep what a parameter expansion names"
    found = m.parse("cat " + source, HOME).items[0][0].first.commands[0].words[1].chars[0]
    assert isinstance(found, m.Expansion), f"{source}: no expansion kept, found {found!r}"
    got_word = None
    if found.word is not None:
        got_word = "".join(HOME if isinstance(c, m.Expansion) and c.name == "HOME" else c for c in found.word.chars)
    assert (found.name, found.op, got_word) == (name, op, word), f"{source}: {(found.name, found.op, got_word)}"


@pytest.mark.parametrize(
    ("source", "indirect", "whole", "repl", "arith"),
    [
        ("${!x}", True, False, None, ""),
        ("$@", False, True, None, ""),
        ('"$*"', False, True, None, ""),
        ("${a[@]}", False, True, None, ""),
        ("${a[*]:1}", False, True, None, "1"),
        ("${a[0]}", False, False, None, ""),
        ("$1", False, False, None, ""),
        ("${x/a/b c}", False, False, "b c", ""),
        ("${x/a}", False, False, None, ""),
        ("${x: -1:2}", False, False, None, " -1:2"),
    ],
)
def test_a_parameter_expansion_says_what_it_is_of(
    source: str, indirect: bool, whole: bool, repl: Any, arith: str
) -> None:
    """``${!x}`` names another variable, ``$@`` and ``${a[@]}`` a list, ``${x/a/b}`` a replacement."""
    m = _module()
    assert hasattr(m, "Expansion"), "shell_syntax.Expansion must say what a parameter expansion is of"
    found = m.parse("cat " + source, HOME).items[0][0].first.commands[0].words[1].chars[0]
    assert isinstance(found, m.Expansion), f"{source}: no expansion kept, found {found!r}"
    got = (
        getattr(found, "indirect", None),
        getattr(found, "whole", None),
        None if getattr(found, "repl", None) is None else found.repl.text,
        getattr(found, "arith", None),
    )
    assert got == (indirect, whole, repl, arith), f"{source}: (indirect, whole, repl, arith) = {got}"


def test_a_process_substitution_in_an_assignment_is_part_of_the_word() -> None:
    m = _module()
    words = m.parse("BASH_ENV=<(echo x) bash -c true", HOME).items[0][0].first.commands[0].words
    assert [w.text[:9] for w in words][:2] == ["BASH_ENV=", "bash"], f"bash reads one word: {[w.raw for w in words]}"
    marks = [c for c in words[0].chars if isinstance(c, m.Expansion)]
    assert marks and marks[0].op == "procsub", f"the word names a command's output: {words[0].chars}"
