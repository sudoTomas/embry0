"""Agent definitions repository — CRUD + reset for agent_definitions table."""

from typing import Any

import structlog

from athanor.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

BUILTIN_SEED: dict[str, dict[str, Any]] = {
    "triage": {
        "description": "Analyzes the issue and configures the optimal pipeline. Assesses complexity, determines confidence, and can request more information or split oversized tasks.",
        "model": "claude-sonnet-4-6",
        "tools": [],
        "skills": [],
        "system_prompt": "",
        "execution_mode": None,
        "auth_mode": None,
    },
    "developer": {
        "description": "Implements code changes, creates branches, commits, pushes, and opens PRs. Runs inside a sandbox container via Claude Code.",
        "model": "claude-opus-4-7",
        "tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        "skills": ["superpowers:subagent-driven-development", "superpowers:verification-before-completion"],
        "system_prompt": "",
        "execution_mode": None,
        "auth_mode": None,
    },
    "review": {
        "description": "Reviews code changes by running tests, linting, type checking, and code review. Returns structured JSON with decision, validation results, and documentation review.",
        "model": "claude-sonnet-4-6",
        "tools": ["Read", "Bash", "Glob", "Grep"],
        "skills": [],
        "system_prompt": "",
        "execution_mode": None,
        "auth_mode": None,
    },
}

_ALLOWED_UPDATE_FIELDS = {"description", "model", "tools", "skills", "system_prompt", "execution_mode", "auth_mode"}


class AgentDefinitionsRepository:
    """CRUD operations for the agent_definitions table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def list_all(self) -> list[dict[str, Any]]:
        """Return all agent definitions ordered by type."""
        rows = await self._db.fetch("SELECT * FROM agent_definitions ORDER BY type")
        return [dict(r) for r in rows]

    async def get(self, agent_type: str) -> dict[str, Any] | None:
        """Fetch a single agent definition by type."""
        row = await self._db.fetchrow("SELECT * FROM agent_definitions WHERE type = $1", agent_type)
        if row is None:
            return None
        return dict(row)

    async def create(
        self,
        agent_type: str,
        description: str,
        model: str,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        system_prompt: str = "",
        execution_mode: str | None = None,
        auth_mode: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new custom agent definition."""
        row = await self._db.fetchrow(
            """
            INSERT INTO agent_definitions
                (type, description, model, tools, skills, system_prompt, is_builtin, execution_mode, auth_mode)
            VALUES ($1, $2, $3, $4, $5, $6, false, $7, $8)
            RETURNING *
            """,
            agent_type,
            description,
            model,
            tools or [],
            skills or [],
            system_prompt,
            execution_mode,
            auth_mode,
        )
        logger.info("agent_definition_created", agent_type=agent_type)
        assert row is not None, "INSERT ... RETURNING must return a row"
        return dict(row)

    async def update(self, agent_type: str, **fields: Any) -> dict[str, Any]:
        """Update allowed fields on an agent definition."""
        unknown = set(fields) - _ALLOWED_UPDATE_FIELDS
        if unknown:
            raise ValueError(f"Unknown fields for update: {unknown}")

        if not fields:
            row = await self.get(agent_type)
            if row is None:
                raise ValueError(f"Agent '{agent_type}' not found")
            return row

        set_clauses = []
        params: list[Any] = []
        for i, (key, value) in enumerate(fields.items(), start=1):
            set_clauses.append(f"{key} = ${i}")
            params.append(value)

        params.append(agent_type)
        sql = (
            f"UPDATE agent_definitions SET {', '.join(set_clauses)}, updated_at = NOW() "
            f"WHERE type = ${len(params)} RETURNING *"
        )
        row = await self._db.fetchrow(sql, *params)
        if row is None:
            raise ValueError(f"Agent '{agent_type}' not found")
        logger.info("agent_definition_updated", agent_type=agent_type, fields=list(fields))
        return dict(row)

    async def delete(self, agent_type: str) -> None:
        """Delete a custom agent definition. Raises ValueError for built-in agents."""
        agent = await self.get(agent_type)
        if agent is None:
            raise ValueError(f"Agent '{agent_type}' not found")
        if agent.get("is_builtin"):
            raise ValueError(f"Cannot delete built-in agent '{agent_type}'")
        await self._db.execute("DELETE FROM agent_definitions WHERE type = $1", agent_type)
        logger.info("agent_definition_deleted", type=agent_type)

    async def reset(self, agent_type: str) -> dict[str, Any]:
        """Reset a built-in agent to its seed values. Raises ValueError for custom agents."""
        if agent_type not in BUILTIN_SEED:
            raise ValueError(f"'{agent_type}' is not a built-in agent")
        seed = BUILTIN_SEED[agent_type]
        row = await self._db.fetchrow(
            """
            UPDATE agent_definitions
            SET description = $1,
                model = $2,
                tools = $3,
                skills = $4,
                system_prompt = $5,
                execution_mode = $6,
                auth_mode = $7,
                updated_at = NOW()
            WHERE type = $8
            RETURNING *
            """,
            seed["description"],
            seed["model"],
            seed["tools"],
            seed["skills"],
            seed["system_prompt"],
            seed["execution_mode"],
            seed["auth_mode"],
            agent_type,
        )
        if row is None:
            raise ValueError(f"Agent '{agent_type}' not found in database")
        logger.info("agent_definition_reset", agent_type=agent_type)
        return dict(row)
