"""Pydantic schema for the QA agent's result.json output.

The agent writes this file at the end of its run. The orchestrator
parses + validates it before persisting summary fields to JobState
and uploading to MinIO. Validation failures land an `inconclusive`
attempt with `result_json_invalid` in the exit reason.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QAReadyCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    status: int
    duration_ms: int


class QABootResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str
    duration_ms: int
    ready_checks: list[QAReadyCheckResult] = Field(min_length=1)


class QASeedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ran: bool
    command: str | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    note: str | None = None


class QAE2EResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ran: bool
    command: str | None = None
    exit_code: int | None = None
    tests: dict[str, int] = Field(default_factory=dict)
    junit_artifact: str | None = None


class QAAcceptanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion: str
    status: Literal["passed", "failed", "inconclusive"]
    evidence: list[str] = Field(default_factory=list)
    notes: str | None = None
    console_errors: list[str] = Field(default_factory=list)
    network_failures: list[dict] = Field(default_factory=list)
    log_excerpts: list[dict] = Field(default_factory=list)


class QAAnomaly(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["console_error", "network_error", "unexpected_state", "crash"]
    detail: str
    evidence_paths: list[str] = Field(default_factory=list)
    ts: str | None = None


class QAResult(BaseModel):
    """Top-level result.json contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    job_id: str
    attempt_n: int
    phase_reached: Literal["boot", "seed", "e2e", "exploratory", "report"]
    overall: Literal["passed", "failed", "inconclusive"]

    boot: QABootResult | None = None
    seed: QASeedResult | None = None
    e2e: QAE2EResult | None = None
    acceptance_results: list[QAAcceptanceResult]
    anomalies: list[QAAnomaly]

    @model_validator(mode="after")
    def _overall_consistent_with_findings(self) -> "QAResult":
        # Crash anomaly forces failed.
        if any(a.category == "crash" for a in self.anomalies) and self.overall != "failed":
            raise ValueError("overall must be 'failed' when an anomaly has category='crash'")
        # Any failed acceptance result forces failed.
        any_failed = any(r.status == "failed" for r in self.acceptance_results)
        if any_failed and self.overall != "failed":
            raise ValueError("overall must be 'failed' when any acceptance_result.status == 'failed'")
        # Inconclusive only when no failures.
        if self.overall == "inconclusive" and any_failed:
            raise ValueError("overall='inconclusive' is incompatible with any failed acceptance result")
        return self
