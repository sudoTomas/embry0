"""A check of a whole value uses ``fullmatch``, never ``match`` with ``$``.

Python's ``$`` also matches just before a final newline, so
``re.match(r"^[a-z]+$", "abc\\n")`` succeeds: a check written that way takes a
value that ends in a newline. ``fullmatch`` (or ``\\Z``) does not.

This test reads every module under ``embry0/`` and fails on a ``match`` call
whose pattern holds a ``$`` anchor. A call is ``<pattern>.match(...)`` or
``re.match(...)``, with ``re`` or its ``match`` under any name they are
imported as and the pattern positional or ``pattern=``. It reads the pattern
when the call names it: a literal, a module or class attribute
(``_NAME.match``, ``self._NAME.match``), an imported name,
``re.compile(...)`` written inline, or a local variable assigned one of those.
``$`` is found in the parsed pattern, so ``\\$`` and ``[$]`` are not anchors.
It also fails if it reads fewer than ``MIN_MATCH_CALLS`` calls in all.

Two lists hold the deliberate exceptions, each with its reason:

- ``PARSERS``: a ``match`` that parses rather than checks. It reads one line
  of text, takes groups out of it, or puts ``$`` inside an alternation.
- ``UNCHECKED``: a ``match`` whose pattern the test cannot read, such as a
  glob translated at run time or a loop variable.

A failure names the call. A check of a value: use ``fullmatch``. A parser:
add it to ``PARSERS`` with the reason.
"""

from __future__ import annotations

import ast
import functools
import importlib
import re
from pathlib import Path
from re import _constants, _parser  # the parse tree is the one exact way to find a ``$`` anchor
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "embry0"
#: How many ``match`` calls the guard reads under ``embry0/`` at least. A scan
#: that silently finds fewer has stopped reading something; raise it as calls are added.
MIN_MATCH_CALLS = 61

#: (module path, the pattern as the call names it) -> why ``match`` with ``$`` is meant there.
PARSERS: dict[tuple[str, str], str] = {
    ("embry0/execution/runtime/docker.py", "_SIZE"): (
        "parses a size docker printed into groups; the trailing \\s* takes a final newline anyway"
    ),
    ("embry0/notifications/inbound_parser.py", "_DIRECTIVE_RE"): (
        "parses one line of a comment into groups; the trailing \\s* takes a final newline anyway"
    ),
    ("embry0/safety/policy.py", "_RULE_RE"): (
        "parses a deny rule into tool and path; with fullmatch a rule ending in a newline would be skipped, "
        "so the predicate would refuse less"
    ),
    ("embry0/services/done_when.py", "_BULLET"): "parses one line of an issue body into a bullet's text",
    ("embry0/services/healing/deploys.py", "_MARKER_RE"): "parses a stripped deploy marker line into sha and services",
    ("embry0/services/healing/forge.py", "_PR_URL"): "parses a stripped pull request URL into owner, repo and number",
    ("embry0/services/merge_approvals.py", "_PR_URL"): "parses a stripped pull request URL into repo and number",
    ("embry0/services/merge_recommendation.py", "_BOILERPLATE"): (
        "a prefix match: $ ends only one alternative, the others match the start of the text"
    ),
    ("embry0/services/pr_review/triggers.py", "_COMMAND_RE"): (
        "a prefix match: (?:\\s|$) ends the command word, the rest of the line follows"
    ),
    ("embry0/services/proposals.py", "_LOCATION"): "parses a stripped table cell into path and line",
    ("embry0/services/proposals.py", "_LOGIN"): "takes a login out of a CODEOWNERS entry split on whitespace",
    ("embry0/services/signature_attribution.py", "_HEADING"): "parses one Markdown line into a heading",
    ("embry0/services/standards.py", "_HEADING"): "parses one stripped Markdown line into a heading",
    ("embry0/workflows/issue_to_pr/evidence.py", "_HEADING_RE"): "parses one Markdown line into a heading",
    ("embry0/workflows/pr_review/summary.py", "_FENCE_RE"): "parses one Markdown line into a code fence",
    ("embry0/workflows/verification/runner.py", "_RESULT_RE"): (
        "parses the stripped result line into exit code and time"
    ),
}

