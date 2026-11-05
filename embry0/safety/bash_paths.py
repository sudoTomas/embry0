"""The paths a shell command names, sorted into reads and writes for the Ring-2 rules.

Claude Code refuses a Bash command that names a path its permission rules deny.
``evaluate_ring2_paths`` applies the same rules to a shell command from its
text, so they also hold in embry0's ``PreToolUse`` hook and in the executors
that have no CLI. This module finds the paths; the policy module matches them.

The command is parsed the way bash parses it (``embry0.safety.shell_syntax``)
and every part of it is read: substitutions, here-documents, subshells,
pipelines, ``if``/``case``/loops, functions (a function's body is read where it
is defined and again where it is called), aliases, ``trap`` commands (read at
the end, in every directory the command was in) and ``eval``.

**Where relative paths resolve.** A ``cd`` (or ``pushd``, ``popd``, a function
or an alias that runs one) moves the directory later relative paths resolve
against. The directory is followed as a set: ``cd X; cmd`` runs ``cmd`` in
``X`` or, when the ``cd`` failed, where it was; ``cd X && cmd`` only in ``X``.
A subshell, a pipeline and a command run by another program leave it where it
was. A ``cd`` to a directory only known at run time (a variable, a
substitution, a glob, ``-``, ``~-``, a ``CDPATH`` lookup, ``popd``) leaves it
unknown, and a relative path in an unknown directory can be any path: it is
read as ``…/path``, which every deny rule with a ``**`` reaches.

Every operand of a command names a path, except where it is only data: the
operands of ``echo``, ``printf``, ``basename``, ``dirname``, ``test`` and
``[``, the words of ``[[ … ]]`` and of a ``case``, the pattern of a
``grep``-like command or ``git grep`` (however it is given: ``-e``, ``-ePAT``,
``--regexp=PAT``), the script of ``sed`` and ``awk``, ``jq``'s filter, a
here-document's body, a here-string, a leading assignment's value and an
option word (``-x``, ``--opt=value``). Each path is

- a **write** when it is the target of an output redirection, an operand of a
  command that creates, changes or removes the files it names (``tee``,
  ``touch``, ``rm``, ...), the destination of ``cp``, ``mv``, ``install``,
  ``ln`` or ``rsync`` (and the file that lands in it), a file ``sed -i`` edits,
  ``dd``'s ``of=``, or a file ``find -delete`` removes;
- a **read** otherwise. A write is checked as a read too.

A command run by another is read as well: ``sh -c``/``bash -c`` (whatever
options come first), ``bash -s`` and a shell fed a here-document or a
here-string, ``eval``, ``trap``, ``find -exec`` (``{}`` is every file the
``find`` can reach; ``-execdir`` runs in a directory that is not known),
``xargs``, ``env`` (``-C`` too), ``nice``, ``nohup``, ``setsid``, ``stdbuf``,
``timeout``, ``flock``, ``script -c``, ``su -c``, ``sudo`` (``-D`` too),
``watch`` and the like, a script a shell reads from its standard input
(``. /dev/stdin <<<``, ``BASH_ENV=/dev/stdin``; one it would read from a
command's output, ``source <(…)``, is refused), and ``git -c`` (a setting's value can be a command:
``alias.x=!cmd``, ``core.pager``, ``core.sshCommand``). Through such a runner
the data rules above are not applied: every operand is a path. An operand of
any other command, or of git, that reads as a shell command (a shell, a
runner, a command that reads or writes files, or text with a ``;``, ``|``,
``>`` or a substitution in it) is read as a command too, in case the program
runs it.

A word with a glob names the paths the glob can match: within one segment,
never a hidden name unless the segment starts with a ``.``, as bash expands it
by default. A ``shopt`` that may change that (``dotglob``, ``globstar``,
``extglob``, ``nocaseglob``, ``globskipdots`` off), a ``GLOBIGNORE`` or a
``BASHOPTS`` anywhere in the command makes every glob segment match anything.
A variable stands for each literal value the command gives it, anywhere in it
(``embry0.safety.shell_variables``), for its default word (``${X:-word}``),
for a transformation of a literal value as bash computes it
(``embry0.safety.shell_transforms``), and for the value it held before, which
is only known at run time. An unquoted value is split at the blanks and at each
value ``IFS`` can hold; a list (``"$@"``, ``"${a[@]}"``) keeps its order, so
``set -- cd /; "$@"`` moves the shell.
A value only known at run time inside a word (``/workspace/$X.log``) can be
any text, ``/`` included. A brace expansion is listed up to 64 words; past that
a sequence (``{1..1000}``) is read as a glob and any other brace as a word that
can name any path. A word whose value starts with text only known at run time
names no path this module can know.

This reads the text, not what runs. A path built at run time (a command's
output, a value ``read`` from a file, arithmetic, a transformation of such a
value, and such a value holding ``..``), a program's own ``open()``, commands an
interpreter or a script file runs, the paths a program reads from its input
(``xargs``), and the files an archiver or a downloader writes are not seen:
Ring 2 does not bind the programs an agent runs, and Ring 1 is the boundary
for them. A command this module cannot read raises :class:`BashSyntaxError`,
which the policy treats as a refusal; so does one whose reading costs more than
a limit of work (embry0.safety.shell_work), counted in units no machine changes.
"""

from __future__ import annotations

import os
import pwd
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Final

from embry0.safety.brace_expansion import brace_alternatives
from embry0.safety.path_patterns import NOT_SLASH, AnyName, CharSet, Lit, One, Part, PathRef, Star, Wild
from embry0.safety.shell_commands import (
    ASSIGNMENT,
    CODE_HEADS,
    CODE_MARKS,
    DATA_COMMANDS,
    DECLARATIONS,
    FIND_COMMANDS,
    FIND_OR,
    FIND_READS,
    FIND_VALUES,
    FIND_WRITES,
    GIT_GREP,
    GIT_OTHER,
    GIT_WRITE,
    GIT_WRITES_PATHS,
    HARMLESS_OPTIONS,
    PATTERN_COMMANDS,
    RUNNERS,
    SHELLS,
    STARTUP_FILES,
    WRITE_ALL,
    WRITE_LAST,
    WRITE_TREE,
    Options,
    Runner,
    parse_options,
)
from embry0.safety.shell_syntax import (
    ACT,
    CWD,
    EXT,
    LIT,
    MAX_NESTING,
    OLD,
    RUN,
    WILD,
    AndOr,
    Arith,
    BashSyntaxError,
    Case,
    Command,
    Cond,
    Coproc,
    Expansion,
    FunctionDef,
    Group,
    If,
    Loop,
    Pipeline,
    Redirected,
    Script,
    Simple,
    Subshell,
    Word,
    parse,
    parse_prompt,
)
from embry0.safety.shell_transforms import printf
from embry0.safety.shell_variables import (
    ANYWHERE,
    MAX_TEXT,
    MAX_VALUES,
    MAX_WORDS,
    SEP,
    UNKNOWN,
    Value,
    Variables,
    generalized,
)
from embry0.safety.shell_work import Exhausted, Work

__all__ = ["BashPaths", "BashSyntaxError", "PathRef", "bash_paths"]

_GLOB_CHARS: Final[frozenset[str]] = frozenset("*?[")
_MAX_PATHS: Final[int] = 20_000
_MAX_CWDS: Final[int] = 8
_MAX_SPECULATIVE: Final[int] = 4096

# The directories a command may be in at a point; None stands for one that is not known.
Cwds = frozenset[str | None]
_UNKNOWN: Final[Cwds] = frozenset({None})
_MAX_HEADS: Final[int] = 8
# Files that are a process's standard input, and ones that are another descriptor.
# The prompts bash expands (PS4 for set -x; the others in an interactive shell), and the command it runs
# before a prompt.
PROMPTS: Final[tuple[str, ...]] = ("PS0", "PS1", "PS2", "PS3", "PS4", "PROMPT_COMMAND")
# printf's arguments: a character of each kind but data as one printf passes through, and back.
_OPAQUE: Final[dict[int, str]] = {RUN: WILD, EXT: WILD, CWD: "\uf8fe", OLD: "\uf8ff"}
_KIND_OF: Final[dict[str, int]] = {WILD: RUN, "\uf8fe": CWD, "\uf8ff": OLD}
# A command string's text for a character of each kind but data: the shell's directories are read
# again as the variables that hold them, and text only known at run time as a variable no command sets.
_RUN_TIME: Final[str] = "${__embry0_run_time__}"
_TEXT_OF: Final[dict[int, str]] = {RUN: _RUN_TIME, EXT: _RUN_TIME, CWD: "${PWD}", OLD: "${OLDPWD}"}
# An environment variable that holds a function bash defines when it starts (bash 4.3's export -f).
_EXPORTED_FUNCTION: Final[re.Pattern[str]] = re.compile(r"BASH_FUNC_[^=]*(%%|\(\))=")
# An array element with its own index: [2]=x, [k]+=x.
_INDEXED: Final[re.Pattern[str]] = re.compile(r"\[[^]]*\]\+?=")
_STDIN: Final[re.Pattern[str]] = re.compile(r"/dev/stdin|/dev/fd/0|/proc/(?:self|thread-self|\d+)/fd/0")
_FD: Final[re.Pattern[str]] = re.compile(r"/dev/fd/\d+|/proc/(?:self|thread-self|\d+)/fd/\d+")


@dataclass(frozen=True)
class BashPaths:
    """The paths of one shell command."""

    reads: tuple[PathRef, ...] = ()
    writes: tuple[PathRef, ...] = ()
    commands: tuple[str, ...] = ()  # every command word it runs, at any depth (a name, or a path to one)


@dataclass(frozen=True)
class _Mode:
    wide_globs: bool = False  # a glob option may have changed: a glob segment matches anything
    cdpath: bool = False  # CDPATH may be set: a relative cd may land anywhere


