"""Pipeline templates repository — CRUD + duplicate for graph-based pipeline definitions."""

import uuid
from collections.abc import Callable
from typing import Any

import structlog

from embry0.storage.database import DatabasePool
from embry0.workflows.row_behaviour import (
    TEMPLATE_DEFAULTS,
    checked_delivers,
    checked_job_kind,
    checked_leader_agent,
)

logger = structlog.get_logger(__name__)

#: The columns that say how embry0 treats a template's jobs (#167, ``embry0.workflows.row_behaviour``): the
#: operator's, like ``default_for_kind``, so the boot seed writes them on a first insert and never after.
BEHAVIOUR_FIELDS: tuple[str, ...] = tuple(TEMPLATE_DEFAULTS)

_UPDATABLE_FIELDS = frozenset(
    {"name", "description", "graph_definition", "sandbox_profile", "default_for_kind", *BEHAVIOUR_FIELDS}
)

_CHECKS: dict[str, Callable[[Any], Any]] = {
    "delivers": checked_delivers,
    "job_kind": checked_job_kind,
    "leader_agent": checked_leader_agent,
    "fixed_route": bool,
    "offered_to_triage": bool,
}


def _behaviour(fields: dict[str, Any]) -> dict[str, Any]:
    """Each behaviour column ``fields`` names, checked: a malformed value raises ValueError naming its field."""
    return {key: _CHECKS[key](fields[key]) for key in BEHAVIOUR_FIELDS if key in fields}


def _seeded_behaviour(fields: dict[str, Any]) -> dict[str, Any]:
    """Every behaviour column, as ``fields`` set it or at its default."""
    return _behaviour({**TEMPLATE_DEFAULTS, **{k: v for k, v in fields.items() if v is not None}})


class PipelineTemplatesRepository:
    """CRUD operations for the pipeline_templates table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def list_all(self) -> list[dict[str, Any]]:
        """List all pipeline templates (summary only, no graph_definition)."""
        rows = await self._db.fetch(
            "SELECT id, name, description, sandbox_profile, is_builtin, default_for_kind, "
            "fixed_route, offered_to_triage, job_kind, delivers, leader_agent, created_at, updated_at "
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
        **behaviour: Any,
    ) -> dict[str, Any]:
        """Insert a new pipeline template and return the created record.

        ``behaviour`` holds any of :data:`BEHAVIOUR_FIELDS`; one left out is
        at its default (a template of the deployment's own, as before #167).
        A malformed one raises ValueError naming its field.
        """
        unknown = set(behaviour) - set(BEHAVIOUR_FIELDS)
        if unknown:
            raise ValueError(f"Unknown template fields: {sorted(unknown)}")
        columns = _seeded_behaviour(behaviour)
        template_id = str(uuid.uuid4())
        await self._db.execute(
            """
            INSERT INTO pipeline_templates (
                id, name, description, graph_definition, sandbox_profile, default_for_kind,
                fixed_route, offered_to_triage, job_kind, delivers, leader_agent
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            template_id,
            name,
            description,
            graph_definition,
            sandbox_profile,
            default_for_kind,
            *(columns[key] for key in BEHAVIOUR_FIELDS),
        )
        logger.info("pipeline_template_created", template_id=template_id, name=name)
        row = await self.get(template_id)
        return row  # type: ignore[return-value]

    async def update(self, template_id: str, **fields: Any) -> dict[str, Any]:
        """Update allowed fields on a pipeline template and return the updated record.

        A malformed behaviour column raises ValueError naming its field. A
        rename moves each agent's ``quest_template`` that named the old name
        to the new one, in the same transaction.
        """
        valid = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
        valid.update(_behaviour(valid))
        if valid:
            sets: list[str] = []
            args: list[Any] = [template_id]
            idx = 2
            for key, value in valid.items():
                sets.append(f"{key} = ${idx}")
                args.append(value)
                idx += 1
            sets.append("updated_at = NOW()")
            async with self._db.transaction() as conn:
                old_name = await conn.fetchval("SELECT name FROM pipeline_templates WHERE id = $1", template_id)
                await conn.execute(f"UPDATE pipeline_templates SET {', '.join(sets)} WHERE id = $1", *args)
                if "name" in valid and old_name is not None and valid["name"] != old_name:
                    # An agent's guild task names its template (#167): it follows a rename, in the same
                    # transaction, so the task keeps running what the operator chose.
                    moved = await conn.execute(
                        "UPDATE agent_definitions SET quest_template = $1, updated_at = NOW() WHERE quest_template = $2",
                        valid["name"],
                        old_name,
                    )
                    logger.info("agent_quest_templates_renamed", old=old_name, new=valid["name"], result=moved)
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
        **behaviour: Any,
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

        The behaviour columns (:data:`BEHAVIOUR_FIELDS`, #167) are the
        operator's too: a first insert takes ``behaviour`` (each one left out
        at its default), and an existing row keeps what it holds, so an
        operator's change to a built-in survives a restart.
        """
        unknown = set(behaviour) - set(BEHAVIOUR_FIELDS)
        if unknown:
            raise ValueError(f"Unknown template fields: {sorted(unknown)}")
        columns = _seeded_behaviour(behaviour)
        existing = await self._db.fetchrow(
            "SELECT id FROM pipeline_templates WHERE name = $1",
            name,
        )
        template_id = str(existing["id"]) if existing else str(uuid.uuid4())
        await self._db.execute(
            """
            INSERT INTO pipeline_templates (
                id, name, description, graph_definition,
                sandbox_profile, default_for_kind, is_builtin, updated_at,
                fixed_route, offered_to_triage, job_kind, delivers, leader_agent
            )
            VALUES (
                $1, $2, $3, $4, $5,
                CASE
                    WHEN EXISTS (SELECT 1 FROM pipeline_templates WHERE default_for_kind = $6) THEN NULL
                    ELSE $6
                END,
                TRUE, NOW(),
                $7, $8, $9, $10, $11
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
            *(columns[key] for key in BEHAVIOUR_FIELDS),
        )
        logger.info("pipeline_template_seeded", template_id=template_id, name=name)
        row = await self.get(template_id)
        return row  # type: ignore[return-value]

    async def duplicate(self, template_id: str, new_name: str) -> dict[str, Any]:
        """Create a copy of an existing template with a new name.

        The copy runs the same route and its jobs are treated the same way:
        it takes the graph, the sandbox profile and what the row says about
        its jobs (:data:`BEHAVIOUR_FIELDS`), so a copy of a template triage
        is not offered is not offered either. It is no kind's default.

        Raises ValueError if the source template does not exist.
        """
        source = await self.get(template_id)
        if source is None:
            raise ValueError(f"Pipeline template not found: {template_id}")
        behaviour: dict[str, Any] = {key: source.get(key) for key in BEHAVIOUR_FIELDS}
        return await self.create(
            name=new_name,
            graph_definition=source["graph_definition"],
            sandbox_profile=source["sandbox_profile"],
            **behaviour,
        )
