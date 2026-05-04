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

from athanor.workflows.qa.subtask_result_schema import (
    SubTaskResult,
    SubTaskStatus,
)

COMMENT_MARKER = "<!-- athanor:qa-sticky-comment -->"


_FAILED_STATUSES = frozenset(
    {
        SubTaskStatus.QA_FAILURE,
        SubTaskStatus.E2E_FAILURE,
        SubTaskStatus.BOOT_FAILURE,
        SubTaskStatus.READY_CHECK_FAILED,
        SubTaskStatus.INFRA_FAILURE,
    }
)


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
        failing_count = sum(1 for r in results if r.status in _FAILED_STATUSES)
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
        if r.status in _FAILED_STATUSES:
            lines.append(_detail_block(r, expanded=True))
            lines.append("")

    # Passed/skipped collapsed under one summary
    passed_or_skipped = [r for r in sorted_results if r.status not in _FAILED_STATUSES]
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