_MAX_ROUNDS: Final[int] = 6


def bash_paths(command: str, *, cwd: str, home: str, work: Work | None = None) -> BashPaths:
    """The paths ``command`` reads and writes, run in ``cwd`` by a user whose home is ``home``.

    ``work`` counts what reading it costs, in every round (embry0.safety.shell_work);
    past a limit the command is refused (``BashSyntaxError``).
    """
    script = parse(command, home)
    work = work if work is not None else Work()
    try:
        return _read(script, cwd=cwd, home=home, work=work)
    except Exhausted as exc:
        if exc.kind == "parsed":
            raise BashSyntaxError("the command runs more text than embry0 checks") from None
        raise BashSyntaxError("the command is too complex to check") from None


def _read(script: Script, *, cwd: str, home: str, work: Work) -> BashPaths:
    mode = _Mode()
    values: dict[str, frozenset[Value]] = {}
    folded: frozenset[str] = frozenset()
    parsed: dict[str, Script] = {}  # run text read in one round is read alike in the next
    widened: frozenset[str] = frozenset()  # variables still growing a round before the last
    for round_ in range(_MAX_ROUNDS + 1):
        scanner = _Scanner(home, mode, values, folded, work, parsed)
        scanner.run(script, cwd)
        found, found_values = scanner.found_mode(), scanner.vars.collected()
        found_values = {name: _widened(name, vals) if name in widened else vals for name, vals in found_values.items()}
        found_folded = folded | scanner.vars.folding
        if found == mode and found_values == values and found_folded == folded:
            return BashPaths(
                reads=tuple(scanner.reads),
                writes=tuple(scanner.writes),
                commands=tuple(dict.fromkeys(scanner.commands)),
            )
        if round_ == _MAX_ROUNDS:
            # A value this round read was still to change, so a path the command names may not be known yet.
            raise BashSyntaxError("the command passes values through more steps than embry0 follows")
        # A setting or a value seen anywhere applies to the whole command: read it again with it.
        if round_ == _MAX_ROUNDS - 1:
            # Values still growing (X=$X/a in a loop, PATH=/x:$PATH read again each round) are each
            # read as one wider value from now on, so the last round can settle.
            widened = frozenset(name for name, vals in found_values.items() if vals != values.get(name))
            found_values = {
                name: _widened(name, vals) if name in widened else vals for name, vals in found_values.items()
            }
            found_folded |= widened
        mode, values, folded = found, found_values, found_folded
    raise AssertionError("unreachable")


def _widened(name: str, values: frozenset[Value]) -> frozenset[Value]:
    """A growing variable's values as one that stands for them all and any longer one: their common
    start, then anything. A list, or what the store keeps beside a variable, can then be anywhere."""
    if "\x00" in name or name.endswith("[@]"):
        return values | {ANYWHERE}
    return frozenset({generalized(set(values))})


# ---- scanning ------------------------------------------------------------------


@dataclass(frozen=True)
class _Outcome:
    """The directories a command may leave the shell in when it succeeds, and when it fails."""

    ok: Cwds
    fail: Cwds

    @property
    def either(self) -> Cwds:
        return self.ok | self.fail


def _cap(cwds: Iterable[str | None]) -> Cwds:
    found = frozenset(cwds)
    if not found:
        return _UNKNOWN
    if len(found) > _MAX_CWDS:
        return _UNKNOWN
    return found


def _same(cwds: Cwds) -> _Outcome:
    return _Outcome(cwds, cwds)


