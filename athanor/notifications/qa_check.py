"""Orchestrator-side athanor/qa GitHub Check Run writer.

A single check named `athanor/qa` gates each PR. The check is created or
updated idempotently per (head_sha, name): on first call we POST; on
subsequent calls (e.g. PR re-runs after a force-push) we PATCH.

This module follows the same pattern as athanor/notifications/github.py:
per-call httpx client, no long-lived state, errors logged + raised.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from athanor.workflows.qa.subtask_result_schema import FAILED_STATUSES, SubTaskResult, SubTaskStatus

logger = structlog.get_logger(__name__)

_GITHUB_API = "https://api.github.com"
CHECK_NAME = "athanor/qa"


@dataclass(frozen=True, slots=True)
class QACheckPayload:
    name: str
    status: str  # "in_progress" | "completed"
    conclusion: str | None  # "success" | "failure" | "neutral" | None when in_progress
    title: str
    summary: str


def _failing_apps(results: list[SubTaskResult]) -> list[str]:
    return [r.app_name for r in results if r.status in FAILED_STATUSES]


def build_check_payload(
    *,
    overall_status: str,
    apps_to_qa: list[str],
    results: list[SubTaskResult],
    in_progress: bool,
    failure_summary: str | None = None,
) -> QACheckPayload:
    """Translate (overall_status, results) -> GitHub check fields.

    Maps athanor's outcome semantics onto GitHub's check status + conclusion:
      pending          -> status=in_progress, conclusion=None
      passed (no apps) -> status=completed, conclusion=neutral ("skipped")
      passed           -> status=completed, conclusion=success
      failed           -> status=completed, conclusion=failure (lists failing apps)
      infra_error      -> status=completed, conclusion=failure (distinct title)
    """
    if in_progress:
        completed = sum(1 for r in results if r.status != SubTaskStatus.SKIPPED)
        return QACheckPayload(
            name=CHECK_NAME,
            status="in_progress",
            conclusion=None,
            title=f"Running QA on {len(apps_to_qa)} apps — {completed} completed",
            summary="QA in progress.",
        )

    if not apps_to_qa:
        return QACheckPayload(
            name=CHECK_NAME,
            status="completed",
            conclusion="neutral",
            title="QA skipped — no affected apps",
            summary="No apps were affected by this PR's diff.",
        )

    if overall_status == "passed":
        return QACheckPayload(
            name=CHECK_NAME,
            status="completed",
            conclusion="success",
            title=f"QA passed — {len(apps_to_qa)} apps validated",
            summary=f"All {len(apps_to_qa)} affected apps passed QA.",
        )

    if overall_status == "infra_error":
        return QACheckPayload(
            name=CHECK_NAME,
            status="completed",
            conclusion="failure",
            title="QA infra error — could not complete",
            summary=failure_summary or "Athanor encountered an infra error.",
        )

    failing = _failing_apps(results)
    return QACheckPayload(
        name=CHECK_NAME,
        status="completed",
        conclusion="failure",
        title=f"QA failed — {', '.join('apps/' + a for a in failing)}",
        summary=f"{len(failing)} of {len(results)} apps did not pass QA.",
    )


def _headers(github_token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _find_existing_check_run_id(
    client: httpx.AsyncClient,
    *,
    repo: str,
    head_sha: str,
    headers: dict[str, str],
) -> int | None:
    """Look up an existing athanor/qa check run for this head_sha. Idempotent."""
    url = f"{_GITHUB_API}/repos/{repo}/commits/{head_sha}/check-runs?check_name={CHECK_NAME}"
    resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        return None
    data = resp.json()
    runs = data.get("check_runs") or []
    if not runs:
        return None
    return int(runs[0]["id"])


async def write_aggregate_check(
    *,
    github_token: str,
    repo: str,
    head_sha: str,
    overall_status: str,
    apps_to_qa: list[str],
    results: list[SubTaskResult],
    in_progress: bool,
    failure_summary: str | None = None,
) -> dict[str, Any]:
    """Create or update the athanor/qa GitHub Check Run for this head_sha.

    Idempotent: if a check with name=athanor/qa already exists for this commit,
    PATCH it; otherwise POST. Returns the raw response JSON on success.
    """
    payload = build_check_payload(
        overall_status=overall_status,
        apps_to_qa=apps_to_qa,
        results=results,
        in_progress=in_progress,
        failure_summary=failure_summary,
    )

    body: dict[str, Any] = {
        "name": payload.name,
        "head_sha": head_sha,
        "status": payload.status,
        "output": {
            "title": payload.title,
            "summary": payload.summary,
        },
    }
    if payload.conclusion is not None:
        body["conclusion"] = payload.conclusion

    headers = _headers(github_token)
    async with httpx.AsyncClient(timeout=15.0) as client:
        existing_id = await _find_existing_check_run_id(client, repo=repo, head_sha=head_sha, headers=headers)
        if existing_id is None:
            url = f"{_GITHUB_API}/repos/{repo}/check-runs"
            resp = await client.post(url, json=body, headers=headers)
        else:
            url = f"{_GITHUB_API}/repos/{repo}/check-runs/{existing_id}"
            # PATCH cannot include head_sha
            patch_body = {k: v for k, v in body.items() if k != "head_sha"}
            resp = await client.patch(url, json=patch_body, headers=headers)

    if resp.status_code in (200, 201):
        result: dict[str, Any] = resp.json()
        logger.info(
            "qa_check_run_written",
            repo=repo,
            head_sha=head_sha,
            check_run_id=result.get("id"),
            status=payload.status,
            conclusion=payload.conclusion,
        )
        return result

    logger.error(
        "qa_check_run_failed",
        repo=repo,
        head_sha=head_sha,
        status=resp.status_code,
        body=resp.text[:200],
    )
    resp.raise_for_status()  # Raise for caller; logs above for visibility
    return {}  # unreachable but satisfies type checker
