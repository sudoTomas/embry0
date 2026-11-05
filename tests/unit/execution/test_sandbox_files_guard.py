"""The invariants that keep file movement safe: exec only as the agent, no ``docker cp``, no archive API.

A copy into or out of a sandbox that bypassed :mod:`embry0.execution.sandbox_files`
would have to run something as another user, use ``docker cp``, or use the
archive API, so the guard refuses all three:

* **an exec as anyone but the agent** — outside an allowlist of (path,
  function), any ``user=`` / ``reader=`` argument other than the literal
  ``"agent"`` (a name, ``"0"``, ``"0:0"`` or a constant alike), a ``user`` /
  ``reader`` key in a dict literal (what ``**`` would carry), and any ``-u`` /
  ``-u<x>`` / ``--user`` / ``--user=`` / ``--privileged`` as an argv element or
  inside a shell string;
* **a ``docker cp``** — the ``"cp"`` subcommand as an argv element (however the
  container reference is built), passed to ``append`` / ``extend``, or a
  ``docker cp`` / ``docker container cp`` shell string, anywhere;
* **the archive API** — ``put_archive`` / ``get_archive`` or an Engine
  ``/archive`` URL, anywhere.

Docstrings and bare string statements are documentation, not commands, and
are skipped. ``test_the_guard_catches_each_form`` pins every form.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "embry0"

#: (path → None for the whole file, or the functions) allowed another user or a user flag, each with its reason.
_USER_ALLOWED: dict[str, set[str] | None] = {
    # The stager and reader themselves: write and remove drops as root, read as the agent.
    "embry0/execution/sandbox_files.py": None,
    # Writes embry0's root-owned git config.
    "embry0/execution/sandbox_git.py": None,
    # The exec builder renders `-u <user>` for whoever calls it.
    "embry0/execution/docker_client.py": {"build_exec_cmd"},
    # A capability-scoped throwaway `docker run --user root` that chowns a volume's mount point (#453).
    "embry0/execution/runtime/docker.py": {"chown_volume_cmd"},
    # `git push -u` (set upstream), a git flag.
    "embry0/workflows/issue_to_pr/nodes/_developer.py": {"_rescue_push_branch"},
    # The shell's `set -u`.
    "embry0/workflows/pr_review/checkout.py": {"build_checkout_script"},
    # `user=` is the LLM prompt's user message.
    "embry0/services/background_models.py": {"ask"},
    "embry0/services/brief_writer.py": {"tick"},
    "embry0/services/slop_meter/judge.py": {"judge"},
    # The Ring-3 shell analyzer's tables of command flags.
    "embry0/safety/bash_paths.py": None,
    "embry0/safety/shell_commands.py": None,
}

_USER_FLAG = re.compile(r"(?:^|\s)(-u\S*|--user(?:=\S*)?|--privileged)(?=\s|$)")
_DOCKER_CP = re.compile(r"\bdocker\s+(?:container\s+)?cp\b")


def _prose_ids(tree: ast.AST) -> set[int]:
    """Strings that are documentation (a docstring or a bare string statement), never a command."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            ids.add(id(node.value))
    return ids


def _walk_with_function(node: ast.AST, function: str | None = None) -> Iterator[tuple[ast.AST, str | None]]:
    """Every node with the name of the innermost function it sits in."""
    yield node, function
    inner = node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
    for child in ast.iter_child_nodes(node):
        yield from _walk_with_function(child, inner)


def _is_agent(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "agent"


def _user_offence(node: ast.AST, prose: set[int]) -> str | None:
    if isinstance(node, ast.Call):
        for kw in node.keywords:
            if kw.arg in ("user", "reader") and not _is_agent(kw.value):
                return f"{kw.arg}={ast.unparse(kw.value)[:40]} (only the agent may run commands)"
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and key.value in ("user", "reader")
                and isinstance(value, (ast.Constant, ast.Name, ast.Attribute))
                and not _is_agent(value)
            ):
                return f'a "{key.value}" key {ast.unparse(value)[:40]} (what ** would pass)'
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose:
        found = _USER_FLAG.search(node.value)
        if found:
            return f"a {found.group(1)!r} user flag"
    return None


def _cp_or_archive_offence(node: ast.AST, prose: set[int]) -> str | None:
    if isinstance(node, (ast.List, ast.Tuple)) and any(
        isinstance(e, ast.Constant) and e.value == "cp" for e in node.elts
    ):
        return 'an argv with the "cp" subcommand'
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("append", "extend"):
        for arg in node.args:
            if isinstance(arg, ast.Constant) and arg.value == "cp":
                return f'.{node.func.attr}("cp")'
    if isinstance(node, ast.Attribute) and node.attr in ("put_archive", "get_archive"):
        return f".{node.attr}() (the archive API)"
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose:
        if _DOCKER_CP.search(node.value):
            return 'a "docker cp" shell string'
        if "/archive" in node.value:
            return 'an Engine "/archive" URL'
    return None


