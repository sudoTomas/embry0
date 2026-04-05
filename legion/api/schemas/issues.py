"""Pydantic request/response models for issues."""

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_REPO_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$")


class CreateIssueRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    priority: str = "medium"
    repo: str | None = None
    github_sync_enabled: bool = False
    auto_triage: bool = True

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        allowed = {"critical", "high", "medium", "low"}
        if v not in allowed:
            msg = f"priority must be one of {allowed}"
            raise ValueError(msg)
        return v

    @field_validator("repo")
    @classmethod
    def validate_repo_format(cls, v: str | None) -> str | None:
        if v is not None and not _REPO_PATTERN.match(v):
            msg = "repo must be in 'owner/name' format"
            raise ValueError(msg)
        return v


class UpdateIssueRequest(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    body: str | None = None
    status: str | None = None
    priority: str | None = None
    labels: list[str] | None = None
    repo: str | None = None
    github_sync_enabled: bool | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is not None:
            allowed = {"open", "triaging", "in_progress", "closed", "cancelled"}
            if v not in allowed:
                msg = f"status must be one of {allowed}"
                raise ValueError(msg)
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str | None) -> str | None:
        if v is not None:
            allowed = {"critical", "high", "medium", "low"}
            if v not in allowed:
                msg = f"priority must be one of {allowed}"
                raise ValueError(msg)
        return v

    @field_validator("repo")
    @classmethod
    def validate_repo_format(cls, v: str | None) -> str | None:
        if v is not None and not _REPO_PATTERN.match(v):
            msg = "repo must be in 'owner/name' format"
            raise ValueError(msg)
        return v


class IssueResponse(BaseModel):
    id: str
    title: str
    body: str
    status: str
    priority: str
    labels: list[str]
    repo: str | None
    parent_issue_id: str | None
    github_number: int | None
    github_url: str | None
    github_sync_enabled: bool
    github_synced_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    children_count: int = 0
    jobs_count: int = 0
    active_agent: str | None = None


class IssueDetailResponse(IssueResponse):
    children: list[IssueResponse] = Field(default_factory=list)
    jobs: list[dict] = Field(default_factory=list)


class IssueListResponse(BaseModel):
    issues: list[IssueResponse]
    total: int
    offset: int
    limit: int
