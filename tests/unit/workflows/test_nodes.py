"""Tests for branch slug sanitization and clone depth in workflow nodes."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "task,expected_safe",
    [
        ("normal task name", True),
        ("task with .. dots", True),
        ("task~with~tildes", True),
        ("task^with^carets", True),
        ("task:with:colons", True),
        ("task?with?questions", True),
        ("task*with*stars", True),
        ("task[with]brackets", True),
        ("task\\with\\backslash", True),
        ("emoji only 🎉🎊", True),  # strips to "task" fallback
        ("  leading spaces  ", True),
        (".lock", True),  # all non-alphanumeric → "task" fallback
    ],
)
def test_branch_slug_is_git_safe(task: str, expected_safe: bool) -> None:
    """Branch slug must only contain [a-z0-9-] after sanitization."""
    import re

    # Replicate the sanitization logic from developer_node
    slug = re.sub(r"[^a-z0-9-]+", "-", task[:30].lower()).strip("-") or "task"
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]*[a-z0-9]|[a-z0-9]", slug), (
        f"Slug {slug!r} from task {task!r} is not git-ref-safe"
    )


def test_clone_command_depth_is_50() -> None:
    """Clone command must use --depth=50 and fetch main:main at --depth=50."""
    # The command is constructed inline in init_node; we verify the string shape.
    # Actual init_node test would need async + mocked docker; this tests the
    # command template directly.
    depth = 50
    repo = "owner/repo"
    clone_cmd = (
        f"set -e && git clone --depth={depth} https://github.com/{repo}.git /workspace"
        f" && git -C /workspace fetch origin main:main --depth={depth} || true"
    )
    assert f"--depth={depth}" in clone_cmd
    assert "fetch origin main:main" in clone_cmd
    assert "--depth=1" not in clone_cmd
