"""Sticky athanor/qa PR comment renderer.

This module is pure rendering — no GitHub API calls. Task 25 adds the
upsert that finds an existing comment by COMMENT_MARKER and PATCHes vs POSTs.

Layout:
  - Header line: status banner with run number
  - Summary table: per-app result, duration, trace link
  - Per-app collapsibles: failed apps auto-expanded with reason + trace,
    passed/skipped collapsed under one summary
  - Workspace warnings (if any) in a final collapsed section
"""

from __future__ import annotations

import httpx
import structlog

from athanor.workflows.qa.subtask_result_schema import (
    FAILED_STATUSES,
    SubTaskResult,
    SubTaskStatus,
)

_logger = structlog.get_logger(__name__)
_GITHUB_API = "https://api.github.com"

COMMENT_MARKER = "<!-- athanor:qa-sticky-comment -->"


def _human_status(s: SubTaskStatus) -> str:
    return {
        SubTaskStatus.PASSED: "Passed",
        SubTaskStatus.QA_FAILURE: "QA fail",
        SubTaskStatus.E2E_FAILURE: "E2E fail",
        SubTaskStatus.BOOT_FAILURE: "Boot fail",
        SubTaskStatus.READY_CHECK_FAILED: "Ready-check fail",
        SubTaskStatus.INFRA_FAILURE: "Infra fail",
        SubTaskStatus.SKIPPED: "Skipped",
    }[s]


