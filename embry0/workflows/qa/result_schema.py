"""Pydantic schema for the QA agent's result.json output.

The agent writes this file at the end of its run. The orchestrator
parses + validates it before persisting summary fields to JobState
and uploading to MinIO. Validation failures land an `inconclusive`
attempt with `result_json_invalid` in the exit reason.
"""

from __future__ import annotations

from typing import Any, Literal

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
    network_failures: list[dict[str, Any]] = Field(default_factory=list)
    log_excerpts: list[dict[str, Any]] = Field(default_factory=list)


class QAAnomaly(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["console_error", "network_error", "unexpected_state", "crash", "guardrail_violation"]
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
    def _overall_consistent_with_findings(self) -> QAResult:
        # Auto-coerce overall to "failed" when the agent's anomalies /
        # acceptance_results are inconsistent with the reported overall.
        # Earlier behavior raised ValueError, which rejected the agent's
        # entire report and surfaced the run as `infra_failure` even though
        # the agent succeeded — observed 2026-05-06 on the dashboard app
        # (agent emitted a crash anomaly but set overall='inconclusive';
        # Pydantic rejected, dashboard showed infra_failure instead of
        # the agent's actual finding). Coerce + log so the dashboard
        # reflects the QA finding while the inconsistency is visible.
        import logging  # local import — schema module stays import-light

        log = logging.getLogger(__name__)

        crash_present = any(a.category == "crash" for a in self.anomalies)
        any_failed = any(r.status == "failed" for r in self.acceptance_results)

        if crash_present and self.overall != "failed":
            log.warning(
                "qa_result_overall_coerced_to_failed",
                extra={"reason": "crash_anomaly_present", "agent_overall": self.overall},
            )
            self.overall = "failed"

        if any_failed and self.overall != "failed":
            log.warning(
                "qa_result_overall_coerced_to_failed",
                extra={"reason": "failed_acceptance_result", "agent_overall": self.overall},
            )
            self.overall = "failed"

        # Belt-and-braces: this branch was unreachable above (caught by the
        # any_failed coerce) — keep as a defensive no-op so future edits
        # to the rules above don't silently regress the invariant.
        if self.overall == "inconclusive" and any_failed:
            self.overall = "failed"

        return self
