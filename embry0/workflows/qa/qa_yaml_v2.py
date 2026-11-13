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

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from embry0.services.linear_regex import UnsupportedPattern, compile_linear_search
from embry0.workflows.qa._glob_match import GlobPatternError, check_glob
from embry0.workflows.qa.browser_backends import backend_for

#: The validation context a stored document is read with (:func:`parse_qa_yaml_v2`): a glob this grammar no
#: longer writes, which the release before read, is read as it read it. Without it a glob must be one it writes.
STORED: dict[str, bool] = {"stored": True}

logger = structlog.get_logger(__name__)

#: Why a glob starting with ``!`` is refused when a setup is written. The grammar has no negation: such a glob only
#: ever matched a path that starts with ``!``. A setup stored with one keeps reading, as before, and says so.
NEGATION = "a glob cannot start with '!': negation is not supported, so list only the paths it should match"


def _check_glob(pattern: str, info: ValidationInfo) -> None:
    stored = bool((info.context or {}).get("stored"))
    if pattern.startswith("!") and not stored:
        raise ValueError(f"{NEGATION} (glob {pattern!r})")
    try:
        check_glob(pattern, stored=stored)
    except GlobPatternError as exc:
        raise ValueError(str(exc)) from exc


def _validate_browser_backend(v: str | None) -> str | None:
    """Shared validator: a browser_backend value must name a registered
    backend (browser_backends.py is the single source of truth — no
    duplicated Literal to drift). None means "inherit from the next layer".
    Raises ValueError with the known-backends list on unknown names.
    """
    if v is not None:
        backend_for(v)
    return v


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

    @field_validator("expect_body_regex")
    @classmethod
    def _body_regex_linear(cls, v: str | None, info: ValidationInfo) -> str | None:
        """A Python regular expression the boot phase can search a ready check's body with in time linear in it
        (:func:`~embry0.services.linear_regex.compile_linear_search`). A stored one is read as it is: a boot whose
        check holds one the engine refuses fails that check, saying why (``embry0.workflows.qa.boot``)."""
        if v is None or (info.context or {}).get("stored"):
            return v
        reason = body_regex_unusable(v)
        if reason is not None:
            raise ValueError(reason)
        return v


def body_regex_unusable(pattern: str) -> str | None:
    """Why ``pattern`` cannot be a ready check's ``expect_body_regex``, or None when it can."""
    try:
        compile_linear_search(pattern)
    except re.error as exc:
        return f"expect_body_regex {pattern[:80]!r} is not a Python regular expression: {exc}"
    except UnsupportedPattern as exc:
        return f"expect_body_regex {pattern[:80]!r} cannot be searched in time linear in the body: {exc}"
    return None


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
    # RAV-1061: `rebuild_on` was declared here but had zero consumers and
    # was unreachable by design (the image builder runs out-of-band and
    # never sees a parsed qa.yaml; it keys rebuilds on the lockfile sha).
    # Removed rather than wired — extra="forbid" now rejects it loudly.
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


#: Characters that make a gitignore pattern segment match more than one name.
_GLOB_CHARS = frozenset("*?[\\")


def keep_pattern_problem(pattern: str) -> str | None:
    """Why ``pattern`` may not be a ``shared_volume.keep`` entry, or None when it may.

    The first path segment (after an optional leading ``/``) must be a plain
    name: no ``*``, ``?``, ``[`` or backslash. In gitignore matching a literal
    segment matches only that exact name, so every path such a pattern keeps
    has a component of exactly that name (at any depth when the pattern has
    no other slash, at the top of the tree when it has one). No pattern can
    then keep every file, however its wildcards are written, and this holds
    without re-implementing git's matcher. A test against sample paths would
    not: a pattern can avoid the samples' names and still keep everything
    else (``[!dn.]*`` keeps ``build/``, ``out/`` and every top-level file).
    """
    if not pattern or len(pattern) > 200 or any(ord(c) < 0x20 or ord(c) == 0x7F for c in pattern):
        return "each pattern is 1-200 printable characters"
    if pattern[0] in "!#":
        return "a negated pattern or a comment keeps nothing the clean would remove"
    segments = (pattern[1:] if pattern.startswith("/") else pattern).rstrip("/").split("/")
    first = segments[0]
    if not first or any(c in _GLOB_CHARS for c in first):
        return "its first path segment must be a plain name (no *, ?, [ or backslash)"
    if any(seg in (".", "..") for seg in segments):
        return "a . or .. segment names no path"
    return None


