"""No block of the package or its tests defines one name twice.

Python keeps the last binding, so every caller, the ones written above the
second definition included, runs the second. A test helper redefined lower in
its module silently changed the data of the tests above it (#360), and ruff's
F811 does not see it once the first definition has been used.

What counts, in each block of statements (a module, a class or function body,
or one branch of an ``if``, ``try``, ``with``, ``for``, ``while`` or ``match``
inside one): a function or class defined twice, or a name both imported and
defined. Nested functions and classes are blocks of their own.

What does not, on purpose:

- two branches of an ``if``/``else`` or ``try``/``except`` each defining a name:
  they are alternatives, not one block;
- a name imported twice (``import a.b`` and ``import a.c`` both bind ``a``);
- a plain assignment over a definition (``f = cache(f)``): rebinding a name
  to a wrapped version of itself is common, and is not looked at;
- a property's ``.setter``, ``.getter`` or ``.deleter``, an ``overload`` and a
  ``singledispatch`` ``.register``, read from the decorator's own name, which
  reuse a name on purpose.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREES = ("embry0", "tests")

#: Decorators, by the attribute they end in, that define a name again on purpose.
_REUSING_ATTRIBUTES = frozenset({"setter", "getter", "deleter", "register", "overload"})

_Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def _reuses_on_purpose(node: _Definition) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id == "overload":
            return True
        if isinstance(target, ast.Attribute) and target.attr in _REUSING_ATTRIBUTES:
            return True
    return False


def _bindings(stmt: ast.stmt) -> Iterator[tuple[str, int, bool]]:
    """The names ``stmt`` binds in its block: ``(name, line, defined)``, ``defined`` false for an import."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if not _reuses_on_purpose(stmt):
            yield stmt.name, stmt.lineno, True
    elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
        for alias in stmt.names:
            if alias.name != "*":
                yield alias.asname or alias.name.split(".")[0], stmt.lineno, False


def _inner_blocks(stmt: ast.stmt) -> Iterator[list[ast.stmt]]:
    """The blocks a compound statement holds, each its own; a definition's body is a scope, walked apart."""
    for field in ("body", "orelse", "finalbody"):
        block = getattr(stmt, field, None)
        if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
            yield block
    for handler in getattr(stmt, "handlers", ()):
        yield handler.body
    for case in getattr(stmt, "cases", ()):
        yield case.body


def _shadowed(body: list[ast.stmt], where: str) -> list[str]:
    seen: dict[str, list[tuple[int, bool]]] = defaultdict(list)
    for stmt in body:
        for name, line, defined in _bindings(stmt):
            seen[name].append((line, defined))
    found = [
        f"{where}{name} at lines {[line for line, _ in bound]}"
        for name, bound in seen.items()
        if sum(defined for _, defined in bound) >= 2 or (any(d for _, d in bound) and not all(d for _, d in bound))
    ]
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found += _shadowed(stmt.body, f"{where}{stmt.name}.")
        else:
            for block in _inner_blocks(stmt):
                found += _shadowed(block, where)
    return found


def _shadowed_in(source: str) -> list[str]:
    return _shadowed(ast.parse(source).body, "")


def test_no_module_defines_a_name_twice_in_one_block() -> None:
    found = [
        f"{path.relative_to(ROOT)}: {entry}"
        for tree in TREES
        for path in sorted((ROOT / tree).rglob("*.py"))
        for entry in _shadowed(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body, "")
    ]
    assert not found, "a name defined twice in one block; every caller runs the last one:\n" + "\n".join(found)


def test_the_check_sees_each_kind_of_shadowing() -> None:
    """The check itself, on what it is meant to find."""
    cases = {
        "a module-level helper used, then defined again (#360)": (
            "def helper():\n    return 1\n\ndef test_a():\n    assert helper()\n\ndef helper():\n    return 2\n",
            ["helper at lines [1, 7]"],
        ),
        "two definitions in one if block": (
            "if True:\n    def f(): ...\n    def f(): ...\n",
            ["f at lines [2, 3]"],
        ),
        "a nested function defined twice": (
            "def outer():\n    def inner(): ...\n    def inner(): ...\n",
            ["outer.inner at lines [2, 3]"],
        ),
        "a method defined twice": ("class C:\n    def m(self): ...\n    def m(self): ...\n", ["C.m at lines [2, 3]"]),
        "a name imported, then defined": ("from os import path\n\ndef path(): ...\n", ["path at lines [1, 3]"]),
    }
    for what, (source, expected) in cases.items():
        assert _shadowed_in(source) == expected, f"{what}: {_shadowed_in(source)}"


def test_the_check_leaves_alone_what_reuses_a_name_on_purpose() -> None:
    cases = {
        "a property's getter, setter and deleter": (
            "class C:\n    @property\n    def x(self): ...\n    @x.getter\n    def x(self): ...\n"
            "    @x.setter\n    def x(self, v): ...\n    @x.deleter\n    def x(self): ...\n"
        ),
        "an overload, bare and from typing": (
            "@overload\ndef f(a: int) -> int: ...\n@typing.overload\ndef f(a: str) -> str: ...\ndef f(a): ...\n"
        ),
        "a singledispatch registration": (
            "@singledispatch\ndef f(a): ...\n@f.register\ndef _(a: int): ...\n@f.register(str)\ndef _(a): ...\n"
        ),
        "the two branches of an if and of a try": (
            "if X:\n    def f(): ...\nelse:\n    def f(): ...\n"
            "try:\n    from a import g\nexcept ImportError:\n    def g(): ...\n"
        ),
        "a name imported twice": "import os.path\nimport os\n",
        "an assignment over a definition": "def f(): ...\nf = wrap(f)\n",
    }
    for what, source in cases.items():
        assert _shadowed_in(source) == [], f"{what}: {_shadowed_in(source)}"


def test_a_word_in_a_decorator_s_arguments_exempts_nothing() -> None:
    """The exemption is read from the decorator's own name: a parametrize list naming "overloaded_error" (as one
    test module's does) does not let a test defined twice under it through."""
    source = (
        '@pytest.mark.parametrize("case", ["overloaded_error"])\ndef test_a(case): ...\n'
        '@pytest.mark.parametrize("case", ["setter.x"])\ndef test_a(case): ...\n'
    )
    assert _shadowed_in(source) == ["test_a at lines [2, 4]"], _shadowed_in(source)
