"""No embry0 code builds a git command for a sandbox except through ``embry0.execution.sandbox_git``.

A git command embry0 runs in a sandbox must start with that module's prelude,
or git reads the configuration, hooks and drivers the agent can write. The
module renders every such command (``sandbox_git.git``) as a call to a shell
function (``embry0_git``) its prelude defines, so a rendered command run
without the prelude fails. This guard refuses the other way in: a git command
written out by hand anywhere in ``embry0/`` outside the module, as

* an **argv list** whose first element is the git executable
  (``["git", ...]``, ``[GIT, ...]``, ``["/usr/bin/git", ...]``) — refused in
  every file, since a list literal starting with the git executable is never
  prose; or
* a **shell string** with ``git`` in command position (at the start, after a
  separator, a subshell, a substitution or a command wrapper such as
  ``command``/``xargs``/``timeout``/``if``, or after an env assignment), then
  a subcommand, an option or an interpolation — refused outside the module and
  a short allowlist of files that run git on the orchestrator or show a git
  command to an agent as text.

The detector over-matches on purpose (an unknown subcommand, a bare ``git -``,
a run-time ``f"git {sub}"`` and a ``"git " + sub`` concatenation all count);
``test_the_detector_catches_every_form`` pins each form, and the negative
cases that must stay quiet. Backtick spans are inline-code markup in embry0's
agent prompts, never command substitution in a command embry0 runs, so their
contents are read as prose; the module is the one place that builds commands.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "embry0"

#: The one module that may write git commands for a sandbox.
_MODULE = "embry0/execution/sandbox_git.py"

#: Files the shell detector skips: they run git on the orchestrator, or show a
#: git command to an agent as text. None of them builds a command for a sandbox.
#: The argv detector still runs on them.
_SHELL_ALLOWLIST = {
    "embry0/skills/sync.py": "clones skill sources on the orchestrator (GitError messages name git)",
    "embry0/services/slop_meter/mirror.py": "keeps bare mirrors on the orchestrator (GitError messages name git)",
    "embry0/services/context_audit_command.py": "builds the git ls-files command the auditor agent runs itself",
    "embry0/services/slop.py": "the Slopmaster agent's prompt cites git commands as text",
    "embry0/safety/bash_paths.py": "the Ring-3 bash analyzer parses git commands; its error messages name git, but it builds none",
}

# git subcommands (git 2.39), including ones a brief might forget (diff-tree,
# check-attr, format-patch). The detector also fires on an option or an
# interpolation after ``git``, so this list is a convenience, not the boundary.
_SUBCOMMANDS = (
    "add am apply archive bisect blame branch bundle cat-file check-attr check-ignore checkout cherry-pick clean "
    "clone commit config describe diff diff-tree fetch for-each-ref format-patch fsck gc grep hash-object init log "
    "ls-files ls-remote ls-tree maintenance merge merge-base mv notes pull push read-tree rebase reflog remote "
    "repack reset restore rev-list rev-parse rm show show-ref sparse-checkout stash status submodule switch "
    "symbolic-ref tag update-index update-ref worktree write-tree lfs"
).split()

# A command-position lead: the start of the string; a separator (``;`` ``&&``
# ``||`` ``|`` ``(`` newline backtick); a substitution ``$(``; a shell keyword
# or command wrapper, with its own args allowed before git (``command git``,
# ``timeout 30 git``, ``xargs -0 git``, ``env X=1 git``, ``if git``); a leading
# ``!``; or one or more env assignments (``GIT_DIR=x git``).
_LEAD = (
    r"(?:^|[;&|(\n`]|\$\(|&&|\|\|"
    # keyword or wrapper with git immediately after (``if git``, ``command git``)
    r"|\b(?:then|do|else|fi|if|while|until|exec|command)\b\s+"
    # a wrapper that takes a bounded run of its own args before git (``timeout 30 git``, ``xargs -0 git``)
    r"|\b(?:timeout|xargs|env|nice|sudo|time)\b(?:\s+[^\s;&|()]+){0,3}\s+"
    r"|!\s*"
    r"|(?:\b[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+)"
)
# the git executable as a whole word: bare ``git`` or a path ``…/git``; not
# GITHUB, not git_x, not a URL path segment ``/git/``.
_GIT = r"(?:git|[\w.\-]*/git)(?![\w\-/])"
# then a subcommand, a short or long option, an interpolation, or (a bare
# ``git`` at the end of a string, as the left of a ``"git " + sub`` concat).
_TAIL = r"(?:\s+(?:--?\S|(?:" + "|".join(_SUBCOMMANDS) + r")(?![\w-]))|\s*\x00|\s+$)"
_SHELL_GIT = re.compile(_LEAD + r"\s*" + _GIT + _TAIL)
_BACKTICKS = re.compile(r"`[^`]*`")


def _prose_ids(tree: ast.AST) -> set[int]:
    """Strings that are documentation, not code: docstrings and bare string statements."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            ids.add(id(node.value))
    return ids