def _human_duration(ms: int) -> str:
    """Format a millisecond duration as a human-readable string.

    Defensive: clamps negative values to 0 (sub-task duration_ms can theoretically
    be 0 from a fast no-op result, never < 0; but if it somehow goes negative
    we don't want to render '-0.0s' to a user).
    """
    if ms < 0:
        ms = 0
    if ms >= 60_000:
        m, s = divmod(ms // 1000, 60)
        return f"{m}m {s:>2}s"
    return f"{ms / 1000:.1f}s"


def _summary_row(r: SubTaskResult) -> str:
    trace = f"[trace]({r.trace_url})" if r.trace_url else "—"
    return (
        f"| apps/{r.app_name} | {_human_status(r.status)} | "
        f"{_human_duration(r.duration_ms)} | {trace} |"
    )


def _detail_block(r: SubTaskResult, *, expanded: bool) -> str:
    open_attr = " open" if expanded else ""
    lines = [f"<details{open_attr}>"]
    lines.append(f"<summary>apps/{r.app_name} — {_human_status(r.status)}</summary>")
    lines.append("")
    if r.failure_summary:
        lines.append(f"**Reason:** {r.failure_summary}")
        lines.append("")
    if r.trace_url:
        lines.append(f"**Trace:** {r.trace_url}")
    lines.append("</details>")
    return "\n".join(lines)


def render_qa_comment(
    *,
    run_number: int,
    overall_status: str,
    apps_to_qa: list[str],
    results: list[SubTaskResult],
    failure_summary: str | None = None,
    validation_warnings: list[str] | None = None,
) -> str:
    """Render the sticky athanor/qa PR comment in Markdown.

    Returns a string ready to POST/PATCH as the comment body. The first line
    is COMMENT_MARKER so the upsert helper (Task 25) can identify the
    sticky comment among others on the PR.
    """
    lines: list[str] = [COMMENT_MARKER, ""]
    lines.append(f"## athanor QA · run #{run_number}")
    lines.append("")

    if overall_status == "infra_error":
        lines.append("**Status: Infra error** — QA could not complete")
        lines.append("")
        if failure_summary:
            lines.append(f"> {failure_summary}")
        return "\n".join(lines)

    if not apps_to_qa:
        lines.append("**QA skipped** — no affected apps in this PR's diff.")
        return "\n".join(lines)

    if overall_status == "passed":
        lines.append(
            f"**Status: Passed** — {len(apps_to_qa)} apps validated"
        )
    else:
        failing_count = sum(1 for r in results if r.status in FAILED_STATUSES)
        lines.append(
            f"**Status: Failed** — {failing_count} of {len(results)} apps did not pass QA"
        )
    lines.append("")

    sorted_results = sorted(results, key=lambda r: r.app_name)

    lines.append("| App | Result | Duration | Trace |")
    lines.append("|---|---|---:|:---:|")
    for r in sorted_results:
        lines.append(_summary_row(r))
    lines.append("")

    # Failed apps expanded
    for r in sorted_results:
        if r.status in FAILED_STATUSES:
            lines.append(_detail_block(r, expanded=True))
            lines.append("")

    # Passed/skipped collapsed under one summary
    passed_or_skipped = [r for r in sorted_results if r.status not in FAILED_STATUSES]
    if passed_or_skipped:
        lines.append("<details>")
        lines.append("<summary>passed / skipped (collapsed)</summary>")
        lines.append("")
        for r in passed_or_skipped:
            lines.append(
                f"- apps/{r.app_name} — {_human_status(r.status)} "
                f"({_human_duration(r.duration_ms)})"
            )
        lines.append("")
        lines.append("</details>")

    if validation_warnings:
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>workspace warnings</summary>")
        lines.append("")
        for w in validation_warnings:
            lines.append(f"- {w}")
        lines.append("</details>")

    return "\n".join(lines)


def _gh_headers(github_token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def upsert_sticky_comment(
    *,
    github_token: str,
    repo: str,
    issue_number: int,
    body: str,
) -> int:
    """Create or update the sticky athanor/qa comment on a PR.

    Lookup is by COMMENT_MARKER substring inside the comment body. If multiple
    comments contain the marker (race), the EARLIEST one is updated; later
    duplicates are not touched (caller can reconcile separately).

    Returns the comment id (int) of the comment created or updated. Raises
    httpx.HTTPStatusError on GitHub API failure.

    Endpoints:
      - GET  /repos/{repo}/issues/{issue_number}/comments  (paginated)
      - PATCH /repos/{repo}/issues/comments/{comment_id}
      - POST /repos/{repo}/issues/{issue_number}/comments
    """
    headers = _gh_headers(github_token)
    list_url = f"{_GITHUB_API}/repos/{repo}/issues/{issue_number}/comments"

    async with httpx.AsyncClient(timeout=15.0) as client:
        existing_id = await _find_sticky_comment_id(client, list_url, headers)

        if existing_id is not None:
            patch_url = f"{_GITHUB_API}/repos/{repo}/issues/comments/{existing_id}"
            resp = await client.patch(patch_url, json={"body": body}, headers=headers)
            if resp.status_code in (200, 201):
                _logger.info(
                    "qa_sticky_comment_updated",
                    repo=repo,
                    issue_number=issue_number,
                    comment_id=existing_id,
                )
                return existing_id
            _logger.error(
                "qa_sticky_comment_update_failed",
                repo=repo,
                issue_number=issue_number,
                status=resp.status_code,
                body=resp.text[:200],
            )
            resp.raise_for_status()
            return existing_id  # unreachable

        post_url = f"{_GITHUB_API}/repos/{repo}/issues/{issue_number}/comments"
        resp = await client.post(post_url, json={"body": body}, headers=headers)
        if resp.status_code in (200, 201):
            new_id = int(resp.json()["id"])
            _logger.info(
                "qa_sticky_comment_created",
                repo=repo,
                issue_number=issue_number,
                comment_id=new_id,
            )
            return new_id

        _logger.error(
            "qa_sticky_comment_create_failed",
            repo=repo,
            issue_number=issue_number,
            status=resp.status_code,
            body=resp.text[:200],
        )
        resp.raise_for_status()
        return -1  # unreachable


async def _find_sticky_comment_id(
    client: httpx.AsyncClient,
    list_url: str,
    headers: dict[str, str],
) -> int | None:
    """Walk paginated comments looking for COMMENT_MARKER. Returns earliest match."""
    page = 1
    while True:
        url = f"{list_url}?per_page=100&page={page}"
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return None
        items = resp.json() or []
        for item in items:
            body = item.get("body") or ""
            if COMMENT_MARKER in body:
                return int(item["id"])
        if len(items) < 100:
            return None
        page += 1
        if page > 20:  # safety stop after 2000 comments — no real PR has this many
            return None
