"""Pydantic request/response models for the API."""

import ipaddress
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from embry0.agents.identity import DISPLAY_NAME_MAX, TAGLINE_MAX, validate_avatar, validate_color
from embry0.api.schemas.repo_path import validate_repo_path
from embry0.execution.auth_provider import env_override_refusal
from embry0.execution.profile_isolation import ISOLATION_FIELDS, field_problem
from embry0.workflows._validation import FIXED_NODE_TYPES

# Slash-separated alnum + . _ - : segments. Colon allowed for ISO timestamps in
# filenames. No leading/trailing slash. No '..' segments. No double slashes.
_SAFE_QA_PATH = re.compile(r"^[A-Za-z0-9._:\-]+(/[A-Za-z0-9._:\-]+)*$")

# RFC-1123-ish hostname: dot-separated labels, alnum with inner hyphens.
_HOSTNAME_PATTERN = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*$"
)


class QAJobOverrides(BaseModel):
    """Caller-provided knobs for a ``pipeline=qa`` job creation request.

    All fields are optional. ``acceptance_criteria`` defaults to empty (the
    QA agent then exercises the app freely); ``sandbox_profile`` defaults to
    the qa.yaml-resolved value; ``qa_timeout_seconds`` overrides the default
    QA budget when present. ``base_branch`` is passed through to JobState if
    set. ``force_all_apps`` short-circuits the affected-set computation in
    qa_orchestrator_node so every declared app is QA'd.
    """

    model_config = ConfigDict(extra="forbid")

    acceptance_criteria: list[str] = Field(default_factory=list)
    sandbox_profile: str | None = None  # overrides qa.yaml's value when set
    qa_timeout_seconds: int | None = Field(default=None, gt=0, le=86400)
    # Phase-C: base_branch override. Used by init_orchestrator_node's
    # `git diff origin/<base_branch>..HEAD` for affected-set computation.
    # Defaults to "main" when None (existing behavior); set to "master" or
    # "develop" for repos with a different default branch.
    base_branch: str | None = Field(default=None, max_length=255)
    # Phase-C: bypass diff-aware affected-set selection and QA every app
    # declared under apps: in qa.yaml. Equivalent to setting
    # `qa_required: always` in qa.yaml — but per-job rather than per-repo.
    force_all_apps: bool = False
    # EMB-39: force named conditional_acceptance_criteria groups ON for this
    # run regardless of diff relevance. ["*"] forces every group. Required
    # for deployed-target/standalone runs where an empty diff means the
    # changed_paths/affected_apps predicates never match. An unknown name
    # fails the run before fan-out (never silently ignored).
    force_conditional_groups: list[str] = Field(default_factory=list)
    # Browser backend for this run (#229) — outranks every qa.yaml layer and
    # the deployment default. None = use the yaml/deployment resolution.
    # Validated against the backend registry at request time.
    browser_backend: str | None = None

    @field_validator("browser_backend")
    @classmethod
    def _browser_backend_registered(cls, v: str | None) -> str | None:
        if v is not None:
            from embry0.workflows.qa.browser_backends import backend_for

            backend_for(v)  # raises ValueError with the known-backends list
        return v


class ContextType(StrEnum):
    git = "git"
    http = "http"
    local = "local"
    none = "none"


class JobContext(BaseModel):
    """What a job operates on. `git` reproduces today's clone-a-repo behavior;
    http/local/none are non-code contexts (execution not yet implemented)."""

    model_config = ConfigDict(extra="forbid")

    type: ContextType
    repo: str | None = None  # git:   owner/name
    ref: str | None = None  # git:   branch/sha (init applies default "main")
    url: str | None = None  # http:  source URL
    path: str | None = None  # local: absolute host path

    @field_validator("repo")
    @classmethod
    def _validate_repo(cls, v: str | None) -> str | None:
        return None if v is None else validate_repo_path(v)

    @model_validator(mode="after")
    def _validate_per_type(self) -> "JobContext":
        if self.type == ContextType.git:
            if not self.repo:
                raise ValueError("context.repo is required for type=git")
            if self.url or self.path:
                raise ValueError("context.url/path not allowed for type=git")
        elif self.type == ContextType.http:
            if not self.url:
                raise ValueError("context.url is required for type=http")
            if not re.match(r"^https?://", self.url):
                raise ValueError("context.url must be an http(s) URL")
            if self.repo or self.ref or self.path:
                raise ValueError("only context.url is allowed for type=http")
        elif self.type == ContextType.local:
            if not self.path:
                raise ValueError("context.path is required for type=local")
            if not self.path.startswith("/") or ".." in self.path.split("/"):
                raise ValueError("context.path must be absolute with no '..' segments")
            if self.repo or self.ref or self.url:
                raise ValueError("only context.path is allowed for type=local")
        else:  # none
            if self.repo or self.ref or self.url or self.path:
                raise ValueError("type=none takes no fields")
        return self