class CacheSharedVolume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    scope: Literal["per-job", "per-repo"] = "per-job"
    # Gitignore-style patterns the warmer keeps, beyond the dependency
    # directories it always keeps (volume_warmer.DEFAULT_KEEP), when it removes
    # untracked and ignored files between rounds: output a postinstall step
    # writes elsewhere in the tree, which a round that skips the install would
    # otherwise lose. Each must start with a plain name (keep_pattern_problem).
    keep: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("keep")
    @classmethod
    def _keep_patterns(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for raw in v:
            pattern = raw.strip()
            problem = keep_pattern_problem(pattern)
            if problem:
                raise ValueError(f"shared_volume.keep: {pattern!r}: {problem}")
            out.append(pattern)
        return out

    @field_validator("scope", mode="before")
    @classmethod
    def _scope_implemented(cls, v: Any) -> Any:
        # RAV-1061: the schema used to accept per-org while the orchestrator
        # silently skipped volume warming for it — a permanently inert knob.
        # Reject with a better message than the generic Literal complaint.
        if v == "per-org":
            raise ValueError("shared_volume.scope: per-org is not implemented — use per-repo")
        return v


class CacheTurboRemote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Off by default: the Turbo remote-cache token (TURBO_TOKEN) is a single
    # deployment-wide credential injected into every QA sandbox that enables
    # this, so every such repository's code can read it and read or write the
    # shared cache. A deployment that runs the self-hosted cache turns it on
    # per repository with `cache.turbo_remote: { enabled: true }`.
    enabled: bool = False


class CacheConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prebaked_image: CachePrebakedImage = Field(default_factory=CachePrebakedImage)
    shared_volume: CacheSharedVolume = Field(default_factory=CacheSharedVolume)
    turbo_remote: CacheTurboRemote = Field(default_factory=CacheTurboRemote)
    # RAV-1400: repo-specific dependency install command, used by BOTH the
    # shared-volume warmer and the prebaked-image builder. None keeps the
    # historical default (``npm ci --prefer-offline``). Needed by repos whose
    # install requires flags the repo itself doesn't pin — e.g.
    # ``npm ci --legacy-peer-deps`` for a repo with React 18/19 peer
    # conflicts and no .npmrc.
    install_command: str | None = Field(default=None, min_length=1)


class ParallelismConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_concurrent_apps: int = Field(default=8, gt=0, le=64)


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
    guardrails: list[str] = Field(default_factory=list)
    """EMB-31: forbidden-action rules injected as a DO-NOT section of the QA
    agent prompt. Merged as a UNION with per-app/app-local guardrails —
    unlike acceptance criteria, safety rules are additive and cannot be
    removed by a narrower layer."""
    browser_backend: str | None = None
    """Browser backend for the QA agent (#229; docs/glossary.md#browser-backend).
    Validated against the backend registry. None inherits the deployment
    default (config qa_browser_backend_default, itself defaulting to
    "playwright"). Precedence: job override > app_local > app > defaults >
    deployment default."""

    _browser_backend_known = field_validator("browser_backend")(_validate_browser_backend)


class AppWhen(BaseModel):
    """Relevance predicate for a deployed app (see AppEntry.when)."""

    model_config = ConfigDict(extra="forbid")
    changed_paths: list[str] = Field(default_factory=list, min_length=1)

    @field_validator("changed_paths")
    @classmethod
    def _globs_compile(cls, v: list[str], info: ValidationInfo) -> list[str]:
        # Same contract as ConditionalWhen: bad globs are parse errors, caught
        # at config-write time — never at QA-run time, where a stored typo
        # would crash the orchestrator node on every run for that repo.
        for pattern in v:
            if not pattern.strip():
                raise ValueError("changed_paths entries must be non-empty")
            _check_glob(pattern, info)
        return v

    @model_validator(mode="after")
    def _at_least_one_predicate(self) -> AppWhen:
        # `when: {}` would parse as a silent always-run no-op (the
        # default_factory default skips min_length) — reject it, matching
        # ConditionalWhen.
        if not self.changed_paths:
            raise ValueError("app `when` must declare changed_paths")
        return self


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
    when: AppWhen | None = None
    """Relevance gate for ``target: deployed`` apps only.

    Managed apps are gated by the workspace provider's affected-set. Deployed
    apps have no workspace topology, so without this they run on EVERY QA run —
    which makes a repo that serves several apps off one deployment unable to
    declare more than one without paying for all of them each time. Declaring
    ``when.changed_paths`` narrows a deployed app to runs whose diff touches it.

    Absent (the default) preserves the previous behaviour: always run.
    """
    boot_command: str | None = Field(default=None, min_length=1)
    frontend_url: str = Field(min_length=1)
    sandbox_profile: str | None = None
    ready_checks: list[QAReadyCheck] | None = None
    boot_timeout_seconds: int | None = Field(default=None, gt=0, le=3600)
    seed_command: str | None = None
    guardrails: list[str] | None = None
    """EMB-31: EXTENDS defaults.guardrails (union), never replaces."""
    e2e: QAE2E | None = None
    auth: QAAuth | None = None
    browser_backend: str | None = None
    """Per-app browser backend override (see DefaultsBlock.browser_backend)."""

    _browser_backend_known = field_validator("browser_backend")(_validate_browser_backend)

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
            if self.when is not None:
                raise ValueError(
                    "`when` must not be set for target: managed apps — managed apps "
                    "are gated by the workspace provider's affected-set"
                )
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
    def _globs_compile(cls, v: list[str], info: ValidationInfo) -> list[str]:
        for pattern in v:
            _check_glob(pattern, info)
        return v

    @model_validator(mode="after")
    def _at_least_one_predicate(self) -> ConditionalWhen:
        if not (self.changed_paths or self.affected_apps or self.labels):
            raise ValueError(
                "conditional `when` must declare at least one predicate (changed_paths, affected_apps, or labels)"
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
        if not _CONDITIONAL_GROUP_NAME_RE.fullmatch(v):
            raise ValueError(f"conditional group name {v!r} must match [A-Za-z0-9][A-Za-z0-9._- ]{{0,99}}")
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
    app_local: dict[str, AppLocalConfig] = Field(default_factory=dict)
    """RAV-1251: per-app heavy overrides embedded in the stored document —
    the DB-native equivalent of ``apps/<name>/.embry0/app.yaml`` files
    (which are deprecated). Keys must name declared ``apps:``; entries
    outrank tree-collected app-local files at resolution."""

    @model_validator(mode="after")
    def _validate_app_local_keys(self) -> QAYamlConfigV2:
        unknown = [n for n in self.app_local if n not in self.apps]
        if unknown:
            raise ValueError(f"app_local references unknown apps: {sorted(unknown)}")
        return self

    @model_validator(mode="after")
    def _validate_conditional_groups(self) -> QAYamlConfigV2:
        seen: set[str] = set()
        for group in self.conditional_acceptance_criteria:
            if group.name in seen:
                raise ValueError(f"duplicate conditional group name {group.name!r}")
            seen.add(group.name)
            unknown_apps = [a for a in group.apps if a not in self.apps]
            if unknown_apps:
                raise ValueError(f"conditional group {group.name!r} scopes unknown apps: {sorted(unknown_apps)}")
            for app_name in group.when.affected_apps:
                entry = self.apps.get(app_name)
                if entry is None:
                    raise ValueError(f"conditional group {group.name!r} predicate references unknown app {app_name!r}")
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
    guardrails: list[str] | None = None
    """EMB-31: EXTENDS defaults + app-entry guardrails (union), never replaces."""
    browser_backend: str | None = None
    """App-local browser backend override — the strongest yaml layer (see
    DefaultsBlock.browser_backend)."""

    _browser_backend_known = field_validator("browser_backend")(_validate_browser_backend)


# QAYamlConfigV2.app_local forward-references AppLocalConfig (defined above
# only now) — resolve the deferred annotation so validation works.
QAYamlConfigV2.model_rebuild()


# -------- Parsers --------


def parse_qa_yaml_v2(raw: str, *, writing: bool = False) -> QAYamlConfigV2:
    """``raw`` as a test setup. A stored one by default; ``writing`` for a text about to be stored, which may hold
    only globs this release writes (a stored one also reads a glob the release before wrote, as it read it).

    A stored setup holding a glob that starts with ``!`` reads, and each read logs one warning naming them
    (:func:`negated_globs`); a written one is refused (:data:`NEGATION`).
    """
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("qa.yaml must be a YAML mapping at the top level")
    cfg = QAYamlConfigV2.model_validate(data, context=None if writing else STORED)
    negated = negated_globs(cfg)
    if negated:
        logger.warning("qa_config_negated_globs", globs=[g[:100] for g in negated[:10]], count=len(negated))
    return cfg


def negated_globs(cfg: BaseModel) -> list[str]:
    """Every ``changed_paths`` glob of ``cfg`` that starts with ``!``, in document order."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, AppWhen | ConditionalWhen):
            found.extend(p for p in value.changed_paths if p.startswith("!"))
        if isinstance(value, BaseModel):
            for name in type(value).model_fields:
                walk(getattr(value, name))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    walk(cfg)
    return found


def unusable_body_regexes(cfg: BaseModel) -> list[tuple[str, str]]:
    """Each ready check of ``cfg`` whose ``expect_body_regex`` a boot cannot search, as ``(http, why)``, in
    document order, once each: one a setup stored before the pattern was checked can hold
    (:func:`body_regex_unusable`)."""
    found: dict[tuple[str, str], str] = {}

    def walk(value: Any) -> None:
        if isinstance(value, QAReadyCheck) and value.expect_body_regex:
            why = body_regex_unusable(value.expect_body_regex)
            if why is not None:
                found.setdefault((value.http, value.expect_body_regex), why)
        if isinstance(value, BaseModel):
            for name in type(value).model_fields:
                walk(getattr(value, name))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    walk(cfg)
    return [(http, why) for (http, _pattern), why in found.items()]


def body_regex_warning(cfg: BaseModel) -> str | None:
    """What a person is told of a setup whose ready checks hold a body pattern a boot cannot search: it is kept, and
    every boot with it fails at once. None when it holds none."""
    unusable = unusable_body_regexes(cfg)
    if not unusable:
        return None
    listed = "; ".join(f"{http}: {why}" for http, why in unusable[:5])
    more = f" and {len(unusable) - 5} more" if len(unusable) > 5 else ""
    return (
        f"This test setup holds ready checks whose body pattern a boot cannot search ({listed}{more}). It is kept as "
        "it was, but every boot with it fails at once; a new save refuses them."
    )


def negation_warning(cfg: BaseModel) -> str | None:
    """What a person is told of a setup holding a glob that starts with ``!``: it reads, and matches only paths
    that start with ``!``. None when it holds none."""
    negated = negated_globs(cfg)
    if not negated:
        return None
    listed = ", ".join(repr(g) for g in negated[:5]) + (f" and {len(negated) - 5} more" if len(negated) > 5 else "")
    return (
        f"This test setup holds globs that start with '!' ({listed}): negation is not supported, so each matches "
        "only paths that start with '!'. It is kept as it was; a new save refuses them."
    )
