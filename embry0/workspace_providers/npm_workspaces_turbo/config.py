"""Provider-specific config for npm-workspaces + Turborepo."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class NpmTurboConfig(BaseModel):
    """Config block under `workspace_provider.config` in qa.yaml."""

    model_config = ConfigDict(extra="forbid")

    affected_filter: str = "[origin/${base_branch}]"
    """Turbo filter syntax. Phase 1 ignores this field at runtime — affected()
    returns all apps. Phase 3 wires it into `turbo run --filter=...`."""

    turbo_config_path: Path = Field(default=Path("turbo.json"))
    apps_glob: str = "apps/*"
    packages_glob: str = "packages/*"