# Job fields a pr-review job refuses (#30): its model, tools, profile and
# budget come from the repo's pr_review settings and the deployment's own, and
# what it reviews is the pull request ``number`` names, nothing else.
_PR_REVIEW_REFUSED_FIELDS = (
    "agent_models",
    "pipeline_config",
    "pipeline_template",
    "sandbox_profile",
    "max_budget_usd",
    "execution_mode_override",
    "auth_mode_override",
    "additional_context",
    "task",
    "branch",
    "qa",
    "context",
    "issue_number",
)


class JobCreateRequest(BaseModel):
    repo: str | None = Field(default=None, max_length=200)
    # ``task`` is required for the legacy issue-to-pr flow but optional for
    # ``pipeline='qa'`` (which has no LLM-author task to summarize). Validated
    # in the handler when pipeline != 'qa'.
    task: str | None = Field(default=None, max_length=50000)
    issue_number: int | None = None
    pipeline: Literal["issue-to-pr", "qa", "onboard", "pr-review"] | None = None
    branch: str | None = Field(default=None, max_length=255)
    qa: QAJobOverrides | None = None
    context: JobContext | None = None
    pipeline_template: str | None = None
    pipeline_config: dict[str, Any] | None = None
    sandbox_profile: str | None = None
    max_budget_usd: float | None = Field(None, gt=0, le=1000)
    additional_context: str | None = None
    agent_models: dict[str, str] | None = Field(
        default=None,
        description=(
            "Per-agent model override. Keys are agent type strings (e.g. 'developer', "
            "'review'); values are model ids. When present, overrides whatever triage "
            "decides and the deployment's default model."
        ),
    )
    execution_mode_override: str | None = None
    auth_mode_override: str | None = None
    # EMB-50, onboard pipeline only: skip the boot/ready-check smoke phase
    # (schema validation still gates the store write).
    skip_smoke: bool = False
    # #30, pr-review pipeline only: the pull request (GitHub) or merge request
    # iid (GitLab) to review now.
    number: int | None = Field(default=None, ge=1)

    @field_validator("repo")
    @classmethod
    def validate_repo_format(cls, v: str | None) -> str | None:
        return None if v is None else validate_repo_path(v)

    @model_validator(mode="after")
    def _enforce_task_for_non_qa(self) -> "JobCreateRequest":
        """``task`` is required (and non-empty) for everything except ``pipeline='qa'``.

        QA jobs are issue-less and don't need an LLM-author task description; the
        handler synthesizes a placeholder. All other pipelines (issue-to-pr, custom
        templates) still need a task to drive triage / dispatch.
        """
        if self.pipeline not in ("qa", "onboard", "pr-review"):
            if self.task is None or not self.task.strip():
                raise ValueError("task is required and must be non-empty for non-qa pipelines")
        return self

    @model_validator(mode="after")
    def _refuse_overrides_for_pr_review(self) -> "JobCreateRequest":
        """#30: a review runs on the repo's ``pr_review`` settings and nothing else,
        and ``number`` means nothing to any other pipeline.

        Refused rather than dropped, so a caller never believes an override
        took effect (RAV-1058 was overrides accepted and silently ignored).
        """
        if self.pipeline != "pr-review":
            if self.number is not None:
                raise ValueError("number is for pipeline=pr-review only")
            return self
        sent = [name for name in _PR_REVIEW_REFUSED_FIELDS if getattr(self, name) is not None]
        if self.skip_smoke:
            sent.append("skip_smoke")
        if sent:
            raise ValueError(
                f"pipeline=pr-review takes no {', '.join(sent)}: the review runs on the repo's pr_review settings"
            )
        return self

    @model_validator(mode="after")
    def _resolve_context(self) -> "JobCreateRequest":
        """Reconcile the deprecated top-level `repo` alias with `context`.

        - qa pipeline: repo required, context untouched (qa is always git).
        - repo only        -> context = {git, repo}
        - context only     -> as-is
        - both             -> must be git + matching repo, else reject
        - neither          -> context = {none}
        """
        if self.pipeline in ("qa", "onboard", "pr-review"):
            if self.repo is None:
                raise ValueError(f"repo is required for pipeline={self.pipeline}")
            if self.pipeline == "pr-review" and self.number is None:
                raise ValueError("number is required for pipeline=pr-review")
            return self
        if self.context is not None and self.repo is not None:
            if self.context.type != ContextType.git or self.context.repo != self.repo:
                raise ValueError("repo and context conflict; omit repo or send context.type=git with matching repo")
        if self.context is None:
            if self.repo is not None:
                self.context = JobContext(type=ContextType.git, repo=self.repo)
            else:
                self.context = JobContext(type=ContextType.none)
        return self


