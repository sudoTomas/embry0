"""Pydantic request/response models for the API."""

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REPO_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")

# Slash-separated alnum + . _ - : segments. Colon allowed for ISO timestamps in
# filenames. No leading/trailing slash. No '..' segments. No double slashes.
_SAFE_QA_PATH = re.compile(r"^[A-Za-z0-9._:\-]+(/[A-Za-z0-9._:\-]+)*$")


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


class ContextType(str, Enum):
    git = "git"
    http = "http"
    local = "local"
    none = "none"


class JobContext(BaseModel):
    """What a job operates on. `git` reproduces today's clone-a-repo behavior;
    http/local/none are non-code contexts (execution lands in INT-600/601)."""

    model_config = ConfigDict(extra="forbid")

    type: ContextType
    repo: str | None = None    # git:   owner/name
    ref: str | None = None     # git:   branch/sha (init applies default "main")
    url: str | None = None     # http:  source URL
    path: str | None = None    # local: absolute host path

    @field_validator("repo")
    @classmethod
    def _validate_repo(cls, v: str | None) -> str | None:
        if v is not None and not _REPO_PATTERN.match(v):
            raise ValueError("repo must be in 'owner/name' format")
        return v

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


class JobCreateRequest(BaseModel):
    repo: str | None = Field(default=None, max_length=200)
    # ``task`` is required for the legacy issue-to-pr flow but optional for
    # ``pipeline='qa'`` (which has no LLM-author task to summarize). Validated
    # in the handler when pipeline != 'qa'.
    task: str | None = Field(default=None, max_length=50000)
    issue_number: int | None = None
    pipeline: Literal["issue-to-pr", "qa"] | None = None
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
            "decides."
        ),
    )
    execution_mode_override: str | None = None
    auth_mode_override: str | None = None

    @field_validator("repo")
    @classmethod
    def validate_repo_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _REPO_PATTERN.match(v):
            msg = "repo must be in 'owner/name' format"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _enforce_task_for_non_qa(self) -> "JobCreateRequest":
        """``task`` is required (and non-empty) for everything except ``pipeline='qa'``.

        QA jobs are issue-less and don't need an LLM-author task description; the
        handler synthesizes a placeholder. All other pipelines (issue-to-pr, custom
        templates) still need a task to drive triage / dispatch.
        """
        if self.pipeline != "qa":
            if self.task is None or not self.task.strip():
                raise ValueError("task is required and must be non-empty for non-qa pipelines")
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
        if self.pipeline == "qa":
            if self.repo is None:
                raise ValueError("repo is required for pipeline=qa")
            return self
        if self.context is not None and self.repo is not None:
            if self.context.type != ContextType.git or self.context.repo != self.repo:
                raise ValueError(
                    "repo and context conflict; omit repo or send context.type=git with matching repo"
                )
        if self.context is None:
            if self.repo is not None:
                self.context = JobContext(type=ContextType.git, repo=self.repo)
            else:
                self.context = JobContext(type=ContextType.none)
        return self


class JobResponse(BaseModel):
    job_id: str
    status: str
    repo: str
    task: str
    issue_number: int | None = None
    pipeline_template: str | None = None
    sandbox_profile: str | None = None
    total_cost_usd: float = 0.0
    budget_overrun_usd: float = 0.0
    pr_url: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    pipeline_config: dict[str, Any] | None = None
    trace_id: str | None = None
    error_code: str | None = None
    cost_breakdown: list[dict[str, Any]] = Field(default_factory=list)


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    offset: int
    limit: int


class SandboxProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    base_image: str = "athanor-sandbox:latest"
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

    @field_validator("type")
    @classmethod
    def validate_agent_type(cls, v: str) -> str:
        if not _AGENT_TYPE_PATTERN.match(v):
            msg = "type must be lowercase alphanumeric with hyphens/underscores"
            raise ValueError(msg)
        return v


class AgentUpdateRequest(BaseModel):
    description: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    system_prompt: str | None = None


# --- Pipeline Templates ---


class TemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    graph_definition: dict[str, Any]
    agent_models: dict[str, str] = Field(default_factory=dict)
    sandbox_profile: str | None = None


class TemplateUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    graph_definition: dict[str, Any] | None = None
    agent_models: dict[str, str] | None = None
    sandbox_profile: str | None = None


class TemplateDuplicateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


# --- Integration Config ---


class IntegrationConfigUpdate(BaseModel):
    trigger_labels: list[str] | None = None
    webhook_secret: str | None = None
    slack_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None


# --- Provider Config ---


class ProviderConfigUpdate(BaseModel):
    provider_mode: str | None = Field(None, pattern="^(anthropic_api|claude_max|ollama)$")
    model_heavy: str | None = None
    model_medium: str | None = None
    model_light: str | None = None
    default_model: str | None = None
    ollama_base_url: str | None = None


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