class _Scanner:
    def __init__(
        self,
        home: str,
        mode: _Mode,
        values: dict[str, frozenset[Value]] | None = None,
        folded: frozenset[str] = frozenset(),
        work: Work | None = None,
        parsed: dict[str, Script] | None = None,
    ) -> None:
        self.home = home
        self.mode = mode
        self.work = work if work is not None else Work()
        self.parsed = parsed if parsed is not None else {}
        self.vars = Variables(home, values or {}, folded, self.work)
        self.reads: list[PathRef] = []
        self.writes: list[PathRef] = []
        self.fed: list[str] = []  # what the compound command being read is fed (a here-string, a here-document)
        self.commands: list[str] = []
        self._recorded: set[tuple[PathRef, bool]] = set()
        self.functions: dict[str, Command] = {}
        self.aliases: dict[str, str] = {}
        self.expanding: set[str] = set()
        self.traps: list[Script] = []
        self.seen: set[str | None] = set()
        self.calling: set[str] = set()
        self.recursed: set[str] = set()
        self.unknown_done: set[tuple[str, Cwds]] = set()
        self.found: list[PathRef] | None = None  # what find's {} stands for, in a find -exec command
        self.running = 0  # commands that run commands, nested
        self.wide_seen = False
        self.cdpath_seen = False

    def found_mode(self) -> _Mode:
        return _Mode(
            wide_globs=self.mode.wide_globs or self.wide_seen,
            cdpath=self.mode.cdpath or self.cdpath_seen,
        )

    def run(self, script: Script, cwd: str) -> None:
        self.script(script, frozenset({_normalize(cwd)}))
        # A prompt the command gives a value (PS4, printed by set -x before each command) is expanded,
        # and its command substitutions run, in any directory the command was in.
        everywhere = frozenset(self.seen) or frozenset({_normalize(cwd)})
        for name in PROMPTS:
            for value in self.vars.values(name):
                text = "".join(_TEXT_OF.get(k, c) for c, k in value)
                if name == "PROMPT_COMMAND":
                    self.script(self.reparse(text), everywhere)
                else:
                    self.prompt(text, everywhere)
        # bash calls command_not_found_handle for a command it cannot find: anywhere the command was.
        if "command_not_found_handle" in self.functions:
            self.call_function("command_not_found_handle", everywhere)
        # A trap runs when its signal comes or the shell exits: in any directory the command was in.
        while self.traps:
            traps, self.traps = self.traps, []
            everywhere = frozenset(self.seen)
            for trap in traps:
                self.script(trap, everywhere)

    def tick(self) -> None:
        self.work.spend("commands")

    # -- structure --------------------------------------------------------------

    def script(self, script: Script, cwds: Cwds) -> _Outcome:
        last = _same(cwds)
        current = cwds
        for chain, background in script.items:
            if background:
                self.and_or(chain, current)  # it runs in a subshell: the directory stays
                last = _same(current)
                continue
            last = self.and_or(chain, current)
            current = _cap(last.either)
            self.seen |= current
        return last

    def and_or(self, chain: AndOr, cwds: Cwds) -> _Outcome:
        out = self.pipeline(chain.first, cwds)
        for op, pipeline in chain.rest:
            if op == "&&":
                after = self.pipeline(pipeline, out.ok)
                out = _Outcome(after.ok, _cap(out.fail | after.fail))
            else:
                after = self.pipeline(pipeline, out.fail)
                out = _Outcome(_cap(out.ok | after.ok), after.fail)
        return out

    def pipeline(self, pipeline: Pipeline, cwds: Cwds) -> _Outcome:
        if len(pipeline.commands) == 1:
            out = self.command(pipeline.commands[0], cwds)
        else:
            for command in pipeline.commands[:-1]:
                self.command(command, cwds)  # each runs in a subshell
            last = self.command(pipeline.commands[-1], cwds)
            # The last one runs in the shell itself when lastpipe is on.
            out = _Outcome(_cap(cwds | last.ok), _cap(cwds | last.fail))
        return _Outcome(out.fail, out.ok) if pipeline.negated else out

    def command(self, node: Command, cwds: Cwds) -> _Outcome:
        self.tick()
        self.seen |= cwds
        self.redirections(node, cwds)
        fed = [h.body for h in node.heredocs] + [t for w in node.herestrings for t in self.herestring(w)]
        if isinstance(node, Simple):
            for word in node.words:
                self.word_subs(word, cwds)
            # A command with no input of its own reads what a compound command around it is fed.
            return self.simple(node.words, cwds, stdin=fed or list(self.fed), via="shell")
        if not fed:
            return self.compound(node, cwds)
        outer, self.fed = self.fed, fed
        try:
            return self.compound(node, cwds)
        finally:
            self.fed = outer

    def compound(self, node: Command, cwds: Cwds) -> _Outcome:
        if isinstance(node, Subshell):
            self.script(node.body, cwds)
            return _same(cwds)
        if isinstance(node, Group):
            return self.script(node.body, cwds)
        if isinstance(node, If):
            return self.if_(node, cwds)
        if isinstance(node, Loop):
            return self.loop(node, cwds)
        if isinstance(node, Case):
            return self.case(node, cwds)
        if isinstance(node, Arith):
            for sub in node.subs:
                self.script(sub, cwds)
            return _same(cwds)
        if isinstance(node, Cond):
            for word in node.words:
                self.word_subs(word, cwds)
            return _same(cwds)
        if isinstance(node, FunctionDef):
            if node.body is not None:
                self.functions[node.name] = node.body
                # Read where it is defined too, for a call this module does not see.
                self.call_function(node.name, cwds)
            return _same(cwds)
        if isinstance(node, Coproc):
            if node.body is not None:
                self.command(node.body, cwds)
            return _same(cwds)
        raise BashSyntaxError(f"unknown command form {type(node).__name__}")

    def redirections(self, node: Redirected, cwds: Cwds) -> None:
        for word in node.herestrings + [t for _, t in node.redirects]:
            self.word_subs(word, cwds)
        for heredoc in node.heredocs:
            for sub in heredoc.subs:
                self.script(sub, cwds)
        for op, target in node.redirects:
            text = target.text
            if op in ("<&", ">&") and (text.rstrip("-").isdigit() or text == "-"):
                continue
            if op == "<>":
                self.path(target, cwds, write=False)  # opened for reading and writing
            self.path(target, cwds, write=op != "<")

    def word_subs(self, word: Word, cwds: Cwds) -> None:
        for sub in word.subs:
            self.script(sub, cwds)  # a substitution runs in a subshell
        for text in word.maybe_subs:
            self.speculative(text, cwds, always=True)
        for expansion in _expansions(word.chars):
            if expansion.op == "@P":
                # ${X@P}: X's value is expanded as a prompt, and its command substitutions run.
                plain = Expansion(expansion.name, quoted=True, indirect=expansion.indirect, whole=expansion.whole)
                for value in self.vars.fragments(plain, 0):
                    self.prompt("".join(_TEXT_OF.get(k, c) for c, k in value), cwds)

    def prompt(self, text: str, cwds: Cwds) -> None:
        """Text bash expands as a prompt (``PS4``, ``${X@P}``), read as shell_syntax.parse_prompt reads it.
        Each distinct text is read once and counted as ``parsed`` work, as ``reparse`` counts a command."""
        key = "\x00prompt\x00" + text  # a command holds no NUL, so no command text is this key
        script = self.parsed.get(key)
        if script is None:
            self.work.spend("parsed", len(text))
            script = self.parsed[key] = parse_prompt(text, self.home)
        self.script(script, cwds)

    def if_(self, node: If, cwds: Cwds) -> _Outcome:
        ok: set[str | None] = set()
        fail: set[str | None] = set()
        current = cwds
        for condition, body in node.branches:
            tested = self.script(condition, current)
            ran = self.script(body, tested.ok)
            ok |= ran.ok
            fail |= ran.fail
            current = tested.fail
        if node.orelse is not None:
            ran = self.script(node.orelse, current)
            ok |= ran.ok
            fail |= ran.fail
        else:
            ok |= current  # no branch ran: the if succeeds
        return _Outcome(_cap(ok), _cap(fail))

    def loop(self, node: Loop, cwds: Cwds) -> _Outcome:
        for word in node.items or []:
            self.word_subs(word, cwds)
            self.path(word, cwds, write=False)
        if node.var and node.items is None:
            self.vars.assign_scalar(node.var, self.vars.values("@"))  # for x; do: the positional parameters
        elif node.var and node.items is not None:
            for word in node.items:
                self.assign_expanded(node.var, word, scalar=True)
        if node.arith is not None:
            for sub in node.arith.subs:
                self.script(sub, cwds)
        # The body runs any number of times, and break leaves it anywhere.
        reach = cwds
        for _ in range(4):
            enter = reach
            new: set[str | None] = set(reach)
            if node.condition is not None:
                tested = self.script(node.condition, reach)
                enter = tested.fail if node.until else tested.ok
                new |= tested.either
            ran = self.script(node.body, enter)
            new |= ran.either
            grown = _cap(new)
            if grown == reach:
                break
            reach = grown
        else:
            reach = _cap(reach | _UNKNOWN)
        return _same(reach)

    def case(self, node: Case, cwds: Cwds) -> _Outcome:
        self.word_subs(node.subject, cwds)
        ok: set[str | None] = set(cwds)  # no pattern matched: the case succeeds
        fail: set[str | None] = set()
        before: Cwds = cwds
        for patterns, body in node.items:
            for pattern in patterns:
                self.word_subs(pattern, cwds)
            # ;& runs the next body after this one, from where this one left.
            ran = self.script(body, before)
            ok |= ran.ok
            fail |= ran.fail
            before = _cap(cwds | ran.either)
        return _Outcome(_cap(ok), _cap(fail))

    # -- simple commands ------------------------------------------------------------

    def simple(self, words: list[Word], cwds: Cwds, *, stdin: list[str], via: str) -> _Outcome:
        """A simple command. ``via``: "shell" (as written), "builtin" (behind builtin/command) or "external".

        Commands that run commands (a runner, ``eval``, ``bash -c``, an alias)
        nested more than ``MAX_NESTING`` deep are refused.
        """
        self.work.spend("calls")
        self.running += 1
        try:
            if self.running > MAX_NESTING:
                raise BashSyntaxError("commands that run commands nested too deep to read")
            return self.simple_command(words, cwds, stdin=stdin, via=via)
        finally:
            self.running -= 1

    def reparse(self, text: str) -> Script:
        """Parse text the command runs (``eval``, ``-c``, an alias, standard input). Each distinct text is
        parsed once and counted as ``parsed`` work: past its limit, refused."""
        script = self.parsed.get(text)
        if script is None:
            self.work.spend("parsed", len(text))
            script = self.parsed[text] = parse(text, self.home)
        return script

    def simple_command(self, words: list[Word], cwds: Cwds, *, stdin: list[str], via: str) -> _Outcome:
        for word in words:
            text = word.text
            if "CDPATH" in text:
                self.cdpath_seen = True
            if "GLOBIGNORE" in text or "BASHOPTS" in text:
                self.wide_seen = True
            self.vars.defaults_assigned(word)
        k = 0
        while k < len(words) and ASSIGNMENT.match(words[k].text):
            self.assignment(words[k], cwds, stdin=stdin)
            k += 1
        if k >= len(words):
            return _same(cwds)
        head = words[k]
        args = words[k + 1 :]
        if any(isinstance(c, Expansion) for c in head.chars):
            # The command word holds a variable: run the command once for each word it can expand to.
            ok: set[str | None] = set()
            fail: set[str | None] = set()
            for split in self.vars.expand_split(head.chars, head.kinds, code=True):
                heads = [Word(chars=c, kinds=k_, quoted=head.quoted) for c, k_ in split]
                out = self.simple([*words[:k], *heads, *args], cwds, stdin=stdin, via=via)
                ok |= out.ok
                fail |= out.fail
            return _Outcome(_cap(ok), _cap(fail))
        if head.kinds and head.kinds[0] in (RUN, EXT):
            # A command only known at run time: it may be any function defined so far, a shell that
            # runs what it is fed, or any word of the substitution that makes it ($(which docker)).
            for sub in head.subs:
                self.commands.extend(_script_words(sub))
            self.operands(args, cwds)
            for text in stdin:
                self.speculative(text, cwds, always=True)
            return self.unknown_code(cwds)
        self.commands.append(head.text)
        name = os.path.basename(head.text)
        self.vars.assign("_", self.values_of(args[-1] if args else head))  # $_: the last word of the last command
        if "/" in head.text:
            self.path(head, cwds, write=False)
        if via == "shell" and head.plain and name in self.aliases and name not in self.expanding:
            return self.alias_call(name, args, cwds, stdin=stdin)
        if via == "shell" and head.plain and name in self.functions:
            self.operands(args, cwds)
            self.positionals(args)
            return self.call_function(name, cwds)

        if name in ("cd", "pushd", "popd"):
            return self.cd(name, args, cwds)
        if name in ("builtin", "command"):
            rest = args
            while rest and rest[0].text.startswith("-") and rest[0].text != "-":
                if name == "command" and set(rest[0].text[1:]) & set("vV"):
                    return _same(cwds)  # command -v: it only says what a name is
                rest = rest[1:]
            if rest and rest[0].text == "--":
                rest = rest[1:]
            return self.simple(rest, cwds, stdin=stdin, via="external" if via == "external" else "builtin")
        if name in RUNNERS:
            self.runner(RUNNERS[name], args, cwds, stdin=stdin)
            return _same(cwds)
        if name in SHELLS:
            self.shell(args, cwds, stdin=stdin)
            return _same(cwds)
        if name == "eval":
            return self.eval_(args, cwds)
        if name == "trap":
            self.trap(args, cwds)
            return _same(cwds)
        if name == "alias":
            self.alias_definition(args, cwds)
            return _same(cwds)
        if name == "shopt":
            self.shopt(args)
        if name == "find":
            self.find(args, cwds)
            return _same(cwds)
        if name in ("source", "."):
            return self.source(args, cwds, stdin=stdin)
        if name in DECLARATIONS:
            self.declaration(name, args, cwds)
        if name == "printf":
            self.printf_v(args)
        if name == "set":
            self.set_positional(args)
        if name == "shift":
            self.vars.shifted("@")
        if name == "unset":
            for word in args:
                if "[" in word.text:
                    self.vars.fold(word.text.partition("[")[0])  # an element removed: the list's order is lost
        if name == "hash":
            # hash -p PATH NAME: NAME runs PATH.
            texts = [w.text for w in args]
            if "-p" in texts and texts.index("-p") + 1 < len(texts):
                self.commands.append(texts[texts.index("-p") + 1])
        if name == "read":
            self.read(args, stdin)
        if name in ("mapfile", "readarray"):
            return self.mapfile(args, cwds, stdin)
        if via == "shell" and name in DATA_COMMANDS:
            return _same(cwds)
        if name == "dd":
            for word in args:
                for key, write in (("if=", False), ("of=", True)):
                    if word.text.startswith(key):
                        self.path(word, cwds, write=write, skip=len(key))
            return _same(cwds)

        spec: Options | None = None
        run_cwds = cwds
        if via == "shell" and name in PATTERN_COMMANDS:
            spec = PATTERN_COMMANDS[name]
        elif name == "git":
            args, spec, run_cwds = self.git(args, cwds, wrapped=via != "shell")
        if spec is not None:
            parsed = parse_options(args, spec)
            for pos in parsed.reads:
                self.path(args[pos], run_cwds, write=False)
            for pos, offset in parsed.attached_reads:
                self.path(args[pos], run_cwds, write=False, skip=offset)
            for pos in parsed.operands:
                self.path(args[pos], run_cwds, write=parsed.in_place or spec.operands_written)
                if name == "git":
                    # git rebase -x CMD, git submodule foreach CMD, git bisect run CMD …
                    self.speculative_word(args[pos], run_cwds)
            return _same(cwds)
        if name == "git":
            self.operands(args, run_cwds)
            return _same(cwds)

        if name in WRITE_LAST:
            self.copy(name, args, cwds)
            return _same(cwds)
        if name == "patch":
            self.patch(args, cwds)
            return _same(cwds)
        operands = [w for w in args if not w.text.startswith("-") or w.text == "-"]
        # Removing or changing a directory recursively reaches everything under it.
        tree = name in WRITE_TREE and _recursive(args)
        for word in operands:
            self.path(word, cwds, write=name in WRITE_ALL, tree=tree)
            self.speculative_word(word, cwds)
        return _same(cwds)

    def assign_expanded(self, name: str, word: Word, *, scalar: bool = False) -> None:
        """``name`` takes each word ``word`` expands to as an argument does (a for loop's item): braces
        listed, and a glob kept (it stands for the files it matches)."""
        for exp_chars, exp_kinds in self.expanded(word.chars, word.kinds):
            for alt_chars, alt_kinds in brace_alternatives(exp_chars, exp_kinds):
                value = tuple(zip(alt_chars, alt_kinds))
                if scalar:
                    self.vars.assign_scalar(name, [value])
                else:
                    self.vars.assign(name, [value])

    def words(self, word: Word) -> list[Word]:
        """The words ``word`` can expand to, its variables' values put in and split as bash splits them."""
        out: list[Word] = []
        for split in self.vars.expand_split(word.chars, word.kinds):
            out.extend(Word(chars=c, kinds=k) for c, k in split)
        return out

    def texts(self, words: list[Word], *, code: bool = True) -> list[str]:
        """The texts a command string made of ``words`` can be (for eval, -c and the like), run-time text left out.

        ``code``: the text is run, so none may be left out; past ``MAX_WORDS``
        texts, or with a value folded away, the command is refused. Otherwise
        (an argument that may be run) the first ones are read.
        """
        joined = [""]
        for word in words:
            options = [
                " ".join("".join(_TEXT_OF.get(k, c) for c, k in zip(chars, kinds)) for chars, kinds in split)
                for split in self.vars.expand_split(word.chars, word.kinds, code=code)
            ]
            joined = list(dict.fromkeys(f"{j} {o}" if j else o for j in joined for o in options))
            if len(joined) > MAX_WORDS:
                if code:
                    raise BashSyntaxError("a command string can be more texts than embry0 checks")
                joined = joined[:_MAX_HEADS]
        return joined

    def operands(self, args: list[Word], cwds: Cwds) -> None:
        """Arguments of a command this module cannot read: every one may be a path or a command."""
        for word in args:
            if word.text.startswith("-") and word.text != "-":
                continue
            self.path(word, cwds, write=False)
            self.speculative_word(word, cwds)

    def declaration(self, name: str, args: list[Word], cwds: Cwds) -> None:
        """``declare``, ``local``, … : their assignments, and with ``-n``, namerefs (``declare -n R=X``)."""
        nameref = False
        cases = ""
        for word in args:
            text = word.text
            if text.startswith("-") and not ASSIGNMENT.match(text):
                nameref = nameref or (name != "export" and "n" in text[1:])
                cases += "".join(c for c in text[1:] if c in "luc") if name != "export" else ""
                continue
            if cases:
                # declare -l / -u / -c: the variable's values are lowered, raised or capitalized.
                self.vars.case(text.partition("=")[0].partition("[")[0], cases)
            if nameref:
                ref, _, _ = text.partition("=")
                self.vars.nameref(ref, self.values_of(_suffix(word, len(ref) + 1)) if "=" in text else [])
            elif ASSIGNMENT.match(text):
                self.assignment(word, cwds, stdin=[])

    def values_of(self, word: Word) -> list[Value]:
        """The values ``word`` can expand to, unsplit (an assignment's)."""
        return [tuple(zip(c, k)) for c, k in self.vars.expand(word.chars, word.kinds)]

    def assignment(self, word: Word, cwds: Cwds, *, stdin: list[str]) -> None:
        text = word.text
        head = text.split("=", 1)[0]
        name = re.sub(r"\[.*", "", head).rstrip("+")
        value = _assigned_tildes(_suffix(word, text.index("=") + 1))
        if name == "BASH_CMDS":
            # An element of BASH_CMDS is the program a name runs.
            for _, element in [(head, value)] if "[" in head else _indexed(word.array):
                self.commands.extend(text for text in self.texts([element], code=False))
        if name == "BASH_ALIASES":
            # An element of BASH_ALIASES is an alias.
            for key, element in [(head[len(name) + 1 : -1], value)] if "[" in head else _indexed(word.array):
                for alias in self.texts([element]):
                    self.aliases[key] = alias
                    self.speculative(alias, cwds, always=True)
        if word.array:
            self.array(name, word.array, append=head.endswith("+"))
        elif "[" in head:
            # One element: it holds the value, and the list's order is lost.
            self.vars.assign(name, self.values_of(value))
            self.vars.fold(name)
        else:
            self.vars.assign_word(name, value, append=head.endswith("+"))
        if name == "CDPATH":
            self.cdpath_seen = True
        elif name in ("GLOBIGNORE", "BASHOPTS"):
            self.wide_seen = True
        elif name in STARTUP_FILES:
            # bash runs the file a BASH_ENV (or ENV) names before the command's script, and that file
            # may set glob options.
            self.wide_seen = True
            self.sourced(value, cwds, stdin=stdin)

    def printf_v(self, args: list[Word]) -> None:
        """``printf -v NAME FORMAT ARGS``: NAME holds the format, an argument, or the format with them put in."""
        texts = [w.text for w in args]
        name: str | None = None
        k = 0
        while k < len(args) and texts[k].startswith("-"):
            if texts[k] == "--":
                k += 1
                break
            if texts[k] == "-v" and k + 1 < len(args):
                name = texts[k + 1]
                k += 2
                continue
            k += 1
        if name is None or k >= len(args):
            return
        base = re.sub(r"\[.*", "", name)
        values: list[Value] = []
        combinations: list[list[str]] = [[]]
        for word in args[k:]:
            # Run-time text and the shell's directories pass through printf as opaque characters.
            options = [
                "".join(_OPAQUE.get(kind, c) for c, kind in zip(chars, kinds))
                for chars, kinds in self.vars.expand(word.chars, word.kinds)
            ]
            combinations = [[*c, o] for c in combinations for o in options]
            if len(combinations) > MAX_WORDS:
                # More than are listed: what it holds can be anywhere, and is not run.
                self.vars.assign(base, [ANYWHERE])
                self.vars.folding.add(base)
                return
        for format_, *arguments in combinations:
            try:
                printed = printf(format_, arguments, opaque=frozenset(_KIND_OF))
            except Exhausted:
                values.append(ANYWHERE)  # longer than is computed: it can be anywhere, and is not run
                self.vars.folding.add(base)
                continue
            values.append(UNKNOWN if printed is None else tuple((c, _KIND_OF.get(c, LIT)) for c in printed))
        if base != name:
            self.vars.assign(base, values)  # printf -v a[1]: one element, and the list's order is lost
            self.vars.fold(base)
        else:
            self.vars.assign_scalar(name, values)

    def read(self, args: list[Word], stdin: list[str]) -> None:
        """``read NAME…`` (``-a NAME``) fed a here-string or a here-document: each name holds a field of
        its first line, the last one the rest; the array, every field. Input from a pipe or a file is
        only known at run time."""
        names: list[str] = []
        array: str | None = None
        raw = False
        delimiter = "\n"
        count: int | None = None
        k = 0
        while k < len(args):
            text = args[k].text
            if text == "--":
                names.extend(w.text for w in args[k + 1 :])
                break
            if text.startswith("-") and len(text) > 1:
                flags = text[1:]
                for j, flag in enumerate(flags):
                    if flag == "r":
                        raw = True
                    elif flag in "adinNptu":
                        value = flags[j + 1 :] or (args[k + 1].text if k + 1 < len(args) else "")
                        if not flags[j + 1 :]:
                            k += 1
                        if flag == "a":
                            array = value
                        elif flag == "d":
                            delimiter = value[:1] or "\x00"
                        elif flag in "nN" and value.isdigit():
                            count = int(value)
                            delimiter = delimiter if flag == "n" else "\x00"
                        break
                k += 1
                continue
            names.append(text)
            k += 1
        if array is None and not names:
            names = ["REPLY"]
        for text in stdin:
            line = text.split(delimiter)[0] if delimiter in text else text
            line = line if count is None else line[:count]
            for content in {line, line if raw else re.sub(r"\\(.)", r"\1", line.replace("\\\n", ""))}:
                for separators in self.vars.ifs():
                    fields = [f for f in re.split("[" + re.escape("".join(separators)) + "]+", content) if f]
                    if not separators:
                        fields = [content]
                    if array is not None:
                        self.vars.assign(array, [_literal(f) for f in fields])
                        self.vars.assign_list(array, [_literal(SEP.join(fields))])
                    for i, target in enumerate(names):
                        last = i == len(names) - 1
                        found = fields[i:] if last else fields[i : i + 1]
                        if found:
                            rest = content[content.find(found[0]) :].rstrip(" \t\n") if last else found[0]
                            self.vars.assign_scalar(target, [_literal(rest), *(_literal(f) for f in found)])

    def mapfile(self, args: list[Word], cwds: Cwds, stdin: list[str]) -> _Outcome:
        """``mapfile ARRAY`` fed a here-string or a here-document: each line an element, in order. A
        callback (``-C``) runs in the shell, any number of times: where it leaves the shell."""
        name = "MAPFILE"
        out = _same(cwds)
        strip = False
        delimiter = "\n"
        k = 0
        while k < len(args):
            text = args[k].text
            if text.startswith("-") and len(text) > 1:
                flag = text[1]
                value_word = _suffix(args[k], 2) if len(text) > 2 else (args[k + 1] if k + 1 < len(args) else Word())
                k += 1 if len(text) > 2 else 2
                if flag == "t":
                    strip = True
                    k -= 1 if len(text) <= 2 else 0
                elif flag == "d":
                    delimiter = value_word.text[:1] or "\x00"
                elif flag == "C":
                    for code in self.texts([value_word]):
                        ran = self.script(self.reparse(code), cwds)
                        out = _Outcome(_cap(out.ok | ran.ok | ran.fail), _cap(out.fail | ran.ok | ran.fail))
                continue
            name = text
            k += 1
        for text in stdin:
            lines = text.split(delimiter)
            if lines and lines[-1] == "":
                lines = lines[:-1]
            elements = [line if strip else line + delimiter for line in lines]
            self.vars.assign(name, [_literal(e) for e in elements])
            self.vars.assign_list(name, [_literal(SEP.join(elements))])
        return out

    def set_positional(self, args: list[Word]) -> None:
        """``set -- A B``: $1, $2, $@ and $* hold them."""
        texts = [w.text for w in args]
        if "--" in texts:
            values = args[texts.index("--") + 1 :]
        elif args and not texts[0].startswith(("-", "+")):
            values = args
        else:
            return
        self.positionals(values)

    def positionals(self, words: list[Word]) -> None:
        """A shell's, a function's or ``set``'s arguments: ``$1``, ``$2``, … and the list ``$@`` hold them."""
        self.array("@", words)

    def array(self, name: str, elements: list[Word], *, append: bool = False) -> None:
        """``name=(…)``: each element a value, expanded as an argument is, and the whole list in order.

        An element with its own index (``[2]=x``) is put at it; one whose index
        is not a number, or that can expand to a varying number of words, leaves
        the order unknown.
        """
        positions: dict[int, list[Value]] = {}
        index = 0
        ordered = True
        for element in elements:
            m = _INDEXED.match(element.text)
            if m and element.kinds[0] == ACT and element.kinds[m.end() - 1] == ACT:
                subscript = element.text[1 : element.text.index("]")]
                element = _suffix(element, m.end())
                if subscript.isdigit() and not m.group().endswith("+="):
                    index = int(subscript)
                else:
                    ordered = False
            sequences: list[list[Value]] = []
            for split in self.vars.expand_split(element.chars, element.kinds):
                sequence: list[Value] = []
                for chars, kinds in split:
                    for alt_chars, alt_kinds in brace_alternatives(chars, kinds):
                        value = tuple(zip(alt_chars, alt_kinds))
                        self.vars.assign(name, [value])
                        sequence.append(value)
                sequences.append(sequence)
            counts = {len(sequence) for sequence in sequences}
            if len(counts) != 1:
                ordered = False
                continue
            count = counts.pop()
            for j in range(count):
                positions[index + j] = list(dict.fromkeys(sequence[j] for sequence in sequences))
            index += count
        lists: list[Value] = [()]
        for position in sorted(positions) if ordered else ():
            lists = [old + ((SEP, LIT),) + new if old else new for old in lists for new in positions[position]]
            if len(lists) > MAX_VALUES or len(lists[0]) > MAX_TEXT:
                ordered = False  # more lists, or a longer one, than are kept
                break
        if not ordered:
            self.vars.fold(name)
            return
        self.vars.assign_list(name, lists, append=append)

    def source(self, args: list[Word], cwds: Cwds, *, stdin: list[str]) -> _Outcome:
        """``source FILE`` / ``. FILE``: the file is read; what it holds runs in this shell."""
        if args and args[0].text == "--":
            args = args[1:]  # the end of its options, as for every builtin
        if not args:
            return _same(cwds)
        out = self.sourced(args[0], cwds, stdin=stdin)
        self.operands(args[1:], cwds)
        if args[1:]:
            self.positionals(args[1:])
        return out

    def sourced(self, word: Word, cwds: Cwds, *, stdin: list[str]) -> _Outcome:
        """A file the shell runs: standard input's text (a here-document or a here-string) is read as script."""
        if any(isinstance(c, Expansion) and c.op == "procsub" for c in word.chars):
            raise BashSyntaxError("the shell would run the output of a command, which cannot be checked")
        texts = {w.text for w in self.words(word)}
        if any(_STDIN.fullmatch(t) for t in texts):
            out = _same(cwds)
            for text in stdin:
                out = self.script(self.stdin_script(text), cwds)
            return out
        if "-" in texts:
            # A file named -: bash and dash open it, and a shell given - before its options end reads
            # standard input instead (shell). Both are read, standard input's text as script.
            out = _same(cwds)
            for text in stdin:
                out = self.script(self.stdin_script(text), cwds)
            self.path(word, cwds, write=False)
            return out
        if any(_FD.fullmatch(t) for t in texts):
            raise BashSyntaxError("the shell would run what a file descriptor holds, which cannot be checked")
        self.path(word, cwds, write=False)
        return _same(cwds)

    def shopt(self, args: list[Word]) -> None:
        setting = ""
        for word in args:
            text = word.text
            if RUN in word.kinds:
                self.wide_seen = True
            elif text in ("-s", "-u"):
                setting = text
            elif text.startswith("-"):
                if "o" in text:
                    return  # set -o options: no glob option among them
            elif setting == "-s" and text not in HARMLESS_OPTIONS:
                self.wide_seen = True
            elif setting == "-u" and text == "globskipdots":
                self.wide_seen = True

    def unknown_code(self, cwds: Cwds) -> _Outcome:
        """Code this module cannot read may call any function defined so far."""
        ok: set[str | None] = set(cwds)
        fail: set[str | None] = set(cwds)
        for name in list(self.functions):
            if (name, cwds) in self.unknown_done:
                continue
            self.unknown_done.add((name, cwds))
            out = self.call_function(name, cwds)
            ok |= out.ok
            fail |= out.fail
        return _Outcome(_cap(ok), _cap(fail))

    def call_function(self, name: str, cwds: Cwds) -> _Outcome:
        body = self.functions[name]
        if name in self.calling:
            # A recursive call: its body may run in any directory the recursion reaches.
            if name not in self.recursed:
                self.recursed.add(name)
                self.command(body, _UNKNOWN)
            return _same(_cap(cwds | _UNKNOWN))
        self.calling.add(name)
        try:
            return self.command(body, cwds)
        finally:
            self.calling.discard(name)

    def alias_definition(self, args: list[Word], cwds: Cwds) -> None:
        for word in args:
            text = word.text
            if "=" not in text or text.startswith("-"):
                continue
            name = text.partition("=")[0]
            for value in self.texts([_suffix(word, len(name) + 1)]):
                self.aliases[name] = value
                self.speculative(value, cwds, always=True)

    def alias_call(self, name: str, args: list[Word], cwds: Cwds, *, stdin: list[str]) -> _Outcome:
        """An alias: its text with the words after it, as bash reads it."""
        self.expanding.add(name)
        try:
            script = self.reparse(self.aliases[name])
            items = script.items
            if len(items) == 1 and not items[0][0].rest and not items[0][1]:
                pipeline = items[0][0].first
                last = pipeline.commands[-1] if pipeline.commands else None
                if len(pipeline.commands) == 1 and isinstance(last, Simple):
                    self.redirections(last, cwds)
                    for word in last.words:
                        self.word_subs(word, cwds)
                    out = self.simple([*last.words, *args], cwds, stdin=stdin, via="shell")
                    return _Outcome(out.fail, out.ok) if pipeline.negated else out
            out = self.script(script, cwds)
            self.operands(args, _cap(out.either))
            return _same(_cap(out.either))
        finally:
            self.expanding.discard(name)

    def eval_(self, args: list[Word], cwds: Cwds) -> _Outcome:
        if args and args[0].text == "--":
            args = args[1:]  # the end of eval's options: not part of the text it runs
        ok: set[str | None] = set()
        fail: set[str | None] = set()
        for text in self.texts(args):
            ran = self.script(self.reparse(text), cwds)
            ok |= ran.ok
            fail |= ran.fail
        out = _Outcome(_cap(ok), _cap(fail))
        if any(RUN in w.kinds for w in args):
            unknown = self.unknown_code(_cap(out.either))
            return _Outcome(_cap(out.ok | unknown.ok), _cap(out.fail | unknown.fail))
        return out

    def trap(self, args: list[Word], cwds: Cwds) -> None:
        operands = list(args)
        while operands and operands[0].text in ("-p", "-l", "-P", "--"):
            if operands[0].text == "--":
                operands = operands[1:]
                break
            operands = operands[1:]
        if not operands:
            return
        action = operands[0]
        if RUN in action.kinds:
            self.unknown_code(cwds)
        self.traps.extend(self.reparse(text) for text in self.texts([action]))

    def cd(self, name: str, args: list[Word], cwds: Cwds) -> _Outcome:
        """Where the shell may be after a cd, pushd or popd (one another program runs moves only that program:
        a runner's outcome is not the shell's)."""
        operands: list[Word] = []
        k = 0
        while k < len(args):
            text = args[k].text
            if text == "--":
                operands.extend(args[k + 1 :])
                break
            if re.fullmatch(r"-[LPe@]+", text):
                k += 1
                continue
            operands.append(args[k])
            k += 1
        if name == "popd":
            moved: Cwds = _UNKNOWN
        elif not operands:
            # cd alone goes HOME, whatever the command made HOME.
            moved = _UNKNOWN if name == "pushd" else _cap(self.directories(_home_word(), cwds))
        else:
            target = operands[0]
            if target.text == "-" or (name == "pushd" and re.fullmatch(r"[-+]\d+", target.text)):
                moved = _UNKNOWN  # the previous directory, or one from the stack
            else:
                self.path(target, cwds, write=False)
                moved = _cap(self.directories(target, cwds))
        return _Outcome(moved, cwds)

    def directories(self, target: Word, cwds: Cwds) -> set[str | None]:
        """Where ``cd target`` from any of ``cwds`` can land (None: a directory that is not known)."""
        found: set[str | None] = set()
        for word in self.words(target) or [Word()]:
            text = word.text
            for cwd in cwds:
                found.add(self.directory(word, cwd))
            if self.mode.cdpath and not text.startswith(("/", "./", "../")) and text not in (".", ".."):
                found.add(None)  # a CDPATH entry may hold it
        return found

    def directory(self, target: Word, cwd: str | None) -> str | None:
        """Where ``cd target`` from ``cwd`` lands; None when that is not known."""
        if not target.chars:
            return cwd
        kinds = target.kinds
        if any(k in (RUN, EXT, OLD) for k in kinds) or any(
            k == ACT and c in "*?[{" for c, k in zip(target.chars, kinds)
        ):
            return None
        text = target.text
        if kinds[0] == CWD:
            if cwd is None or CWD in kinds[1:]:
                return None
            return _normalize(cwd + "/" + text[1:])
        if CWD in kinds:
            return None
        if text.startswith("/"):
            return _normalize(text)
        return None if cwd is None else _normalize(os.path.join(cwd, text))

    # -- commands that run commands ---------------------------------------------------------

    def herestring(self, word: Word) -> list[str]:
        """The texts a here-string can feed the command (run as script if the command is a shell)."""
        try:
            return self.texts([word])
        except BashSyntaxError:
            return [_UNCHECKED]

    def stdin_script(self, text: str) -> Script:
        if isinstance(text, _Unchecked):
            raise BashSyntaxError("the shell would run a here-string that can be more texts than embry0 checks")
        return self.reparse(text)

    def speculative_word(self, word: Word, cwds: Cwds) -> None:
        for text in self.texts([word], code=False):
            self.speculative(text, cwds)

    def speculative(self, text: str, cwds: Cwds, *, always: bool = False) -> None:
        """Read ``text`` as a command, in case the program it is given to runs it.

        Only text that reads as one is read (``always``: any text): a shell or
        another command that reaches files first, or a control operator, a
        redirection or a substitution in it. Text this module cannot parse has
        each word that looks like a path read.
        """
        text = text.replace(WILD, "")
        if not text.strip() or (not always and not any(c.isspace() for c in text)):
            return
        if len(text) > _MAX_SPECULATIVE:
            self.crude(text, cwds)
            return
        try:
            script = self.reparse(text)
        except BashSyntaxError:
            self.crude(text, cwds)
            return
        if not always and not CODE_MARKS.search(text):
            first = _first_head(script)
            if first is None:
                return
            name = os.path.basename(first)
            if not ("/" in first or name in CODE_HEADS or name in SHELLS or name in RUNNERS):
                return
            if name in DATA_COMMANDS:
                return
        self.script(script, cwds)

    def crude(self, text: str, cwds: Cwds) -> None:
        for token in re.findall(r"[^\s;|&()<>'\"`]+", text):
            if not token.startswith(("/", "~", ".")):
                continue
            if token.startswith("~"):
                user, sep, rest = token[1:].partition("/")
                if user:
                    continue
                token = self.home + sep + rest
            word = Word()
            word.add(token, LIT)
            self.path(word, cwds, write=False)

    def shell(self, args: list[Word], cwds: Cwds, *, stdin: list[str]) -> None:
        """``sh``/``bash`` and the like: the -c script, a script file, or commands fed on standard input."""
        k = 0
        command_flag = False
        stdin_flag = False
        while k < len(args):
            text = args[k].text
            if text in ("--", "-"):
                # - is the same as --: the options end, and with no operand after them the shell
                # reads its script from standard input (bash -, sh -, bash -x -).
                k += 1
                break
            if text in ("--rcfile", "--init-file"):
                self.wide_seen = True  # the rc file the shell runs first may set glob options
                if k + 1 < len(args):
                    # An interactive shell runs this file first: standard input's text, when it names that.
                    self.sourced(args[k + 1], cwds, stdin=stdin)
                k += 2
                continue
            if text.startswith("--"):
                k += 1
                continue
            if len(text) > 1 and text[0] in "-+":
                flags = text[1:]
                if text[0] == "-" and "i" in flags:
                    self.wide_seen = True  # an interactive shell runs ~/.bashrc, which may set glob options
                command_flag = command_flag or (text[0] == "-" and "c" in flags)
                stdin_flag = stdin_flag or (text[0] == "-" and "s" in flags)
                takes = "o" in flags or "O" in flags
                if "O" in flags:
                    name = args[k + 1].text if k + 1 < len(args) else ""
                    if (text[0] == "-" and name not in HARMLESS_OPTIONS) or (text[0] == "+" and name == "globskipdots"):
                        self.wide_seen = True
                k += 2 if takes else 1
                continue
            break
        operands = args[k:]
        if command_flag:
            if operands:
                script = operands[0]
                if RUN in script.kinds:
                    self.unknown_code(cwds)
                for text in self.texts([script]):
                    self.script(self.reparse(text), cwds)
                self.operands(operands[1:], cwds)  # $0, $1, … the script may use as paths
                if operands[1:]:
                    self.assign_expanded("0", operands[1])
                    self.positionals(operands[2:])
            return
        if operands and not stdin_flag:
            self.sourced(operands[0], cwds, stdin=stdin)  # a script file, or standard input by another name
            self.operands(operands[1:], cwds)
            self.positionals(operands[1:])
            return
        self.operands(operands, cwds)
        self.positionals(operands)
        for text in stdin:
            self.script(self.stdin_script(text), cwds)

    def runner(self, spec: Runner, args: list[Word], cwds: Cwds, *, stdin: list[str]) -> None:
        """A command that runs another: read its options and the files they name, then the command it runs."""
        k, run_cwds = self.runner_options(spec, args, 0, cwds, stdin=stdin)
        if spec.first_operand_written or spec.first_operand_read:
            if k < len(args):
                self.path(args[k], cwds, write=spec.first_operand_written)
                # options may follow it (flock FILE -c CMD, script FILE -c CMD)
                k, run_cwds = self.runner_options(spec, args, k + 1, run_cwds)
        if not spec.command_operands:
            # Options may follow an operand (su - root -c CMD): read every one.
            rest: list[Word] = []
            while k < len(args):
                if args[k].text.startswith("-") and args[k].text != "-":
                    after, run_cwds = self.runner_options(spec, args, k, run_cwds, stdin=stdin)
                    if after > k:
                        k = after
                        continue
                rest.append(args[k])
                k += 1
            self.operands(rest, cwds)
            return
        operands = args[k + spec.skip_operands :]
        if not operands:
            return
        if spec.joined:
            for text in self.texts(operands):
                self.script(self.reparse(text), run_cwds)
            return
        self.simple(operands, run_cwds, stdin=stdin, via="external")

    def runner_options(
        self, spec: Runner, args: list[Word], k: int, cwds: Cwds, *, stdin: list[str] | None = None
    ) -> tuple[int, Cwds]:
        """Read a runner's options from ``args[k]``; the index of its first operand, and where it runs."""
        while k < len(args):
            word = args[k]
            text = word.text
            if spec.assignments and ASSIGNMENT.match(text):
                self.assignment(word, cwds, stdin=stdin or [])
                k += 1
                continue
            if spec.assignments and "=" in text and not text.startswith(("-", "=")):
                # NAME=VALUE with a name no shell assigns: an environment variable the command gets.
                # BASH_FUNC_f%%=() { …; } is a function a bash it starts defines.
                if _EXPORTED_FUNCTION.match(text):
                    definition = self.reparse("f" + text.partition("=")[2])
                    for chain, _ in definition.items:
                        for command in chain.first.commands:
                            if isinstance(command, FunctionDef) and command.body is not None:
                                self.command(command.body, cwds)
                k += 1
                continue
            if text == "--":
                return k + 1, cwds
            if not text.startswith("-") or text == "-":
                return k, cwds
            if text.startswith("--"):
                name, eq, _ = text.partition("=")
                takes = name in (
                    spec.value_long | spec.script_long | spec.read_long | spec.write_long | spec.chdir_long
                )
                value: Word | None = None
                if takes:
                    if eq:
                        value = _suffix(word, len(name) + 1)
                    elif k + 1 < len(args):
                        value = args[k + 1]
                if value is not None:
                    cwds = self.option_value(spec, name, value, cwds, long=True)
                k += 2 if takes and not eq else 1
                continue
            consumed_next = False
            for j in range(1, len(text)):
                ch = text[j]
                if ch in spec.value_short + spec.script_short + spec.read_short + spec.write_short + spec.chdir_short:
                    if j + 1 < len(text):
                        value = _suffix(word, j + 1)
                    elif k + 1 < len(args):
                        value = args[k + 1]
                        consumed_next = True
                    else:
                        value = Word()
                    cwds = self.option_value(spec, ch, value, cwds, long=False)
                    break
            k += 2 if consumed_next else 1
        return k, cwds

    def option_value(self, spec: Runner, option: str, value: Word, cwds: Cwds, *, long: bool) -> Cwds:
        script = spec.script_long if long else spec.script_short
        reads = spec.read_long if long else spec.read_short
        writes = spec.write_long if long else spec.write_short
        chdir = spec.chdir_long if long else spec.chdir_short
        if option in script:
            if RUN in value.kinds:
                self.unknown_code(cwds)
            for text in self.texts([value]):
                self.script(self.reparse(text), cwds)
        elif option in reads or option in writes:
            self.path(value, cwds, write=option in writes)
        elif option in chdir:
            self.path(value, cwds, write=False)
            return _cap(self.directories(value, cwds))
        return cwds

    def git(self, args: list[Word], cwds: Cwds, *, wrapped: bool) -> tuple[list[Word], Options | None, Cwds]:
        """Read git's own options; the subcommand's arguments, how to read them, and where git runs."""
        k = 0
        while k < len(args) and args[k].text.startswith("-"):
            text = args[k].text
            if text.startswith("--config-env"):
                raise BashSyntaxError("git --config-env takes a setting from the environment, which cannot be checked")
            name, eq, _ = text.partition("=")
            if name in ("--git-dir", "--work-tree", "--exec-path") and eq:
                self.path(args[k], cwds, write=False, skip=len(name) + 1)
                k += 1
                continue
            if k + 1 < len(args):
                value = args[k + 1]
                if text == "-C":
                    self.path(value, cwds, write=False)
                    cwds = _cap(self.directories(value, cwds))
                elif text == "-c":
                    # alias.x=!cmd, core.pager=cmd, core.sshCommand=cmd, include.path=file …
                    setting = value.text
                    if "=" in setting:
                        offset = setting.index("=") + 1
                        self.path(value, cwds, write=False, skip=offset)
                        for text in self.texts([_suffix(value, offset)]):
                            self.speculative(text.lstrip("!"), cwds, always=True)
                elif text in ("--git-dir", "--work-tree", "--exec-path"):
                    self.path(value, cwds, write=False)
            k += 2 if text in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path") else 1
        if k >= len(args):
            return [], None, cwds
        if wrapped:
            return args[k + 1 :], None, cwds
        subcommand = args[k].text
        if subcommand == "grep":
            return args[k + 1 :], GIT_GREP, cwds
        return args[k + 1 :], GIT_WRITE if subcommand in GIT_WRITES_PATHS else GIT_OTHER, cwds

    def find(self, args: list[Word], cwds: Cwds) -> None:
        k = 0
        while k < len(args) and (args[k].text in ("-H", "-L", "-P") or re.fullmatch(r"-O\d*", args[k].text)):
            k += 1
        if k < len(args) and args[k].text == "-D":
            k += 2
        starts: list[Word] = []
        while k < len(args) and not (args[k].text.startswith(("-", "(", "!", ",")) and args[k].text != "-"):
            starts.append(args[k])
            k += 1
        if not starts:
            here = Word()
            here.add(".", LIT)
            starts = [here]
        expression = args[k:]
        # What {} and -delete stand for: every file under the starting points, or the ones -name names.
        name: Word | None = None
        if not any(w.text in FIND_OR for w in expression):
            name = next((expression[i + 1] for i, w in enumerate(expression[:-1]) if w.text == "-name"), None)
        found: list[PathRef] = []
        for start in starts:
            # find walks the whole tree under each starting point
            for ref in self.path(start, cwds, write=False, tree=True):
                found.append(ref)
                if name is not None:
                    found.append(PathRef((*ref.parts, Lit("/"), Wild(), *_name_pattern(name))))
                else:
                    found.append(ref.below())
        while k < len(args):
            text = args[k].text
            if text in FIND_COMMANDS:
                end = k + 1
                while end < len(args) and args[end].text not in (";", "+"):
                    end += 1
                command = args[k + 1 : end]
                run_cwds = _UNKNOWN if text in ("-execdir", "-okdir") else cwds
                if command and os.path.basename(command[0].text) in SHELLS:
                    self.record(found, write=True)  # the script can do anything with them
                outer, self.found = self.found, found
                try:
                    self.simple(command, run_cwds, stdin=[], via="external")
                finally:
                    self.found = outer
                k = end + 1
                continue
            if text == "-delete":
                self.record(found, write=True)
            elif text in FIND_WRITES or text in FIND_READS:
                if k + 1 < len(args):
                    self.path(args[k + 1], cwds, write=text in FIND_WRITES)
                k += 2
                continue
            elif text in FIND_VALUES:
                k += 2
                continue
            k += 1

    def copy(self, name: str, args: list[Word], cwds: Cwds) -> None:
        """cp, mv, install, ln, rsync: sources read (mv: removed), the destination and what lands in it written."""
        target: Word | None = None
        operands: list[Word] = []
        k = 0
        while k < len(args):
            text = args[k].text
            if text in ("-t", "--target-directory") and k + 1 < len(args):
                target = args[k + 1]
                k += 2
                continue
            if text.startswith("--target-directory="):
                target = _suffix(args[k], len("--target-directory="))
            elif text.startswith("-t") and len(text) > 2 and not text.startswith("--"):
                target = _suffix(args[k], 2)
            elif not text.startswith("-") or text == "-":
                operands.append(args[k])
            k += 1
        if target is None and len(operands) >= 2:
            target = operands.pop()
        for source in operands:
            self.path(source, cwds, write=name == "mv", tree=name == "mv")
        if target is None:
            return
        self.path(target, cwds, write=True, tree=_recursive(args))
        for source in operands:
            # A destination that is a directory receives the source by its own name.
            landed = _landing(target, source)
            if landed is not None:
                self.path(landed, cwds, write=True)

    def patch(self, args: list[Word], cwds: Cwds) -> None:
        """patch [options] [file [patchfile]]: the file and -o's are written, the patch file read."""
        operands: list[Word] = []
        k = 0
        while k < len(args):
            text = args[k].text
            if text in ("-o", "--output", "-i", "--input") and k + 1 < len(args):
                self.path(args[k + 1], cwds, write=text in ("-o", "--output"))
                k += 2
                continue
            if text[:2] in ("-o", "-i") and len(text) > 2 and not text.startswith("--"):
                self.path(args[k], cwds, write=text.startswith("-o"), skip=2)
            elif text in ("-p", "-d", "-D", "-z", "-B", "-Y", "-F", "-g", "-V") and k + 1 < len(args):
                k += 1
            elif not text.startswith("-") or text == "-":
                operands.append(args[k])
            k += 1
        if operands:
            self.path(operands[0], cwds, write=True)
        if len(operands) > 1:
            self.path(operands[1], cwds, write=False)

    # -- paths ----------------------------------------------------------------------

    def record(self, refs: Iterable[PathRef], *, write: bool) -> None:
        target = self.writes if write else self.reads
        for ref in refs:
            key = (ref, write)
            if key in self._recorded:
                continue
            self._recorded.add(key)
            target.append(ref)
            if len(self._recorded) > _MAX_PATHS:
                raise BashSyntaxError(f"the command names more than {_MAX_PATHS} paths, more than embry0 checks")

    def path(self, word: Word, cwds: Cwds, *, write: bool, skip: int = 0, tree: bool = False) -> list[PathRef]:
        """Record the paths ``word`` names (from ``skip``); with ``tree``, everything under them too."""
        chars, kinds = word.chars[skip:], word.kinds[skip:]
        if not chars or (chars == ["-"]):
            return []
        refs: list[PathRef] = []
        if self.found is not None and "{}" in "".join(chars):
            at = "".join(chars).index("{}")
            if at == 0:
                rest = _plain_parts(chars[2:], kinds[2:])
                refs = [PathRef((*f.parts, *rest)) for f in self.found]
            else:
                chars = chars[:at] + [WILD] + chars[at + 2 :]
                kinds = kinds[:at] + [RUN] + kinds[at + 2 :]
        if not refs:
            for exp_chars, exp_kinds in self.expanded(chars, kinds):
                if not exp_chars or exp_kinds[0] == RUN:
                    continue  # starts with text only known at run time: names no path this module can know
                for alt_chars, alt_kinds in brace_alternatives(exp_chars, exp_kinds):
                    for t_chars, t_kinds in self.tildes(alt_chars, alt_kinds):
                        for cwd in cwds:
                            refs.append(self.ref(t_chars, t_kinds, cwd))
        out = list(dict.fromkeys(refs))
        self.record(out, write=write)
        if tree:
            self.record((r.below() for r in out), write=write)
        return out

    def ref(self, chars: list[str], kinds: list[int], cwd: str | None) -> PathRef:
        """The paths one word (after brace expansion) names, resolved against ``cwd``."""
        if not chars:
            return PathRef.exact(cwd) if cwd is not None else PathRef((Wild(),))
        base: str | None
        if kinds[0] == CWD:
            base, chars, kinds = cwd, chars[1:], kinds[1:]
        elif kinds[0] == OLD:
            base, chars, kinds = None, chars[1:], kinds[1:]
        elif chars[0] == "/":
            base = "/"
        else:
            base = cwd
        segments: list[list[tuple[str, int]]] = [] if base is None else [_lit_segment(s) for s in base.split("/") if s]
        unknown = base is None
        current: list[tuple[str, int]] = []
        pieces: list[list[tuple[str, int]]] = []
        for c, k in zip(chars, kinds):
            if c == "/" and k in (LIT, ACT):
                pieces.append(current)
                current = []
            else:
                current.append((c, k))
        pieces.append(current)
        for piece in pieces:
            text = "".join(c for c, _ in piece)
            plain = all(k in (LIT, ACT) for _, k in piece)
            if not piece or (plain and text == "."):
                continue
            if plain and text == "..":
                if segments and any(k == RUN for _, k in segments[-1]):
                    unknown, segments = True, []  # back over a value only known at run time: anywhere
                elif segments:
                    segments.pop()
                continue
            segments.append(piece)
        parts: list[Part] = [Wild()] if unknown else []
        for segment in segments:
            parts.append(Lit("/"))
            parts.extend(self.segment_parts(segment))
        if not parts:
            parts = [Lit("/")]
        return PathRef(_merge(parts))

    def expanded(self, chars: list[str], kinds: list[int]) -> list[tuple[list[str], list[int]]]:
        """The words ``chars`` can expand to, variables' values put in, unquoted values split."""
        return [word for split in self.vars.expand_split(chars, kinds) for word in split]

    def tildes(self, chars: list[str], kinds: list[int]) -> list[tuple[list[str], list[int]]]:
        """``~…`` that brace expansion put at the start of a word, expanded as bash does after it."""
        if not (chars and kinds[0] == ACT and chars[0] == "~"):
            return [(chars, kinds)]
        end = next((i for i, c in enumerate(chars) if c == "/"), len(chars))
        if any(k != ACT for k in kinds[1:end]):
            return [(chars, kinds)]
        user = "".join(chars[1:end])
        if user and user not in ("+", "-"):
            try:
                home = pwd.getpwnam(user).pw_dir
            except KeyError:
                raise BashSyntaxError(f"cannot resolve ~{user}: no such user") from None
            return [([*home, *chars[end:]], [LIT] * len(home) + kinds[end:])]
        name, kind = {"": ("HOME", RUN), "+": ("PWD", CWD), "-": ("OLDPWD", OLD)}[user]
        tilde = Word(chars=[Expansion(name, quoted=True), *chars[end:]], kinds=[kind, *kinds[end:]])
        return self.expanded(tilde.chars, tilde.kinds)

    def segment_parts(self, segment: list[tuple[str, int]]) -> list[Part]:
        """The parts one path segment's characters make: literals, glob parts, run-time values."""
        if self.mode.wide_globs and any(k == EXT or (k == ACT and c in _GLOB_CHARS) for c, k in segment):
            # A glob option may have changed: the segment can match anything. What it is with the
            # default options is kept, for the Ring-3 checks (embry0.safety.command_checks).
            return [Wild(glob=tuple(_glob_parts(segment)))]
        return _glob_parts(segment)


