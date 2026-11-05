"""Tests for the Ring-2 path predicate the direct-xAI executor uses (EMB-45).

This reproduces the deny-rule enforcement Claude Code does from settings.json;
a gap here is a sandbox-escape surface, so the deny paths are exercised hard.
"""

from __future__ import annotations

import pytest

from embry0.safety.policy import (
    SafetyPolicy,
    _glob_to_regex,
    default_policy_for_agent,
    evaluate_ring2_paths,
    gate_tool_call,
)

HOME = "/home/agent"


def _dev_policy():
    return default_policy_for_agent("developer")


# ---- glob translation ----------------------------------------------------


@pytest.mark.parametrize(
    ("glob", "path", "matches"),
    [
        ("/etc/**", "/etc/passwd", True),
        ("/etc/**", "/etc", True),  # trailing /** also matches the dir itself
        ("/etc/**", "/etc/ssl/certs/ca.pem", True),
        ("/etc/**", "/etcetera/x", False),  # must not match a sibling prefix
        ("/workspace/**", "/workspace/src/main.py", True),
        ("/workspace/.claude/**", "/workspace/.claude/settings.json", True),
        ("/workspace/.claude/**", "/workspace/src/app.py", False),
    ],
)
def test_glob_to_regex(glob, path, matches):
    import re

    assert bool(re.match(f"^{_glob_to_regex(glob)}$", path)) is matches


# ---- deny enforcement ----------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "path"),
    [
        ("Read", "/etc/passwd"),
        ("Read", "/root/.bashrc"),
        ("Read", "/proc/self/environ"),
        ("Write", "/etc/hosts"),
        ("Write", "/usr/bin/evil"),
        ("Write", "/bin/sh"),
        ("Edit", "/root/x"),
        ("Write", "/workspace/.claude/settings.json"),
        ("Edit", "/workspace/.claude/settings.json"),
    ],
)
def test_denied_host_paths(tool, path):
    v = evaluate_ring2_paths(_dev_policy(), tool, {"file_path": path}, home=HOME)
    assert not v.allowed, f"{tool} {path} should be denied"


@pytest.mark.parametrize(
    ("tool", "path"),
    [
        ("Read", "/etc/a\nb/passwd"),
        ("Read", "/etc/\n/passwd"),
        ("Read", "/root/x\ny"),
        ("Write", "/usr/lib\n/x"),
        ("Read", "/etc/passwd\n"),
        ("Write", "/workspace/.claude/settings.json\n"),
        ("Edit", "/workspace/.claude/settings\n.json"),
    ],
)
def test_deny_globs_match_paths_with_a_newline_in_any_segment(tool, path):
    """A deny glob matches a path whatever its segments hold, a newline included."""
    v = evaluate_ring2_paths(_dev_policy(), tool, {"file_path": path}, home=HOME)
    assert not v.allowed, f"{tool} {path!r} should be denied"


def test_denied_home_paths_expand_tilde():
    pol = _dev_policy()
    for tool, path in [
        ("Read", f"{HOME}/.ssh/id_rsa"),
        ("Read", f"{HOME}/.aws/credentials"),
        ("Read", f"{HOME}/.claude/.credentials.json"),
        ("Grep", f"{HOME}/.ssh"),
        ("Glob", f"{HOME}/.gnupg"),
    ]:
        v = evaluate_ring2_paths(pol, tool, {"file_path": path, "path": path}, home=HOME)
        assert not v.allowed, f"{tool} {path} should be denied"


def test_path_traversal_is_normalized_then_denied():
    # A workspace-relative path that climbs out to /etc must still be caught.
    v = evaluate_ring2_paths(_dev_policy(), "Read", {"file_path": "/workspace/../etc/passwd"}, home=HOME)
    assert not v.allowed


def test_relative_traversal_resolved_against_cwd():
    v = evaluate_ring2_paths(_dev_policy(), "Read", {"file_path": "../../etc/shadow"}, cwd="/workspace/sub", home=HOME)
    assert not v.allowed


# ---- allowed paths -------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "path"),
    [
        ("Read", "/workspace/src/main.py"),
        ("Write", "/workspace/out.txt"),
        ("Edit", "/workspace/pkg/mod.py"),
        ("Read", "relative/file.py"),  # resolves under cwd=/workspace
        ("Glob", "/workspace/src"),
        ("Grep", "/workspace"),
    ],
)
def test_allowed_workspace_paths(tool, path):
    v = evaluate_ring2_paths(_dev_policy(), tool, {"file_path": path, "path": path}, home=HOME)
    assert v.allowed, f"{tool} {path} should be allowed"


def test_glob_bare_pattern_is_not_a_path_target():
    # A relative glob pattern with no path arg is scoped to the workspace, not denied.
    v = evaluate_ring2_paths(_dev_policy(), "Glob", {"pattern": "**/*.py"}, home=HOME)
    assert v.allowed


def test_glob_absolute_pattern_is_checked():
    v = evaluate_ring2_paths(_dev_policy(), "Glob", {"pattern": "/etc/**"}, home=HOME)
    assert not v.allowed


def test_non_filesystem_tool_allowed():
    v = evaluate_ring2_paths(_dev_policy(), "WebFetch", {"url": "https://example.com/etc/passwd"}, home=HOME)
    assert v.allowed


