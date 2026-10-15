"""Posting a review on each forge, and its fallbacks (#30)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from embry0.execution.forge_client import ForgeWrite
from embry0.workflows.pr_review.publish import PublishError, publish_github, publish_gitlab
from embry0.workflows.pr_review.summary import SummaryContext, review_marker
from embry0.workflows.pr_review.verdict import Finding, Verdict

SHA = "a" * 40
CTX = SummaryContext(
    job_id="job-1", head_sha=SHA, job_url="https://embry0.example.com/jobs/job-1", from_fork=False, depth="full"
)
PATCH = "@@ -1,2 +1,3 @@\n a\n+b\n c"  # new lines: 1 context(old 1), 2 added, 3 context(old 2)
REFS = {"base_sha": "b" * 40, "start_sha": "b" * 40, "head_sha": SHA}


def _verdict(*findings: Finding) -> Verdict:
    return Verdict(summary="Reviewed.", findings=findings, checks_run=(), suggested_dev_checks=(), previous_findings=())


class _GitHub:
    def __init__(self, *, head_sha: str = SHA, patch: str = PATCH, answers: list[ForgeWrite] | None = None) -> None:
        self.head_sha = head_sha
        self.patch = patch
        self.answers = answers or [ForgeWrite(200)]
        self.reviews: list[dict[str, Any]] = []
        self.files_read = 0

    async def get_pull(self, repo: str, number: int) -> dict[str, Any] | None:
        return {"head": {"sha": self.head_sha}}

    async def list_pull_file_patches(self, repo: str, number: int) -> list[dict[str, Any]]:
        self.files_read += 1
        return [{"filename": "src/app.py", "status": "modified", "patch": self.patch}]

    async def create_pull_review(
        self, repo: str, number: int, *, commit_id: str | None, body: str, comments: list[dict[str, Any]]
    ) -> ForgeWrite:
        self.reviews.append({"commit_id": commit_id, "body": body, "comments": comments})
        return self.answers.pop(0)


class _GitLab:
    def __init__(
        self,
        *,
        diff_refs: dict[str, str] | None = REFS,
        refuse_lines: tuple[int, ...] = (),
        note: ForgeWrite = ForgeWrite(201),
    ) -> None:
        self.diff_refs = diff_refs
        self.refuse_lines = refuse_lines
        self.note = note
        self.positions: list[dict[str, Any]] = []
        self.notes: list[str] = []

    async def get_merge_request(self, repo: str, iid: int) -> dict[str, Any] | None:
        return {"iid": iid, "diff_refs": self.diff_refs}

    async def list_merge_request_diffs(self, repo: str, iid: int) -> list[dict[str, Any]]:
        return [{"old_path": "src/app.py", "new_path": "src/app.py", "diff": PATCH}]

    async def create_mr_discussion(self, repo: str, iid: int, *, body: str, position: dict[str, Any]) -> ForgeWrite:
        self.positions.append(position)
        assert body.startswith("**") and body.endswith(review_marker("job-1", SHA))
        return (
            ForgeWrite(400, "line_code can't be blank")
            if position["new_line"] in self.refuse_lines
            else ForgeWrite(201)
        )

    async def post_mr_note(self, repo: str, iid: int, body: str) -> ForgeWrite:
        self.notes.append(body)
        return self.note


ON_DIFF = Finding("src/app.py", 2, "important", "Unhandled error.")
OFF_DIFF = Finding("docs/guide.md", 1, "suggestion", "Stale doc.")


# ---- GitHub ----------------------------------------------------------------


async def test_github_posts_one_comment_review_pinned_to_the_reviewed_commit() -> None:
    client = _GitHub()
    result = await publish_github(
        client, repo="acme/widgets", number=7, head_sha=SHA, verdict=_verdict(ON_DIFF, OFF_DIFF), ctx=CTX
    )

    assert (result.inline_count, result.listed_count) == (1, 1)
    [review] = client.reviews
    assert review["commit_id"] == SHA
    assert review["comments"] == [
        {
            "path": "src/app.py",
            "line": 2,
            "side": "RIGHT",
            "body": f"**Important** — Unhandled error.\n\n{review_marker('job-1', SHA)}",
        }
    ]
    assert "### Elsewhere in the code" in review["body"] and "`docs/guide.md:1` — Stale doc." in review["body"]


async def test_a_422_retries_once_without_comments_or_commit_id() -> None:
    client = _GitHub(answers=[ForgeWrite(422, "Line could not be resolved"), ForgeWrite(200)])
    result = await publish_github(
        client, repo="acme/widgets", number=7, head_sha=SHA, verdict=_verdict(ON_DIFF, OFF_DIFF), ctx=CTX
    )

    assert len(client.reviews) == 2, "a 422 must be retried exactly once"
    retry = client.reviews[1]
    assert retry["comments"] == [] and retry["commit_id"] is None
    assert "`src/app.py:2` — Unhandled error." in retry["body"] and "`docs/guide.md:1` — Stale doc." in retry["body"]
    assert (result.inline_count, result.listed_count) == (0, 2)


async def test_a_second_refusal_fails_with_the_forges_message() -> None:
    client = _GitHub(answers=[ForgeWrite(422, "first"), ForgeWrite(422, "Pull request is locked")])
    with pytest.raises(PublishError, match="422.*Pull request is locked"):
        await publish_github(client, repo="acme/widgets", number=7, head_sha=SHA, verdict=_verdict(ON_DIFF), ctx=CTX)


async def test_a_non_422_refusal_is_not_retried() -> None:
    client = _GitHub(answers=[ForgeWrite(403, "Resource not accessible by integration")])
    with pytest.raises(PublishError, match="403"):
        await publish_github(client, repo="acme/widgets", number=7, head_sha=SHA, verdict=_verdict(ON_DIFF), ctx=CTX)
    assert len(client.reviews) == 1


async def test_at_most_thirty_inline_comments_and_the_rest_in_the_summary() -> None:
    patch = "@@ -0,0 +1,40 @@\n" + "\n".join(f"+line {n}" for n in range(1, 41))
    findings = [Finding("src/app.py", n, "suggestion", f"note {n}") for n in range(1, 36)]
    client = _GitHub(patch=patch)
    result = await publish_github(
        client, repo="acme/widgets", number=7, head_sha=SHA, verdict=_verdict(*findings), ctx=CTX
    )

    assert len(client.reviews[0]["comments"]) == 30
    assert (result.inline_count, result.listed_count) == (30, 5)
    assert all(f"note {n}" in client.reviews[0]["body"] for n in range(31, 36))


async def test_head_moved_since_review_posts_everything_in_the_summary_on_github() -> None:
    client = _GitHub(head_sha="f" * 40)
    result = await publish_github(
        client, repo="acme/widgets", number=7, head_sha=SHA, verdict=_verdict(ON_DIFF), ctx=CTX
    )
    assert client.files_read == 0 and client.reviews[0]["comments"] == []
    assert (result.inline_count, result.listed_count) == (0, 1)


# ---- GitLab ----------------------------------------------------------------


async def test_gitlab_posts_a_discussion_per_finding_then_one_summary_note() -> None:
    context_line = Finding("src/app.py", 3, "critical", "Wrong default.")
    client = _GitLab()
    result = await publish_gitlab(
        client,
        repo="acme/products/web",
        number=5,
        head_sha=SHA,
        verdict=_verdict(ON_DIFF, context_line, OFF_DIFF),
        ctx=CTX,
    )

    assert client.positions == [
        {
            "position_type": "text",
            **REFS,
            "old_path": "src/app.py",
            "new_path": "src/app.py",
            "new_line": 3,
            "old_line": 2,
        },
        {"position_type": "text", **REFS, "old_path": "src/app.py", "new_path": "src/app.py", "new_line": 2},
    ], "context lines need both numbers, added lines only the new one; critical first"
    [note] = client.notes
    assert "`docs/guide.md:1` — Stale doc." in note and note.rstrip().endswith(review_marker("job-1", SHA))
    assert (result.inline_count, result.listed_count) == (2, 1)


async def test_a_refused_position_moves_its_finding_into_the_summary() -> None:
    client = _GitLab(refuse_lines=(2,))
    result = await publish_gitlab(
        client, repo="acme/products/web", number=5, head_sha=SHA, verdict=_verdict(ON_DIFF), ctx=CTX
    )
    assert "`src/app.py:2` — Unhandled error." in client.notes[0]
    assert (result.inline_count, result.listed_count) == (0, 1)


async def test_a_discussion_that_raises_moves_its_finding_into_the_summary() -> None:
    class _Unreachable(_GitLab):
        async def create_mr_discussion(self, repo: str, iid: int, *, body: str, position: dict[str, Any]) -> ForgeWrite:
            raise httpx.ConnectError("connection refused")

    client = _Unreachable()
    result = await publish_gitlab(
        client, repo="acme/products/web", number=5, head_sha=SHA, verdict=_verdict(ON_DIFF), ctx=CTX
    )
    assert "`src/app.py:2` — Unhandled error." in client.notes[0], "a discussion that failed must not lose its finding"
    assert (result.inline_count, result.listed_count) == (0, 1)


async def test_a_refused_summary_fails_with_the_forges_message() -> None:
    client = _GitLab(note=ForgeWrite(500, "Internal Server Error"))
    with pytest.raises(PublishError, match="500.*Internal Server Error"):
        await publish_gitlab(
            client, repo="acme/products/web", number=5, head_sha=SHA, verdict=_verdict(ON_DIFF), ctx=CTX
        )


@pytest.mark.parametrize(
    "diff_refs",
    [None, {**REFS, "head_sha": "f" * 40}, {"base_sha": "b" * 40}, {**REFS, "start_sha": ""}],
    ids=["no-refs", "head-moved", "partial-refs", "current-head-without-start-sha"],
)
async def test_head_moved_since_review_posts_everything_in_the_summary_on_gitlab(diff_refs) -> None:
    client = _GitLab(diff_refs=diff_refs)
    result = await publish_gitlab(
        client, repo="acme/products/web", number=5, head_sha=SHA, verdict=_verdict(ON_DIFF), ctx=CTX
    )
    assert client.positions == [], "no inline comment may be placed against lines of another commit"
    assert (result.inline_count, result.listed_count) == (0, 1)