def _glob_parts(segment: list[tuple[str, int]]) -> list[Part]:
    """One path segment's parts as bash's pathname expansion reads it with its default options."""
    parts: list[Part] = []
    i = 0
    while i < len(segment):
        c, k = segment[i]
        first = i == 0
        if k in (RUN, CWD, OLD):
            parts.append(Wild())
        elif k == EXT:
            parts.append(AnyName())
        elif k == ACT and c == "*":
            while i + 1 < len(segment) and segment[i + 1] == ("*", ACT):
                i += 1
            parts.append(Star(guard=first))
        elif k == ACT and c == "?":
            parts.append(One(NOT_SLASH, guard=first))
        elif k == ACT and c == "[" and (bracket := _bracket(segment, i)) is not None:
            chars, i = bracket
            parts.append(One(chars, guard=first))
            continue
        else:
            parts.append(Lit(c))
        i += 1
    return parts


# ---- helpers -----------------------------------------------------------------------


class _Unchecked(str):
    """Standard input whose text cannot be listed: refused if the shell runs it."""


_UNCHECKED: Final[str] = _Unchecked("")


def _script_words(script: Script) -> list[str]:
    """The words of a script's simple commands, at its top level."""
    words: list[str] = []
    for chain, _ in script.items:
        for pipeline in [chain.first, *(p for _, p in chain.rest)]:
            for command in pipeline.commands:
                if isinstance(command, Simple):
                    words.extend(w.text for w in command.words)
    return words


