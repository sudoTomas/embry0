"""Pydantic models for the /api/v1/qa/... dashboard surface.

This is the entire contract the dashboard sees. FastAPI auto-generates the
OpenAPI document from these — keep them tight and well-named, since the
typescript client is generated from this same source.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_STATUS = Literal[
    "passed",
    "qa_failure",
    "e2e_failure",
    "boot_failure",
    "ready_check_failed",
    "infra_failure",
    "skipped",
    "inconclusive",
]
"""SubTaskStatus values — mirrored as Literal so OpenAPI surfaces them as enum."""


_OVERALL = Literal["passed", "failed"]
"""Run-level rollup."""


class CacheHitsModel(BaseModel):
    """Mirror of athanor.workflows.qa.subtask_result_schema.CacheHits for the
    JSON surface. The list shape for turbo_remote_* is final per Phase 2."""

    model_config = ConfigDict(extra="forbid")

    prebaked_image: bool = False
    shared_volume: bool = False
    turbo_remote_hits: list[str] = Field(default_factory=list)
    turbo_remote_misses: list[str] = Field(default_factory=list)


class RepoEntry(BaseModel):
    """One repo's row in GET /api/v1/qa/repos."""

    model_config = ConfigDict(extra="forbid")

    repo: str
    latest_run_id: str
    latest_status: _OVERALL
    latest_started_at: datetime
    latest_app_count: int


class RunListItem(BaseModel):
    """One row in GET /api/v1/qa/repos/{repo}/runs."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    repo: str
    started_at: datetime
    overall_status: _OVERALL
    app_count: int


# Alias used in some response types for clarity. Same shape as RunListItem.
RunSummaryItem = RunListItem


class AppResult(BaseModel):
    """One per-app result inside RunDetail.apps."""

    model_config = ConfigDict(extra="forbid")

    app_name: str
    status: _STATUS
    duration_ms: int
    cache_hits: CacheHitsModel = Field(default_factory=CacheHitsModel)
    trace_url: str | None = None
    failure_summary: str | None = None


class RunDetail(BaseModel):
    """Full run detail returned from GET /api/v1/qa/runs/{run_id}."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    repo: str
    started_at: datetime
    overall_status: _OVERALL
    apps: list[AppResult]


class AppHistoryItem(BaseModel):
    """One row in GET /api/v1/qa/repos/{repo}/apps/{app}/history."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    app_name: str
    status: _STATUS
    duration_ms: int
    started_at: datetime
    failure_summary: str | None = None