@pytest.mark.parametrize(
    "command",
    ["cat /etc/passwd", "ls /proc/self", "cat ~/.ssh/id_rsa", "echo x > /workspace/.claude/settings.json"],
)
def test_bash_that_names_a_denied_path_is_refused(command):
    """The CLI refuses these; the direct executors and the SDK hook must too."""
    v = evaluate_ring2_paths(_dev_policy(), "Bash", {"command": command}, home=HOME)
    assert not v.allowed, f"{command!r} names a denied path; evaluate_ring2_paths allowed it"
    assert "matches deny rule" in v.reason, v.reason


@pytest.mark.parametrize(
    "command",
    ["cat README.md", "echo /etc/passwd", "grep -rn /etc/hosts .", "ls /usr/bin", "echo x > /workspace/out.txt"],
)
def test_bash_that_names_no_denied_path_is_allowed(command):
    v = evaluate_ring2_paths(_dev_policy(), "Bash", {"command": command}, home=HOME)
    assert v.allowed, f"{command!r}: {v.reason}"


def test_a_double_leading_slash_is_the_root():
    """normpath keeps exactly two leading slashes; Linux reads //etc as /etc."""
    for tool_input in ({"file_path": "//etc/passwd"}, {"file_path": "//root/.bashrc"}):
        v = evaluate_ring2_paths(_dev_policy(), "Read", tool_input, home=HOME)
        assert not v.allowed, f"Read {tool_input['file_path']} is /etc or /root on Linux; it was allowed"


def test_a_grep_pattern_is_not_a_path():
    """Grep's pattern is a regular expression: searching the workspace for '/etc/passwd' reads no /etc file."""
    v = evaluate_ring2_paths(_dev_policy(), "Grep", {"pattern": "/etc/passwd", "path": "/workspace"}, home=HOME)
    assert v.allowed, f"a Grep of the workspace for the text '/etc/passwd' was refused: {v.reason}"


# ---- combined gate -------------------------------------------------------


def test_gate_denies_tool_not_in_allowlist():
    v = gate_tool_call(_dev_policy(), "NetTool", {"file_path": "/workspace/x"}, home=HOME)
    assert not v.allowed  # name gate (evaluate_policy)


def test_gate_denies_dangerous_bash():
    v = gate_tool_call(_dev_policy(), "Bash", {"command": "rm -rf /"}, home=HOME)
    assert not v.allowed  # Ring-3 content check


def test_gate_denies_host_read_via_ring2():
    v = gate_tool_call(_dev_policy(), "Read", {"file_path": "/etc/passwd"}, home=HOME)
    assert not v.allowed  # Ring-2 path check


def test_gate_allows_normal_workspace_read():
    v = gate_tool_call(_dev_policy(), "Read", {"file_path": "/workspace/main.py"}, home=HOME)
    assert v.allowed


def test_gate_checks_monitor_as_a_shell_tool():
    """Monitor (CLI 2.1.316) runs a shell command: the Bash patterns and the Bash path check apply to it."""
    dev = _dev_policy()
    pol = SafetyPolicy(allowed_tools=["Monitor"], deny_rules=dev.deny_rules, content_checks=dev.content_checks)
    ring3 = gate_tool_call(pol, "Monitor", {"command": "printenv HOME", "description": "x"}, home=HOME)
    assert not ring3.allowed and "dangerous bash pattern" in ring3.reason, f"Monitor printenv: {ring3}"
    ring2 = gate_tool_call(pol, "Monitor", {"command": "tail -f /root/app.log", "description": "x"}, home=HOME)
    assert not ring2.allowed and "matches deny rule" in ring2.reason, f"Monitor tail /root: {ring2}"
    benign = gate_tool_call(pol, "Monitor", {"command": "tail -f /workspace/build.log", "description": "x"}, home=HOME)
    assert benign.allowed, f"Monitor tail of a workspace log: {benign}"


@pytest.mark.parametrize(
    "command",
    [
        "cat /et?/passwd",
        "cat /{etc,x}/passwd",
        "cat ~/.ss?/canary",
        "cat /etc/$NAME",
        "ls /*",
        "find / -name passwd",
        'echo "$(cat /etc/passwd)"',
        "cd / && cat etc/passwd",
    ],
)
def test_a_glob_or_a_run_time_value_that_can_reach_a_denied_path_is_refused(command):
    v = evaluate_ring2_paths(_dev_policy(), "Bash", {"command": command}, home=HOME)
    assert not v.allowed, f"{command!r} can reach a denied path; evaluate_ring2_paths allowed it"


@pytest.mark.parametrize(
    "command", ["cat src/*.py", "rm -rf build/*", "ls ~/.cache/*", "cat $F", "find . -name '*.py'"]
)
def test_a_glob_that_cannot_reach_a_denied_path_is_allowed(command):
    v = evaluate_ring2_paths(_dev_policy(), "Bash", {"command": command}, home=HOME)
    assert v.allowed, f"{command!r}: {v.reason}"


def test_a_command_that_cannot_be_read_is_refused():
    v = evaluate_ring2_paths(_dev_policy(), "Bash", {"command": "cat /etc/passwd 'x"}, home=HOME)
    assert not v.allowed and "cannot be checked" in v.reason, v


def test_notebook_edit_is_a_file_tool():
    for path in ("/workspace/.claude/settings.nb.json", "/etc/x.ipynb", f"{HOME}/.ssh/x.ipynb"):
        v = evaluate_ring2_paths(_dev_policy(), "NotebookEdit", {"notebook_path": path, "new_source": "x"}, home=HOME)
        assert not v.allowed, f"NotebookEdit {path} must be refused: {v}"
    v = evaluate_ring2_paths(_dev_policy(), "NotebookEdit", {"notebook_path": "/workspace/n.ipynb"}, home=HOME)
    assert v.allowed, v


