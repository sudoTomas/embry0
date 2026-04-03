"""Pydantic request/response models for the API."""

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

_REPO_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")


class JobCreateRequest(BaseModel):
    repo: str = Field(..., max_length=200)
    task: str = Field(..., min_length=1, max_length=50000)
    issue_number: int | None = None
    pipeline_template: str | None = None
    pipeline_config: dict[str, Any] | None = None
    sandbox_profile: str | None = None
    max_budget_usd: float | None = Field(None, gt=0, le=1000)
    additional_context: str | None = None

    @field_validator("repo")
    @classmethod
    def validate_repo_format(cls, v: str) -> str:
        if not _REPO_PATTERN.match(v):
            msg = "repo must be in 'owner/name' format"
            raise ValueError(msg)
        return v


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


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    offset: int
    limit: int


class GraphExecuteRequest(BaseModel):
    workflow: str = Field(..., description="Workflow name")
    input_state: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] | None = None


class GraphResumeRequest(BaseModel):
    command: dict[str, Any] = Field(default_factory=dict)


class SandboxProfileRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    base_image: str = "legion-sandbox:latest"
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
