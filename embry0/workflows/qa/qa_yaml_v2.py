"""Pydantic schema for `.embry0/qa.yaml` v2 — multi-app monorepo contract.

v2 replaces v1 (single-app, no workspace concept). The migrator at
embry0/cli/migrate_qa_config.py converts existing v1 files. v1 reading
remains via embry0.workflows.qa.qa_yaml.parse_qa_yaml — used ONLY by the
migrator now.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from embry0.workflows.qa._glob_match import GlobPatternError, compile_glob

# Hosts that resolve to the SANDBOX itself, not an external deployment.
# A deployed-target frontend_url pointing here is always a config error —
# probes and the browser run inside the sandbox, whose loopback has nothing
# listening (the deployed app lives outside).
_SANDBOX_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})


def _is_sandbox_loopback(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host.lower() in _SANDBOX_LOOPBACK_HOSTS


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


_ENV_VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class QAAuthStorageState(BaseModel):
    """Source of the Playwright storageState (cookies + localStorage) JSON
    that pre-authenticates the QA browser context (EMB-40).

    Exactly one of:
      - ``command``: a repo-provided script run inside the sandbox (after
        boot/seed, before e2e/exploratory) that performs a login and writes
        the storageState JSON to ``$QA_STORAGE_STATE_PATH``.
      - ``secret``: the name of a qa-scoped env var (``QA_``-prefixed, set
        via /environments with scope=qa) whose VALUE is the storageState
        JSON — the "mint once via scripted login, reuse until expiry" route.
    """

    model_config = ConfigDict(extra="forbid")
    command: str | None = Field(default=None, min_length=1)
    secret: str | None = Field(default=None, min_length=1)
    timeout_seconds: int = Field(default=300, gt=0, le=1800)

    @field_validator("secret")
    @classmethod
    def _secret_is_qa_scoped_env_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _ENV_VAR_NAME_RE.fullmatch(v):
            raise ValueError(f"secret {v!r} is not a valid env var name (uppercase A-Z, 0-9, _)")
        if not v.startswith("QA_"):
            raise ValueError(
                f"secret {v!r} must start with QA_ — storageState secrets are qa-scoped env vars, "
                "and qa scope requires the QA_ key prefix"
            )
        from embry0.execution.auth_provider import RESERVED_ENV_KEYS, RESERVED_ENV_PREFIXES

        if v in RESERVED_ENV_KEYS or any(v.startswith(p) for p in RESERVED_ENV_PREFIXES):
            raise ValueError(f"secret {v!r} is a reserved infrastructure env var and can never hold a user value")
        return v

    @model_validator(mode="after")
    def _exactly_one_source(self) -> QAAuthStorageState:
        if (self.command is None) == (self.secret is None):
            raise ValueError("storage_state_from requires exactly one of `command` or `secret`")
        return self


class QAAuth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storage_state_from: QAAuthStorageState


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
    auth: QAAuth | None = None
    acceptance_criteria_template: list[str] = Field(default_factory=list)


class AppEntry(BaseModel):
    """Per-app entry under root `apps:`. Lightweight overrides only.

    Heavy overrides go in apps/<name>/.embry0/app.yaml (parsed separately).

    ``target`` selects the lifecycle (EMB-27):
      - ``managed`` (default): the pipeline boots the app in-sandbox —
        ``boot_command`` is required, exactly the pre-existing behavior.
      - ``deployed``: the app is ALREADY RUNNING outside the sandbox
        (e.g. a live compose stack on the host). No boot, no seed —
        ``ready_checks`` become a liveness gate on the external URL.
    """

    model_config = ConfigDict(extra="forbid")
    target: Literal["managed", "deployed"] = "managed"
    boot_command: str | None = Field(default=None, min_length=1)
    frontend_url: str = Field(min_length=1)
    sandbox_profile: str | None = None
    ready_checks: list[QAReadyCheck] | None = None
    boot_timeout_seconds: int | None = Field(default=None, gt=0, le=3600)
    seed_command: str | None = None
    e2e: QAE2E | None = None
    auth: QAAuth | None = None

    @field_validator("frontend_url")
    @classmethod
    def _frontend_url_http(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"frontend_url must be http(s), got {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_target_shape(self) -> AppEntry:
        if self.target == "managed":
            if not self.boot_command:
                raise ValueError("boot_command is required for target: managed apps")
            return self
        # target == "deployed"
        if self.boot_command is not None:
            raise ValueError("boot_command must not be set for target: deployed apps — the app is already running")
        if self.seed_command is not None:
            raise ValueError(
                "seed_command must not be set for target: deployed apps — never seed an externally running instance"
            )
        if _is_sandbox_loopback(self.frontend_url):
            raise ValueError(
                f"frontend_url {self.frontend_url!r} points at the sandbox's own loopback; "
                "a deployed target must be an external URL (LAN IP or an extra_hosts alias)"
            )
        for rc in self.ready_checks or []:
            if _is_sandbox_loopback(rc.http):
                raise ValueError(
                    f"ready_check {rc.http!r} points at the sandbox's own loopback; "
                    "deployed-target checks must probe the external URL"
                )
        return self


class PackageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    no_cascade: bool = False


# -------- Conditional acceptance criteria (EMB-39) --------

# Group names are force-knob handles (QAJobOverrides.force_conditional_groups)
# and observability keys — keep them identifier-ish. "*" is deliberately
# unrepresentable: it is reserved as the force-everything wildcard.
_CONDITIONAL_GROUP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\- ]{0,99}$")


class ConditionalWhen(BaseModel):
    """Relevance predicate for one conditional criteria group.

    Within one list: OR. Between non-empty fields: AND. A group with an
    empty diff (deployed/standalone runs) can therefore never match via
    ``changed_paths``/``affected_apps`` — only ``labels`` or the job-level
    force knob fire it there.
    """

    model_config = ConfigDict(extra="forbid")
    changed_paths: list[str] = Field(default_factory=list)
    """Glob patterns (see _glob_match grammar) vs repo-relative changed files."""
    affected_apps: list[str] = Field(default_factory=list)
    """qa.yaml app names vs the diff-derived MANAGED affected set."""
    labels: list[str] = Field(default_factory=list)
    """Issue labels (case-insensitive exact match). Empty on standalone runs."""

    @field_validator("changed_paths")
    @classmethod
    def _globs_compile(cls, v: list[str]) -> list[str]:
        for pattern in v:
            try:
                compile_glob(pattern)
            except GlobPatternError as exc:
                raise ValueError(str(exc)) from exc
        return v

    @model_validator(mode="after")
    def _at_least_one_predicate(self) -> ConditionalWhen:
        if not (self.changed_paths or self.affected_apps or self.labels):
            raise ValueError(
                "conditional `when` must declare at least one predicate "
                "(changed_paths, affected_apps, or labels)"
            )
        return self


class ConditionalCriteriaGroup(BaseModel):
    """A named criteria group appended to the resolved set when `when` matches."""

    model_config = ConfigDict(extra="forbid")
    name: str
    when: ConditionalWhen
    criteria: list[str] = Field(min_length=1)
    apps: list[str] = Field(default_factory=list)
    """Apps to append to when fired; empty = every app in the run."""

    @field_validator("name")
    @classmethod
    def _name_shape(cls, v: str) -> str:
        if not _CONDITIONAL_GROUP_NAME_RE.match(v):
            raise ValueError(
                f"conditional group name {v!r} must match "
                "[A-Za-z0-9][A-Za-z0-9._- ]{0,99}"
            )
        return v

    @field_validator("criteria")
    @classmethod
    def _criteria_non_empty(cls, v: list[str]) -> list[str]:
        if any(not c.strip() for c in v):
            raise ValueError("conditional criteria entries must be non-empty strings")
        return v


class QAYamlConfigV2(BaseModel):
    """Top-level .embry0/qa.yaml v2 contract.

    ``workspace_provider`` may be omitted ONLY when every declared app is
    ``target: deployed`` (EMB-27) — there is no workspace topology to map
    when nothing boots in-sandbox. Any managed app requires a provider.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    workspace_provider: WorkspaceProviderRef | None = None
    defaults: DefaultsBlock = Field(default_factory=DefaultsBlock)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    qa_required: Literal["auto", "always", "never"] = "auto"
    parallelism: ParallelismConfig = Field(default_factory=ParallelismConfig)
    apps: dict[str, AppEntry] = Field(default_factory=dict)
    packages: dict[str, PackageEntry] = Field(default_factory=dict)
    conditional_acceptance_criteria: list[ConditionalCriteriaGroup] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_conditional_groups(self) -> QAYamlConfigV2:
        seen: set[str] = set()
        for group in self.conditional_acceptance_criteria:
            if group.name in seen:
                raise ValueError(f"duplicate conditional group name {group.name!r}")
            seen.add(group.name)
            unknown_apps = [a for a in group.apps if a not in self.apps]
            if unknown_apps:
                raise ValueError(
                    f"conditional group {group.name!r} scopes unknown apps: {sorted(unknown_apps)}"
                )
            for app_name in group.when.affected_apps:
                entry = self.apps.get(app_name)
                if entry is None:
                    raise ValueError(
                        f"conditional group {group.name!r} predicate references "
                        f"unknown app {app_name!r}"
                    )
                if entry.target != "managed":
                    # A deployed app never enters the diff-derived affected set,
                    # so this predicate could never fire — config error, not a
                    # silently dead gate.
                    raise ValueError(
                        f"conditional group {group.name!r} predicate references "
                        f"deployed app {app_name!r} — affected_apps only matches "
                        "managed apps (deployed apps are never diff-affected)"
                    )
        return self

    @model_validator(mode="after")
    def _provider_required_unless_all_deployed(self) -> QAYamlConfigV2:
        if self.workspace_provider is None:
            if not self.apps:
                raise ValueError("workspace_provider is required when no apps are declared")
            managed = [n for n, a in self.apps.items() if a.target == "managed"]
            if managed:
                raise ValueError(
                    "workspace_provider is required — these apps are target: managed "
                    f"and need workspace topology: {sorted(managed)}"
                )
        return self

    def deployed_app_names(self) -> list[str]:
        """Names of apps with ``target: deployed``, sorted."""
        return sorted(n for n, a in self.apps.items() if a.target == "deployed")

    def managed_app_names(self) -> list[str]:
        """Names of apps with ``target: managed``, sorted."""
        return sorted(n for n, a in self.apps.items() if a.target == "managed")


# -------- App-local override file --------


class AppLocalConfig(BaseModel):
    """Schema for apps/<name>/.embry0/app.yaml — heavy overrides only."""

    model_config = ConfigDict(extra="forbid")

    sandbox_profile: str | None = None
    ready_checks: list[QAReadyCheck] | None = None
    boot_timeout_seconds: int | None = Field(default=None, gt=0, le=3600)
    seed_command: str | None = None
    e2e: QAE2E | None = None
    auth: QAAuth | None = None
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