def _first_head(script: Script) -> str | None:
    for chain, _ in script.items:
        for command in chain.first.commands:
            if isinstance(command, Simple):
                for word in command.words:
                    if not ASSIGNMENT.match(word.text):
                        return word.text
            return None
    return None


def _lit_segment(text: str) -> list[tuple[str, int]]:
    return [(c, LIT) for c in text]


def _merge(parts: list[Part]) -> tuple[Part, ...]:
    out: list[Part] = []
    for part in parts:
        if isinstance(part, Lit) and out and isinstance(out[-1], Lit):
            out[-1] = Lit(out[-1].text + part.text)
        elif isinstance(part, Wild) and out and isinstance(out[-1], Wild):
            continue
        else:
            out.append(part)
    return tuple(out)


def _plain_parts(chars: list[str], kinds: list[int]) -> list[Part]:
    return [Wild() if k in (RUN, EXT, CWD, OLD) else Lit(c) for c, k in zip(chars, kinds)]


def _name_pattern(word: Word) -> list[Part]:
    """find -name's pattern as one segment: find matches it without bash's hidden-name rule."""
    parts: list[Part] = []
    for c, k in zip(word.chars, word.kinds):
        if k in (RUN, EXT, CWD, OLD):
            parts.append(Wild())
        elif c == "*":
            parts.append(Star())
        elif c in "?[":
            parts.append(One(NOT_SLASH))
        else:
            parts.append(Lit(c))
    return parts


