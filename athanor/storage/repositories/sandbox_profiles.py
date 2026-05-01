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
        description: str = "",
        dind_enabled: bool = False,
        idle_timeout_seconds: int = 600,
        extra_networks: list[str] | None = None,
        env_defaults: dict[str, str] | None = None,
        is_builtin: bool = False,
    ) -> None:
        """Create or update a sandbox profile.

        Builtin profiles set is_builtin=True; they're seeded by Athanor and the
        API rejects deletion + name changes for them.
        """
        await self._db.execute(
            """
            INSERT INTO sandbox_profiles (
                name, base_image, additional_packages, setup_commands,
                memory, cpus, pids_limit, cap_drop, cap_add, security_opt,
                agent_timeout_seconds, container_timeout_seconds,
                description, dind_enabled, idle_timeout_seconds,
                extra_networks, env_defaults, is_builtin, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                      $13, $14, $15, $16, $17, $18, NOW())
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
                description = EXCLUDED.description,
                dind_enabled = EXCLUDED.dind_enabled,
                idle_timeout_seconds = EXCLUDED.idle_timeout_seconds,
                extra_networks = EXCLUDED.extra_networks,
                env_defaults = EXCLUDED.env_defaults,
                is_builtin = EXCLUDED.is_builtin,
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
            description,
            dind_enabled,
            idle_timeout_seconds,
            extra_networks or [],
            env_defaults or {},
            is_builtin,
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
        """Delete a sandbox profile. Raises ValueError for builtin profiles."""
        existing = await self.get(name)
        if existing is None:
            return
        if existing.get("is_builtin"):
            raise ValueError(f"Cannot delete builtin profile '{name}'")
        await self._db.execute("DELETE FROM sandbox_profiles WHERE name = $1", name)
        logger.info("sandbox_profile_deleted", name=name)
