"""Response models for the healing-case read API (#278)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from embry0.services.healing.lifecycle import fix_deployed


class CaseTracker(BaseModel):
    kind: str
    key: str
    url: str | None = None


class CasePullRequestRef(BaseModel):
    """The PR that stands for a case in a one-line view: the merged one,
    else the newest open one, else the newest."""

    url: str
    number: int | None = None
    head_sha: str | None = None
    state: str


class CasePullRequest(CasePullRequestRef):
    merge_sha: str | None = None
    job_id: str | None = None
    opened_at: datetime
    merged_at: datetime | None = None
    closed_at: datetime | None = None


class CaseDeployMarker(BaseModel):
    """The FIRST deploy that carried the merge — confirm-healed's anchor."""

    sha: str
    at: datetime
    services: list[str] | None = None


class CaseVerdict(BaseModel):
    verdict: str
    evidence: dict[str, Any] | None = None
    checked_at: datetime | None = None


class CaseTimestamps(BaseModel):
    """When the case last entered each stage; None for a stage it skipped."""

    detected_at: datetime
    tracked_at: datetime | None = None
    accepted_at: datetime | None = None
    pr_open_at: datetime | None = None
    merged_at: datetime | None = None
    deployed_at: datetime | None = None
    verified_at: datetime | None = None
    closed_at: datetime | None = None


class CaseResponse(BaseModel):
    id: str
    repo: str
    title: str
    # Vocabulary fields are plain strings ON PURPOSE: this model serves what
    # is stored. origin has no CHECK at all, and a newer build's value can
    # outlive a rollback; typing them as enums would turn one such row into
    # a 500 for the whole page. The enums guard the writes and the filters.
    origin: str
    stage: str
    resolution_path: str | None = None
    # Whether a merged fix is known to be running: merge_sha + deployed_at.
    # NOT implied by stage — a case reaches verified/closed with no fix when
    # it resolved itself or outside the code. Use this, never a stage rank.
    fix_deployed: bool
    severity: str | None = None
    fixability: str | None = None
    occurrence_count: int = 1
    last_seen_at: datetime | None = None
    signature: str | None = None
    signature_id: str | None = None
    # How the watcher chose signature_id (#657): "evidence" (a line the
    # proposal quotes), "fallback" (the window's loudest line), or None.
    signature_source: str | None = None
    # Slop dungeon (migration 118): the watcher's slopometer verdict on its
    # finding ({nature, slop_score, kinds, evidence}), None when it gave none.
    work_nature: dict[str, Any] | None = None
    fingerprint: str | None = None
    watcher_run_id: str | None = None
    tracker: CaseTracker | None = None
    issue_id: str | None = None
    pr: CasePullRequestRef | None = None
    merge_sha: str | None = None
    deploy_marker: CaseDeployMarker | None = None
    healed_verdict: CaseVerdict
    parent_case_id: str | None = None
    caused_regression_case_id: str | None = None
    note: str | None = None
    timestamps: CaseTimestamps
    created_at: datetime
    updated_at: datetime

    @classmethod
    def fields_from_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        """Fold a flat ``healing_cases`` row into the nested response shape."""
        return {
            **{
                k: row[k]
                for k in (
                    "id",
                    "repo",
                    "title",
                    "origin",
                    "stage",
                    "resolution_path",
                    "severity",
                    "fixability",
                    "occurrence_count",
                    "last_seen_at",
                    "signature",
                    "signature_id",
                    "fingerprint",
                    "watcher_run_id",
                    "issue_id",
                    "merge_sha",
                    "parent_case_id",
                    "caused_regression_case_id",
                    "note",
                    "created_at",
                    "updated_at",
                )
            },
            # .get: a row built by hand (tests, older fixtures) may not carry it.
            "signature_source": row.get("signature_source"),
            "work_nature": row.get("work_nature"),
            "tracker": (
                CaseTracker(kind=row["tracker"], key=row["tracker_key"], url=row["tracker_url"])
                if row["tracker"]
                else None
            ),
            "fix_deployed": fix_deployed(
                merge_sha=row["merge_sha"], merged_at=row["merged_at"], deployed_at=row["deployed_at"]
            ),
            "deploy_marker": (
                CaseDeployMarker(sha=row["deploy_sha"], at=row["deploy_marker_at"], services=row["deploy_services"])
                if row["deploy_sha"]
                else None
            ),
            "healed_verdict": CaseVerdict(
                verdict=row["healed_verdict"],
                evidence=row["verdict_evidence"],
                checked_at=row["verdict_checked_at"],
            ),
            "timestamps": CaseTimestamps(**{name: row[name] for name in CaseTimestamps.model_fields}),
        }


class CaseSummary(CaseResponse):
    jobs_count: int = 0
    prs_count: int = 0

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CaseSummary:
        pr = (
            CasePullRequestRef(
                url=row["pr_url"], number=row["pr_number"], head_sha=row["pr_head_sha"], state=row["pr_state"]
            )
            if row.get("pr_url")
            else None
        )
        return cls(
            **cls.fields_from_row(row), pr=pr, jobs_count=row.get("jobs_count", 0), prs_count=row.get("prs_count", 0)
        )


class CaseJob(BaseModel):
    job_id: str
    status: str
    job_kind: str | None = None
    pr_url: str | None = None
    error_code: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    attached_at: datetime


class CaseHistoryEvent(BaseModel):
    id: int
    event: str
    event_key: str | None = None
    from_stage: str | None = None
    to_stage: str
    detail: dict[str, Any]
    occurred_at: datetime


def _current(prs: list[CasePullRequest]) -> CasePullRequestRef | None:
    """Same choice the list query makes: merged, else newest open, else newest."""
    for state in ("merged", "open"):
        match = [p for p in prs if p.state == state]
        if match:
            return CasePullRequestRef(**match[-1].model_dump(include=set(CasePullRequestRef.model_fields)))
    return CasePullRequestRef(**prs[-1].model_dump(include=set(CasePullRequestRef.model_fields))) if prs else None


class CaseDetailResponse(CaseResponse):
    jobs: list[CaseJob]
    prs: list[CasePullRequest]
    events: list[CaseHistoryEvent]

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CaseDetailResponse:
        prs = [CasePullRequest(**p) for p in row["prs"]]
        return cls(
            **cls.fields_from_row(row),
            pr=_current(prs),
            prs=prs,
            jobs=[CaseJob(**j) for j in row["jobs"]],
            events=[CaseHistoryEvent(**e) for e in row["events"]],
        )


class CaseListResponse(BaseModel):
    cases: list[CaseSummary]
    total: int
    limit: int
    # Pass back as ``cursor`` for the next page; None on the last page.
    next_cursor: str | None = None
