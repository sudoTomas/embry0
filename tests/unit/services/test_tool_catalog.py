"""Agent definitions may list only the tools embry0 gates (embry0.services.tool_catalog).

The Claude CLI offers more tools than an agent may list, and a CLI release can
add one that runs commands. Every write of a definition is checked: the API
(test_agent_tools_api.py) and the repository create and reset every seeder
writes through.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.safety import policy


def _db() -> MagicMock:
    """A database that would accept the write, so only the check can stop it."""
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value={"type": "custom"})
    db.execute = AsyncMock()
    return db


def _catalog() -> Any:
    try:
        return importlib.import_module("embry0.services.tool_catalog")
    except ModuleNotFoundError:
        raise AssertionError("embry0.services.tool_catalog must hold the tools an agent can list") from None


@pytest.mark.parametrize(
    "tool",
    ["Monitor", "Workflow", "CronCreate", "EnterWorktree", "SendMessage", "mcp__elsewhere__run", "mcp__", "bash"],
)
def test_a_tool_outside_the_catalog_is_unknown(tool: str) -> None:
    assert _catalog().unknown_tools(["Read", tool]) == [tool], f"{tool!r} must not be listable"


def test_every_catalog_tool_is_known() -> None:
    catalog = _catalog()
    names = [row["name"] for row in catalog.tool_catalog()]
    assert catalog.unknown_tools(names) == []


def test_no_shell_tool_but_bash_is_in_the_catalog() -> None:
    """Bash is the one shell tool an agent may list; the others are withheld from every agent."""
    shell_tools = getattr(policy, "SHELL_TOOLS", None)
    assert shell_tools is not None, "embry0.safety.policy.SHELL_TOOLS must name the tools that run shell commands"
    names = {row["name"] for row in _catalog().tool_catalog()}
    assert names & shell_tools == {"Bash"}, f"shell tools in the catalog: {sorted(names & shell_tools)}"


def test_a_tool_of_the_definitions_own_mcp_server_is_known() -> None:
    servers = {"tracker": {"command": "npx", "args": ["tracker-mcp"]}}
    assert _catalog().unknown_tools(["mcp__tracker__search"], mcp_servers=servers) == []
    assert _catalog().unknown_tools(["mcp__other__search"], mcp_servers=servers) == ["mcp__other__search"]


def test_check_agent_tools_names_the_unknown_tools() -> None:
    with pytest.raises(ValueError, match="Monitor") as exc:
        _catalog().check_agent_tools("custom", ["Read", "Monitor"])
    assert "GET /tools" in str(exc.value)


def test_every_builtin_seed_lists_only_catalog_tools() -> None:
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    unknown = {
        agent_type: _catalog().unknown_tools(seed["tools"], mcp_servers=seed["mcp_servers"])
        for agent_type, seed in BUILTIN_SEED.items()
    }
    assert not {k: v for k, v in unknown.items() if v}, f"seeds listing tools outside the catalog: {unknown}"


@pytest.mark.asyncio
async def test_the_repository_refuses_to_create_a_definition_with_an_unknown_tool() -> None:
    """Every seeder writes through create(); the check runs before the database is touched."""
    from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

    db = _db()
    repo = AgentDefinitionsRepository(db)
    try:
        await repo.create(agent_type="custom", description="d", model="m", tools=["Read", "Monitor"])
    except ValueError as exc:
        assert "Monitor" in str(exc), str(exc)
    else:
        raise AssertionError("create() wrote a definition listing Monitor")
    assert not db.fetchrow.called, "the definition reached the database"


@pytest.mark.asyncio
async def test_a_seed_with_an_unknown_tool_fails_seeding(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.storage.repositories import agent_definitions
    from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository
    from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents

    seed = {**agent_definitions.BUILTIN_SEED["developer"], "tools": ["Read", "Bash", "Monitor"]}
    monkeypatch.setattr(agent_definitions, "BUILTIN_SEED", {"developer": seed})
    monkeypatch.setattr("embry0.storage.seeds.agents_builtin.BUILTIN_SEED", {"developer": seed})
    db = _db()
    repo = AgentDefinitionsRepository(db)

    async def missing(_agent_type: str) -> None:
        return None

    repo.get = missing  # type: ignore[method-assign]
    try:
        await seed_missing_builtin_agents(repo)
    except ValueError as exc:
        assert "Monitor" in str(exc), str(exc)
    else:
        raise AssertionError("a builtin seed listing Monitor was written")
    assert not db.fetchrow.called, "the seed reached the database"

    try:
        await repo.reset("developer")
    except ValueError as exc:
        assert "Monitor" in str(exc), str(exc)
    else:
        raise AssertionError("reset restored a seed listing Monitor")
    assert not db.fetchrow.called, "the reset reached the database"


def test_the_subagent_tool_is_agent_and_task_is_its_older_name() -> None:
    catalog = _catalog()
    names = {row["name"] for row in catalog.tool_catalog()}
    assert "Agent" in names and "Task" not in names, f"both CLIs call the subagent tool Agent: {sorted(names)}"
    assert catalog.unknown_tools(["Task"]) == [], "a stored definition that says Task still saves"
    assert catalog.canonical_tools(["Read", "Task", "Agent"]) == ["Read", "Agent"]


def _stored_repo(stored: dict[str, Any]) -> Any:
    from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

    db = _db()
    db.fetchrow = AsyncMock(return_value=stored)
    return AgentDefinitionsRepository(db), db


@pytest.mark.asyncio
async def test_update_refuses_an_unknown_tool_the_definition_already_stores() -> None:
    _catalog()
    repo, db = _stored_repo({"type": "custom", "tools": ["Read", "LegacyTool"], "mcp_servers": {}})
    try:
        await repo.update("custom", mcp_servers={"x": {"command": "x"}})
    except ValueError as exc:
        assert "LegacyTool" in str(exc), str(exc)
    else:
        raise AssertionError("update() saved a definition that lists a tool outside the catalog")
    assert db.fetchrow.await_count == 1, "only the stored row was read; nothing was written"


@pytest.mark.asyncio
async def test_update_refuses_a_new_unknown_tool() -> None:
    _catalog()
    repo, db = _stored_repo({"type": "custom", "tools": ["Read"], "mcp_servers": {}})
    try:
        await repo.update("custom", tools=["Read", "Monitor"])
    except ValueError as exc:
        assert "Monitor" in str(exc), str(exc)
    else:
        raise AssertionError("update() saved a definition listing Monitor")


@pytest.mark.asyncio
async def test_update_saves_catalog_tools_and_the_older_name() -> None:
    _catalog()
    repo, db = _stored_repo({"type": "custom", "tools": ["Read", "Task"], "mcp_servers": {}})
    await repo.update("custom", tools=["Read", "Task", "Bash"])
    assert db.fetchrow.await_count == 2, "the stored row is read, then the update written"