def test_tilde_user_is_that_users_home_not_home_plus_user():
    refused = evaluate_ring2_paths(_dev_policy(), "Read", {"file_path": "~root/.bashrc"}, home=HOME)
    assert not refused.allowed and "/root/.bashrc" in refused.reason, refused
    unknown = evaluate_ring2_paths(_dev_policy(), "Read", {"file_path": "~nosuchuser-000001/x"}, home=HOME)
    assert not unknown.allowed and "nosuchuser" in unknown.reason, unknown


# Each form the shell reads one way and a word-by-word reading another. Every
# command reads /etc/passwd (or ~/.ssh) when bash runs it.
_SHELL_FORMS = [
    ("quoted here-document delimiter", 'cat <<"E"OF >/dev/null\nx\nEOF\ncat /etc/passwd'),
    ("<<EOF inside quotes", "echo '<<EOF'\ncat /etc/passwd\nEOF"),
    ("grep -ePAT", "grep -eroot /etc/passwd"),
    ("grep --regexp=PAT", "grep --regexp=root /etc/passwd"),
    ("sed -ep", "sed -ep /etc/passwd"),
    ("sed --expression=", "sed --expression=p /etc/passwd"),
    ("grep -fFILE", "grep -f/etc/passwd /dev/null"),
    ("bash -o pipefail -c", "bash -o pipefail -c 'cat /etc/passwd'"),
    ("bash +x -c", "bash +x -c 'cat /etc/passwd'"),
    ("sh -c --", "sh -c -- 'cat /etc/passwd'"),
    ('"$(…)"', 'echo "$(cat /etc/passwd)"'),
    ("backticks", "echo `cat /etc/passwd`"),
    ('X="$(…)"', 'X="$(cat /etc/passwd)"; echo "$X"'),
    ('<<< "$(…)"', 'cat <<< "$(cat /etc/passwd)"'),
    ('"$(<file)"', 'echo "$(</etc/passwd)"'),
    ("line continuation", "cat /et\\\nc/passwd"),
    ("$'…'", "cat $'/etc/passwd'"),
    ('$"…"', 'cat $"/etc/passwd"'),
    ("glob ?", "cat /et?/passwd"),
    ("glob []", "cat /[e]tc/passwd"),
    ("brace", "cat /{etc,x}/passwd"),
    ("glob under ~", "cat ~/.ss?/id_rsa"),
    ("cd /", "cd / && cat etc/passwd"),
    ("cd ..", "cd .. && cat etc/passwd"),
    ("cd $HOME", "cd $HOME && cat .ssh/id_rsa"),
    ("setsid", "setsid bash -c 'cat /etc/passwd'"),
    ("script -c", "script -qc 'cat /etc/passwd' /dev/null"),
    ("flock -c", "flock /tmp/lock -c 'cat /etc/passwd'"),
    ("find -exec sh -c", "find . -exec sh -c 'cat /etc/passwd' \\;"),
    ("find -execdir", "find . -execdir cat /etc/passwd \\;"),
    ("nice", "nice -n 5 cat /etc/passwd"),
    ("timeout", "timeout -s KILL 5 cat /etc/passwd"),
    ("stdbuf", "stdbuf -o L cat /etc/passwd"),
    ("nohup", "nohup cat /etc/passwd"),
    ("~user", "cat ~root/.bashrc"),
    ("quoted eval", "eval 'cat /etc/passwd'"),
    ("here-document in bash -c", "bash -c 'cat <<EOF\n$(cat /etc/passwd)\nEOF'"),
]


@pytest.mark.parametrize(("form", "command"), _SHELL_FORMS, ids=[f for f, _ in _SHELL_FORMS])
def test_every_shell_form_that_reaches_a_denied_path_is_refused(form, command):
    v = gate_tool_call(_dev_policy(), "Bash", {"command": command}, home=HOME)
    assert not v.allowed, f"{form}: {command!r} reads a denied path when bash runs it; the gate allowed it"


@pytest.mark.parametrize(
    "command",
    [
        "git grep -n '/etc/' -- src",
        "git grep -e /etc/hosts",
        "git log --grep=/etc/hosts",
        "basename /etc/hosts",
        "dirname /proc/self",
        "test -f /etc/os-release && echo yes",
        "[ -d /root ] || echo no",
    ],
)
def test_data_and_stat_only_commands_are_allowed(command):
    v = gate_tool_call(_dev_policy(), "Bash", {"command": command}, home=HOME)
    assert v.allowed, f"{command!r} reads no denied file: {v.reason}"


def test_an_edit_rule_refuses_writes_too():
    """Claude Code applies an Edit(path) rule to every tool that writes, Write and shell writes included."""
    pol = SafetyPolicy(allowed_tools=["Write", "Bash", "NotebookEdit"], deny_rules=["Edit(//srv/**)"])
    for tool, tool_input in (
        ("Write", {"file_path": "/srv/x", "content": "x"}),
        ("NotebookEdit", {"notebook_path": "/srv/n.ipynb", "new_source": "x"}),
        ("Bash", {"command": "echo x > /srv/x"}),
    ):
        v = evaluate_ring2_paths(pol, tool, tool_input, home=HOME)
        assert not v.allowed, f"{tool} {tool_input}: an Edit rule must refuse it"
    assert evaluate_ring2_paths(pol, "Bash", {"command": "cat /srv/x"}, home=HOME).allowed, "an Edit rule allows reads"


