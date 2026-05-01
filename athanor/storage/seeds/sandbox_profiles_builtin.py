"""Builtin sandbox profile seeding.

Run at orchestrator startup to upsert the canonical Athanor sandbox profiles.
The seeds always overwrite — users who want a customized profile should
clone the builtin under a different name.
"""

from typing import Any

import structlog

from athanor.storage.repositories.sandbox_profiles import SandboxProfilesRepository

logger = structlog.get_logger(__name__)


# Each value is the kwargs (minus `name` and `is_builtin`) passed to
# SandboxProfilesRepository.upsert. is_builtin=True is added by the seed
# function below.
BUILTIN_SANDBOX_PROFILES: dict[str, dict[str, Any]] = {
    "slim": {
        "base_image": "athanor-sandbox:latest",
        "description": "Default lightweight sandbox: Python + Node + Claude CLI.",
        "memory": "8g",
        "cpus": "4",
        "pids_limit": 256,
        "agent_timeout_seconds": 300,
        "container_timeout_seconds": 3600,
        "idle_timeout_seconds": 600,
        "dind_enabled": False,
        "extra_networks": [],
        "env_defaults": {},
    },
    "qa-jvm": {
        "base_image": "athanor-sandbox-qa:latest",
        "description": "Full-stack QA runtime: Node + Java JDK + Python + Playwright + DinD.",
        "memory": "12g",
        "cpus": "6",
        "pids_limit": 512,
        "agent_timeout_seconds": 600,
        "container_timeout_seconds": 7200,
        "idle_timeout_seconds": 600,
        "dind_enabled": True,
        # backend is allowlisted because dind_enabled=True (sandbox needs
        # to reach tcp://dind:2376 for Docker CLI calls).
        "extra_networks": ["backend"],
        "env_defaults": {"LANG": "C.UTF-8"},
    },
}


async def seed_builtin_sandbox_profiles(repo: SandboxProfilesRepository) -> None:
    """Idempotently upsert all builtin profiles. Always wins over local edits."""
    for name, fields in BUILTIN_SANDBOX_PROFILES.items():
        await repo.upsert(
            name=name,
            is_builtin=True,
            _allow_builtin_overwrite=True,  # we ARE the seed; overwrite freely
            **fields,
        )
        logger.info("sandbox_profile_seeded", name=name)
