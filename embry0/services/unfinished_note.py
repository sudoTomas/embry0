"""The note embry0 leaves on a change request a job delivers unfinished work onto (#182).

A job whose pull or merge request already existed (a follow-up's, or an
earlier failed job's it continued) cannot say in that request's body what
it left unfinished: the body is not the job's. So it says so in one sticky
comment, found again by :data:`MARKER`, and labels the request
``human-merge-only`` where only a person may merge it: a job that failed at
its finish line (``done_when.on_unmet: fail``) or a "merge as is" after a
salvage commit. A change ``done_when.on_unmet: review`` let through with
items unmet gets the comment without the label.

When a later job finishes cleanly on the same change request, the comment
is edited to say so (:func:`settle_unfinished_note`); a change request that
carries none gets nothing, and a job a person completed with "merge as is"
settles nothing, since it delivered what stood rather than finishing it. The label stays: a person removes it once they
have reviewed the change.

GitHub pull requests and GitLab merge requests, through the same calls the
QA and verification comments use. Everything here raises what the forge
raises; the caller logs it and goes on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

from embry0.execution.github_client import GitHubClient
from embry0.execution.gitlab_client import make_gitlab_client
from embry0.notifications.qa_comment import edit_sticky_comment, upsert_sticky_comment
from embry0.workflows.verification.result import HUMAN_MERGE_LABEL

#: What the comment carries so that a later job finds it again.
MARKER: Final = "<!-- embry0:unfinished-delivery -->"

_GITHUB_PR_RE = re.compile(r"^https?://[^/]+/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>\d+)")
_GITLAB_MR_RE = re.compile(r"^https?://[^/]+/(?P<repo>.+?)/-/merge_requests/(?P<number>\d+)")


@dataclass(frozen=True)
class ForgeAccess:
    """How the orchestrator reaches the forges: its GitHub token, its configuration (GitLab) and the credential
    resolver."""

    github_token: str = ""
    config: Any = None
    credential_resolver: Any = None


def settled_text(job_id: str) -> str:
    """What the comment says once a later job finished the work."""
    return (
        f"{MARKER}\n\n**A later embry0 job finished this work.** Job `{job_id}` completed on this branch after an "
        "earlier job left it unfinished, so the earlier note here no longer applies. A `human-merge-only` label "
        "stays until a person who has reviewed the change removes it."
    )


async def post_unfinished_note(pr_url: str, parts: list[str], *, human_merge_only: bool, access: ForgeAccess) -> bool:
    """Say ``parts`` in the change request's sticky comment, and label it ``human-merge-only`` when asked.

    False when ``pr_url`` names no GitHub pull request or GitLab merge request.
    """
    body = "\n\n".join([MARKER, *parts])
    github = _GITHUB_PR_RE.match(pr_url or "")
    if github:
        repo, number = github["repo"], int(github["number"])
        client = GitHubClient(default_token=access.github_token, credential_resolver=access.credential_resolver)
        await upsert_sticky_comment(
            github_token=access.github_token, repo=repo, issue_number=number, body=body, client=client, marker=MARKER
        )
        if human_merge_only:
            resp = await client.post(
                f"/repos/{repo}/issues/{number}/labels", repo=repo, json={"labels": [HUMAN_MERGE_LABEL]}
            )
            resp.raise_for_status()
        return True
    gitlab = _GITLAB_MR_RE.match(pr_url or "")
    if gitlab:
        repo, number = gitlab["repo"], int(gitlab["number"])
        lab = make_gitlab_client(access.config, credential_resolver=access.credential_resolver)
        await lab.upsert_sticky_mr_note(repo, number, body, marker=MARKER)
        if human_merge_only and not await lab.add_mr_labels(repo, number, [HUMAN_MERGE_LABEL]):
            raise RuntimeError("merge request label was not accepted")
        return True
    return False


async def settle_unfinished_note(pr_url: str, job_id: str, *, access: ForgeAccess) -> bool:
    """Edit the change request's sticky comment, when it has one, to say job ``job_id`` finished the work.

    False when it carries none, or ``pr_url`` names no change request: nothing
    is posted then.
    """
    body = settled_text(job_id)
    github = _GITHUB_PR_RE.match(pr_url or "")
    if github:
        client = GitHubClient(default_token=access.github_token, credential_resolver=access.credential_resolver)
        return await edit_sticky_comment(
            client, repo=github["repo"], issue_number=int(github["number"]), body=body, marker=MARKER
        )
    gitlab = _GITLAB_MR_RE.match(pr_url or "")
    if gitlab:
        lab = make_gitlab_client(access.config, credential_resolver=access.credential_resolver)
        return await lab.edit_sticky_mr_note(gitlab["repo"], int(gitlab["number"]), body, marker=MARKER)
    return False
