"""Pydantic schema for `.embry0/qa.yaml` v2 — multi-app monorepo contract.

v2 replaces v1 (single-app, no workspace concept). The migrator at
embry0/cli/migrate_qa_config.py converts existing v1 files. v1 reading
remains via embry0.workflows.qa.qa_yaml.parse_qa_yaml — used ONLY by the
migrator now.
"""

from __future__ import annotations

from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# -------- Shared building blocks --------


class QAReadyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    http: str
    # ``expect_status`` may be a single int (legacy form, default 200) or a
    # list of ints. The list form is required for apps whose root path is
    # auth-gated and returns a non-200 success indicator (e.g. a Next.js
    # app that 403s on `/` until the user logs in — the dev server is
    # still "up"). To express "boot is up if I get any of these", supply
    # ``expect_status: [200, 401, 403]``.
    expect_status: int | list[int] = Field(default=200)
    expect_body_regex: str | None = None

    @field_validator("expect_status")
    @classmethod
    def _expect_status_in_range(cls, v: int | list[int]) -> int | list[int]:
        codes = v if isinstance(v, list) else [v]
        if not codes:
            raise ValueError("expect_status list must not be empty")
        for code in codes:
            if not (100 <= code <= 599):
                raise ValueError(f"expect_status entry {code} out of range 100..599")
        return v

    def status_codes(self) -> list[int]:
        """Return ``expect_status`` normalized to a list of int codes."""
        return [self.expect_status] if isinstance(self.expect_status, int) else list(self.expect_status)

    @field_validator("http")
    @classmethod
    def _http_url_only(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"http must be an http(s) URL, got {v!r}")
        return v


class QAE2E(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1)
    timeout_seconds: int = Field(default=600, gt=0, le=3600)


# -------- Top-level v2 sections --------


class WorkspaceProviderRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)


class CachePrebakedImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    rebuild_on: list[str] = Field(default_factory=lambda: ["lockfile_change"])


class CacheSharedVolume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    scope: Literal["per-job", "per-repo", "per-org"] = "per-job"


class CacheTurboRemote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


class CacheConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prebaked_image: CachePrebakedImage = Field(default_factory=CachePrebakedImage)
    shared_volume: CacheSharedVolume = Field(default_factory=CacheSharedVolume)
    turbo_remote: CacheTurboRemote = Field(default_factory=CacheTurboRemote)


class ParallelismConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_concurrent_apps: int = Field(default=4, gt=0, le=64)


class DefaultsBlock(BaseModel):
    """Defaults applied to every app unless overridden."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["dind", "process"] = "process"
    sandbox_profile: str = Field(default="slim", min_length=1)
    ready_checks: list[QAReadyCheck] = Field(default_factory=list)
    boot_timeout_seconds: int = Field(default=600, gt=0, le=3600)
    seed_command: str | None = None
    seed_timeout_seconds: int = Field(default=120, gt=0, le=1800)
    e2e: QAE2E | None = None
    acceptance_criteria_template: list[str] = Field(default_factory=list)


class AppEntry(BaseModel):
    """Per-app entry under root `apps:`. Lightweight overrides only.

    Heavy overrides go in apps/<name>/.embry0/app.yaml (parsed separately).
    """

    model_config = ConfigDict(extra="forbid")
    boot_command: str = Field(min_length=1)
    frontend_url: str = Field(min_length=1)
    sandbox_profile: str | None = None
    ready_checks: list[QAReadyCheck] | None = None
    boot_timeout_seconds: int | None = Field(default=None, gt=0, le=3600)
    seed_command: str | None = None
    e2e: QAE2E | None = None

    @field_validator("frontend_url")
    @classmethod
    def _frontend_url_http(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"frontend_url must be http(s), got {v!r}")
        return v


class PackageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    no_cascade: bool = False


class QAYamlConfigV2(BaseModel):
    """Top-level .embry0/qa.yaml v2 contract."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    workspace_provider: WorkspaceProviderRef
    defaults: DefaultsBlock = Field(default_factory=DefaultsBlock)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    qa_required: Literal["auto", "always", "never"] = "auto"
    parallelism: ParallelismConfig = Field(default_factory=ParallelismConfig)
    apps: dict[str, AppEntry] = Field(default_factory=dict)
    packages: dict[str, PackageEntry] = Field(default_factory=dict)


# -------- App-local override file --------


class AppLocalConfig(BaseModel):
    """Schema for apps/<name>/.embry0/app.yaml — heavy overrides only."""

    model_config = ConfigDict(extra="forbid")

    sandbox_profile: str | None = None
    ready_checks: list[QAReadyCheck] | None = None
    boot_timeout_seconds: int | None = Field(default=None, gt=0, le=3600)
    seed_command: str | None = None
    e2e: QAE2E | None = None
    acceptance_criteria: list[str] | None = None
    """If non-None, REPLACES (does not extend) defaults.acceptance_criteria_template."""


# -------- Parsers --------


def parse_qa_yaml_v2(raw: str) -> QAYamlConfigV2:
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("qa.yaml must be a YAML mapping at the top level")
    return QAYamlConfigV2.model_validate(data)


def parse_app_local_yaml(raw: str) -> AppLocalConfig:
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError("app.yaml must be a YAML mapping at the top level (or empty)")
    return AppLocalConfig.model_validate(data)