#: (module path, the receiver as written) -> why the test cannot read the pattern, and what it is.
UNCHECKED: dict[tuple[str, str], str] = {
    ("embry0/api/route_table.py", "entry.path_regex"): "a route's own path regex, matched the way Starlette matches it",
    ("embry0/services/areas.py", "_glob(p)"): "a path glob translated at run time; it ends in \\Z",
    ("embry0/services/instruction_files.py", "pattern"): "a path glob from _compile, which ends it in \\Z",
    ("embry0/services/slop_meter/weights.py", "_compiled(pattern)"): (
        "a gitignore glob translated at run time; it ends in \\Z"
    ),
    ("embry0/workflows/qa/_glob_match.py", "rx"): "a glob from compile_glob, which ends it in \\Z",
    ("embry0/workflows/verification/gate.py", "rx"): "a failure-line parser from _FAILURE_LINE_RES, read line by line",
}

# Only these nodes are evaluated to find a pattern's value: names, attributes,
# literals, f-strings, concatenation and ``re.compile``. Nothing else runs.
_EVALUABLE = (
    ast.Name,
    ast.Attribute,
    ast.Constant,
    ast.Load,
    ast.BinOp,
    ast.Add,
    ast.BitOr,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Call,
    ast.keyword,
)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def has_dollar(pattern: str | bytes, flags: int = 0) -> bool:
    """Whether ``pattern`` holds a ``$`` anchor, read from its parse tree."""

    def walk(node: Any) -> bool:
        if isinstance(node, _parser.SubPattern):
            return any(walk(item) for item in node.data)
        if isinstance(node, tuple | list):
            return node == (_constants.AT, _constants.AT_END) or any(walk(item) for item in node)
        return False

    return walk(_parser.parse(pattern, flags))


def _is_re_compile(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and ast.unparse(node.func) == "re.compile"


def _evaluate(expr: ast.expr, namespace: dict[str, Any]) -> Any:
    for node in ast.walk(expr):
        if not isinstance(node, _EVALUABLE) or (isinstance(node, ast.Call) and not _is_re_compile(node)):
            raise LookupError(f"not evaluated: {ast.unparse(expr)}")
    return eval(compile(ast.Expression(expr), "<pattern>", "eval"), dict(namespace))


def _static(expr: ast.expr) -> str | None:
    """The pattern text of a literal, an f-string or ``re.compile`` of one; a formatted value reads as ``x``."""
    if _is_re_compile(expr):
        assert isinstance(expr, ast.Call)
        return _static(expr.args[0]) if expr.args else None
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.JoinedStr):
        parts = [v.value if isinstance(v, ast.Constant) else "x" for v in expr.values]
        return "".join(str(p) for p in parts)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left, right = _static(expr.left), _static(expr.right)
        return None if left is None or right is None else left + right
    return None


def _bound_names(scope: ast.AST) -> set[str]:
    """Names a function or comprehension binds itself: parameters, assignment and loop targets."""
    names: set[str] = set()
    if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
        args = scope.args
        names |= {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg] if a}
    if isinstance(scope, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
        targets: list[ast.expr] = [g.target for g in scope.generators]
    else:
        targets = []
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign):
                targets += node.targets
            elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.For | ast.AsyncFor | ast.NamedExpr):
                targets.append(node.target)
    for target in targets:
        names |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
    return names


def _re_names(tree: ast.Module, namespace: dict[str, Any]) -> tuple[set[str], set[str]]:
    """The names ``re`` and ``re.match`` go by in a module: its imports, anywhere in it, and its globals."""
    modules, matches = {"re"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.asname or alias.name for alias in node.names if alias.name == "re"}
        elif isinstance(node, ast.ImportFrom) and node.module == "re" and node.level == 0:
            matches |= {alias.asname or alias.name for alias in node.names if alias.name == "match"}
    modules |= {name for name, value in namespace.items() if value is re}
    matches |= {name for name, value in namespace.items() if value is re.match}
    return modules, matches


