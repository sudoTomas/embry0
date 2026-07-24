"""Builtin agent-definition seeding (RAV-604).

Migration 3 inserted the original builtin agent rows, so agents added to
``BUILTIN_SEED`` after a deployment's first migration run never reach its
database. This seeder runs at orchestrator startup and inserts any
``BUILTIN_SEED`` agent that is missing — insert-if-missing only, never
overwriting: builtin rows are operator-editable, and the explicit
``POST /agents/{type}/reset`` endpoint is the restore path. (The qa and
onboarding agents are force-synced separately by their own seeders.)
"""

from typing import Any

import structlog

from embry0.storage.repositories.agent_definitions import (
    BUILTIN_SEED,
    AgentDefinitionsRepository,
)

logger = structlog.get_logger(__name__)


async def seed_missing_builtin_agents(repo: AgentDefinitionsRepository) -> None:
    """Insert any BUILTIN_SEED agent absent from agent_definitions."""
    for agent_type, seed in BUILTIN_SEED.items():
        existing = await repo.get(agent_type)
        if existing is not None:
            continue
        seed_fields: dict[str, Any] = seed
        await repo.create(
            agent_type=agent_type,
            description=seed_fields["description"],
            model=seed_fields["model"],
            tools=seed_fields["tools"],
            skills=seed_fields["skills"],
            system_prompt=seed_fields["system_prompt"],
            execution_mode=seed_fields["execution_mode"],
            auth_mode=seed_fields["auth_mode"],
            mcp_servers=seed_fields["mcp_servers"],
        )
        # create() marks rows non-builtin (it serves the custom-agent API);
        # only seeders may set is_builtin, so flip it via direct SQL — the
        # same convention seed_qa_agent/seed_onboarding_agent use.
        await repo._db.execute(
            "UPDATE agent_definitions SET is_builtin = true WHERE type = $1",
            agent_type,
        )
        logger.info("builtin_agent_seeded", agent_type=agent_type)