class JobResponse(BaseModel):
    job_id: str
    status: str
    # NULL for non-git jobs (http/local/none contexts, RAV-600/604) — a
    # required str made /api/v1/jobs 500 once the first repo-less job row
    # existed.
    repo: str | None = None
    task: str
    # True when a list was asked for ``brief`` tasks and this one was cut to
    # its first line; the full text is on GET /jobs/{job_id}.
    task_truncated: bool = False
    issue_number: int | None = None
    # Internal issues-table FK. NULL means the job did not originate from an
    # embry0 issue (operator/API dispatch) — the Console board's explicit
    # operator-job signal.
    issue_id: str | None = None
    # Latest workflow stage persisted by the executor at each node transition
    # (migration 35). NULL for legacy rows and jobs that never streamed one.
    current_stage: str | None = None
    pipeline_template: str | None = None
    sandbox_profile: str | None = None
    total_cost_usd: float = 0.0
    budget_overrun_usd: float = 0.0
    pr_url: str | None = None
    # #280: what became of the job's pull request — none / pr_open / merged /
    # closed — independent of `status` (a `failed` job's PR can still merge).
    # Typed str, not an enum: one row written by a newer build must not 500 a
    # list. delivery_state_at is the FORGE's time of that state; NULL when it
    # was derived from `status` and merge sync has not confirmed it yet.
    delivery_state: str = "none"
    delivery_state_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    pipeline_config: dict[str, Any] | None = None
    trace_id: str | None = None
    error_code: str | None = None
    # RAV-601: triage's kind classification + the non-code result text
    # (finalize_output's interim deliverable until RAV-603).
    job_kind: str | None = None
    result_summary: str | None = None
    cost_breakdown: list[dict[str, Any]] = Field(default_factory=list)
    # #307: when an orchestrator shutdown last stopped this job (cleared once
    # boot resumed it), and how many times boot recovered it after a crash.
    interrupted_at: datetime | None = None
    resume_attempts: int = 0


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    offset: int
    limit: int


class SandboxProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    base_image: str = "embry0-sandbox:latest"
    additional_packages: list[str] = Field(default_factory=list)
    setup_commands: list[str] = Field(default_factory=list)
    memory: str = "8g"
    cpus: str = "4"
    pids_limit: int = Field(256, ge=1, le=65535)
    cap_drop: list[str] = Field(default_factory=lambda: ["ALL"])
    cap_add: list[str] = Field(default_factory=list)
    security_opt: list[str] = Field(default_factory=lambda: ["no-new-privileges"])
    agent_timeout_seconds: int = Field(300, ge=1)
    container_timeout_seconds: int = Field(3600, ge=1)
    description: str = ""
    dind_enabled: bool = False
    idle_timeout_seconds: int = Field(default=600, gt=0)
    extra_networks: list[str] = Field(default_factory=list)
    env_defaults: dict[str, str] = Field(default_factory=dict)
    # hostname -> IP map emitted as --add-host flags at sandbox create
    # (EMB-28: lets browser-capable sandboxes reach an externally deployed
    # app by its real vhost name). The 'dind' alias is orchestrator-owned.
    extra_hosts: dict[str, str] = Field(default_factory=dict)

    @field_validator("extra_hosts")
    @classmethod
    def _validate_extra_hosts(cls, v: dict[str, str]) -> dict[str, str]:
        for host, ip in v.items():
            if not _HOSTNAME_PATTERN.match(host):
                raise ValueError(f"extra_hosts: invalid hostname {host!r}")
            if host == "dind":
                raise ValueError("extra_hosts: 'dind' is reserved for the orchestrator")
            try:
                ipaddress.ip_address(ip)
            except ValueError as exc:
                raise ValueError(f"extra_hosts: value for {host!r} must be an IP literal, got {ip!r}") from exc
        return v

    @field_validator("env_defaults")
    @classmethod
    def _env_defaults_not_reserved(cls, v: dict[str, str]) -> dict[str, str]:
        # #544: the env-var API's check. SandboxManager.create drops a refused
        # key from stored rows too, but silently — refuse it here with a 422.
        for key, value in v.items():
            refusal = env_override_refusal(key, value)
            if refusal is not None:
                raise ValueError(f"env_defaults: {refusal}")
        return v

    # #553: the isolation fields take only the safe defaults. SandboxManager.create
    # checks again, so a row written before this or around the API fails closed.
    @field_validator(*ISOLATION_FIELDS)
    @classmethod
    def _validate_isolation_field(cls, v: Any, info: ValidationInfo) -> Any:
        problem = field_problem(str(info.field_name), v)
        if problem is not None:
            raise ValueError(problem)
        return v