def scan(source: str, namespace: dict[str, Any]) -> list[tuple[int, str, str]]:
    """``(line, pattern as named, kind)`` for each ``match`` call in ``source``.

    A call is ``<pattern>.match(...)``, or ``re.match(...)`` under any name
    ``re`` or its ``match`` is imported as. ``namespace`` is the module's
    globals, which resolves module attributes and imported names. The kind:
    ``"dollar"``, the pattern holds ``$``; ``"clean"``, it does not;
    ``"unread"``, the test cannot tell what the pattern is; ``"other"``, the
    receiver is not a regular expression.
    """
    tree = ast.parse(source)
    re_modules, re_matches = _re_names(tree, namespace)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def enclosing(node: ast.AST) -> list[ast.AST]:
        chain = []
        while node in parents:
            node = parents[node]
            chain.append(node)
        return chain

    out: list[tuple[int, str, str]] = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr == "match":
            re_function = isinstance(func.value, ast.Name) and func.value.id in re_modules
        elif isinstance(func, ast.Name) and func.id in re_matches:
            re_function = True
        else:
            continue
        flags: ast.expr | None = None
        named_expr: ast.expr | None = func.value if isinstance(func, ast.Attribute) else None
        if re_function:
            # re.match(pattern, string, flags), each also by keyword; *args or **kwargs cannot be read.
            keywords = {k.arg: k.value for k in call.keywords if k.arg}
            unpacked = any(isinstance(a, ast.Starred) for a in call.args) or len(keywords) < len(call.keywords)
            named_expr = None if unpacked else (call.args[0] if call.args else keywords.get("pattern"))
            flags = call.args[2] if len(call.args) > 2 else keywords.get("flags")
        if named_expr is None:
            out.append((call.lineno, ast.unparse(call), "unread"))
            continue
        named = ast.unparse(named_expr)
        scope_ns = dict(namespace)
        for owner in enclosing(call):
            if isinstance(owner, ast.ClassDef) and owner.name in namespace:
                scope_ns["self"] = scope_ns["cls"] = namespace[owner.name]
                break
        target: ast.expr | None = named_expr
        root = named_expr
        while isinstance(root, ast.Attribute):
            root = root.value
        local = isinstance(root, ast.Name) and not (root.id in ("self", "cls") and root is not named_expr)
        scope = next(
            (
                s
                for s in enclosing(call)
                if isinstance(s, _SCOPES) and local and getattr(root, "id", "") in _bound_names(s)
            ),
            None,
        )
        if scope is not None:
            # A local: read the one plain assignment to it in that scope; anything else is unread.
            assigned = [
                n.value
                for n in ast.walk(scope)
                if isinstance(n, ast.Assign)
                and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name)
                and n.targets[0].id == getattr(root, "id", None)
            ]
            target = assigned[0] if root is named_expr and len(assigned) == 1 else None
        value: Any = None
        if target is not None:
            try:
                value = _evaluate(target, scope_ns)
            except Exception:  # noqa: BLE001 — any failure means "not evaluated"; _static may still read it
                value = _static(target)
        if value is None:
            out.append((call.lineno, named, "unread"))
            continue
        if isinstance(value, re.Pattern):
            pattern, pattern_flags = value.pattern, value.flags
        elif isinstance(value, str | bytes):
            pattern, pattern_flags = value, 0
            if flags is not None:
                try:
                    pattern_flags = int(_evaluate(flags, scope_ns))
                except Exception:  # noqa: BLE001 — flags cannot add or remove a $ anchor
                    pattern_flags = 0
        else:
            out.append((call.lineno, named, "other"))  # not a regular expression: some other object's ``match``
            continue
        out.append((call.lineno, named, "dollar" if has_dollar(pattern, pattern_flags) else "clean"))
    return out


def findings(source: str, namespace: dict[str, Any]) -> list[tuple[int, str, str]]:
    """The calls :func:`scan` reports as ``"dollar"`` or ``"unread"``: the ones a test acts on."""
    return [found for found in scan(source, namespace) if found[2] in ("dollar", "unread")]


def _module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


