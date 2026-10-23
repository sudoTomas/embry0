"""Pipeline templates repository — CRUD + duplicate for graph-based pipeline definitions."""

import uuid
from typing import Any

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

_UPDATABLE_FIELDS = frozenset({"name", "description", "graph_definition", "sandbox_profile", "default_for_kind"})


class PipelineTemplatesRepository:
    """CRUD operations for the pipeline_templates table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def list_all(self) -> list[dict[str, Any]]:
        """List all pipeline templates (summary only, no graph_definition)."""
        rows = await self._db.fetch(
            "SELECT id, name, description, sandbox_profile, is_builtin, default_for_kind, created_at, updated_at "
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
        sandbox_profile: str | None = None,
        default_for_kind: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new pipeline template and return the created record."""
        template_id = str(uuid.uuid4())
        await self._db.execute(
            """
            INSERT INTO pipeline_templates (
                id, name, description, graph_definition, sandbox_profile, default_for_kind
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            template_id,
            name,
            description,
            graph_definition,
            sandbox_profile,
            default_for_kind,
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
        sandbox_profile: str | None = None,
        default_for_kind: str | None = None,
    ) -> dict[str, Any]:
        """Idempotent upsert for seed-time builtin pipeline templates.

        Match key is ``name`` (the pipeline_templates.name column is UNIQUE).
        Wins over local edits of the graph, the description and the profile —
        users who want a customized variant should clone the builtin under a
        different name. ``is_builtin`` is forced to TRUE.

        ``default_for_kind`` is the exception: it is an operator's routing
        decision, not part of the seed's content. An existing row keeps its
        stored kind, so clearing a builtin's kind or moving it to another
        template survives a restart; a first insert takes the kind only while
        no other template holds it, so the seed never trips the partial
        unique index ``idx_pipeline_templates_default_kind`` at boot.
        """
        existing = await self._db.fetchrow(
            "SELECT id FROM pipeline_templates WHERE name = $1",
            name,
        )
        template_id = str(existing["id"]) if existing else str(uuid.uuid4())
        await self._db.execute(
            """
            INSERT INTO pipeline_templates (
                id, name, description, graph_definition,
                sandbox_profile, default_for_kind, is_builtin, updated_at
            )
            VALUES (
                $1, $2, $3, $4, $5,
                CASE
                    WHEN EXISTS (SELECT 1 FROM pipeline_templates WHERE default_for_kind = $6) THEN NULL
                    ELSE $6
                END,
                TRUE, NOW()
            )
            ON CONFLICT (name) DO UPDATE SET
                description = EXCLUDED.description,
                graph_definition = EXCLUDED.graph_definition,
                sandbox_profile = EXCLUDED.sandbox_profile,
                is_builtin = TRUE,
                updated_at = NOW()
            """,
            template_id,
            name,
            description,
            graph_definition,
            sandbox_profile,
            default_for_kind,
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
            sandbox_profile=source["sandbox_profile"],
        )