def _bracket(segment: list[tuple[str, int]], i: int) -> tuple[CharSet, int] | None:
    """A ``[…]`` at ``segment[i]``: the characters it matches, and the index after it; None if it does not close."""
    j = i + 1
    negated = False
    if j < len(segment) and segment[j][0] in "!^" and segment[j][1] == ACT:
        negated = True
        j += 1
    members: set[str] = set()
    anything = False
    start = j
    while j < len(segment):
        c, k = segment[j]
        if c == "]" and k == ACT and j > start:
            break
        if c == "[" and j + 1 < len(segment) and segment[j + 1][0] in ":=.":
            close = next(
                (
                    m
                    for m in range(j + 2, len(segment) - 1)
                    if segment[m][0] == segment[j + 1][0] and segment[m + 1][0] == "]"
                ),
                None,
            )
            if close is not None:
                anything = True  # a class like [:alpha:]: read as any character
                j = close + 2
                continue
        if j + 2 < len(segment) and segment[j + 1][0] == "-" and segment[j + 2][0] != "]":
            lo, hi = ord(c), ord(segment[j + 2][0])
            if hi - lo > 512:
                anything = True
            else:
                members.update(chr(v) for v in range(lo, hi + 1))
            j += 3
            continue
        members.add(c)
        j += 1
    else:
        return None
    if anything:
        return NOT_SLASH, j + 1
    if negated:
        return CharSet(None, frozenset(members) | {"/"}), j + 1
    return CharSet(frozenset(members - {"/"})), j + 1


