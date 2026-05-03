"""Pydantic request/response models for issues."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from athanor.api.schemas import _REPO_PATTERN
from athanor.orchestration.state import AskUserChannel


class CreateIssueRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    priority: str = "medium"
    repo: str | None = None
    github_sync_enabled: bool = False
    auto_triage: bool = True
    # Per-issue list of channels the orchestrator fans out agent ask-user
    # questions to. Typed as the enum here so Pydantic 422s any unknown
    # channel string at the API boundary. The bound is ``len(AskUserChannel)
    # + 1`` — one slot of headroom for the next channel addition without an
    # immediate breaking change. The default factory builds a fresh list per
    # request — never share a mutable.
    notification_channels: list[AskUserChannel] = Field(
        default_factory=lambda: [AskUserChannel.DASHBOARD],
        max_length=len(AskUserChannel) + 1,
    )

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

    @field_validator("notification_channels")
    @classmethod
    def _no_duplicate_channels(
        cls, v: list[AskUserChannel] | None
    ) -> list[AskUserChannel] | None:
        if v is None:
            return v
        if len(v) != len(set(v)):
            msg = "notification_channels must not contain duplicates"
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
    # Optional update of the per-issue notification channels. Same enum
    # validation + max_length bound as CreateIssueRequest. Omitting the key
    # leaves the existing value untouched (the route uses
    # ``model_dump(exclude_none=True)``).
    notification_channels: list[AskUserChannel] | None = Field(
        default=None,
        max_length=len(AskUserChannel) + 1,
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is not None:
            allowed = {"open", "triaging", "in_progress", "awaiting_input", "closed", "cancelled"}
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

    @field_validator("notification_channels")
    @classmethod
    def _no_duplicate_channels(
        cls, v: list[AskUserChannel] | None
    ) -> list[AskUserChannel] | None:
        if v is None:
            return v
        if len(v) != len(set(v)):
            msg = "notification_channels must not contain duplicates"
            raise ValueError(msg)
        return v


class IssueResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    children_closed_count: int = 0
    jobs_count: int = 0
    active_agent: str | None = None
    # Plain ``list[str]`` on the response side — clients (the dashboard,
    # webhook consumers) can compare against literal channel names without
    # importing the AskUserChannel enum. The DB jsonb default is
    # ``["dashboard"]`` so legacy rows always populate this field.
    notification_channels: list[str] = Field(default_factory=lambda: ["dashboard"])


class IssueDetailResponse(IssueResponse):
    children: list[IssueResponse] = Field(default_factory=list)
    jobs: list[dict[str, Any]] = Field(default_factory=list)


class IssueListResponse(BaseModel):
    issues: list[IssueResponse]
    total: int
    offset: int
    limit: int