# Forms a shell reads differently from a word-by-word reading, or that move the
# directory, found after the first round. Each reads /etc/passwd, ~/.ssh or
# writes the sandbox's settings when bash runs it, and each one ran through
# both the CLI and the hook before.
_MORE_SHELL_FORMS = [
    ('\\" in backticks in "…"', 'echo "`cat \\"/etc/passwd\\"`"'),
    ("$((cmd) )", "echo $((cat /etc/passwd) )"),
    ('"$((cmd) )"', 'echo "$((cat /etc/passwd) )"'),
    ("case in $(…)", 'echo "$(case x in x) cat /etc/passwd;; esac)"'),
    ("here-document with ) in $(…)", "echo \"$(cat <<'X'\n)\nX\ncat /etc/passwd)\""),
    ("$'\\0'", "cat $'\\0'/etc/passwd"),
    ("$'\\x00'", "cat $'\\x00'/etc/passwd"),
    ("$'\\u0000'", "cat $'\\u0000'/etc/passwd"),
    ("\"\"$'\\0'", "cat \"\"$'\\0'/etc/passwd"),
    ("~+", "cat ~+/../../etc/passwd"),
    ("~-", "cat ~-/../etc/passwd"),
    ("<< in (( ))", "(( y = 1 << 2 ))\ncat /etc/passwd"),
    ("<< in $[ ]", "echo $[1<<2]\ncat /etc/passwd"),
    ("trap", "trap 'cat /etc/passwd' EXIT"),
    ("git -c alias", "git -c alias.x='!cat /etc/passwd' x"),
    ("git -c core.pager", "git -c core.pager='cat /etc/passwd' log -1"),
    ("git -c core.sshCommand", "git -c core.sshCommand='cat /etc/passwd' fetch"),
    ("bash -s -- arg <<", "bash -s -- arg <<'X'\ncat /etc/passwd\nX"),
    ("CDPATH=", "CDPATH=/ cd etc && cat passwd"),
    ("export CDPATH", "export CDPATH=/; cd etc; cat passwd"),
    ("cd glob ~/.ss?", "cd ~/.ss? && cat canary"),
    ("cd glob /e?c", "cd /e?c && cat passwd"),
    ('cd "$(…)"', 'cd "$(echo ~)" && cat .ssh/canary'),
    ("cd ${HOME%/}", "cd ${HOME%/} && cat .ssh/canary"),
    ("function that cds", "f() { cd ~; }; cd /workspace; f; cat .ssh/canary"),
    ("cd -", "cd ~; cd /tmp; cd - >/dev/null; cat .ssh/canary"),
    ('set --; cd "$1"', 'set -- ~; cd "$1" && cat .ssh/canary'),
    ("cd glob to .claude", "cd .cl?ude && echo '{}' > settings.json"),
    ('cd "$PWD/…"', "cd \"$PWD/.claude\" && echo '{}' > settings.json"),
    ('cd "$(dirname …)"', 'cd "$(dirname /etc/passwd)" && cat passwd'),
    ("a cd that fails", "cd /nonexistent/b; cat ../etc/passwd"),
    ("env -C", "env -C /etc cat passwd"),
    ("git -C", "git -C ~ diff --no-index .ssh/canary /dev/null"),
    ("alias", "alias x='cat /etc/passwd'\nx"),
    ("BASH_ENV", "BASH_ENV=/etc/passwd bash -c true"),
    ("unknown runner of sh -c", "strace -f sh -c 'cat /etc/passwd'"),
    ("git rebase -x", "git rebase -x 'cat /etc/passwd' HEAD~1"),
    ("find -delete", "find . -name settings.json -delete"),
    ("find -exec rm {}", "find .claude -exec rm {} +"),
    ("cp a glob into .claude", "cp *.json .claude/"),
    ("rm .*", "rm -rf .*"),
    ("dotglob", "shopt -s dotglob\nrm -f */settings.json"),
    ("here-document delimiter with a CR", "cat <<EOF\r\nx\nEOF\r\ncat /etc/passwd\nEOF"),
    ("here-document delimiter $X", "cat <<$X\nx\n$X\ncat /etc/passwd\n$"),
    ("~user after a brace", "cat {~root,x}/.bashrc"),
    ("$1 of bash -c", "bash -c 'cat \"$1\"' _ /etc/passwd"),
    ("!(…) subshell", "!(cat /etc/passwd)"),
]


@pytest.mark.parametrize(("form", "command"), _MORE_SHELL_FORMS, ids=[f for f, _ in _MORE_SHELL_FORMS])
def test_every_further_shell_form_that_reaches_a_denied_path_is_refused(form, command):
    v = gate_tool_call(_dev_policy(), "Bash", {"command": command}, cwd="/workspace", home=HOME)
    assert not v.allowed, f"{form}: {command!r} reaches a denied path when bash runs it; the gate allowed it"


# Writes with a glob in the workspace: bash's glob cannot reach the sandbox's
# settings (no hidden names, no "/" crossed), so none is refused.
_WORKSPACE_GLOB_COMMANDS = [
    "sed -i 's/foo/bar/g' *.py",
    "rm -f *.log",
    "rm -rf *.egg-info build dist",
    "rm -rf ./*",
    "mv *.txt archive/",
    "chmod +x *.sh",
    "touch *.py",
    "rm -f ./*.pyc",
    'rm -f "./${name}.log"',
    "rm -- *.tmp",
    "git rm --cached *.log",
    "git checkout -- *.py",
    "ls ~/*",
    "cp *.json dist/",
    "rm -rf node_modules/.cache/*",
    "rm -f {a,b,c}.txt",
    "rm -f file{1..300}.txt",
    "cd build && rm -rf *",
    "rm -f .claude/commands/*.md",
    "cp templates/* .claude/commands/",
    "echo '{}' > ?claude/settings.json",
    "find . -name '*.pyc' -delete",
    "find . -type f -name '*.orig' -exec rm {} +",
]


