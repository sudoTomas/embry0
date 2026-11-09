"""Sticky embry0/qa PR comment renderer.

This module is pure rendering — no GitHub API calls. Task 25 adds the
upsert that finds an existing comment by COMMENT_MARKER and PATCHes vs POSTs.

Layout:
  - Header line: status banner with run number
  - Summary table: per-app result, duration, cache
  - Per-app collapsibles: failed apps auto-expanded with the reason,
    passed/skipped collapsed under one summary
  - Workspace warnings (if any) in a final collapsed section
"""

from __future__ import annotations

import structlog

from embry0.execution.github_client import GitHubClient
from embry0.workflows.qa.subtask_result_schema import (
    FAILED_STATUSES,
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)

_logger = structlog.get_logger(__name__)

COMMENT_MARKER = "<!-- embry0:qa-sticky-comment -->"


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


def _cache_glyphs(hits: CacheHits) -> str:
    """Three glyph slots for [image, volume, turbo]: 🔥 = hit, · = miss/none."""
    image_g = "\U0001f525" if hits.prebaked_image else "\xb7"
    volume_g = "\U0001f525" if hits.shared_volume else "\xb7"
    turbo_total = len(hits.turbo_remote_hits) + len(hits.turbo_remote_misses)
    if turbo_total == 0:
        turbo_g = "\xb7"
    else:
        hit_ratio = len(hits.turbo_remote_hits) / turbo_total
        turbo_g = "\U0001f525" if hit_ratio >= 0.5 else "\xb7"
    return f"{image_g}{volume_g}{turbo_g}"


def _summary_row(r: SubTaskResult) -> str:
    cache = _cache_glyphs(r.cache_hits)
    return f"| apps/{r.app_name} | {_human_status(r.status)} | {_human_duration(r.duration_ms)} | {cache} |"


def _detail_block(r: SubTaskResult, *, expanded: bool) -> str:
    open_attr = " open" if expanded else ""
    lines = [f"<details{open_attr}>"]
    lines.append(f"<summary>apps/{r.app_name} — {_human_status(r.status)}</summary>")
    lines.append("")
    if r.failure_summary:
        lines.append(f"**Reason:** {r.failure_summary}")
        lines.append("")
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
    """Render the sticky embry0/qa PR comment in Markdown.

    Returns a string ready to POST/PATCH as the comment body. The first line
    is COMMENT_MARKER so the upsert helper (Task 25) can identify the
    sticky comment among others on the PR.
    """
    lines: list[str] = [COMMENT_MARKER, ""]
    lines.append(f"## embry0 QA · run #{run_number}")
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
        lines.append(f"**Status: Passed** — {len(apps_to_qa)} apps validated")
    else:
        failing_count = sum(1 for r in results if r.status in FAILED_STATUSES)
        lines.append(f"**Status: Failed** — {failing_count} of {len(results)} apps did not pass QA")
    lines.append("")

    sorted_results = sorted(results, key=lambda r: r.app_name)

    lines.append("| App | Result | Duration | Cache |")
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
            lines.append(f"- apps/{r.app_name} — {_human_status(r.status)} ({_human_duration(r.duration_ms)})")
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


async def upsert_sticky_comment(
    *,
    github_token: str,
    repo: str,
    issue_number: int,
    body: str,
    client: GitHubClient | None = None,
    marker: str = COMMENT_MARKER,
) -> int:
    """Create or update the sticky embry0/qa comment on a PR.

    Lookup is by ``marker`` (default: the QA COMMENT_MARKER; verification passes
    its own, #299) as a substring of the comment body. If multiple
    comments contain the marker (race), the EARLIEST one is updated; later
    duplicates are not touched (caller can reconcile separately).

    Returns the comment id (int) of the comment created or updated. Raises
    httpx.HTTPStatusError on GitHub API failure.

    Endpoints:
      - GET  /repos/{repo}/issues/{issue_number}/comments  (paginated)
      - PATCH /repos/{repo}/issues/comments/{comment_id}
      - POST /repos/{repo}/issues/{issue_number}/comments
    """
    # GitHubClient resolves GITHUB_TOKEN__<OWNER> for `repo`; this call site
    # took a flat token and never did, so per-owner PATs silently did not
    # apply to QA sticky comments (#158).
    client = client or GitHubClient(default_token=github_token)
    existing_id = await _find_sticky_comment_id(client, repo=repo, issue_number=issue_number, marker=marker)

    if existing_id is not None:
        resp = await client.patch(f"/repos/{repo}/issues/comments/{existing_id}", repo=repo, json={"body": body})
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

    resp = await client.post(f"/repos/{repo}/issues/{issue_number}/comments", repo=repo, json={"body": body})
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


async def edit_sticky_comment(
    client: GitHubClient,
    *,
    repo: str,
    issue_number: int,
    body: str,
    marker: str,
) -> bool:
    """Replace the body of the comment on ``issue_number`` that carries ``marker``; False when there is none.

    Never creates a comment (``upsert_sticky_comment`` does). Raises
    ``httpx.HTTPStatusError`` when GitHub refuses the read or the edit.
    """
    existing_id = await _find_sticky_comment_id(client, repo=repo, issue_number=issue_number, marker=marker)
    if existing_id is None:
        return False
    resp = await client.patch(f"/repos/{repo}/issues/comments/{existing_id}", repo=repo, json={"body": body})
    resp.raise_for_status()
    return True


async def _find_sticky_comment_id(
    client: GitHubClient,
    *,
    repo: str,
    issue_number: int,
    marker: str = COMMENT_MARKER,
) -> int | None:
    """Earliest comment containing ``marker``, or None.

    Pagination is the shared Link-header walk now; this module used to be the
    only place in the codebase that paginated at all, via a hand-rolled page
    counter with a 20-page stop (#158).
    """
    items = await client.paginate(
        f"/repos/{repo}/issues/{issue_number}/comments", repo=repo, per_page=100, max_pages=20
    )
    for item in items:
        if marker in (item.get("body") or ""):
            return int(item["id"])
    return None
