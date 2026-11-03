"""What each shell command does with its arguments, for embry0.safety.bash_paths.

Which operands are data and which name files (``grep``'s pattern, ``sed``'s
script, ``jq``'s filter, a ``git`` message), which commands write what they
name, which run another command (and how their options are laid out), and
which run a shell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Final

from embry0.safety.shell_syntax import RUN, Word

# ---- options of the commands whose operands are partly data --------------------


@dataclass(frozen=True)
class Options:
    pattern_short: str = ""  # its value is a pattern or a program: data
    pattern_file_short: str = ""  # its value is a file of patterns: read, and no operand is the pattern
    file_short: str = ""  # its value is another file the command reads
    value_short: str = ""  # its value is something else: data
    pattern_long: frozenset[str] = frozenset()
    pattern_file_long: frozenset[str] = frozenset()
    file_long: frozenset[str] = frozenset()
    value_long: frozenset[str] = frozenset()
    pair_long: frozenset[str] = frozenset()  # two values, both data (jq --arg NAME VALUE)
    pair_file_long: frozenset[str] = frozenset()  # two values, the second a file read (jq --rawfile)
    first_operand_is_pattern: bool = True
    operands_written: bool = False  # git checkout/restore: the paths named are rewritten
    in_place_short: str = ""  # sed -i: makes the file operands writes; takes an optional attached suffix
    in_place_long: frozenset[str] = frozenset()


GREP = Options(
    pattern_short="e",
    pattern_file_short="f",
    value_short="ABCmdD",
    pattern_long=frozenset({"--regexp"}),
    pattern_file_long=frozenset({"--file"}),
    file_long=frozenset({"--exclude-from"}),
    value_long=frozenset(
        {
            "--after-context",
            "--before-context",
            "--context",
            "--max-count",
            "--directories",
            "--devices",
            "--include",
            "--exclude",
            "--exclude-dir",
            "--label",
            "--binary-files",
        }
    ),
)
RG = Options(
    pattern_short="e",
    pattern_file_short="f",
    value_short="ABCmgtTjMrE",
    pattern_long=frozenset({"--regexp"}),
    pattern_file_long=frozenset({"--file"}),
    file_long=frozenset({"--ignore-file"}),
    value_long=frozenset(
        {
            "--glob",
            "--iglob",
            "--type",
            "--type-not",
            "--type-add",
            "--threads",
            "--max-columns",
            "--replace",
            "--encoding",
            "--max-count",
            "--context",
            "--after-context",
            "--before-context",
            "--max-depth",
            "--max-filesize",
            "--sort",
            "--sortr",
            "--colors",
            "--context-separator",
            "--field-match-separator",
            "--path-separator",
            "--pre",
            "--pre-glob",
            "--engine",
        }
    ),
)
SED = Options(
    pattern_short="e",
    pattern_file_short="f",
    value_short="l",
    pattern_long=frozenset({"--expression"}),
    pattern_file_long=frozenset({"--file"}),
    value_long=frozenset({"--line-length"}),
    in_place_short="i",
    in_place_long=frozenset({"--in-place"}),
)
AWK = Options(
    pattern_short="e",
    pattern_file_short="f",
    file_short="i",
    value_short="vF",
    pattern_long=frozenset({"--source"}),
    pattern_file_long=frozenset({"--file"}),
    file_long=frozenset({"--include"}),
    value_long=frozenset({"--assign", "--field-separator"}),
)
JQ = Options(
    pattern_file_short="f",
    file_short="L",
    pattern_file_long=frozenset({"--from-file"}),
    value_long=frozenset({"--indent"}),
    pair_long=frozenset({"--arg", "--argjson"}),
    pair_file_long=frozenset({"--slurpfile", "--rawfile"}),
)
GIT_GREP = Options(
    pattern_short="e",
    pattern_file_short="f",
    value_short="ABCmO",
    pattern_long=frozenset({"--regexp"}),
    pattern_file_long=frozenset({"--file"}),
    value_long=frozenset({"--max-depth", "--threads", "--context", "--after-context", "--before-context"}),
)
# Any other git subcommand: messages and search strings are data, -F names a file read.
GIT_OTHER = Options(
    pattern_short="mSG",
    file_short="F",
    pattern_long=frozenset({"--message", "--grep", "--author", "--committer"}),
    file_long=frozenset({"--file"}),
    first_operand_is_pattern=False,
)
GIT_WRITE = replace(GIT_OTHER, operands_written=True)
# git subcommands that rewrite, move or remove the paths they are given.
GIT_WRITES_PATHS: Final[frozenset[str]] = frozenset({"checkout", "restore", "rm", "mv", "clean", "reset"})
PATTERN_COMMANDS: Final[dict[str, Options]] = {
    "grep": GREP,
    "egrep": GREP,
    "fgrep": GREP,
    "zgrep": GREP,
    "rgrep": GREP,
    "rg": RG,
    "ag": Options(value_short="ACBGgm"),
    "ack": Options(value_short="ABC", pattern_long=frozenset({"--match"})),
    "sed": SED,
    "awk": AWK,
    "gawk": AWK,
    "mawk": AWK,
    "nawk": AWK,
    "jq": JQ,
    "diff": Options(file_long=frozenset({"--from-file", "--to-file"}), first_operand_is_pattern=False),
}


@dataclass
class Parsed:
    data: set[int] = field(default_factory=set)  # positions in args that are data
    reads: set[int] = field(default_factory=set)  # positions that name a file read
    attached_reads: list[tuple[int, int]] = field(default_factory=list)  # (position, offset of the value)
    operands: list[int] = field(default_factory=list)
    in_place: bool = False


def parse_options(args: list[Word], spec: Options) -> Parsed:
    """Sort ``args`` into data, the files the options name, and operands."""
    out = Parsed()
    pattern_given = False
    k = 0
    only_operands = False
    while k < len(args):
        text = args[k].text
        if only_operands or text == "-" or not text.startswith("-") or args[k].kinds[:1] == [RUN]:
            out.operands.append(k)
            k += 1
            continue
        out.data.add(k)
        if text == "--":
            only_operands = True
            k += 1
            continue
        if text.startswith("--"):
            name, eq, _ = text.partition("=")
            if name in spec.pair_long or name in spec.pair_file_long:
                out.data.update({k + 1, k + 2})
                if name in spec.pair_file_long:
                    out.reads.add(k + 2)
                k += 3
                continue
            if name in spec.in_place_long:
                out.in_place = True
            elif name in spec.pattern_long or name in spec.value_long:
                pattern_given = pattern_given or name in spec.pattern_long
                if not eq:
                    out.data.add(k + 1)
                    k += 1
            elif name in spec.pattern_file_long or name in spec.file_long:
                pattern_given = pattern_given or name in spec.pattern_file_long
                if eq:
                    out.attached_reads.append((k, len(name) + 1))
                else:
                    out.reads.add(k + 1)
                    k += 1
            k += 1
            continue
        # A cluster of short options: -rn, -ePAT, -ep, -fFILE, -i.bak. An option
        # that takes a value takes the rest of the cluster, or the next word.
        for j in range(1, len(text)):
            ch = text[j]
            if ch in spec.in_place_short:
                out.in_place = True
                break
            if ch in spec.pattern_short or ch in spec.value_short:
                pattern_given = pattern_given or ch in spec.pattern_short
                if j + 1 == len(text):
                    out.data.add(k + 1)
                    k += 1
                break
            if ch in spec.pattern_file_short or ch in spec.file_short:
                pattern_given = pattern_given or ch in spec.pattern_file_short
                if j + 1 < len(text):
                    out.attached_reads.append((k, j + 1))
                else:
                    out.reads.add(k + 1)
                    k += 1
                break
        k += 1
    out.data -= out.reads
    operands = [p for p in out.operands if p not in out.data and p not in out.reads]
    if spec.first_operand_is_pattern and not pattern_given and operands:
        out.data.add(operands[0])
        operands = operands[1:]
    out.operands = operands
    return out


# ---- the commands a scan knows ---------------------------------------------------

# Commands whose operands are data: they print, compare or only stat what they are given.
DATA_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "echo",
        "printf",
        "basename",
        "dirname",
        "test",
        "[",
        "true",
        "false",
        ":",
        "export",
        "local",
        "declare",
        "readonly",
        "typeset",
        "unset",
        "unalias",
        "set",
        "shopt",
        "shift",
        "return",
        "exit",
        "break",
        "continue",
        "sleep",
        "kill",
        "wait",
        "type",
        "which",
        "hash",
        "read",
        "let",
        "seq",
        "expr",
    }
)
# The builtins that set variables from NAME=VALUE operands.
DECLARATIONS: Final[frozenset[str]] = frozenset({"export", "local", "declare", "readonly", "typeset"})
WRITE_ALL: Final[frozenset[str]] = frozenset(
    {"tee", "touch", "truncate", "mkdir", "rmdir", "rm", "unlink", "shred", "chmod", "chown", "chgrp"}
)
WRITE_LAST: Final[frozenset[str]] = frozenset({"cp", "mv", "install", "ln", "rsync"})
# Writers that reach everything under a directory when recursive.
WRITE_TREE: Final[frozenset[str]] = frozenset({"rm", "chmod", "chown", "chgrp"})
SHELLS: Final[frozenset[str]] = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash", "mksh"})
ASSIGNMENT: Final[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=")
HARMLESS_OPTIONS: Final[frozenset[str]] = frozenset(
    {
        "nullglob",
        "failglob",
        "lastpipe",
        "expand_aliases",
        "inherit_errexit",
        "pipefail",
        "errexit",
        "nounset",
        "xtrace",
        "checkwinsize",
        "histappend",
        "cmdhist",
        "huponexit",
        "globasciiranges",
        "globskipdots",
        "extquote",
        "sourcepath",
        "progcomp",
        "promptvars",
        "interactive_comments",
        "complete_fullquote",
        "force_fignore",
        "nocasematch",
        "patsub_replacement",
        "shift_verbose",
    }
)
# Variables a shell reads as the name of a file it runs.
STARTUP_FILES: Final[frozenset[str]] = frozenset({"BASH_ENV", "ENV"})
# A head that makes text read as a command when a program gets it as one argument.
CODE_HEADS: Final[frozenset[str]] = frozenset(
    {
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "tac",
        "nl",
        "od",
        "xxd",
        "hexdump",
        "strings",
        "base64",
        "cut",
        "sort",
        "uniq",
        "wc",
        "diff",
        "cmp",
        "file",
        "stat",
        "ls",
        "find",
        "tar",
        "zip",
        "unzip",
        "gzip",
        "gunzip",
        "zcat",
        "bzip2",
        "xz",
        "source",
        ".",
        "eval",
        "exec",
        "cd",
        "pushd",
        "git",
        "dd",
        "patch",
        "python",
        "python3",
        "node",
        "perl",
        "ruby",
        "php",
        "curl",
        "wget",
        "vi",
        "vim",
        "nano",
        "ed",
        "ex",
        "trap",
        "alias",
    }
)
CODE_MARKS: Final[re.Pattern[str]] = re.compile(r"[;|&<>\n`]|\$\(")


@dataclass(frozen=True)
class Runner:
    """A command that runs another: the options that take a value, and how its operands are laid out."""

    value_short: str = ""
    value_long: frozenset[str] = frozenset()
    script_short: str = ""  # its value is a command string the runner runs
    script_long: frozenset[str] = frozenset()
    read_short: str = ""  # its value is a file read
    read_long: frozenset[str] = frozenset()
    write_short: str = ""  # its value is a file written
    write_long: frozenset[str] = frozenset()
    chdir_short: str = ""  # its value is the directory the command runs in
    chdir_long: frozenset[str] = frozenset()
    skip_operands: int = 0  # operands before the command: a duration, a mask, a user
    first_operand_written: bool = False  # flock's lock file, script's log
    first_operand_read: bool = False  # chroot's new root
    joined: bool = False  # the remaining words form one command string (watch)
    assignments: bool = False  # env: NAME=value words before the command
    command_operands: bool = True  # whether its operands are a command (not script's)


RUNNERS: Final[dict[str, Runner]] = {
    "env": Runner(
        value_short="uP",
        value_long=frozenset({"--unset"}),
        script_short="S",
        script_long=frozenset({"--split-string"}),
        chdir_short="C",
        chdir_long=frozenset({"--chdir"}),
        assignments=True,
    ),
    "sudo": Runner(
        value_short="ugChprtUT",
        value_long=frozenset({"--user", "--group", "--close-from", "--host", "--prompt", "--role", "--type"}),
        chdir_short="D",
        chdir_long=frozenset({"--chdir"}),
        assignments=True,
    ),
    "doas": Runner(value_short="uC"),
    "nohup": Runner(),
    "nice": Runner(value_short="n", value_long=frozenset({"--adjustment"})),
    "time": Runner(value_short="fo", value_long=frozenset({"--format", "--output"})),
    "exec": Runner(value_short="a"),
    "timeout": Runner(value_short="sk", value_long=frozenset({"--signal", "--kill-after"}), skip_operands=1),
    "stdbuf": Runner(value_short="ioe", value_long=frozenset({"--input", "--output", "--error"})),
    "setsid": Runner(),
    "ionice": Runner(value_short="cnpPu", value_long=frozenset({"--class", "--classdata", "--pid", "--pgid"})),
    "taskset": Runner(value_short="", skip_operands=1),
    "chrt": Runner(skip_operands=1),
    "unbuffer": Runner(),
    "xargs": Runner(
        value_short="IEeLlnPsd",
        value_long=frozenset({"--delimiter", "--max-args", "--max-procs", "--max-chars", "--max-lines", "--eof"}),
        read_short="a",
        read_long=frozenset({"--arg-file"}),
    ),
    "flock": Runner(
        value_short="wE",
        value_long=frozenset({"--timeout", "--conflict-exit-code"}),
        script_short="c",
        script_long=frozenset({"--command"}),
        first_operand_written=True,
    ),
    "script": Runner(
        value_short="Ee",
        value_long=frozenset({"--echo", "--logging-format", "--output-limit"}),
        script_short="c",
        script_long=frozenset({"--command"}),
        write_short="OIBT",
        write_long=frozenset({"--log-out", "--log-in", "--log-io", "--log-timing"}),
        first_operand_written=True,
        command_operands=False,
    ),
    "su": Runner(
        value_short="sgGw",
        value_long=frozenset({"--shell", "--group"}),
        script_short="c",
        script_long=frozenset({"--command"}),
        command_operands=False,
    ),
    "runuser": Runner(
        value_short="usgGw",
        value_long=frozenset({"--user", "--shell", "--group"}),
        script_short="c",
        script_long=frozenset({"--command"}),
    ),
    "watch": Runner(value_short="nq", value_long=frozenset({"--interval", "--chgexit"}), joined=True),
    "chroot": Runner(value_long=frozenset({"--userspec", "--groups"}), first_operand_read=True),
    "busybox": Runner(),
    # Programs that trace, confine, limit or wrap the command after their options.
    "strace": Runner(value_short="eoOpPsSuEXabI", value_long=frozenset({"--output", "--trace", "--signal"})),
    "ltrace": Runner(value_short="eoOpsSuEnaAl", value_long=frozenset({"--output"})),
    "parallel": Runner(value_short="jSIEdN", value_long=frozenset({"--jobs", "--sshlogin", "--delimiter"})),
    "nsenter": Runner(value_short="tSG", value_long=frozenset({"--target", "--setuid", "--setgid"})),
    "unshare": Runner(),
    "firejail": Runner(),
    "systemd-run": Runner(value_short="pEuMH", value_long=frozenset({"--property", "--setenv", "--unit"})),
    "prlimit": Runner(value_short="p", value_long=frozenset({"--pid"})),
    "fakeroot": Runner(value_short="isl"),
    "proot": Runner(value_short="rbwqkm", value_long=frozenset({"--rootfs", "--bind", "--cwd"})),
    "xvfb-run": Runner(value_short="nsfep", value_long=frozenset({"--server-num", "--server-args"})),
    "dbus-run-session": Runner(),
    "catchsegv": Runner(),
    "valgrind": Runner(),
    "setpriv": Runner(),
    "numactl": Runner(value_short="CmNpiP"),
    "cpulimit": Runner(value_short="lpe", value_long=frozenset({"--limit", "--pid", "--exe"})),
    "faketime": Runner(skip_operands=1),
    "torsocks": Runner(),
    "proxychains": Runner(value_short="f"),
    "proxychains4": Runner(value_short="f"),
    "sg": Runner(script_short="c", skip_operands=1),
    "run0": Runner(value_short="uUgD", value_long=frozenset({"--user", "--group", "--chdir", "--unit", "--property"})),
    "pkexec": Runner(value_long=frozenset({"--user"})),
    "ssh-agent": Runner(value_short="aEPt"),
    "dbus-launch": Runner(value_long=frozenset({"--config-file"})),
    "gosu": Runner(skip_operands=1),
    "su-exec": Runner(skip_operands=1),
    "tini": Runner(value_short="sgplew"),
    "dumb-init": Runner(value_short="r", value_long=frozenset({"--rewrite"})),
    "setarch": Runner(skip_operands=1),
    "linux32": Runner(),
    "linux64": Runner(),
    "cgexec": Runner(value_short="g"),
    "chpst": Runner(value_short="uUebnlLmdofpc/"),
}
FIND_COMMANDS: Final[frozenset[str]] = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
FIND_WRITES: Final[frozenset[str]] = frozenset({"-fprint", "-fprint0", "-fprintf", "-fls"})
FIND_READS: Final[frozenset[str]] = frozenset({"-newer", "-anewer", "-cnewer", "-samefile"})
# find tests and actions that take one value, which is data.
FIND_VALUES: Final[frozenset[str]] = frozenset(
    {
        "-name",
        "-iname",
        "-path",
        "-ipath",
        "-wholename",
        "-iwholename",
        "-regex",
        "-iregex",
        "-lname",
        "-ilname",
        "-type",
        "-xtype",
        "-size",
        "-perm",
        "-user",
        "-group",
        "-uid",
        "-gid",
        "-maxdepth",
        "-mindepth",
        "-mtime",
        "-atime",
        "-ctime",
        "-mmin",
        "-amin",
        "-cmin",
        "-used",
        "-links",
        "-inum",
        "-fstype",
        "-printf",
        "-regextype",
        "-context",
    }
)
FIND_OR: Final[frozenset[str]] = frozenset({"-o", "-or", "!", "-not", "(", ")", ","})