@pytest.mark.parametrize("command", _WORKSPACE_GLOB_COMMANDS)
def test_a_workspace_glob_that_cannot_reach_a_denied_path_is_allowed(command):
    """The full gate: a plain glob is read with bash's default options, so it reaches no denied path
    and no .env (Ring 3 widens only a glob an option in the same command changes, test_command_checks)."""
    v = gate_tool_call(_dev_policy(), "Bash", {"command": command}, cwd="/workspace", home=HOME)
    assert v.allowed, f"{command!r} cannot reach a denied path as bash expands it: {v.reason}"


@pytest.mark.parametrize(
    "command",
    ['cd "$X" && cat notes.txt', "cd $(git rev-parse --show-toplevel) && cat README.md", "cd - && ls src"],
)
def test_a_relative_path_after_a_cd_that_is_not_known_is_refused(command):
    v = gate_tool_call(_dev_policy(), "Bash", {"command": command}, cwd="/workspace", home=HOME)
    assert not v.allowed and "not known" in v.reason, f"{command!r}: the directory is not known, so {v}"


@pytest.mark.parametrize("command", ['cd "$X" && ls', 'cd "$X" && cat /workspace/README.md', 'cd "$X"; make'])
def test_a_cd_that_is_not_known_leaves_absolute_paths_and_no_paths_allowed(command):
    v = gate_tool_call(_dev_policy(), "Bash", {"command": command}, cwd="/workspace", home=HOME)
    assert v.allowed, f"{command!r} names no relative path: {v.reason}"