def _landing(target: Word, source: Word) -> Word | None:
    """The file ``cp source target`` writes when ``target`` is a directory: target/<source's name>."""
    chars, kinds = list(source.chars), list(source.kinds)
    while chars and chars[-1] == "/" and kinds[-1] in (LIT, ACT):
        chars.pop()
        kinds.pop()
    cut = max((i for i, (c, k) in enumerate(zip(chars, kinds)) if c == "/" and k in (LIT, ACT)), default=-1)
    base_chars, base_kinds = chars[cut + 1 :], kinds[cut + 1 :]
    if not base_chars or "".join(base_chars) in (".", ".."):
        return None
    if base_kinds[0] in (CWD, OLD):
        base_chars, base_kinds = [WILD], [RUN]
    return Word(chars=[*target.chars, "/", *base_chars], kinds=[*target.kinds, LIT, *base_kinds])


def _home_word() -> Word:
    """A word that is $HOME, as ``cd`` alone reads it."""
    return Word(chars=[Expansion("HOME", quoted=True)], kinds=[RUN])


def _expansions(chars: list[str]) -> Iterator[Expansion]:
    """The parameter expansions among ``chars``, and inside their words."""
    for c in chars:
        if isinstance(c, Expansion):
            yield c
            for inner in (c.word, c.repl):
                if inner is not None:
                    yield from _expansions(inner.chars)


