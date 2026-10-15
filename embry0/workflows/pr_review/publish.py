"""Post a review on the forge (#30).

Runs in the orchestrator with the forge token (``ForgeClient.token_for``);
nothing is posted from the sandbox. Inline comments are placed only when the
pull request's head is still the reviewed commit: once someone pushed, the
lines the agent named belong to another version, so every finding goes into
the summary instead.

GitHub: one ``POST pulls/{n}/reviews`` (``event: COMMENT``); on a 422 it is
retried once with every finding in the body and without ``commit_id``.
GitLab: one diff discussion per inline finding (a refused position moves the
finding into the summary), then one summary note.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from embry0.workflows.pr_review.diff_positions import FileIndex, build_line_index, files_from_github, files_from_gitlab
from embry0.workflows.pr_review.placement import by_severity, place_findings
from embry0.workflows.pr_review.summary import SummaryContext, inline_body, render_summary, review_marker
from embry0.workflows.pr_review.verdict import Finding, Verdict

if TYPE_CHECKING:
    from embry0.execution.github_client import GitHubClient
    from embry0.execution.gitlab_client import GitLabClient

logger = structlog.get_logger(__name__)

_POSITION_REFS = ("base_sha", "start_sha", "head_sha")


class PublishError(RuntimeError):
    """The forge refused the review after the fallback. The message is the forge's."""


@dataclass(frozen=True)
class PublishResult:
    inline_count: int
    listed_count: int


async def publish_github(
    client: GitHubClient, *, repo: str, number: int, head_sha: str, verdict: Verdict, ctx: SummaryContext
) -> PublishResult:
    pull = await client.get_pull(repo, number)
    head = (pull or {}).get("head")
    current = str(head.get("sha") or "") if isinstance(head, dict) else ""
    index: dict[str, FileIndex] = {}
    if current == head_sha:
        index = build_line_index(files_from_github(await client.list_pull_file_patches(repo, number)))
    else:
        logger.info(
            "pr_review_head_moved", forge="github", repo=repo, number=number, reviewed=head_sha, current=current or None
        )
    placement = place_findings(verdict.findings, index)
    marker = review_marker(ctx.job_id, head_sha)
    comments = [
        {"path": c.path, "line": c.line, "side": "RIGHT", "body": inline_body(c.finding, marker=marker)}
        for c in placement.inline
    ]
    listed = list(placement.listed)
    first = await client.create_pull_review(
        repo,
        number,
        commit_id=head_sha,
        body=render_summary(verdict, inline_count=len(comments), listed=listed, ctx=ctx),
        comments=comments,
    )
    if first.ok:
        return PublishResult(inline_count=len(comments), listed_count=len(listed))
    if first.status_code != 422:
        raise PublishError(f"GitHub refused the review ({first.status_code}): {first.detail}")
    # One retry: every finding in the body and no commit pin. A line GitHub
    # could not resolve, or a head that moved, are the usual causes of a 422.
    logger.info("pr_review_github_422_fallback", repo=repo, number=number, detail=first.detail[:300])
    everything = by_severity([c.finding for c in placement.inline] + listed)
    second = await client.create_pull_review(
        repo,
        number,
        commit_id=None,
        body=render_summary(verdict, inline_count=0, listed=everything, ctx=ctx),
        comments=[],
    )
    if not second.ok:
        raise PublishError(f"GitHub refused the review ({second.status_code}): {second.detail}")
    return PublishResult(inline_count=0, listed_count=len(everything))


def _diff_refs(mr: dict[str, Any] | None) -> dict[str, str]:
    raw = (mr or {}).get("diff_refs")
    if not isinstance(raw, dict):
        return {}
    refs = {key: str(raw.get(key) or "") for key in _POSITION_REFS}
    return refs if all(refs.values()) else {}


async def publish_gitlab(
    client: GitLabClient, *, repo: str, number: int, head_sha: str, verdict: Verdict, ctx: SummaryContext
) -> PublishResult:
    refs = _diff_refs(await client.get_merge_request(repo, number))
    index: dict[str, FileIndex] = {}
    if refs and refs["head_sha"] == head_sha:
        index = build_line_index(files_from_gitlab(await client.list_merge_request_diffs(repo, number)))
    else:
        logger.info(
            "pr_review_head_moved",
            forge="gitlab",
            repo=repo,
            number=number,
            reviewed=head_sha,
            current=refs.get("head_sha"),
        )
    placement = place_findings(verdict.findings, index)
    marker = review_marker(ctx.job_id, head_sha)
    listed: list[Finding] = list(placement.listed)
    inline = 0
    for comment in placement.inline:
        position: dict[str, Any] = {
            "position_type": "text",
            **refs,
            "old_path": comment.old_path,
            "new_path": comment.path,
            "new_line": comment.line,
        }
        if comment.kind == "context" and comment.old_line is not None:
            position["old_line"] = comment.old_line
        try:
            result = await client.create_mr_discussion(
                repo, number, body=inline_body(comment.finding, marker=marker), position=position
            )
        except Exception:
            logger.warning("pr_review_gitlab_discussion_failed", repo=repo, number=number, exc_info=True)
            listed.append(comment.finding)
            continue
        if result.ok:
            inline += 1
        else:
            logger.info(
                "pr_review_gitlab_position_refused",
                repo=repo,
                number=number,
                path=comment.path,
                line=comment.line,
                status=result.status_code,
                detail=result.detail[:300],
            )
            listed.append(comment.finding)
    listed = by_severity(listed)
    note = await client.post_mr_note(repo, number, render_summary(verdict, inline_count=inline, listed=listed, ctx=ctx))
    if not note.ok:
        raise PublishError(f"GitLab refused the review summary ({note.status_code}): {note.detail}")
    return PublishResult(inline_count=inline, listed_count=len(listed))
