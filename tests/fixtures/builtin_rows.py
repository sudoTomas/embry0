"""The built-in rows a deployment always has, for a test whose database holds only what the migrations wrote.

The boot seeder upserts the built-in pipeline templates before anything
reads them (``embry0.storage.seeds.pipeline_templates_builtin``); the
migrations write none. What a job's template row says (#167, its
``leader_agent`` above all) is what progression credits its work to, so a
test of that reads the rows the seeder writes.
"""

from __future__ import annotations

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository
from embry0.storage.seeds.pipeline_templates_builtin import seed_builtin_pipeline_templates


async def seed_builtin_templates(db: DatabasePool) -> None:
    """Write the built-in pipeline templates as the boot seeder does."""
    await seed_builtin_pipeline_templates(PipelineTemplatesRepository(db))