@functools.cache
def _tree_scan() -> tuple[tuple[str, int, str, str], ...]:
    """Every ``match`` call under ``embry0/``: ``(module path, line, pattern as named, kind)``."""
    found: list[tuple[str, int, str, str]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "match(" not in source:
            continue
        module = importlib.import_module(_module_name(path))
        rel = path.relative_to(ROOT).as_posix()
        found += [(rel, line, named, kind) for line, named, kind in scan(source, vars(module))]
    return tuple(sorted(found))


def _tree_findings() -> list[tuple[str, int, str, str]]:
    return [found for found in _tree_scan() if found[3] in ("dollar", "unread")]


def test_no_check_uses_match_with_a_dollar_anchor() -> None:
    bad = [
        f"{rel}:{line} {named}.match(...)"
        for rel, line, named, kind in _tree_findings()
        if kind == "dollar" and (rel, named) not in PARSERS
    ]
    assert not bad, (
        "match with a $-anchored pattern takes a value that ends in a newline; use fullmatch, "
        "or list a parser in PARSERS with the reason:\n" + "\n".join(bad)
    )


def test_every_match_the_test_cannot_read_is_listed() -> None:
    unread = [
        f"{rel}:{line} {named}.match(...)"
        for rel, line, named, kind in _tree_findings()
        if kind == "unread" and (rel, named) not in UNCHECKED
    ]
    assert not unread, (
        "the test cannot read these patterns, so it cannot tell whether they end in $; name the pattern "
        "where the test can see it, or list the call in UNCHECKED with what it is:\n" + "\n".join(unread)
    )


def test_every_listed_exception_is_still_in_the_tree() -> None:
    found = {(rel, named, kind) for rel, _, named, kind in _tree_findings()}
    stale = [f"PARSERS {key}" for key in PARSERS if (*key, "dollar") not in found]
    stale += [f"UNCHECKED {key}" for key in UNCHECKED if (*key, "unread") not in found]
    assert not stale, "these exceptions no longer match a call; remove them:\n" + "\n".join(stale)


PLANTED = """
import re

NAME = re.compile(r"^[a-z]+$")
SAFE = re.compile(r"^[a-z]+\\Z")
LITERAL_DOLLAR = re.compile(r"^cost: \\$[0-9]+")
IN_A_CLASS = re.compile(r"[$]")


class Checker:
    _ID = re.compile(r"^[0-9]+$")

    def check(self, value):
        return self._ID.match(value)


def checks(value, rule):
    a = NAME.match(value)
    b = re.match(r"^[a-z]+$", value)
    c = re.compile(r"^x$").match(value)
    pattern = f"^{rule}$"
    d = re.match(pattern, value)
    e = NAME.fullmatch(value)
    f = SAFE.match(value)
    g = LITERAL_DOLLAR.match(value)
    h = IN_A_CLASS.match(value)
    return a, b, c, d, e, f, g, h, rule.match(value)
"""


def test_the_guard_finds_each_form_of_a_dollar_match() -> None:
    namespace: dict[str, Any] = {}
    exec(compile(PLANTED, "<planted>", "exec"), namespace)
    found = sorted((named, kind) for _, named, kind in findings(PLANTED, namespace))
    expected = sorted(
        [
            ("self._ID", "dollar"),
            ("NAME", "dollar"),
            ("'^[a-z]+$'", "dollar"),
            ("re.compile('^x$')", "dollar"),
            ("pattern", "dollar"),
            ("rule", "unread"),
        ]
    )
    # fullmatch, \Z, an escaped \$ and [$] are not reported; a parameter's pattern is reported as unread.
    assert found == expected, f"the guard must report exactly the $-anchored and unreadable matches: {found}"


PLANTED_RE_NAMES = """
import re
import re as _re
from re import match
from re import match as re_match


def checks(value, parts):
    a = _re.match(r"^alias$", value)
    b = match(r"^imported$", value)
    c = re_match(r"^renamed$", value)
    d = re.match(pattern=r"^keyword$", string=value)
    e = re.match(*parts)
    f = _re.match(string=value, pattern=r"^[a-z]+\\Z")
    return a, b, c, d, e, f
"""


def test_the_guard_finds_a_dollar_match_however_re_is_named() -> None:
    namespace: dict[str, Any] = {}
    exec(compile(PLANTED_RE_NAMES, "<planted>", "exec"), namespace)
    found = sorted((named, kind) for _, named, kind in findings(PLANTED_RE_NAMES, namespace))
    expected = sorted(
        [
            ("'^alias$'", "dollar"),
            ("'^imported$'", "dollar"),
            ("'^renamed$'", "dollar"),
            ("'^keyword$'", "dollar"),
            ("re.match(*parts)", "unread"),
        ]
    )
    # re under another name, match imported from re, the pattern= keyword; a call with no readable pattern is unread.
    assert found == expected, f"the guard must report re.match under every name it is called by: {found}"


def test_the_guard_reads_every_match_call_it_should() -> None:
    calls = _tree_scan()
    assert len(calls) >= MIN_MATCH_CALLS, (
        f"the guard read {len(calls)} match calls under embry0/, fewer than {MIN_MATCH_CALLS}: "
        "it has stopped reading some of them"
    )