class ContextConfigRequest(BaseModel):
    system_context: str = ""
    assistant_context: str = ""


class BudgetConfigRequest(BaseModel):
    max_budget_per_job_usd: float | None = Field(None, gt=0)
    daily_cap_usd: float | None = Field(None, gt=0)
    monthly_cap_usd: float | None = Field(None, gt=0)
    rate_limit_per_author_per_hour: int | None = Field(None, ge=1)
    overrun_mode: str | None = Field(None, pattern="^(soft|hard)$")


class BudgetConfigResponse(BaseModel):
    max_budget_per_job_usd: float
    daily_cap_usd: float
    monthly_cap_usd: float
    rate_limit_per_author_per_hour: int
    overrun_mode: str


# --- Agent Definitions ---

_AGENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


class AgentCreateRequest(BaseModel):
    type: str = Field(..., min_length=1, max_length=50)
    description: str
    model: str
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    # RAV-604: expose the full agent_definitions surface so non-code agents
    # are creatable via the API. None = defer to the resolver's global
    # defaults; values mirror embry0/agents/resolver.py's vocabularies.
    execution_mode: str | None = Field(default=None, pattern="^(sdk|cli)$")
    auth_mode: str | None = Field(default=None, pattern="^(api_key|oauth)$")
    mcp_servers: dict[str, Any] = Field(default_factory=dict)
    display_name: str = Field(default="", max_length=DISPLAY_NAME_MAX)
    avatar: str = "golem"
    color: str = "slate"
    tagline: str = Field(default="", max_length=TAGLINE_MAX)

    @field_validator("type")
    @classmethod
    def validate_agent_type(cls, v: str) -> str:
        if not _AGENT_TYPE_PATTERN.match(v):
            msg = "type must be lowercase alphanumeric with hyphens/underscores"
            raise ValueError(msg)
        # A row named after a fixed node is placeable in a template but runs
        # that node, never generic_agent; the scaffolding names are no agent.
        reserved = FIXED_NODE_TYPES | {"__start__", "__end__"}
        if v in reserved:
            msg = f"type {v!r} is reserved for a fixed pipeline node; reserved: {sorted(reserved)}"
            raise ValueError(msg)
        return v

    @field_validator("avatar")
    @classmethod
    def _avatar(cls, v: str) -> str:
        return validate_avatar(v)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        return validate_color(v)


class AgentUpdateRequest(BaseModel):
    description: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    system_prompt: str | None = None
    execution_mode: str | None = Field(default=None, pattern="^(sdk|cli)$")
    auth_mode: str | None = Field(default=None, pattern="^(api_key|oauth)$")
    mcp_servers: dict[str, Any] | None = None
    display_name: str | None = Field(default=None, max_length=DISPLAY_NAME_MAX)
    avatar: str | None = None
    color: str | None = None
    tagline: str | None = Field(default=None, max_length=TAGLINE_MAX)

    @field_validator("avatar")
    @classmethod
    def _avatar(cls, v: str | None) -> str | None:
        return None if v is None else validate_avatar(v)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        return None if v is None else validate_color(v)


# --- Skills ---


class SkillSourceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=40)
    # Only the builtin `local` source is inline; operators write its skills through /skills/inline.
    kind: str = Field(pattern="^(git|path)$")
    url: str = Field(default="", max_length=2000)
    ref: str = Field(default="", max_length=255)
    enabled: bool = True
    credential_id: str | None = Field(default=None, description="reserved; any value is refused in this release")

    @field_validator("credential_id")
    @classmethod
    def _reserved(cls, v: str | None) -> str | None:
        if v is not None:
            raise ValueError("credential_id is reserved: skill sources are public in this release")
        return v


class SkillSourcePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None


class InlineSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=500)
    body: str = Field(max_length=64 * 1024)

    @field_validator("description", "body")
    @classmethod
    def _no_nul(cls, v: str, info: ValidationInfo) -> str:
        # Postgres text cannot hold a NUL byte; refusing it here keeps it a 422, not a 500.
        if "\x00" in v:
            raise ValueError(f"{info.field_name} must not contain a NUL byte")
        return v


# --- Pipeline Templates ---


class _TemplateWriteRequest(BaseModel):
    """#468: a template carries its models on its steps, not in a side map.

    ``agent_models`` used to be accepted and stored, and nothing read it. A
    request that still sends it is refused rather than silently ignored.
    """

    @model_validator(mode="before")
    @classmethod
    def _refuse_agent_models(cls, data: Any) -> Any:
        if isinstance(data, dict) and "agent_models" in data:
            raise ValueError(
                "agent_models is not a template field: set the model on the step's config.model "
                "in graph_definition, or per job with POST /jobs agent_models"
            )
        return data


class TemplateCreateRequest(_TemplateWriteRequest):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    graph_definition: dict[str, Any]
    sandbox_profile: str | None = None
    # RAV-601: at most one template per kind is that kind's default route
    # (DB partial unique index enforces the at-most-one).
    default_for_kind: str | None = Field(default=None, pattern="^(code|research|analysis|ops)$")


class TemplateUpdateRequest(_TemplateWriteRequest):
    name: str | None = None
    description: str | None = None
    graph_definition: dict[str, Any] | None = None
    sandbox_profile: str | None = None
    default_for_kind: str | None = Field(
        default=None,
        pattern="^(code|research|analysis|ops)$",
        description="the kind this template is the default route for; an explicit null clears it, an absent field keeps it",
    )


class TemplateDuplicateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


# --- Integration Config ---


class IntegrationConfigUpdate(BaseModel):
    trigger_labels: list[str] | None = None
    webhook_secret: str | None = None
    slack_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_notify_url: str | None = None
    webhook_notify_secret: str | None = None
    # #157: Linear was env-only and unreachable from Settings.
    linear_api_key: str | None = None
    linear_webhook_secret: str | None = None
    linear_repo_map: str | None = None


# --- Provider Config ---


class ProviderConfigUpdate(BaseModel):
    # RAV-1239: provider_mode/ollama_base_url removed — no execution path
    # ever read them (the DB columns remain, ignored, for ledger reasons).
    model_heavy: str | None = None
    model_medium: str | None = None
    model_light: str | None = None
    # #465: the model every agent runs on unless a pipeline template or the
    # job itself names another; "" clears it. The API layer refuses an id
    # outside the catalog, and one that is disabled.
    default_model: str | None = None
    # RAV-1297: catalog model ids disabled for agent selection. The API
    # layer enforces the interlock: only known catalog models, and never a
    # model currently assigned to a tier.
    disabled_models: list[str] | None = None


# --- QA Presign ---


class QAPresignBatchRequest(BaseModel):
    """Request a batch of presigned URLs for a QA attempt's artifacts.

    The orchestrator validates the sandbox token and returns URLs scoped
    to <job_id>/<attempt_n>/. The sandbox cannot mint URLs outside its
    own prefix.
    """

    model_config = ConfigDict(extra="forbid")

    sandbox_token: str = Field(min_length=16, max_length=128)
    # Each entry is a relative path under the attempt prefix
    # (e.g., "result.json", "screenshots/login.png").
    paths: list[str] = Field(min_length=1, max_length=64)
    expires_seconds: int = Field(default=3600, ge=60, le=21600)
    direction: Literal["put", "get"] = "put"

    @field_validator("paths")
    @classmethod
    def _paths_are_safe(cls, paths: list[str]) -> list[str]:
        for p in paths:
            if not _SAFE_QA_PATH.match(p):
                raise ValueError(
                    f"Unsafe path {p!r}: must be slash-separated alnum/._:- "
                    f"segments, no leading/trailing slash, no '..'."
                )
            if ".." in p.split("/"):
                raise ValueError(f"Unsafe path {p!r}: '..' segment not allowed")
        return paths


class QAPresignedURL(BaseModel):
    path: str
    url: str


class QAPresignBatchResponse(BaseModel):
    bucket: str
    prefix: str  # "<job_id>/<attempt_n>/"
    expires_at: str  # ISO 8601
    urls: list[QAPresignedURL]