def test_a_command_longer_than_the_limit_is_refused_without_running_the_patterns(monkeypatch):
    import re as re_module

    from embry0.safety import policy as policy_module

    limit = getattr(policy_module, "MAX_CHECKED_INPUT", None)
    assert limit == 64 * 1024, f"the policy must bound the input it reads at 64 KiB, has {limit!r}"
    command = "python -c " * (limit // 10 + 1)
    searched: list[str] = []
    search = re_module.search

    def counted(pattern, text, *args, **kwargs):
        if len(text) > limit:
            searched.append(str(pattern))
        return search(pattern, text, *args, **kwargs)

    monkeypatch.setattr(re_module, "search", counted)
    v = gate_tool_call(_dev_policy(), "Bash", {"command": command}, cwd="/workspace", home=HOME)
    assert not v.allowed and "past the 65536" in v.reason, f"a {len(command)}-character command: {v}"
    assert not searched, f"a command past the limit must be refused before any regex reads it; {len(searched)} did"


def test_a_shell_command_longer_than_the_limit_is_refused_by_the_path_check_too():
    from embry0.safety import policy as policy_module

    limit = getattr(policy_module, "MAX_CHECKED_INPUT", 64 * 1024)
    v = evaluate_ring2_paths(_dev_policy(), "Bash", {"command": "true " * (limit // 5 + 1)}, home=HOME)
    assert not v.allowed and "past the 65536" in v.reason, f"the path check read a command past the limit: {v}"


def test_a_long_write_is_not_bounded_when_no_content_rule_reads_it():
    v = gate_tool_call(_dev_policy(), "Write", {"file_path": "/workspace/big.txt", "content": "x" * 200_000}, home=HOME)
    assert v.allowed, f"no content rule reads Write's content, so its length is not bounded: {v}"


# A literal path reaches a command through a variable, a parameter's default,
# HOME reassigned, a list, a transformation, a prompt, or a script the shell
# reads from standard input. Each reaches a denied path when bash runs it (with
# the variable set, the file there, and, for PROMPT_COMMAND, in an interactive
# shell), through both layers.
_VALUE_FORMS = [
    ("${x:-path}", "cat ${x:-/etc/passwd}"),
    ('"${x-path}"', 'cat "${x-/etc/passwd}"'),
    ("${x:=path} then $x", ": ${x:=/etc/passwd}; cat $x"),
    ("${x:+path}", "cat ${x:+/etc/passwd}"),
    ("${x:?path}", "cat ${x:?/etc/passwd}"),
    ("${a[0]:-~/…}", "head ${a[0]:-~/.claude/credentials.json}"),
    ("${!x:-path}", "cat ${!x:-/etc/passwd}"),
    ("HOME= then ~", "HOME=/etc; cat ~/passwd"),
    ("export HOME then cd", "export HOME=/etc; cd; cat passwd; cd /workspace"),
    ("HOME= then $HOME", "HOME=/etc; cat $HOME/passwd"),
    ("HOME from a substitution", "HOME=$(mktemp -d); head ~/x"),
    ("PWD= then ~+", "PWD=/etc; cat ~+/passwd"),
    ("${HOME%/}", "head ${HOME%/}/.claude/credentials.json"),
    (". /dev/stdin <<<", ". /dev/stdin <<< 'cat /etc/passwd'"),
    ("source /dev/stdin <<", "source /dev/stdin <<'Z'\ncat /etc/passwd\nZ"),
    ("source /proc/self/fd/0", "source /proc/self/fd/0 <<< 'cat /etc/passwd'"),
    ("source /dev/fd/3", "source /dev/fd/3 3<<< 'cat /etc/passwd'"),
    ("source <(…)", "source <(echo 'cat /etc/passwd')"),
    ("BASH_ENV=<(…)", "BASH_ENV=<(echo 'cat /etc/passwd') bash -c true"),
    ("BASH_ENV=/dev/stdin", "BASH_ENV=/dev/stdin bash -c true <<< 'cat /etc/passwd'"),
    ("bash /dev/stdin", "bash /dev/stdin <<< 'cat /etc/passwd'"),
    ("bash <(…)", "bash <(echo 'cat /etc/passwd')"),
    ("X= then $X", "F=/etc/passwd; cat $F"),
    ("printf -v", "printf -v p /etc/passwd; cat $p"),
    ("printf -v %s", "printf -v p %s /etc/passwd; cat $p"),
    ("declare -r", "declare -r p=/etc/passwd; cat $p"),
    ("local", "local p=/etc/passwd; cat $p"),
    ("readonly", "readonly p=/etc/passwd; cat $p"),
    ("typeset -a", "typeset -a a=(x /etc/passwd); cat ${a[1]}"),
    ("array", 'a=(/etc/passwd); cat "${a[@]}"'),
    ("array with a glob", 'a=(/etc/pass*); cat "${a[@]}"'),
    ("for item", 'for f in /etc/passwd; do cat "$f"; done'),
    ("for glob item, quoted use", 'for f in .claude/*; do rm "$f"; done'),
    ("set --", 'set -- /etc/passwd; cat "$1"'),
    ("assigned after the use, in a loop", 'for i in 1 2; do cat "$F"; F=/etc/passwd; done'),
    ("eval of a variable", "X='cat /etc/passwd'; eval \"$X\""),
    ("eval of a value holding the directory", 'x="$PWD/.claude/settings.json"; cd /tmp; eval "echo {} > $x"'),
    ("bash -c of a variable", "X='cat /etc/passwd'; bash -c \"$X\""),
    ("a command in a variable", "X='cat /etc/passwd'; $X"),
    # IFS splits an unquoted value at its characters.
    ("IFS= then a split command", "IFS=,; x=cat,/etc/passwd; $x"),
    ("IFS= then eval", "x=cat:/etc/passwd; IFS=: eval '$x'"),
    ("export IFS", "export IFS=,; x=cat,/etc/passwd; $x"),
    ("IFS from $'…'", "IFS=$'\\x2c'; x=cat,/etc/passwd; $x"),
    ("for IFS in", "for IFS in ,; do x=cat,/etc/passwd; $x; done"),
    ("printf -v IFS", "printf -v IFS ,; x=cat,/etc/passwd; $x"),
    ("IFS and a function's $1", "IFS=,; f() { $1; }; f cat,/etc/passwd"),
    # A list keeps its order: "$@" and "${a[@]}" run cd with its directory.
    ('set -- then "$@"', 'set -- cd /; "$@"; cat etc/passwd'),
    ('array then "${a[@]}"', 'a=(cd /); "${a[@]}"; cat etc/passwd'),
    ("a function's arguments", 'f() { "$@"; }; f cd /; cat etc/passwd'),
    ("a function's $1 $2", "f() { $1 $2; }; f cd /; cat etc/passwd"),
    ("bash -c and its arguments", "bash -c '\"$@\"; cat etc/passwd' x cd /"),
    ("eval of $@", 'set -- cd /; eval "$@"; cat etc/passwd'),
    ("shift", 'set -- x cd /; shift; "$@"; cat etc/passwd'),
    ("a slice of $@", 'set -- x cd /; "${@:2}"; cat etc/passwd'),
    ("a slice of an array", 'a=(x cd /); "${a[@]:1}"; cat etc/passwd'),
    ("an element assigned alone", 'a=(x /); a[0]=cd; "${a[@]}"; cat etc/passwd'),
    ("elements with their own index", 'a=([1]=/ [0]=cd); "${a[@]}"; cat etc/passwd'),
    ("an element at an index only known at run time", 'i=1; a=([$i]=/ [0]=cd); "${a[@]}"; cat etc/passwd'),
    ("a value given to an array", 'a=(x /); a=cd; "${a[@]}"; cat etc/passwd'),
    ("an element unset", "a=(x cd /); unset 'a[0]'; \"${a[@]}\"; cat etc/passwd"),
    # A transformation of a literal value is computed.
    ("${x%…}", "x=/etc/passwdX; cat ${x%X}"),
    ("${x##…}", "x=X/etc/passwd; cat ${x##X}"),
    ("${x/…/…}", "x=/etc/pXsswd; cat ${x/X/a}"),
    ("${x//…/…}", "x=/Xtc/passwd; cat ${x//X/e}"),
    ("${x/#…/…}", "x=Q/passwd; cat ${x/#Q//etc}"),
    ("${x/%…/…}", "x=/etc/passQ; cat ${x/%Q/wd}"),
    ("${x/$p/…} anchored by its value", "p='#Q'; x=Q/etc/passwd; cat ${x/$p/}"),
    ("${x/…/&…}, & the match", "x=~/.s; cat ${x/.s/&sh}/id_rsa"),
    ("${x,,}", "x=/ETC/PASSWD; cat ${x,,}"),
    ("${x@L}", "x=/ETC/PASSWD; cat ${x@L}"),
    ("${x@E}", "x='\\x2fetc\\x2fpasswd'; cat ${x@E}"),
    ("${x:2}", "x=XX/etc/passwd; cat ${x:2}"),
    ("${x: -13:11}", "x=/etc/passwdYY; cat ${x: -13:11}"),
    ("a transformed command word", "x=XcdX; ${x//X/} /; cat etc/passwd"),
    ("a folded variable transformed", "".join(f"x=a{i}; " for i in range(17)) + "x=XX/etc/passwd; cat ${x:2}"),
    ("a value past the length kept", "x=" + "a" * 5000 + "/etc/passwd; cat ${x:5000}"),
    ("an extglob pattern", "x=/etc/passwd; cat ${x#@(z)}"),
    # A prompt is expanded, and its command substitutions run: PS4 under set -x, ${X@P}.
    ("PS4 under set -x", "PS4='$(cat /etc/passwd)'; set -x; :"),
    ("PS4 from env", "env PS4='$(cat /etc/passwd)' bash -xc true"),
    ("printf -v PS4", "printf -v PS4 %s '$(cat /etc/passwd)'; set -x; :"),
    ("PROMPT_COMMAND", "PROMPT_COMMAND='cat /etc/passwd'"),
    ("${X@P}", "x='$(cat /etc/passwd)'; echo ${x@P}"),
    ("${!X@P}", "y='$(cat /etc/passwd)'; x=y; echo ${!x@P}"),
    # A function bash defines from its environment.
    ("env BASH_FUNC_f%%", "env 'BASH_FUNC_x%%=() { cat /etc/passwd; }' bash -c x"),
    ("sudo BASH_FUNC_f%%", "sudo 'BASH_FUNC_x%%=() { cat /etc/passwd; }' bash -c x"),
    # Other ways a literal value reaches a variable or a command.
    ("$_", ": /etc/passwd; cat $_"),
    ("declare -l", "declare -l x; x=/ETC/PASSWD; cat $x"),
    ("BASH_ALIASES[…]", "BASH_ALIASES[g]='cat /etc/passwd'\ng"),
    ("BASH_ALIASES=(…)", "BASH_ALIASES=([g]='cat /etc/passwd')\ng"),
    ("command_not_found_handle", "command_not_found_handle() { cat etc/passwd; }; cd /; nosuchcmd"),
    ("read from a here-string", "read -r x <<< /etc/passwd; cat $x"),
    ("read fields", 'read -r a b <<< "x /etc/passwd"; cat $b'),
    ("read with IFS", 'IFS=: read -r a b <<< "x:/etc/passwd"; cat $b'),
    ("read -a", 'read -ra arr <<< "cd /"; "${arr[@]}"; cat etc/passwd'),
    ("read -n", "read -n 4 x <<< /etcXX; cat $x/passwd"),
    ("mapfile", "mapfile -t L <<< $'cd\\n/'; \"${L[@]}\"; cat etc/passwd"),
    ("mapfile -C", "mapfile -C 'cat /etc/passwd' -c 1 a <<< x"),
    ("mapfile -C moves the shell", "mapfile -C 'cd /;:' -c 1 a <<< x; cat etc/passwd"),
    ("a loop fed a here-string", 'while read -r f; do cat "$f"; done <<< /etc/passwd'),
    ("a function fed a here-string", "f() { read x; cat $x; } <<< /etc/passwd; f"),
    ("a run-time command fed a here-string", '$BASH <<< "cat /etc/passwd"'),
    # An assignment expands ~ after = and after each :.
    ("~ in an assignment, used quoted", 'x=~/.ssh/id_rsa; cat "$x"'),
    ("~ after : in an assignment", "x=a:~/.ssh/id_rsa; IFS=:; cat $x"),
    ("export x=~", 'export x=~/.ssh/id_rsa; cat "$x"'),
    # += appends to each value the variable can hold.
    ("+=", "x=.cl; x+=aude/settings.json; echo {} > $x"),
    ("+= twice", "x=~/.s; x+=s; x+=h/id_rsa; cat $x"),
    ("+= in a loop", "x=.cl; while :; do x+=a; done; echo {} > ${x}ude/settings.json"),
    ("+=() then the list", 'a=(); a+=(cd); "${a[@]}" /; cat etc/passwd'),
    # ${!x} and namerefs name another variable.
    ("${!x}", "y=/etc/passwd; x=y; cat ${!x}"),
    ("${!x%…}", "y=/etc/passwdX; x=y; cat ${!x%X}"),
    ("${!x} of a list", 'a=(cd /); c=a[@]; "${!c}"; cat etc/passwd'),
    ("declare -n", "declare -n r=y; y=/etc/passwd; cat $r"),
    ("declare -n, then its target", "declare -n r; r=y; y=/etc/passwd; cat $r"),
    ("assigned through a nameref", "declare -n r=y; r=/etc/passwd; cat $y"),
    ("local -n", "f() { local -n r=y; cat $r; }; y=/etc/passwd; f"),
    ("a chain of namerefs", "; ".join(f"declare -n r{i}=r{i + 1}" for i in range(6)) + "; r6=/etc/passwd; cat $r0"),
    # printf -v gives the value printf prints.
    ("printf -v %b", "printf -v p '%b' '\\x2fetc\\x2fpasswd'; cat $p"),
    ("printf -v escapes", "printf -v p '\\x2fetc\\x2fpasswd'; cat $p"),
    ("printf -v %s/%s", "printf -v p '%s/%s' /etc passwd; cat $p"),
    ("printf -v %.Ns", "printf -v p '%.11s' /etc/passwdXYZ; cat $p"),
    ("printf -v %c", "printf -v p '%c%s' /x etc/passwd; cat $p"),
    ("printf -v a bad number", "printf -v p '/etc/passw%c%.0d' d x; cat $p"),
    ("printf -v %(…)T", "printf -v p '%(/etc/passwd)T' -1; cat $p"),
    ("printf -v -- %s", "printf -v p -- %s /etc/passwd; cat $p"),
    ("printf -v a command word", "printf -v c %s cd; $c /; cat etc/passwd"),
    ("printf -v of the directory", 'printf -v p %s "$PWD"; cd /tmp; echo {} > "$p/.claude/settings.json"'),
    # Values only known at run time: an over-long or folded one as a command word is refused.
    ("a folded command word", "".join(f"x=a{i}; " for i in range(17)) + "x=cd; $x /; cat etc/passwd"),
    (
        "a folded here-string run by a shell",
        "".join(f"x=a{i}; " for i in range(17)) + "x='cat /etc/passwd'; bash <<< \"$x\"",
    ),
    ("an eval of a folded value", "".join(f"x=a{i}; " for i in range(17)) + "x='cat /etc/passwd'; eval \"$x\""),
]


@pytest.mark.parametrize(("form", "command"), _VALUE_FORMS, ids=[f for f, _ in _VALUE_FORMS])
def test_a_path_that_reaches_a_command_through_a_value_is_refused(form, command):
    v = gate_tool_call(_dev_policy(), "Bash", {"command": command}, cwd="/workspace", home=HOME)
    assert not v.allowed, f"{form}: {command!r} reaches a denied path when bash runs it; the gate allowed it"
    assert "check failed" not in v.reason and "hook failed" not in v.reason, f"{form}: refused by a crash: {v.reason}"


@pytest.mark.parametrize(
    ("form", "command"),
    [
        (
            "namerefs past the ones followed",
            "; ".join(f"declare -n r{i}=r{i + 1}" for i in range(40)) + "; r40=/etc/passwd; cat $r0",
        ),
        ("a value printf makes past its limit", "printf -v p '%99999999s' x; cat $p"),
        ("a pattern past its limit", "x=/etc/passwd; cat ${x#" + "?" * 2000 + "}"),
        ("expansions nested past the depth read", "cat " + "${x:-" * 12 + "/etc/passwd" + "}" * 12),
    ],
    ids=lambda v: v if len(v) < 40 else None,
)
def test_a_value_past_what_is_computed_can_be_anywhere(form, command):
    """Past a limit (namerefs followed, a printf width, a pattern's length, nesting) the value is not
    computed: it can be anywhere, so a path built on it is refused."""
    v = gate_tool_call(_dev_policy(), "Bash", {"command": command}, cwd="/workspace", home=HOME)
    assert not v.allowed, (
        f"{form}: a value past what is computed must count as anywhere; the gate allowed {command[:80]!r}"
    )
    assert "check failed" not in v.reason and "hook failed" not in v.reason, f"{form}: refused by a crash: {v.reason}"


@pytest.mark.parametrize(
    "command",
    [
        "cat ${x:-README.md}",
        "F=README.md; cat $F",
        "cd; ls",
        'for f in *.py; do python -m py_compile "$f"; done',
        "CONF=/etc/app.conf true",
        "export PATH=/usr/local/bin:$PATH && npm -v",
        "HOME=/tmp/h npm ci",
        "diff <(sort a.txt) <(sort b.txt)",
        "; ".join(f'for f in src/m{i}.py; do wc -l "$f"; done' for i in range(40)),
        "a=(README.md docs); ls ${a[@]}",
        'for f in *.py; do mv "$f" "${f%.py}.txt"; done',
        'f=src/app.py; cp "$f" "${f%.py}.bak"',
        "v=1.2.3; echo ${v//./-}",
        "printf -v n '%03d' 7; touch \"file$n.log\"",
        "IFS=,; read -r a b <<< 'x,y'; echo \"$a\"",
        'set -- a b; for x; do echo "$x"; done',
        'args=(-q --tb=short); python -m pytest "${args[@]}" tests',
        'MSG="' + "word " * 900 + '"; git commit -m "$MSG"',
        "x=src/m.py; cat ${x/.py/.txt}",
        "declare -n r=y; y=README.md; cat $r",
        "x=readme.md; cat ${x^^}",
        "IFS=/; x=cat/etc/passwd; cat $x",
        "x=abc/etc/passwd; cat ${x##*c}",
        'x=a; x+=b; cat "$x"',
        'CFLAGS+=" -O2"; gcc $CFLAGS x.c',
        'args=(); args+=(-q); args+=(--tb=short); python -m pytest "${args[@]}" tests',
        'files=(); for f in src/*.py; do files+=("$f"); done; ruff check "${files[@]}"',
        'while read -r f; do wc -l "$f"; done <<< "README.md"',
        '"$VENV/bin/python" - <<EOF\nprint(1)\nEOF',
        "PS4='+ $LINENO: '; set -x; ls",
        "declare -u NAME=report; echo $NAME",
        ": src/app.py; cat $_",
        'a=([1]=README.md [0]=cat); "${a[@]}"',
    ],
)
def test_a_value_that_names_no_denied_path_is_allowed(command):
    v = gate_tool_call(_dev_policy(), "Bash", {"command": command}, cwd="/workspace", home=HOME)
    assert v.allowed, f"{command[:80]!r} names no denied path through its values: {v.reason}"
