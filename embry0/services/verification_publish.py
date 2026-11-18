"""Publish a job's verification result (#298, #299).

Runs once at job end, for completed AND failed jobs — after the orchestrator's
PR fallback, so a PR the developer never opened still gets the comment. Reads
the durable ``job_verifications`` row (not workflow state: a job that paused
after verifying resumes with a result stream that no longer carries it).

Three outputs, each best-effort and independent:
  1. deliverables — the base run, the head run and the verdict, as JSON;
  2. a sticky comment on the pull/merge request (texts: verification.render);
  3. the ``human-merge-only`` label when a person has to merge.

Shadow-mode results are stored for comparison and never published.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from embry0.workflows.verification.render import COMMENT_MARKER, render_verification_comment
from embry0.workflows.verification.result import HUMAN_MERGE_LABEL

logger = structlog.get_logger(__name__)

_GITHUB_PR_RE = re.compile(r"^https?://[^/]+/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>\d+)")
_GITLAB_MR_RE = re.compile(r"^https?://[^/]+/(?P<repo>.+?)/-/merge_requests/(?P<number>\d+)")


def verification_deliverables(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Deliverable rows for one result: base runs, head runs, verdict."""
    gate = report.get("gate") or {}
    runs = [r for r in gate.get("runs") or [] if isinstance(r, dict)]
    rows: list[dict[str, Any]] = []
    for side, sha_key in (("base", "base_sha"), ("head", "head_sha")):
        side_runs = [r for r in runs if r.get("side") == side]
        if not side_runs:
            continue
        sha = str(gate.get(sha_key) or "")
        rows.append(
            {
                "type": "report",
                "title": f"Test gate: {side} run ({sha[:12] or 'unknown commit'})",
                "content": json.dumps({"side": side, "sha": sha, "runs": side_runs}, indent=2),
                "media_type": "application/json",
                "metadata": {"kind": "verification_run", "side": side, "sha": sha},
            }
        )
    verdict = {k: v for k, v in report.items() if k != "gate"}
    verdict["gate"] = {k: v for k, v in gate.items() if k != "runs"}
    rows.append(
        {
            "type": "report",
            "title": f"Verification verdict: {gate.get('verdict') or 'unknown'}",
            "content": json.dumps(verdict, indent=2),
            "media_type": "application/json",
            "metadata": {
                "kind": "verification_verdict",
                "verdict": gate.get("verdict"),
                "blocking": bool(report.get("blocking")),
                "human_merge_only": bool(report.get("human_merge_only")),
                "tier": (report.get("tier") or {}).get("tier"),
            },
        }
    )
    return rows


async def _comment_on_github(
    *, repo: str, number: int, body: str, human_merge_only: bool, github_token: str, credential_resolver: Any = None
) -> None:
    from embry0.execution.github_client import GitHubClient
    from embry0.notifications.qa_comment import upsert_sticky_comment

    client = GitHubClient(default_token=github_token, credential_resolver=credential_resolver)
    await upsert_sticky_comment(
        github_token=github_token, repo=repo, issue_number=number, body=body, client=client, marker=COMMENT_MARKER
    )
    if human_merge_only:
        # Safe to send again (#45): a label the pull request already has is not added twice.
        resp = await client.post(
            f"/repos/{repo}/issues/{number}/labels",
            repo=repo,
            json={"labels": [HUMAN_MERGE_LABEL]},
            retry_transient=True,
        )
        resp.raise_for_status()


async def _comment_on_gitlab(
    *, repo: str, number: int, body: str, human_merge_only: bool, config: Any, credential_resolver: Any = None
) -> None:
    from embry0.execution.gitlab_client import make_gitlab_client

    client = make_gitlab_client(config, credential_resolver=credential_resolver)
    if not await client.create_mr_note(repo, number, body):
        raise RuntimeError("merge request note was not accepted")
    if human_merge_only and not await client.add_mr_labels(repo, number, [HUMAN_MERGE_LABEL]):
        raise RuntimeError("merge request label was not accepted")


async def publish_verification(
    *,
    job_id: str,
    pr_url: str | None,
    verifications_repo: Any,
    deliverables_repo: Any,
    github_token: str = "",
    config: Any = None,
    credential_resolver: Any = None,
) -> bool:
    """Publish the job's stored result once. Returns True when it published.

    Never raises. The row is marked published when the deliverables are in —
    a comment that could not be posted is logged, not retried: by the time
    this runs the job is over and nothing will call it again.
    """
    if verifications_repo is None:
        return False
    try:
        row = await verifications_repo.get(job_id)
    except Exception:
        logger.warning("verification_publish_read_failed", job_id=job_id, exc_info=True)
        return False
    if not row or row.get("published_at") is not None or row.get("mode") == "shadow":
        return False
    report = row.get("report") if isinstance(row.get("report"), dict) else {}

    if deliverables_repo is not None:
        for deliverable in verification_deliverables(report):
            try:
                await deliverables_repo.create(job_id=job_id, **deliverable)
            except Exception:
                logger.warning(
                    "verification_deliverable_failed", job_id=job_id, title=deliverable["title"], exc_info=True
                )

    human_merge_only = bool(report.get("human_merge_only"))
    body = render_verification_comment(report)
    github = _GITHUB_PR_RE.match(pr_url or "")
    gitlab = None if github else _GITLAB_MR_RE.match(pr_url or "")
    try:
        if github:
            await _comment_on_github(
                repo=github["repo"],
                number=int(github["number"]),
                body=body,
                human_merge_only=human_merge_only,
                github_token=github_token,
                credential_resolver=credential_resolver,
            )
        elif gitlab:
            await _comment_on_gitlab(
                repo=gitlab["repo"],
                number=int(gitlab["number"]),
                body=body,
                human_merge_only=human_merge_only,
                config=config,
                credential_resolver=credential_resolver,
            )
        else:
            logger.info("verification_comment_skipped_no_change_request", job_id=job_id)
    except Exception:
        logger.warning("verification_comment_failed", job_id=job_id, exc_info=True)

    try:
        await verifications_repo.mark_published(job_id)
    except Exception:
        logger.warning("verification_mark_published_failed", job_id=job_id, exc_info=True)
    return True