def _literal(text: str) -> Value:
    return tuple((c, LIT) for c in text)


def _indexed(elements: list[Word]) -> list[tuple[str, Word]]:
    """An array literal's ``[key]=value`` elements: each key, and its value."""
    out: list[tuple[str, Word]] = []
    for element in elements:
        m = _INDEXED.match(element.text)
        if m:
            out.append((element.text[1 : element.text.index("]")], _suffix(element, m.end())))
    return out


def _assigned_tildes(value: Word) -> Word:
    """An assignment's value with its tildes expanded, as bash expands them: an unquoted ``~``, ``~+``,
    ``~-`` or ``~user`` at its start or after an unquoted ``:``, up to a ``/`` or a ``:``."""
    chars: list[str] = []
    kinds: list[int] = []
    i = 0
    n = len(value.chars)
    while i < n:
        c, k = value.chars[i], value.kinds[i]
        at_start = i == 0 or (value.chars[i - 1] == ":" and value.kinds[i - 1] == ACT)
        if c == "~" and k == ACT and at_start:
            end = i + 1
            while end < n and value.chars[end] not in "/:":
                end += 1
            if all(kind == ACT for kind in value.kinds[i + 1 : end]):
                user = "".join(value.chars[i + 1 : end])
                if user in ("", "+", "-"):
                    name, kind = {"": ("HOME", RUN), "+": ("PWD", CWD), "-": ("OLDPWD", OLD)}[user]
                    chars.append(Expansion(name, quoted=True))
                    kinds.append(kind)
                else:
                    try:
                        home = pwd.getpwnam(user).pw_dir
                    except KeyError:
                        raise BashSyntaxError(f"cannot resolve ~{user}: no such user") from None
                    chars.extend(home)
                    kinds.extend([LIT] * len(home))
                i = end
                continue
        chars.append(c)
        kinds.append(k)
        i += 1
    return Word(chars=chars, kinds=kinds, subs=value.subs, raw=value.raw)


def _suffix(word: Word, skip: int) -> Word:
    return Word(chars=word.chars[skip:], kinds=word.kinds[skip:], subs=word.subs, raw=word.raw)


def _recursive(args: list[Word]) -> bool:
    """Whether the options ask for a recursive operation (-r, -R, -a, --recursive, --archive)."""
    for word in args:
        text = word.text
        if text in ("--recursive", "--archive"):
            return True
        if text.startswith("-") and not text.startswith("--") and set(text[1:]) & set("rRa"):
            return True
    return False


def _normalize(path: str) -> str:
    path = os.path.normpath(path)
    # normpath keeps exactly two leading slashes; Linux reads //etc as /etc.
    if path.startswith("//"):
        path = "/" + path.lstrip("/")
    return path
