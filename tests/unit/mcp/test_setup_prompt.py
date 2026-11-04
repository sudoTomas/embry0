"""The setup procedure names only tools that exist, in the prompt, the guide and the skill alike.

A procedure that names a renamed or removed tool still reads well and sends
the session to call something that is not there. Every tool-shaped name in
the prompt and every backticked one in the guide must be a registered tool,
or one of the field and value names below.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from embry0.mcp_server import server
from embry0.mcp_server.setup_prompt import SETUP_PROMPT

ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs" / "claude-code.md"
SKILL = ROOT / ".claude" / "skills" / "setup-embry0" / "SKILL.md"

#: Names of the same shape as a tool that are fields, kinds or values, not tools.
NOT_TOOLS = {
    "api_key",
    "auto_triage",
    "credential_id",
    "credential_source",
    "dry_run",
    "forge_token",
    "llm_api_key",
    "llm_oauth",
    "no_webhook",
    "not_registered",
    "org_hook",  # in the GitHub scope admin:org_hook
    "scope_kind",
    "scope_value",
}

#: The steps setup cannot do without: the prompt must name each one.
SETUP_TOOLS = {
    "health",
    "provider_status",
    "credential_list",
    "credential_bindings_get",
    "credential_bindings_set",
    "org_probe",
    "org_register",
    "org_discover",
    "org_runs",
    "org_needs",
    "org_act",
    "org_webhook_install",
    "repo_skip_testing",
    "settings_effective",
    "credential_resolve",
}

SNAKE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")
WORD = re.compile(r"\b[a-z]+(?:_[a-z]+)*\b")
BACKTICKED = re.compile(r"`([a-z]+(?:_[a-z]+)*)`")


async def _tools() -> set[str]:
    return {tool.name for tool in await server.mcp.list_tools()}


@pytest.mark.asyncio
async def test_the_setup_prompt_is_served_whole() -> None:
    prompts = {prompt.name: prompt for prompt in await server.mcp.list_prompts()}
    assert "setup" in prompts, f"no setup prompt among {sorted(prompts)}"
    result = await server.mcp.get_prompt("setup")
    assert [message.content.text for message in result.messages] == [SETUP_PROMPT]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_the_prompt_names_only_registered_tools_and_every_setup_step() -> None:
    tools = await _tools()
    unknown = sorted(set(SNAKE.findall(SETUP_PROMPT)) - tools - NOT_TOOLS)
    assert unknown == [], f"the setup prompt names tools that do not exist: {unknown}"
    missing = sorted(SETUP_TOOLS - set(WORD.findall(SETUP_PROMPT)))
    assert missing == [], f"the setup prompt leaves out the step tools {missing}"
    assert SETUP_TOOLS <= tools, f"not registered: {sorted(SETUP_TOOLS - tools)}"


@pytest.mark.asyncio
async def test_the_guide_names_only_registered_tools() -> None:
    tools = await _tools()
    named = set(BACKTICKED.findall(GUIDE.read_text()))
    unknown = sorted({name for name in named if "_" in name} - tools - NOT_TOOLS)
    assert unknown == [], f"docs/claude-code.md names tools that do not exist: {unknown}"
    missing = sorted(SETUP_TOOLS - named)
    assert missing == [], f"the guide leaves out the step tools {missing}"


def test_secrets_stay_out_of_the_chat_in_every_text() -> None:
    assert "Never ask for a secret in this chat" in SETUP_PROMPT
    assert "No tool takes a secret" in SETUP_PROMPT
    assert "No MCP tool takes a secret" in GUIDE.read_text()
    assert "Never take the API key in this chat" in SKILL.read_text()


def test_the_skill_points_at_files_that_exist() -> None:
    text = SKILL.read_text()
    assert text.startswith("---\nname: setup-embry0\ndescription: ")
    paths = set(re.findall(r"`((?:docs|embry0|infra)/[^`\s]+|README\.md)`", text))
    assert paths, "the skill names no file"
    missing = sorted(p for p in paths if not (ROOT / p.split(" ")[0]).exists())
    assert missing == [], f"the skill points at files that do not exist: {missing}"
