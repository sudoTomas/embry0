"""The instruction-file list the context auditor and its schedule share."""

from __future__ import annotations

import pytest

from embry0.services.instruction_files import instruction_files, is_instruction_file


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
    assert is_instruction_file(path)


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
    assert not is_instruction_file(path)


def test_instruction_files_filters_and_sorts() -> None:
    assert instruction_files(["src/a.py", "b/CLAUDE.md", "AGENTS.md", "README.md"]) == ["AGENTS.md", "b/CLAUDE.md"]