def _argv_git(node: ast.AST) -> bool:
    """Whether ``node`` is an argv list/tuple whose first element is the git executable and that looks like a command.

    ``["git", "-C", …]``, ``[GIT, …]``, ``["/usr/bin/git", "status"]``,
    ``["git", *args]`` — but not a data tuple like ``("git", "path", "inline")``.
    """
    if not (isinstance(node, (ast.List, ast.Tuple)) and node.elts):
        return False
    head = node.elts[0]
    is_git_exe = (
        isinstance(head, ast.Constant)
        and isinstance(head.value, str)
        and (head.value == "git" or head.value.endswith("/git"))
    ) or (isinstance(head, ast.Name) and head.id.lower() == "git")
    if not is_git_exe:
        return False
    rest = node.elts[1:]
    if not rest:
        return True
    for item in rest:
        if isinstance(item, (ast.Starred, ast.Name)):
            return True
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            if item.value.startswith("-") or item.value in _SUBCOMMANDS:
                return True
    return False


def git_commands_written_out(source: str, *, shell: bool = True) -> list[tuple[int, str]]:
    """``(line, text)`` for every git command written by hand in ``source``.

    ``shell=False`` finds only the argv form (run on every file); the shell
    form is skipped for the allowlisted files.
    """
    tree = ast.parse(source)
    prose = _prose_ids(tree)
    placeholders = {id(v) for j in ast.walk(tree) if isinstance(j, ast.JoinedStr) for v in j.values}
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if _argv_git(node):
            found.append((node.lineno, "argv " + ast.unparse(node)[:100]))
            continue
        if not shell:
            continue
        text = None
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in prose
            and id(node) not in placeholders
        ):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else "\x00" for v in node.values)
        if text and _SHELL_GIT.search(_BACKTICKS.sub("", text)):
            found.append((node.lineno, _BACKTICKS.sub("", text).strip()[:100]))
    return found


def test_no_embry0_code_writes_a_sandbox_git_command_outside_the_sandbox_git_module():
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == _MODULE:
            continue
        shell = rel not in _SHELL_ALLOWLIST
        for line, text in git_commands_written_out(path.read_text(encoding="utf-8"), shell=shell):
            offenders.append(f"{rel}:{line}: {text}")
    assert not offenders, (
        "git commands written out by hand: a git command embry0 runs in a sandbox must be built with "
        "embry0.execution.sandbox_git (git/command/script) and run with sandbox_git.run, or it reads the "
        "configuration and hooks the agent can write:\n" + "\n".join(offenders)
    )


def test_the_detector_catches_every_form():
    """Each way of writing a git command is caught; prose, data tuples and backticked markup are not."""
    caught = """
a = ["git", "-C", "/workspace", "status"]
b = ("git", "push")
c = [GIT, "status"]
d = ["/usr/bin/git", "status"]
e = ["git", *args]
f = "cd /workspace && git commit -m x"
g = f"git -C /workspace fetch origin {ref}"
h = "set -e; git reset -q --hard HEAD"
i = "x=$(git rev-parse HEAD)"
j = "(git apply --index p.diff || true)"
k = "if git diff --quiet; then :; fi"
l = "! git merge-base --is-ancestor a b"
m = "while git rev-parse; do :; done"
n = "command git status"
o = "timeout 30 git status"
p = "xargs -0 git check-attr"
q = "GIT_DIR=/x git status"
r = "git --no-pager log"
s = "git --git-dir=/x status"
t = "git -P status"
u = "git diff-tree -r HEAD"
v = "git format-patch -1"
w = f"git {sub}"
x = "git " + sub
"""
    lines = {line for line, _ in git_commands_written_out(caught)}
    expected = set(range(2, 24))  # lines 2..23, every assignment above
    missing = expected - lines
    assert not missing, f"the detector missed these forms (line numbers): {sorted(missing)}"

    quiet = """
context = {"type": "git", "repo": repo}
kinds = ("git", "path", "inline")
msg = "git did not report a commit sha"
note = "the git proxy rewrites the URL"
prompt = "Review the change with `git diff main...HEAD` before merging."
err = "git context requires 'repo'"
cfg = "http://git-proxy:9101/git/acme/widgets.git"
"""
    hits = git_commands_written_out(quiet)
    assert not hits, f"the detector matched prose or data: {hits}"


def test_the_argv_form_is_refused_in_every_file_even_an_allowlisted_one():
    """A real sandbox command hidden as an argv in an allowlisted prose file is still caught."""
    source = 'x = run(docker.build_exec_cmd(c, ["git", "-C", "/workspace", "status"]))\n'
    assert git_commands_written_out(source, shell=False), "the argv detector must run with the shell detector off"


def test_the_listed_modules_still_exist():
    missing = [rel for rel in (_MODULE, *_SHELL_ALLOWLIST) if not (ROOT / rel).is_file()]
    assert not missing, f"listed modules that no longer exist: {missing}"
