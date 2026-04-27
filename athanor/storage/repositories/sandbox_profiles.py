"""Sandbox profiles repository — CRUD for tooling + security configuration."""

from typing import Any

import structlog

from athanor.storage.database import DatabasePool

logger = structlog.get_logger(__name__)


class SandboxProfilesRepository:
    """CRUD operations for the sandbox_profiles table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def upsert(
        self,
        name: str,
        base_image: str = "athanor-sandbox:latest",
        additional_packages: list[str] | None = None,
        setup_commands: list[str] | None = None,
        memory: str = "8g",
        cpus: str = "4",
        pids_limit: int = 256,
        cap_drop: list[str] | None = None,
        cap_add: list[str] | None = None,
        security_opt: list[str] | None = None,
        agent_timeout_seconds: int = 300,
        container_timeout_seconds: int = 3600,
    ) -> None:
        """Create or update a sandbox profile."""
        await self._db.execute(
            """
            INSERT INTO sandbox_profiles (
                name, base_image, additional_packages, setup_commands,
                memory, cpus, pids_limit, cap_drop, cap_add, security_opt,
                agent_timeout_seconds, container_timeout_seconds, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW())
            ON CONFLICT (name) DO UPDATE SET
                base_image = EXCLUDED.base_image,
                additional_packages = EXCLUDED.additional_packages,
                setup_commands = EXCLUDED.setup_commands,
                memory = EXCLUDED.memory,
                cpus = EXCLUDED.cpus,
                pids_limit = EXCLUDED.pids_limit,
                cap_drop = EXCLUDED.cap_drop,
                cap_add = EXCLUDED.cap_add,
                security_opt = EXCLUDED.security_opt,
                agent_timeout_seconds = EXCLUDED.agent_timeout_seconds,
                container_timeout_seconds = EXCLUDED.container_timeout_seconds,
                updated_at = NOW()
            """,
            name,
            base_image,
            additional_packages or [],
            setup_commands or [],
            memory,
            cpus,
            pids_limit,
            cap_drop or ["ALL"],
            cap_add or [],
            security_opt or ["no-new-privileges"],
            agent_timeout_seconds,
            container_timeout_seconds,
        )
        logger.info("sandbox_profile_upserted", name=name)

    async def get(self, name: str) -> dict[str, Any] | None:
        """Fetch a single sandbox profile by name."""
        row = await self._db.fetchrow("SELECT * FROM sandbox_profiles WHERE name = $1", name)
        if row is None:
            return None
        return dict(row)

    async def list(self) -> list[dict[str, Any]]:
        """List all sandbox profiles."""
        rows = await self._db.fetch("SELECT * FROM sandbox_profiles ORDER BY name")
        return [dict(r) for r in rows]

    async def delete(self, name: str) -> None:
        """Delete a sandbox profile."""
        await self._db.execute("DELETE FROM sandbox_profiles WHERE name = $1", name)
        logger.info("sandbox_profile_deleted", name=name)
