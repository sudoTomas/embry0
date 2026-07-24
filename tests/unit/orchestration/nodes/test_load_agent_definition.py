"""load_agent_definition — DB row with code fallback (RAV-602)."""

from unittest.mock import AsyncMock, patch

import pytest

from embry0.orchestration.nodes.agent import load_agent_definition

_FALLBACK = {"model": "m", "tools": ["Read"], "skills": [], "system_prompt": "", "mcp_servers": {}}


@pytest.mark.asyncio
async def test_no_db_returns_fallback():
    assert await load_agent_definition({"configurable": {}}, "developer", _FALLBACK) is _FALLBACK
    assert await load_agent_definition(None, "developer", _FALLBACK) is _FALLBACK


@pytest.mark.asyncio
async def test_db_row_wins():
    row = {"model": "claude-opus-4-7", "tools": ["Read", "Bash"], "skills": ["x"], "system_prompt": "sp"}
    with patch("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository") as repo_cls:
        repo_cls.return_value.get = AsyncMock(return_value=row)
        out = await load_agent_definition({"configurable": {"db": object()}}, "developer", _FALLBACK)
    assert out == row


@pytest.mark.asyncio
async def test_missing_row_returns_fallback():
    with patch("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository") as repo_cls:
        repo_cls.return_value.get = AsyncMock(return_value=None)
        out = await load_agent_definition({"configurable": {"db": object()}}, "developer", _FALLBACK)
    assert out is _FALLBACK


@pytest.mark.asyncio
async def test_load_error_returns_fallback():
    with patch("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository") as repo_cls:
        repo_cls.return_value.get = AsyncMock(side_effect=RuntimeError("db down"))
        out = await load_agent_definition({"configurable": {"db": object()}}, "developer", _FALLBACK)
    assert out is _FALLBACK
