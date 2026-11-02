"""The paths a shell command names, sorted into reads and writes (embry0.safety.bash_paths).

evaluate_ring2_paths matches these against the deny rules, so a word read as
data here is a path no rule can refuse, and a path read as data is one the
Ring-2 check never sees. Both directions are pinned. A path is shown as its
pattern (embry0.safety.path_patterns): ``*``, ``?`` and ``[…]`` are a glob's,
``…`` is text only known at run time (any text, ``/`` included), and a path
that starts with ``…/`` is below a directory that is not known.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from typing import Any

import pytest

HOME = "/home/agent"
CWD = "/workspace"
NL = "\n"


def _module() -> Any:
    spec = importlib.util.find_spec("embry0.safety.bash_paths")
    assert spec is not None, "embry0.safety.bash_paths must sort the paths a Bash command names into reads and writes"
    import embry0.safety.bash_paths as module

    return module


def _work_module() -> Any:
    spec = importlib.util.find_spec("embry0.safety.shell_work")
    assert spec is not None, "embry0.safety.shell_work must count the work reading a command costs, in fixed units"
    import embry0.safety.shell_work as module

    return module


def _paths(command: str, cwd: str = CWD) -> tuple[set[str], set[str]]:
    bash_paths: Callable[..., Any] = _module().bash_paths
    try:
        found = bash_paths(command, cwd=cwd, home=HOME)
    except TypeError:
        raise AssertionError("bash_paths(command, cwd=, home=) must resolve paths against the directory") from None
    except ValueError as exc:  # BashSyntaxError
        raise AssertionError(f"{command!r} is a command bash runs; bash_paths could not read it: {exc}") from None

    return {r.path for r in found.reads}, {w.path for w in found.writes}


def _check(command: str, reads: set[str], writes: set[str], cwd: str = CWD) -> None:
    got_reads, got_writes = _paths(command, cwd)
    assert (got_reads, got_writes) == (reads, writes), (
        f"{command!r}: want reads={sorted(reads)} writes={sorted(writes)}, "
        f"got reads={sorted(got_reads)} writes={sorted(got_writes)}"
    )


def _syntax_error() -> type[Exception]:
    error = getattr(_module(), "BashSyntaxError", None)
    assert error is not None, "bash_paths must raise BashSyntaxError for a command it cannot read"
    return error


def _reads_contain(command: str, path: str) -> None:
    reads, writes = _paths(command)
    assert path in reads | writes, f"{command!r} names {path}; got reads={sorted(reads)} writes={sorted(writes)}"


# ---- the basics ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "reads", "writes"),
    [
        ("cat /etc/passwd", {"/etc/passwd"}, set()),
        ("python3 tool.py /etc/passwd", {"/workspace/tool.py", "/etc/passwd"}, set()),
        ("cat a/b ../c ./d", {"/workspace/a/b", "/c", "/workspace/d"}, set()),
        ("/usr/bin/env true", {"/usr/bin/env"}, set()),
        ("cat '/e'\"tc\"/passwd", {"/etc/passwd"}, set()),
        ("cat /e\\tc/passwd", {"/etc/passwd"}, set()),
        ("cat ~/.ssh/id_rsa", {f"{HOME}/.ssh/id_rsa"}, set()),
        ("cat $HOME/.aws/credentials ${HOME}/.gnupg/x", {f"{HOME}/.aws/credentials", f"{HOME}/.gnupg/x"}, set()),
        ("cd /tmp && cat /root/x | wc -l; ls /proc" + NL + "stat /etc", {"/tmp", "/root/x", "/proc", "/etc"}, set()),
        ("cat < /etc/shadow > out/log", {"/etc/shadow"}, {"/workspace/out/log"}),
        ("cmd &>/dev/null; cmd >| a/b; cmd 2>&1", set(), {"/dev/null", "/workspace/a/b"}),
        ("cat /etc/passwd # a comment naming /root/x", {"/etc/passwd"}, set()),
    ],
)
def test_a_commands_paths(command: str, reads: set[str], writes: set[str]) -> None:
    _check(command, reads, writes)


# ---- here-documents ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "reads"),
    [
        # the delimiter is EOF once its quotes are removed, so the body ends at EOF
        ('cat <<"E"OF >/dev/null' + NL + "x" + NL + "EOF" + NL + "cat /etc/passwd", {"/etc/passwd"}),
        ("cat <<'EOF' >/dev/null" + NL + "x" + NL + "EOF" + NL + "cat /etc/passwd", {"/etc/passwd"}),
        ("cat <<\\EOF >/dev/null" + NL + "x" + NL + "EOF" + NL + "cat /etc/passwd", {"/etc/passwd"}),
        # <<EOF inside quotes opens no here-document
        ("echo '<<EOF'" + NL + "cat /etc/passwd" + NL + "EOF", {"/etc/passwd"}),
        ('echo "<<EOF"' + NL + "cat /root/x", {"/root/x"}),
        # an unquoted delimiter expands the body: its substitutions run
        ("cat <<EOF" + NL + "$(cat /etc/passwd)" + NL + "EOF", {"/etc/passwd"}),
        ("cat <<EOF" + NL + "`cat /etc/passwd`" + NL + "EOF", {"/etc/passwd"}),
        # a quoted delimiter keeps the body as text
        ("cat <<'EOF'" + NL + "$(cat /etc/passwd)" + NL + "EOF", set()),
        ("cat <<-EOF" + NL + "\techo /etc/x" + NL + "\tEOF" + NL + "cat /root/y", {"/root/y"}),
        # a here-document inside a bash -c script
        ("bash -c 'cat <<EOF" + NL + "$(cat /etc/passwd)" + NL + "EOF'", {"/etc/passwd"}),
    ],
)
def test_a_here_document_ends_where_the_shell_ends_it(command: str, reads: set[str]) -> None:
    got, _ = _paths(command)
    assert got == reads, f"{command!r}: want reads={sorted(reads)}, got {sorted(got)}"


# ---- patterns, scripts and the files options name -------------------------------


@pytest.mark.parametrize(
    ("command", "reads"),
    [
        ("grep -eroot /etc/passwd", {"/etc/passwd"}),
        ("grep --regexp=root /etc/passwd", {"/etc/passwd"}),
        ("grep -rnePAT /etc", {"/etc"}),
        ("grep -e root -- /etc/passwd", {"/etc/passwd"}),
        ("sed -ep /etc/passwd", {"/etc/passwd"}),
        ("sed -n -ep /etc/passwd", {"/etc/passwd"}),
        ("sed --expression=p /etc/passwd", {"/etc/passwd"}),
        ("grep -f/etc/patterns src", {"/etc/patterns", "/workspace/src"}),
        ("grep --file=/etc/patterns src", {"/etc/patterns", "/workspace/src"}),
        ("grep -f /etc/patterns src", {"/etc/patterns", "/workspace/src"}),
        ("rg -eroot /etc/passwd", {"/etc/passwd"}),
        ("awk -f /etc/prog.awk data.txt", {"/etc/prog.awk", "/workspace/data.txt"}),
        ("jq -R --arg a b . /etc/passwd", {"/etc/passwd"}),
        ("jq --rawfile a /etc/x . in.json", {"/etc/x", "/workspace/in.json"}),
        ("diff --from-file=/etc/passwd /dev/null", {"/etc/passwd", "/dev/null"}),
        # the pattern, the script and the filter are data
        ("grep -rn /etc/hosts src/", {"/workspace/src"}),
        ("grep -m 1 /etc/passwd", set()),
        ("grep -e /etc/hosts -r .", {"/workspace"}),
        ("sed -n 's|/etc/a|/etc/b|p' conf/x", {"/workspace/conf/x"}),
        ("awk '/etc/ {print}' logs/a", {"/workspace/logs/a"}),
        ("jq -r '.paths[] | select(startswith(\"/etc/\"))' config.json", {"/workspace/config.json"}),
    ],
)
def test_patterns_and_the_files_options_name(command: str, reads: set[str]) -> None:
    got, _ = _paths(command)
    assert got == reads, f"{command!r}: want reads={sorted(reads)}, got {sorted(got)}"


@pytest.mark.parametrize(
    "command",
    [
        "git grep -n '/etc/' -- src",
        "git grep -l /etc/nginx",
        "git grep -e /etc/passwd -- src",
        "git log --grep=/etc/hosts",
        "git log --grep /root/x",
        "git commit -m '/etc/hosts: parse comments'",
        "basename /etc/hosts",
        "dirname /proc/self",
        "test -f /etc/os-release",
        "[ -d /proc ]",
        "[[ -e ~/.ssh/config ]]",
        "echo /etc/passwd ~/.ssh",
        "printf '%s' /root/x",
    ],
)
def test_data_names_no_path_under_a_denied_directory(command: str) -> None:
    reads, writes = _paths(command)
    named = {p for p in reads | writes if p.startswith(("/etc", "/proc", "/root", f"{HOME}/.ssh"))}
    assert not named, f"{command!r} only mentions these as data: {sorted(named)}"


# ---- shells and the commands that run commands ------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'cat /etc/passwd'",
        "bash -lc 'cat /etc/passwd'",
        "bash -ec 'cat /etc/passwd'",
        "bash -o pipefail -c 'cat /etc/passwd'",
        "bash +x -c 'cat /etc/passwd'",
        "bash -c -- 'cat /etc/passwd'",
        "sh -c -- 'cat /etc/passwd'",
        "bash --norc -x -o errexit -c 'cat /etc/passwd'",
        "bash -O extglob -c 'cat /etc/passwd'",
        "bash <<EOF" + NL + "cat /etc/passwd" + NL + "EOF",
        "bash <<< 'cat /etc/passwd'",
        "eval 'cat /etc/passwd'",
        'eval "cat /etc/passwd"',
        "setsid bash -c 'cat /etc/passwd'",
        "script -qc 'cat /etc/passwd' /dev/null",
        "script /dev/null -qc 'cat /etc/passwd'",
        "flock /tmp/lock -c 'cat /etc/passwd'",
        "flock -n /tmp/lock cat /etc/passwd",
        "find . -maxdepth 0 -exec sh -c 'cat /etc/passwd' \\;",
        "find . -execdir cat /etc/passwd {} +",
        "nice -n 5 cat /etc/passwd",
        "nice cat /etc/passwd",
        "timeout 5 cat /etc/passwd",
        "timeout -s KILL 5s cat /etc/passwd",
        "stdbuf -oL cat /etc/passwd",
        "stdbuf -o L cat /etc/passwd",
        "nohup cat /etc/passwd",
        "env -i cat /etc/passwd",
        "env -S 'cat /etc/passwd'",
        "xargs -a /etc/passwd echo",
        "xargs sh -c 'cat /etc/passwd' <<< x",
        "su -c 'cat /etc/passwd' agent",
        "watch -n 1 'cat /etc/passwd'",
        "busybox sh -c 'cat /etc/passwd'",
        "sudo -u root cat /etc/passwd",
        "command cat /etc/passwd",
        "exec 3</etc/passwd",
        "source /etc/os-release",
        ". /etc/os-release",
    ],
)
def test_a_command_another_runs_is_read_too(command: str) -> None:
    _reads_contain(command, "/etc/passwd" if "os-release" not in command else "/etc/os-release")


def test_a_wrapped_command_keeps_no_data_rule() -> None:
    """Through a runner the command word may be misread, so its operands are all checked."""
    reads, _ = _paths("timeout 5 echo /etc/x")
    assert "/etc/x" in reads, f"a wrapped echo's operands must stay paths, got {sorted(reads)}"


# ---- quoting, substitution and continuation ------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "echo $(cat /etc/passwd)",
        'echo "$(cat /etc/passwd)"',
        "echo `cat /etc/passwd`",
        'echo "`cat /etc/passwd`"',
        'ls "$(cat /etc/passwd | head -1)"',
        'X="$(cat /etc/passwd)"; echo "$X"',
        'cat <<< "$(cat /etc/passwd)"',
        'echo "$(</etc/passwd)"',
        'printf %s "$(< /etc/passwd)"',
        "diff <(cat /etc/passwd) /dev/null",
        'echo "${X:-$(cat /etc/passwd)}"',
        "echo $((1 + $(cat /etc/passwd | wc -l)))",
        "cat /et\\" + NL + "c/passwd",
        "cat $'/etc/passwd'",
        "cat $'\\x2fetc/passwd'",
        'cat $"/etc/passwd"',
        "cat ${HOME:-/x}/../../etc/passwd",
        "true && cat /etc/passwd",
        "(cat /etc/passwd)",
        "{ cat /etc/passwd; }",
        "if true; then cat /etc/passwd; fi",
        "f() { cat /etc/passwd; }; f",
    ],
)
def test_substitutions_quotes_and_continuations(command: str) -> None:
    _reads_contain(command, "/etc/passwd")


# ---- globs, braces and values only known at run time ---------------------------


@pytest.mark.parametrize(
    ("command", "reads"),
    [
        ("cat /et?/passwd", {"/et?/passwd"}),
        ("cat /e*/passwd", {"/e*/passwd"}),
        ("cat /[e]tc/passwd", {"/[e]tc/passwd"}),
        ("cat ~/.ss?/canary", {f"{HOME}/.ss?/canary"}),
        ("cat /etc/$X", {"/etc/…"}),
        ("cat /workspace/${name}.log", {"/workspace/….log"}),
        ("cat src/*.py", {"/workspace/src/*.py"}),
        ("ls /*", {"/*"}),
        ("cat /{etc,x}/passwd", {"/etc/passwd", "/x/passwd"}),
        ("cat /e{t,}c/passwd", {"/etc/passwd", "/ec/passwd"}),
        ("cat {/etc/passwd,x}", {"/etc/passwd", "/workspace/x"}),
        ("cat f{1..3}", {"/workspace/f1", "/workspace/f2", "/workspace/f3"}),
        # quoted, a glob character is a character
        ("cat '/et?/passwd'", {"/et?/passwd"}),
        # a value that starts unknown names nothing this module can know
        ("cat $F", set()),
        # a for loop's words are read as paths, its body as commands
        ("for f in src/*.py; do python -m py_compile $f; done", {"/workspace/src/*.py", "/workspace/py_compile"}),
    ],
)
def test_globs_braces_and_run_time_values(command: str, reads: set[str]) -> None:
    got, _ = _paths(command)
    assert got == reads, f"{command!r}: want reads={sorted(reads)}, got {sorted(got)}"


# ---- cd -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "path"),
    [
        ("cd / && cat etc/passwd", "/etc/passwd"),
        ("cd .. && cat etc/passwd", "/etc/passwd"),
        ("cd $HOME && cat .ssh/canary", f"{HOME}/.ssh/canary"),
        ("cd ~ && cat .ssh/canary", f"{HOME}/.ssh/canary"),
        ("cd && cat .ssh/canary", f"{HOME}/.ssh/canary"),
        ("cd /etc; cat passwd", "/etc/passwd"),
        ("pushd / >/dev/null; cat etc/passwd", "/etc/passwd"),
        ("cd src && cat ../../etc/passwd", "/etc/passwd"),
        ("cd - && cat etc/passwd", "…/etc/passwd"),  # not known: below any directory
    ],
)
def test_cd_moves_where_relative_paths_resolve(command: str, path: str) -> None:
    _reads_contain(command, path)


def test_a_write_after_cd_lands_in_the_new_directory() -> None:
    _, writes = _paths("cd .claude && echo '{}' > settings.json")
    assert "/workspace/.claude/settings.json" in writes, sorted(writes)


# ---- writes ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "written"),
    [
        ("echo x >> .claude/settings.json", "/workspace/.claude/settings.json"),
        ("true | tee -a .claude/settings.local.json", "/workspace/.claude/settings.local.json"),
        ("cp src/a .claude/settings.json", "/workspace/.claude/settings.json"),
        ("cp -t/usr/bin x", "/usr/bin"),
        ("cp --target-directory=/usr/bin x", "/usr/bin"),
        ("cp settings.json .claude/", "/workspace/.claude/settings.json"),
        ("cp -r x/. /workspace/.claude", "/workspace/.claude/…"),
        ("mv .claude .old", "/workspace/.claude/…"),
        ("rm -rf .claude", "/workspace/.claude/…"),
        # a source's name lands in the destination directory, a glob's included
        ("cp *.json .claude/", "/workspace/.claude/*.json"),
        ("find . -name settings.json -delete", "/workspace/…settings.json"),
        ("find . -exec rm {} +", "/workspace/…"),
        ("touch /usr/local/bin/x", "/usr/local/bin/x"),
        ("sed -i 's/a/b/' conf/app.ini", "/workspace/conf/app.ini"),
        ("sed -i.bak 's/x/y/' .claude/settings.json", "/workspace/.claude/settings.json"),
        ("dd if=/etc/passwd of=/usr/x", "/usr/x"),
        ("patch .claude/settings.json < p.diff", "/workspace/.claude/settings.json"),
        ("patch -o .claude/settings.json in.json p.diff", "/workspace/.claude/settings.json"),
        ("git checkout -- .claude/settings.json", "/workspace/.claude/settings.json"),
        ("git restore .claude/settings.json", "/workspace/.claude/settings.json"),
    ],
)
def test_writes(command: str, written: str) -> None:
    _, writes = _paths(command)
    assert written in writes, f"{command!r} writes {written}; got writes={sorted(writes)}"


def test_git_add_and_diff_only_read() -> None:
    _, writes = _paths("git add .claude/settings.json && git diff .claude/settings.json")
    assert not writes, sorted(writes)


# ---- ~user ----------------------------------------------------------------------


def test_tilde_user_is_that_users_home() -> None:
    reads, _ = _paths("cat ~root/.bashrc")
    assert reads == {"/root/.bashrc"}, f"~root is /root, not $HOME + 'root': {sorted(reads)}"


def test_tilde_of_an_unknown_user_cannot_be_read() -> None:
    with pytest.raises(_syntax_error(), match="nosuchuser"):
        _module().bash_paths("cat ~nosuchuser-000001/x", cwd=CWD, home=HOME)


# ---- what cannot be read ----------------------------------------------------------


@pytest.mark.parametrize("command", ["cat /etc/passwd 'unterminated", 'echo "$(cat /etc/passwd', "echo `cat"])
def test_a_command_bash_could_not_parse_cannot_be_read(command: str) -> None:
    with pytest.raises(_syntax_error()):
        _module().bash_paths(command, cwd=CWD, home=HOME)


# ---- where relative paths resolve: the directory as a set --------------------------


@pytest.mark.parametrize(
    ("command", "path"),
    [
        # a cd that fails leaves the shell where it was, and ; runs the next command anyway
        ("cd /nonexistent/b; cat ../etc/passwd", "/etc/passwd"),
        ("cd /nonexistent/b; cat ../etc/passwd", "/nonexistent/etc/passwd"),
        # a cd another program runs moves only that program
        ("env cd /nonexistent/b && cat ../etc/passwd", "/etc/passwd"),
        ("(cd /nonexistent/b) && cat ../etc/passwd", "/etc/passwd"),
        ("cd /nonexistent/b | cat && cat ../etc/passwd", "/etc/passwd"),
        # with lastpipe on, a pipeline's last command runs in the shell itself
        ("true | cd /etc && cat passwd", "/etc/passwd"),
        # a command only known at run time may be any function defined so far
        ("f() { cd /etc; }; $X; cat passwd", "/etc/passwd"),
        # cd through a builtin wrapper, eval, a function or an alias moves the shell
        ("builtin cd /etc && cat passwd", "/etc/passwd"),
        ("command cd /etc && cat passwd", "/etc/passwd"),
        ("eval cd /etc && cat passwd", "/etc/passwd"),
        ("f() { cd /etc; }; f && cat passwd", "/etc/passwd"),
        ("alias go='cd /etc'" + NL + "go && cat passwd", "/etc/passwd"),
        ("alias c=cd" + NL + "c /etc && cat passwd", "/etc/passwd"),
        # a function's body is read where it is called, in that directory
        ("f() { cat passwd; }; cd /etc && f", "/etc/passwd"),
        # ~+ and $PWD are the directory the shell is in
        ("cd /etc && cat ~+/passwd", "/etc/passwd"),
        ('cd /etc && cat "$PWD/passwd"', "/etc/passwd"),
        ('cd "$PWD/.claude" && cat settings.json', "/workspace/.claude/settings.json"),
        # env -C, sudo -D and git -C run their command elsewhere
        ("env -C /etc cat passwd", "/etc/passwd"),
        ("env --chdir=/etc cat passwd", "/etc/passwd"),
        ("sudo -D /etc cat passwd", "/etc/passwd"),
        ("git -C ~ diff --no-index .ssh/canary /dev/null", f"{HOME}/.ssh/canary"),
    ],
)
def test_the_directories_a_command_can_be_in(command: str, path: str) -> None:
    _reads_contain(command, path)


@pytest.mark.parametrize(
    "command",
    [
        "CDPATH=/ cd etc && cat passwd",
        "export CDPATH=/; cd etc; cat passwd",
        "printf -v CDPATH /; cd etc && cat passwd",
        "cat /tmp/$X/../passwd",
        "cd ~/.ss? && cat canary",
        "cd /e?c && cat passwd",
        'cd "$(echo ~)" && cat .ssh/canary',
        "cd ${X%/} && cat .ssh/canary",
        "cd ~; cd /tmp; cd - >/dev/null; cat .ssh/canary",
        'set -- ~; cd "$1" && cat .ssh/canary',
        "cd .cl?ude && echo '{}' > settings.json",
        "cd ~- && cat x",
        "cat ~-/x",
        "pushd +1 && cat x",
        "popd && cat x",
        'cd "$(dirname /etc/passwd)" && cat passwd',
        "find . -path ./x -execdir cat passwd \\;",
        "f() { cat x; cd ..; f; }; f",
        "while true; do cd x; done; cat y",
        "for d in a b; do cd $d; done; cat x",
    ],
)
def test_a_relative_path_in_a_directory_that_is_not_known_can_be_anywhere(command: str) -> None:
    reads, writes = _paths(command)
    assert any(p.startswith("…/") for p in reads | writes), (
        f"{command!r}: a relative path there can be below any directory; got reads={sorted(reads)} "
        f"writes={sorted(writes)}"
    )


def test_a_glob_cd_target_is_still_read() -> None:
    reads, _ = _paths("cd /e?c")
    assert "/e?c" in reads, f"cd /e?c reads the directories the glob matches: {sorted(reads)}"


def test_a_trap_runs_in_every_directory_the_command_was_in() -> None:
    reads, _ = _paths("trap 'cat passwd' EXIT; cd /etc")
    assert "/etc/passwd" in reads, f"the EXIT trap runs after cd /etc: {sorted(reads)}"


# ---- forms that once hid a command ------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        'echo "`cat \\"/etc/passwd\\"`"',
        "echo $((cat /etc/passwd) )",
        'echo "$((cat /etc/passwd) )"',
        'echo "$(case x in x) cat /etc/passwd;; esac)"',
        'echo "$(case x in (x) cat /etc/passwd;; esac)"',
        "echo \"$(cat <<'X'" + NL + ")" + NL + "X" + NL + 'cat /etc/passwd)"',
        "x=$(cat <<'X'" + NL + "(" + NL + "X" + NL + 'cat /etc/passwd); echo "$x"',
        "cat $'\\0'/etc/passwd",
        "cat $'\\x00'/etc/passwd",
        "cat $'\\u0000'/etc/passwd",
        "cat $'\\c@'/etc/passwd",
        "cat \"\"$'\\0'/etc/passwd",
        "cat ~+/../../etc/passwd",
        "(( y = 1 << 2 ))" + NL + "cat /etc/passwd",
        "echo $[1<<2]" + NL + "cat /etc/passwd",
        "for (( i = 1 << 2; i < 1; i++ )); do :; done" + NL + "cat /etc/passwd",
        "trap 'cat /etc/passwd' EXIT",
        "git -c alias.x='!cat /etc/passwd' x",
        "git -c core.pager='cat /etc/passwd' log -1",
        "git -c core.sshCommand='cat /etc/passwd' fetch",
        "git rebase -x 'cat /etc/passwd' HEAD~1",
        "git submodule foreach 'cat /etc/passwd'",
        "bash -s -- arg <<'X'" + NL + "cat /etc/passwd" + NL + "X",
        "bash -c 'cat \"$1\"' _ /etc/passwd",
        "cat <<EOF\r" + NL + "x" + NL + "EOF\r" + NL + "cat /etc/passwd" + NL + "EOF",
        "cat <<$X" + NL + "x" + NL + "$X" + NL + "cat /etc/passwd" + NL + "$",
        # an unquoted body joins a line ending in a backslash with the next: E\ + OF ends it
        "cat <<EOF" + NL + "E\\" + NL + "OF" + NL + "cat /etc/passwd" + NL + "EOF",
        "arr=(a $(cat /etc/passwd)); echo ${arr[0]}",
        "!(cat /etc/passwd)",
        "alias x='cat /etc/passwd'" + NL + "x",
        "BASH_ENV=/etc/passwd bash -c true",
        "strace -f sh -c 'cat /etc/passwd'",
        "nsenter -t 1 sh -c 'cat /etc/passwd'",
        "cat {~root,x}/.bashrc",
        "find . -exec sh -c 'cat /etc/passwd' \\;",
        "[[ -n $(cat /etc/passwd) ]]",
        "case $(cat /etc/passwd) in *) ;; esac",
        "f() { cat /etc/passwd; }",
        "X=f; f() { cat /etc/passwd; }; $X",
        "coproc cat /etc/passwd",
        "if cat /etc/passwd; then :; fi",
        "while cat /etc/passwd; do break; done",
        "{ cat; } < /etc/passwd",
    ],
)
def test_every_command_a_form_runs_is_read(command: str) -> None:
    reads, writes = _paths(command)
    named = {p for p in reads | writes if p in ("/etc/passwd", "/root/.bashrc") or p.startswith("/etc/passwd")}
    assert named, f"{command!r} reads /etc/passwd (or /root/.bashrc) when bash runs it; got {sorted(reads | writes)}"


def _refused(command: str, why: str) -> str:
    try:
        found = _module().bash_paths(command, cwd=CWD, home=HOME)
    except _syntax_error() as exc:
        return str(exc)
    raise AssertionError(f"{command!r} must be refused ({why}); it was read as {found}")


def test_a_nul_cannot_reach_the_scanner() -> None:
    message = _refused("cat /etc/passwd\x00", "no shell can be given a NUL")
    assert "NUL" in message, message


def test_git_config_env_cannot_be_checked() -> None:
    message = _refused("git --config-env=alias.x=X x", "the setting's value comes from the environment")
    assert "config-env" in message, message


# ---- globs as bash expands them ----------------------------------------------------


@pytest.mark.parametrize(
    ("command", "written"),
    [
        ("rm -f *.log", "/workspace/*.log"),
        ("sed -i 's/a/b/' src/*.py", "/workspace/src/*.py"),
        ("rm -rf .*", "/workspace/.*"),
        ("rm .claude/*", "/workspace/.claude/*"),
        ("touch file{1..100}.txt", "/workspace/file*.txt"),
    ],
)
def test_a_glob_is_kept_as_a_pattern(command: str, written: str) -> None:
    _, writes = _paths(command)
    assert written in writes, f"{command!r} writes what {written} matches; got {sorted(writes)}"


@pytest.mark.parametrize(
    "command",
    [
        "shopt -s dotglob" + NL + "rm *",
        "shopt -s globstar; rm **",
        "GLOBIGNORE=x; rm *",
        "BASHOPTS=dotglob bash -c 'rm *'",
    ],
)
def test_a_glob_option_makes_every_glob_match_anything(command: str) -> None:
    _, writes = _paths(command)
    assert "/workspace/…" in writes, f"{command!r} may glob hidden names too; got {sorted(writes)}"


@pytest.mark.parametrize(
    ("command", "reads"),
    [
        ("cat x{1..100000000}", {"/workspace/x*"}),
        ("cat " + "{a,b}" * 40, {"/…"}),
        ("cat " + "{a," * 40 + "b" + "}" * 40, {"/…"}),
        ("cat {1..3..2}", {"/workspace/1", "/workspace/3"}),
        ("cat {a..e..2}", {"/workspace/a", "/workspace/c", "/workspace/e"}),
        ("cat {01..3}", {"/workspace/01", "/workspace/02", "/workspace/03"}),
    ],
)
def test_a_brace_expansion_is_counted_before_it_is_listed(
    command: str, reads: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import embry0.safety.brace_expansion as braces

    expand = braces._expand

    def counted(chars: list[str], kinds: list[int], regions: Any) -> Any:
        # Asked to list more than the cap: fail before listing, rather than run out of memory.
        words = braces._brace_count(chars, kinds)
        assert words <= braces.MAX_BRACE_ALTERNATIVES, (
            f"{command[:60]!r}: listing {words}+ words; a brace expansion must be counted, not listed"
        )
        return expand(chars, kinds, regions)

    monkeypatch.setattr(braces, "_expand", counted)
    got, _ = _paths(command)
    assert got == reads, f"{command[:60]!r}: want reads={sorted(reads)}, got {sorted(got)[:5]}"


@pytest.mark.parametrize(
    ("command", "path"),
    [
        ("cat ${x:-/etc/passwd}", "/etc/passwd"),
        ("HOME=/etc; cat ~/passwd", "/etc/passwd"),
        ("HOME=/etc; cat ~/passwd", f"{HOME}/passwd"),
        ("cat ~/x; HOME=/etc", "/etc/x"),
        ("F=/etc/passwd; cat $F", "/etc/passwd"),
        ('X="a /etc/passwd"; cat $X', "/etc/passwd"),
        ("cat $F; F=/etc/passwd", "/etc/passwd"),
        ("Y=/etc; X=$Y/passwd; cat $X", "/etc/passwd"),
        # IFS, lists, positional parameters, transformations, indirection, namerefs, printf -v
        ("IFS=,; x=cat,/etc/passwd; $x", "/etc/passwd"),
        ("x=cat:/etc/passwd; IFS=: eval '$x'", "/etc/passwd"),
        ('set -- cd /; "$@"; cat etc/passwd', "/etc/passwd"),
        ('a=(cd /); "${a[@]}"; cat etc/passwd', "/etc/passwd"),
        ('f() { "$@"; }; f cd /; cat etc/passwd', "/etc/passwd"),
        ("bash -c '\"$@\"; cat etc/passwd' x cd /", "/etc/passwd"),
        ('set -- x cd /; shift; "$@"; cat etc/passwd', "/etc/passwd"),
        ('a=(x cat /etc/passwd); "${a[@]:1}"', "/etc/passwd"),
        ('a=(x cat /etc/pass*); "${a[@]:1}"', "/etc/pass*"),
        ('set -- x cat /etc/passwd; "${@:2}"', "/etc/passwd"),
        ("x=/etc/passwdX; cat ${x%X}", "/etc/passwd"),
        ("x=/Xtc/passwd; cat ${x//X/e}", "/etc/passwd"),
        ("x=XX/etc/passwd; cat ${x:2}", "/etc/passwd"),
        ("x='\\x2fetc\\x2fpasswd'; cat ${x@E}", "/etc/passwd"),
        ("y=/etc/passwd; x=y; cat ${!x}", "/etc/passwd"),
        ("declare -n r=y; y=/etc/passwd; cat $r", "/etc/passwd"),
        ("declare -n r=y; r=/etc/passwd; cat $y", "/etc/passwd"),
        ("printf -v p '%b' '\\x2fetc\\x2fpasswd'; cat $p", "/etc/passwd"),
        ("printf -v p '%s/%s' /etc passwd; cat $p", "/etc/passwd"),
        ('printf -v p %s "$PWD"; cd /; cd "$p"; cat x', "/workspace/x"),
        ("x=cd; ${x} /; cat etc/passwd", "/etc/passwd"),
    ],
)
def test_a_variable_names_each_value_the_command_gives_it(command: str, path: str) -> None:
    _reads_contain(command, path)


@pytest.mark.parametrize(
    ("command", "path"),
    [
        ("x=X$PWD/../etc/passwd; cat ${x#X}", "/…"),
        ("x=" + "a" * 5000 + "; cat $x", "/…"),
        ("x=/etc/$(f)X; cat ${x%X}", None),
    ],
)
def test_a_transformed_directory_or_long_value_can_be_anywhere(command: str, path: str | None) -> None:
    """The shell's directory cut or replaced, or a value too long to keep, can be anywhere; a
    transformation of a value only known at run time names no path this module can know."""
    reads, _ = _paths(command)
    if path is None:
        assert not any(r.startswith("/etc") for r in reads), f"{command!r}: {sorted(reads)}"
    else:
        assert path in reads, f"{command!r}: a value that can be anywhere must read {path}; got {sorted(reads)}"


def test_an_append_names_each_value_once_and_widens_in_a_loop() -> None:
    reads, _ = _paths('x=a; x+=b; x+=c; cat "$x"')
    assert "/workspace/ab" in reads and any(r.startswith("/workspace/abc") for r in reads), (
        f"each append is after each value: {sorted(reads)}"
    )
    assert all(r.startswith("/workspace/a") for r in reads), f"no value grows to anywhere: {sorted(reads)}"
    reads, _ = _paths('x=a; while :; do x+=b; done; cat "$x"')
    assert "/workspace/ab" in reads and any(r.startswith("/workspace/ab") and "…" in r for r in reads), (
        f"in a loop an appended value stands for any number of appends: {sorted(reads)}"
    )
    reads, _ = _paths('x=/workspace/a; while :; do x+=/..; done; cat "$x/etc/passwd"')
    assert any(r.startswith("/…") or r.startswith("…") for r in reads), (
        f"one that climbs can be anywhere: {sorted(reads)}"
    )


def test_a_quoted_value_is_not_split() -> None:
    reads, _ = _paths('X="a /etc/passwd"; cat "$X"')
    assert "/workspace/a /etc/passwd" in reads and "/etc/passwd" not in reads, sorted(reads)


# Hostile commands, each with the kind of work whose limit it reaches (None: another
# bound, such as a value's length or the nesting of runners, ends it first).
_HOSTILE = [
    ("a value concatenated 2000 times", "a=" + "x" * 30000 + "; b=" + "$a" * 2000 + "; cat $b", None),
    ("a replacement over a long value", "x=" + "a" * 30000 + "; cat ${x//a/b}", None),
    ("3000 replacements", "x=" + "ab" * 200 + "; " + " ".join(["cat ${x//a/b};"] * 3000), "transform"),
    ("a starry pattern on a long value", "x=" + "a" * 20000 + "; cat ${x#*a*a*a*a*a*a*a*a*a*a*b}", None),
    ("IFS with 16 values", "".join(f"IFS={chr(97 + i)}; " for i in range(16)) + " ".join(["cat $x$y;"] * 2000), None),
    ("16 lists as commands", "".join(f"set -- a{i} b{i}; " for i in range(16)) + " ".join(['"$@";'] * 2000), "calls"),
    (
        "16 values squared, 4000 times",
        "".join(f"x=a{i}; " for i in range(16)) + " ".join(["cat $x$x;"] * 4000),
        "words",
    ),
    ("a long function called 2000 times", "f() { " + "((1)); " * 1000 + "}; " + "f; " * 2000, "commands"),
    ("a long function's simple commands", "f() { " + ": ; " * 1000 + "}; " + "f; " * 2000, "calls"),
    ("printf with a huge width", "printf -v x '%999999999s' a; cat $x", None),
    ("a chain of 500 namerefs", "; ".join(f"declare -n r{i}=r{i + 1}" for i in range(500)) + "; cat $r0", None),
    ("eval eval eval …", "eval " * 12000, "parsed"),
    ("env -u A env -u A …", "env -u A " * 7000, None),
    ("bash -c bash -c …", "bash -c " * 7000, None),
    ("a function given 15000 arguments", "f() { :; }; f " + "a " * 15000, None),
]
# How far one step can carry a count past its limit before the check sees it.
_OVERSHOOT = {"commands": 1, "calls": 1, "words": 64 * 64, "parsed": 64 * 1024, "transform": 4097}


def _spent(command: str) -> tuple[Any, str | None]:
    """The work reading ``command`` spent, and why it was refused (None: it was read)."""
    m = _module()
    Work = _work_module().Work

    work = Work()
    try:
        m.bash_paths(command[: 64 * 1024], cwd=CWD, home=HOME, work=work)
    except m.BashSyntaxError as exc:
        return work, str(exc)
    return work, None


@pytest.mark.parametrize(("name", "command", "limit"), _HOSTILE, ids=[n for n, _, _ in _HOSTILE])
def test_hostile_commands_cost_bounded_work(name: str, command: str, limit: str | None) -> None:
    """Every kind of work a command costs is counted in units no machine changes, and stops at its
    limit: the bound on how long a check takes holds on a slow runner as on a fast one."""
    work, _ = _spent(command)
    for kind, spent in work.spent.items():
        assert spent <= work.limits[kind] + _OVERSHOOT[kind], (
            f"{name}: {kind} work went to {spent}, past its limit of {work.limits[kind]}"
        )
    if limit is not None:
        assert work.spent[limit] > work.limits[limit], (
            f"{name}: its {limit} work stopped at {work.spent[limit]}, short of the limit "
            f"{work.limits[limit]} it is meant to reach: is that work still counted?"
        )


def test_hostile_commands_take_bounded_time_all_together() -> None:
    """The one clock in these tests, generous: the work limits keep all of them well under it."""
    import time

    started = time.monotonic()
    for _, command, _ in _HOSTILE:
        _spent(command)
    elapsed = time.monotonic() - started
    assert elapsed < 45.0, f"the hostile commands took {elapsed:.1f}s together"


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("nice " * 50 + "cat x", "nested too deep"),
        ("eval " * 2000 + "cat x", "more text than"),
    ],
)
def test_commands_that_run_commands_are_bounded(command: str, reason: str) -> None:
    """A runner nested past MAX_NESTING, or more run text than is parsed again, is refused, not
    followed for minutes or until the interpreter's recursion limit."""
    m = _module()
    with pytest.raises(m.BashSyntaxError, match=reason):
        m.bash_paths(command, cwd=CWD, home=HOME)


def test_commands_that_run_commands_a_few_deep_are_read() -> None:
    reads, _ = _paths("sudo env nice timeout 5 nohup bash -c 'eval \"cat /etc/passwd\"'")
    assert "/etc/passwd" in reads, sorted(reads)
