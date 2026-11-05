"""Whether a path pattern a shell word names can match a deny rule (embry0.safety.path_patterns).

A glob is read as bash expands it by default: within one segment, and never a
hidden name unless the segment starts with a ``.``. A run-time value is any
text. Each case is pinned both ways: a pattern that can reach the rule, and one
that cannot.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest


def _module() -> Any:
    spec = importlib.util.find_spec("embry0.safety.path_patterns")
    assert spec is not None, "embry0.safety.path_patterns must say whether a path pattern can match a deny rule"
    import embry0.safety.path_patterns as module

    return module


def _ref(*parts: Any) -> Any:
    return _module().PathRef(tuple(parts))


def _lit(text: str) -> Any:
    return _module().Lit(text)


def _star(guard: bool = True) -> Any:
    return _module().Star(guard=guard)


def _wild() -> Any:
    return _module().Wild()


SETTINGS = "/workspace/.claude/settings*.json"


@pytest.mark.parametrize(
    ("ref", "rule", "can"),
    [
        # a glob within one segment
        (lambda: _ref(_lit("/workspace/"), _star(), _lit(".py")), SETTINGS, False),
        (lambda: _ref(_lit("/workspace/.claude/"), _star()), SETTINGS, True),
        (lambda: _ref(_lit("/workspace/.claude/"), _star(), _lit(".json")), SETTINGS, True),
        (lambda: _ref(_lit("/workspace/.claude/"), _star(), _lit(".md")), SETTINGS, False),
        # no hidden name unless the segment starts with a dot
        (lambda: _ref(_lit("/workspace/"), _star(), _lit("/settings.json")), SETTINGS, False),
        (lambda: _ref(_lit("/workspace/."), _star(), _lit("/settings.json")), SETTINGS, True),
        (lambda: _ref(_lit("/workspace/"), _star(guard=False), _lit("/settings.json")), SETTINGS, True),
        (lambda: _ref(_lit("/home/agent/"), _star()), "/home/agent/.ssh/**", False),
        (lambda: _ref(_lit("/workspace/"), _module().One(guard=True), _lit("claude/settings.json")), SETTINGS, False),
        (lambda: _ref(_lit("/workspace/"), _module().One(guard=False), _lit("claude/settings.json")), SETTINGS, True),
        (lambda: _ref(_lit("/home/agent/.ss"), _module().One()), "/home/agent/.ssh/**", True),
        # a glob never crosses a /
        (lambda: _ref(_lit("/"), _star(), _lit("/x")), "/etc/**", True),
        (lambda: _ref(_lit("/"), _star(), _lit("c/passwd")), "/etc/**", True),
        (lambda: _ref(_lit("/e"), _star()), "/etc/**", True),
        (lambda: _ref(_lit("/u"), _star()), "/etc/**", False),
        # a run-time value is any text, / included
        (lambda: _ref(_lit("/workspace/"), _wild()), SETTINGS, True),
        (lambda: _ref(_lit("/workspace/"), _wild(), _lit(".log")), SETTINGS, False),
        (lambda: _ref(_wild(), _lit("/passwd")), "/etc/**", True),
        (lambda: _ref(_wild(), _lit("/x.txt")), SETTINGS, False),
        # an extglob is any name in its segment, a hidden one included
        (lambda: _ref(_lit("/workspace/"), _module().AnyName(), _lit("/settings.json")), SETTINGS, True),
        # a bracket matches its characters only
        (lambda: _ref(_lit("/"), _module().One(_module().CharSet(frozenset("e"))), _lit("tc/x")), "/etc/**", True),
        (lambda: _ref(_lit("/"), _module().One(_module().CharSet(frozenset("u"))), _lit("tc/x")), "/etc/**", False),
    ],
)
def test_could_match(ref: Any, rule: str, can: bool) -> None:
    pattern = ref()
    got = _module().could_match(pattern, rule)
    assert got is can, f"{pattern.path!r} against {rule!r}: want {can}, got {got}"


def test_below_is_everything_under_a_path() -> None:
    root = _module().PathRef.exact("/")
    assert root.below().path == "/…", f"below / is every absolute path, got {root.below().path!r}"
    assert _module().could_match(root.below(), "/etc/**"), "everything under / reaches /etc"
    workspace = _module().PathRef.exact("/workspace")
    assert _module().could_match(workspace.below(), SETTINGS), "everything under /workspace reaches its settings"
    assert not _module().could_match(workspace.below(), "/etc/**"), "nothing under /workspace is in /etc"


def test_a_pattern_is_shown_with_its_literals_escaped() -> None:
    pattern = _ref(_lit("/a*b/"), _star(), _lit("."), _wild())
    assert pattern.path == "/a\\*b/*.…", f"a literal * is escaped, a glob's is not: {pattern.path!r}"
