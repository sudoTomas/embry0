"""The built-in instruction-file rules the context auditor and its schedule share."""

from __future__ import annotations

from typing import Any

import pytest


def _built_in() -> Any:
    """The built-in rules, imported when a test asks, so a tree without them still collects this module."""
    from embry0.services.instruction_files import DEFAULT_RULES

    return DEFAULT_RULES


@pytest.mark.parametrize(
    "path",
    [
        "CLAUDE.md",
        "apps/hub/src/app/api/CLAUDE.md",
        "CLAUDE.local.md",
        "AGENTS.md",
        "services/api/AGENTS.md",
        "GEMINI.md",
        "HANDOFF.md",
        "llms.txt",
        ".cursorrules",
        ".github/copilot-instructions.md",
        ".github/instructions/frontend.instructions.md",
        ".github/instructions/deep/api.instructions.md",
        ".cursor/rules/style.mdc",
        ".claude/skills/verify-app/SKILL.md",
        "apps/hub/.claude/skills/deploy/SKILL.md",
        ".claude/commands/ship.md",
        ".claude/agents/reviewer.md",
        ".grok/rules/pstack.md",
        ".grok/skills/verify-leads/SKILL.md",
    ],
)
def test_files_that_steer_an_agent_are_listed(path: str) -> None:
    assert _built_in().matches(path)


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/CLAUDE.md.bak",
        "docs/MY-CLAUDE.md",
        "node_modules/pkg/CLAUDE.md",
        "frontend/node_modules/x/.claude/skills/a/SKILL.md",
        "vendor/lib/AGENTS.md",
        ".github/instructions/notes.md",
        ".claude/settings.json",
        "src/claude.ts",
    ],
)
def test_other_files_are_not(path: str) -> None:
    assert not _built_in().matches(path)


def test_instruction_files_filters_and_sorts() -> None:
    assert _built_in().select(["src/a.py", "b/CLAUDE.md", "AGENTS.md", "README.md"]) == ["AGENTS.md", "b/CLAUDE.md"]


def test_a_glob_naming_a_directory_selects_every_file_under_it_as_git_does() -> None:
    # Git selects a path for a glob pathspec when it is the pattern, lies under it, or matches it whole.
    from embry0.services import instruction_files

    rules_type = getattr(instruction_files, "FileRules", None)
    if rules_type is None:
        pytest.fail("no FileRules: the instruction files are one fixed list no directory glob can name")
    rules = rules_type((".kiro/steering", "docs/agents"), instruction_files.SKIPPED_DIRS)

    paths = [
        ".kiro/steering/a.md",
        ".kiro/steering/img.png",
        ".kiro/steering/deep/b.md",
        ".kiro/steering/node_modules/c.md",
        ".kiro/steeringx/d.md",
        "p/.kiro/steering/e.md",
        "docs/agents",
        "docs/agentsx.md",
    ]
    assert rules.select(paths) == [
        ".kiro/steering/a.md",
        ".kiro/steering/deep/b.md",
        ".kiro/steering/img.png",
        "docs/agents",
    ], "a glob with no wildcard that names a directory must select every file under it, as git's pathspec does"