def offenders(rel: str, source: str) -> list[tuple[int, str]]:
    """``(line, why)`` for each invariant ``source`` (at path ``rel``) breaks."""
    tree = ast.parse(source)
    prose = _prose_ids(tree)
    allowed = _USER_ALLOWED.get(rel, set())
    out: list[tuple[int, str]] = []
    for node, function in _walk_with_function(tree):
        line = getattr(node, "lineno", 0)
        why = _cp_or_archive_offence(node, prose)
        if why:
            out.append((line, why))
        user_ok = allowed is None or (function is not None and function in allowed)
        if not user_ok:
            why = _user_offence(node, prose)
            if why:
                out.append((line, why))
    return out


def test_the_invariants_hold_across_the_package():
    bad = [
        f"{rel}:{line}: {why}"
        for path in sorted(PACKAGE.rglob("*.py"))
        for rel in [path.relative_to(ROOT).as_posix()]
        for line, why in offenders(rel, path.read_text(encoding="utf-8"))
    ]
    assert not bad, (
        "file movement and commands in a sandbox must go through embry0.execution.sandbox_files: no exec as "
        "anyone but the agent, no docker cp and no archive API outside the allowed places:\n" + "\n".join(bad)
    )


_FORMS = """
a = docker.build_exec_cmd(c, cmd, user="0")
b = docker.build_exec_cmd(c, cmd, user="0:0")
d = docker.build_exec_cmd(c, cmd, user=ROOT)
e = docker.build_exec_cmd(c, cmd, **{"user": "root"})
f = read_regular_file(docker, c, p, limit=9, reader="0")
g = ["docker", "exec", "-uroot", c, "sh"]
h = ["docker", "exec", "--user=0", c, "sh"]
i = ["docker", "exec", "-u", "0", c, "sh"]
j = run("docker exec -u root c sh -c 'cat /etc/shadow'")
k = run(f"docker exec --user {who} c id")
m = ["docker", "run", "--privileged", "img"]
n = run("docker container cp a c1:/x")
o = ["cp", f"{c}:{p}", dst]
q = cmd.extend(["cp", ref])
r = cmd.append("cp")
s = ["cp", c + ":" + p]
t = ["cp", "%s:%s" % (c, p)]
u = ["cp", "{}:{}".format(c, p)]
v = ["cp", ref_var]
w = run("docker cp a b")
x = "docker cp a b".split()
y = client.put_archive(c, data)
z = client.get_archive(c, path)
aa = f"http://127.0.0.1/containers/{c}/archive?path=/"
bb = stage(container, data, user="root")
cc = opts = {"user": ADMIN}
"""


def test_the_guard_catches_each_form():
    lines = {line for line, _ in offenders("embry0/somewhere/new.py", _FORMS)}
    expected = set(range(2, 28))  # every assignment line, 2..27
    assert not expected - lines, f"forms missed (line numbers): {sorted(expected - lines)}"

    quiet = """
p = f"cp {src} {dst}"
q = ["rm", "-rf", dropdir]
r = "cp --remove-destination a b"
s = docker.build_exec_cmd(c, cmd, user="agent")
t = {"op": "cp"}
w = {"user": {"login": "octocat"}}
x = ["git", "status", "--porcelain"]
"""
    hits = offenders("embry0/somewhere/new.py", quiet)
    assert hits == [], f"the guard matched a harmless form: {hits}"


def test_the_allowlist_is_by_function_not_by_file():
    # runtime/docker.py may say --user root inside chown_volume_cmd only.
    inside = 'def chown_volume_cmd(self):\n    return ["docker", "run", "--user", "root", "img"]\n'
    elsewhere = 'def run_job(self):\n    return ["docker", "run", "--user", "root", "img"]\n'
    assert offenders("embry0/execution/runtime/docker.py", inside) == []
    assert offenders("embry0/execution/runtime/docker.py", elsewhere), "outside chown_volume_cmd it is refused"
    # and the allowlist never covers docker cp or the archive API
    cp_inside = 'def chown_volume_cmd(self):\n    return ["docker", "cp", f"{c}:/x", "/tmp/x"]\n'
    assert offenders("embry0/execution/runtime/docker.py", cp_inside), "docker cp is refused everywhere"


def test_every_allowlisted_function_still_exists():
    missing = []
    for rel, functions in _USER_ALLOWED.items():
        path = ROOT / rel
        if not path.is_file():
            missing.append(rel)
            continue
        if functions:
            names = {
                n.name
                for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            missing += [f"{rel}:{f}" for f in sorted(functions - names)]
    assert not missing, f"stale allowlist entries: {missing}"
