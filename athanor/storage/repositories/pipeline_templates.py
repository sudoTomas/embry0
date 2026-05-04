"""Pipeline templates repository — CRUD + duplicate for graph-based pipeline definitions."""

import uuid
from typing import Any

import structlog

from athanor.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

_UPDATABLE_FIELDS = frozenset({"name", "description", "graph_definition", "agent_models", "sandbox_profile"})


class PipelineTemplatesRepository:
    """CRUD operations for the pipeline_templates table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def list_all(self) -> list[dict[str, Any]]:
        """List all pipeline templates (summary only, no graph_definition)."""
        rows = await self._db.fetch(
            "SELECT id, name, description, sandbox_profile, is_builtin, created_at, updated_at "
            "FROM pipeline_templates ORDER BY is_builtin DESC, name"
        )
        return [dict(r) for r in rows]

    async def get(self, template_id: str) -> dict[str, Any] | None:
        """Fetch a single pipeline template by id."""
        row = await self._db.fetchrow("SELECT * FROM pipeline_templates WHERE id = $1", template_id)
        if row is None:
            return None
        return dict(row)

    async def create(
        self,
        name: str,
        graph_definition: dict[str, Any],
        description: str = "",
        agent_models: dict[str, Any] | None = None,
        sandbox_profile: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new pipeline template and return the created record."""
        template_id = str(uuid.uuid4())
        await self._db.execute(
            """
            INSERT INTO pipeline_templates (id, name, description, graph_definition, agent_models, sandbox_profile)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            template_id,
            name,
            description,
            graph_definition,
            agent_models or {},
            sandbox_profile,
        )
        logger.info("pipeline_template_created", template_id=template_id, name=name)
        row = await self.get(template_id)
        return row  # type: ignore[return-value]

    async def update(self, template_id: str, **fields: Any) -> dict[str, Any]:
        """Update allowed fields on a pipeline template and return the updated record."""
        valid = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
        if valid:
            sets: list[str] = []
            args: list[Any] = [template_id]
            idx = 2
            for key, value in valid.items():
                sets.append(f"{key} = ${idx}")
                args.append(value)
                idx += 1
            sets.append("updated_at = NOW()")
            await self._db.execute(
                f"UPDATE pipeline_templates SET {', '.join(sets)} WHERE id = $1",
                *args,
            )
            logger.info("pipeline_template_updated", template_id=template_id, fields=list(valid.keys()))
        row = await self.get(template_id)
        return row  # type: ignore[return-value]

    async def delete(self, template_id: str) -> None:
        """Delete a pipeline template by id."""
        await self._db.execute("DELETE FROM pipeline_templates WHERE id = $1", template_id)
        logger.info("pipeline_template_deleted", template_id=template_id)

    async def upsert_builtin(
        self,
        name: str,
        graph_definition: dict[str, Any],
        description: str = "",
        agent_models: dict[str, Any] | None = None,
        sandbox_profile: str | None = None,
    ) -> dict[str, Any]:
        """Idempotent upsert for seed-time builtin pipeline templates.

        Match key is ``name`` (the pipeline_templates.name column is UNIQUE).
        Always wins over local edits — users who want a customized variant
        should clone the builtin under a different name. ``is_builtin`` is
        forced to TRUE.
        """
        existing = await self._db.fetchrow(
            "SELECT id FROM pipeline_templates WHERE name = $1",
            name,
        )
        template_id = str(existing["id"]) if existing else str(uuid.uuid4())
        await self._db.execute(
            """
            INSERT INTO pipeline_templates (
                id, name, description, graph_definition, agent_models,
                sandbox_profile, is_builtin, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, TRUE, NOW())
            ON CONFLICT (name) DO UPDATE SET
                description = EXCLUDED.description,
                graph_definition = EXCLUDED.graph_definition,
                agent_models = EXCLUDED.agent_models,
                sandbox_profile = EXCLUDED.sandbox_profile,
                is_builtin = TRUE,
                updated_at = NOW()
            """,
            template_id,
            name,
            description,
            graph_definition,
            agent_models or {},
            sandbox_profile,
        )
        logger.info("pipeline_template_seeded", template_id=template_id, name=name)
        row = await self.get(template_id)
        return row  # type: ignore[return-value]

    async def duplicate(self, template_id: str, new_name: str) -> dict[str, Any]:
        """Create a copy of an existing template with a new name.

        Raises ValueError if the source template does not exist.
        """
        source = await self.get(template_id)
        if source is None:
            raise ValueError(f"Pipeline template not found: {template_id}")
        return await self.create(
            name=new_name,
            graph_definition=source["graph_definition"],
            agent_models=source["agent_models"],
            sandbox_profile=source["sandbox_profile"],
        )
