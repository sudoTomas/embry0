"""Pydantic models for the /api/v1/qa/... dashboard surface.

This is the entire contract the dashboard sees. FastAPI auto-generates the
OpenAPI document from these — keep them tight and well-named, since the
typescript client is generated from this same source.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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


_OVERALL = Literal["passed", "failed", "infra_error"]
"""Run-level rollup."""


class CacheHitsModel(BaseModel):
    """Mirror of athanor.workflows.qa.subtask_result_schema.CacheHits for the
    JSON surface. The list shape for turbo_remote_* is final per Phase 2."""

    model_config = ConfigDict(extra="forbid")

    prebaked_image: bool = False
    shared_volume: bool = False
    turbo_remote_hits: list[str] = Field(default_factory=list)
    turbo_remote_misses: list[str] = Field(default_factory=list)


class BootPhaseDetail(BaseModel):
    """Detailed boot-phase outcome for a sub-task. None when boot_phase
    state was not captured (legacy runs) or is irrelevant (status=passed
    runs that booted normally — UIs decide whether to render anyway)."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["passed", "timeout", "startup_failed"]
    attempts: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    failed_checks: list[str] = Field(default_factory=list)
    boot_stdout_tail: str = ""


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
    boot_phase: BootPhaseDetail | None = None


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


class DepEdge(BaseModel):
    """One workspace dependency edge — package_name keyed.

    Field names: ``source``/``target`` rather than ``from``/``to`` so we
    avoid pydantic2's reserved-keyword alias dance. The frontend mirrors
    this naming. Empty list returned by Phase 5D until provider exposes
    edges; reserved for the dep-graph view.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str


class AffectedSetResponse(BaseModel):
    """GET /api/v1/qa/runs/{run_id}/affected_set payload.

    One-to-one mirror of the qa_run_metadata table. ``apps_skipped`` is
    every declared-app NOT in apps_to_qa; ``changed_files`` is the diff
    that drove selection; ``dep_graph`` is the workspace edges (empty
    until the workspace_provider exposes them).
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str
    apps_to_qa: list[str]
    apps_skipped: list[str]
    force_all_apps: bool
    changed_files: list[str]
    base_branch: str
    dep_graph: list[DepEdge] = Field(default_factory=list)


class CacheLayerStats(BaseModel):
    """Phase 5E: aggregate hit/miss for a single cache layer over a window."""

    model_config = ConfigDict(extra="forbid")

    layer: Literal["prebaked_image", "shared_volume", "turbo_remote"]
    hits: int = Field(ge=0)
    misses: int = Field(ge=0)
    hit_ratio: float = Field(ge=0.0, le=1.0)


class CacheAnalyticsResponse(BaseModel):
    """Phase 5E: GET /api/v1/qa/repos/{repo}/cache/analytics payload.

    Aggregates per-layer cache hit/miss across every QA sub-task that ran
    against ``repo`` in the last ``window_days`` days. ``layers`` always
    holds exactly three entries (one per layer). ``cold_cache_apps`` is
    the alphabetised list of apps whose aggregate hit ratio fell below
    0.25 — surfaced as 'top offenders' in the UI.
    """

    model_config = ConfigDict(extra="forbid")

    repo: str
    window_days: int = Field(gt=0, le=365)
    total_runs: int = Field(ge=0)
    total_subtasks: int = Field(ge=0)
    layers: list[CacheLayerStats]
    cold_cache_apps: list[str] = Field(default_factory=list)


# ── Phase 5G: workspace_provider admin overrides ────────────────────────────


class WorkspaceProviderOverride(BaseModel):
    """One row from qa_workspace_provider_overrides — what the admin UI lists."""

    model_config = ConfigDict(extra="forbid")

    repo: str
    provider_type: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class WorkspaceProviderOverrideUpsert(BaseModel):
    """Request body for POST /qa/admin/providers/{repo}.

    The admin form edits an existing provider's (type, config) — adding new
    provider TYPES is out of scope for Phase 5G. The orchestrator's
    ``load_provider(type, ...)`` does the type validation when the next QA
    run picks up the override.
    """

    model_config = ConfigDict(extra="forbid")

    provider_type: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
