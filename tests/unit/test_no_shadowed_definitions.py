"""No module of the package or its tests defines one name twice in the same scope.

Python keeps the last definition, so every caller, the ones written above the
second definition included, runs the second. A test helper redefined lower in
its module silently changed the data of the tests above it (#360), and ruff's
F811 does not see it once the first definition has been used. A property's
setter or deleter, an ``overload`` and a ``singledispatch`` registration
reuse a name on purpose and are left out.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREES = ("embry0", "tests")


def _reuses_on_purpose(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        text = ast.unparse(decorator)
        if text.endswith((".setter", ".deleter")) or ".register" in text or "overload" in text:
            return True
    return False


def _shadowed(body: list[ast.stmt], where: str) -> list[str]:
    seen: dict[str, list[int]] = defaultdict(list)
    found: list[str] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not _reuses_on_purpose(node):
                seen[node.name].append(node.lineno)
            if isinstance(node, ast.ClassDef):
                found += _shadowed(node.body, f"{where}{node.name}.")
    found += [f"{where}{name} at lines {lines}" for name, lines in seen.items() if len(lines) > 1]
    return found


def test_no_module_defines_a_name_twice_in_one_scope() -> None:
    found = [
        f"{path.relative_to(ROOT)}: {entry}"
        for tree in TREES
        for path in sorted((ROOT / tree).rglob("*.py"))
        for entry in _shadowed(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body, "")
    ]
    assert not found, "a name defined twice in one scope; every caller runs the last one:\n" + "\n".join(found)


def test_the_check_sees_a_helper_defined_twice() -> None:
    """The check itself: a module-level helper used, then defined again, as #360 found."""
    source = "def helper():\n    return 1\n\ndef test_a():\n    assert helper()\n\ndef helper():\n    return 2\n"
    assert _shadowed(ast.parse(source).body, "") == ["helper at lines [1, 7]"]
    setter = "class C:\n    @property\n    def x(self): ...\n    @x.setter\n    def x(self, v): ...\n"
    assert _shadowed(ast.parse(setter).body, "") == [], "a property's setter reuses its name on purpose"
