"""A unit test skips only on a condition: a missing tool, service or platform.

The skip count of a run should list the environments it lacked. A test kept
behind an unconditional skip, after the code it tested was deleted, pads that
count and tests nothing; delete it instead. A test that cannot run everywhere
takes ``skipif`` or ``pytest.importorskip``.
"""

from __future__ import annotations

import ast
from pathlib import Path

UNIT = Path(__file__).resolve().parent


def _is_unconditional_skip(node: ast.expr) -> bool:
    """``pytest.mark.skip`` or ``pytest.mark.skip(...)``; ``skipif`` is a condition."""
    target = node.func if isinstance(node, ast.Call) else node
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "skip"
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "mark"
    )


def _unconditional_skips(path: Path) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            lines += [d.lineno for d in node.decorator_list if _is_unconditional_skip(d)]
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            marks = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
            lines += [node.lineno for mark in marks if _is_unconditional_skip(mark)]
    return lines


def test_no_unit_test_is_skipped_unconditionally() -> None:
    found = [
        f"{path.relative_to(UNIT)}:{line}" for path in sorted(UNIT.rglob("*.py")) for line in _unconditional_skips(path)
    ]
    assert not found, "unit tests skipped unconditionally; delete them or skip on a condition:\n" + "\n".join(found)
