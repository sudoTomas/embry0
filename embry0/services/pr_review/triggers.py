"""Which forge deliveries ask embry0 to review a pull request (#30).

Pure: no I/O. Two kinds of trigger:

* **ready** — a pull request becomes ready for review (GitHub
  ``ready_for_review``, or ``opened`` with ``draft: false``; GitLab's draft
  flag going true → false, or ``open`` as non-draft). Pushes never trigger.
* **command** — a comment with a line that starts with ``/embry0 review``.
  It is a different trigger from the ``@embry0`` follow-up tag: a comment
  carrying only ``@embry0`` never asks for a review.

GitLab named the draft flag ``work_in_progress`` before ``draft`` existed;
both spellings are read, in ``object_attributes`` and in ``changes``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from embry0.trackers.models import actor_of

TriggerKind = Literal["ready", "command"]

#: Every comment embry0 posts carries this, so its own words never ask it
#: for a review — on GitLab its notes come back through the Note Hook from a
#: user with Developer access.
REVIEW_MARKER = "<!-- embry0-review"
EMBRY0_BRANCH_PREFIX = "embry0/"

_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
# Up to three leading spaces: four make a markdown code block.
_COMMAND_RE = re.compile(r"^ {0,3}/embry0[ \t]+review(?:\s|$)", re.IGNORECASE)
_FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")
_DRAFT_KEYS = ("draft", "work_in_progress")


@dataclass(frozen=True)
class PullRequestInfo:
    """One pull request (GitHub) or merge request (GitLab) as the trigger saw it."""

    forge: str
    repo: str
    number: int
    url: str
    title: str
    body: str
    author: str
    head_ref: str
    head_sha: str
    base_ref: str
    from_fork: bool
    is_open: bool
    draft: bool


@dataclass(frozen=True)
class ReviewTrigger:
    pr: PullRequestInfo
    trigger: TriggerKind
    trigger_key: str
    actor: str  # whom the budget gate charges
    # The author's standing, for the ready-event author gate. GitHub: the
    # pull request's ``author_association`` and ``user.type``. GitLab: the
    # merge request's ``author_id`` (the hook names the author by id only).
    author_association: str = ""
    author_type: str = ""
    gitlab_author_id: int | None = None


@dataclass(frozen=True)
class ReviewCommand:
    """A ``/embry0 review`` comment, before its pull request is resolved."""

    forge: str
    repo: str
    number: int
    comment_id: str
    author: str
    github_comment: Mapping[str, Any] | None = None
    gitlab_user_id: int | None = None
    gitlab_mr: Mapping[str, Any] | None = None


def ready_key(repo: str, number: int, head_sha: str) -> str:
    return f"ready:{repo}#{number}@{head_sha}"


def command_key(forge: str, comment_id: str) -> str:
    return f"command:{forge}:{comment_id}"


def is_embry0_branch(head_ref: str) -> bool:
    return head_ref.startswith(EMBRY0_BRANCH_PREFIX)


def has_review_command(body: str) -> bool:
    """True when a line of ``body`` starts with ``/embry0 review``.

    Lines inside a fenced code block or a ``>`` quote are someone showing the
    command, not asking for it. A body carrying :data:`REVIEW_MARKER` is
    embry0's own and never counts.
    """
    if not body or REVIEW_MARKER in body:
        return False
    fence: str | None = None
    for line in body.splitlines():
        match = _FENCE_RE.match(line)
        if match:
            if fence is None:
                fence = match.group(1)
            elif match.group(1) == fence:
                fence = None
            continue
        if fence is not None or line.lstrip().startswith(">"):
            continue
        if _COMMAND_RE.match(line):
            return True
    return False


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sha(value: Any) -> str | None:
    sha = _str(value).strip().lower()
    return sha if _SHA_RE.fullmatch(sha) else None


def pr_from_github(pr: Mapping[str, Any], *, repo: str) -> PullRequestInfo | None:
    """A GitHub pull-request object (webhook or ``GET /pulls/{n}``); None when unusable.

    A fork is any head repository other than the base, including a deleted
    one (``head.repo: null``): unknown means untrusted, so it is reviewed
    read-only.
    """
    number = _int(pr.get("number"))
    url = _str(pr.get("html_url"))
    head = _map(pr.get("head"))
    base = _map(pr.get("base"))
    sha = _sha(head.get("sha"))
    base_ref = _str(base.get("ref"))
    if number is None or not url or sha is None or not base_ref or not repo:
        return None
    head_repo = _str(_map(head.get("repo")).get("full_name"))
    base_repo = _str(_map(base.get("repo")).get("full_name")) or repo
    user = _map(pr.get("user"))
    return PullRequestInfo(
        forge="github",
        repo=repo,
        number=number,
        url=url,
        title=_str(pr.get("title")),
        body=_str(pr.get("body")),
        author=_str(user.get("login")),
        head_ref=_str(head.get("ref")),
        head_sha=sha,
        base_ref=base_ref,
        from_fork=not head_repo or head_repo.lower() != base_repo.lower(),
        is_open=_str(pr.get("state")) == "open",
        draft=pr.get("draft") is True,
    )


def _gitlab_draft(attrs: Mapping[str, Any]) -> bool:
    for key in _DRAFT_KEYS:
        value = attrs.get(key)
        if isinstance(value, bool):
            return value
    return False


def pr_from_gitlab(mr: Mapping[str, Any], *, repo: str) -> PullRequestInfo | None:
    """A GitLab merge request (hook ``object_attributes``, a note's
    ``merge_request``, or ``GET /merge_requests/:iid``); None when unusable."""
    iid = _int(mr.get("iid"))
    url = _str(mr.get("url")) or _str(mr.get("web_url"))
    last_commit = _map(mr.get("last_commit"))
    sha = _sha(last_commit.get("id")) or _sha(mr.get("sha"))
    base_ref = _str(mr.get("target_branch"))
    if iid is None or not url or sha is None or not base_ref or not repo:
        return None
    source, target = _int(mr.get("source_project_id")), _int(mr.get("target_project_id"))
    author = _map(mr.get("author"))
    return PullRequestInfo(
        forge="gitlab",
        repo=repo,
        number=iid,
        url=url,
        title=_str(mr.get("title")),
        body=_str(mr.get("description")),
        author=_str(author.get("username")),
        head_ref=_str(mr.get("source_branch")),
        head_sha=sha,
        base_ref=base_ref,
        from_fork=source is None or target is None or source != target,
        is_open=_str(mr.get("state")) == "opened",
        draft=_gitlab_draft(mr),
    )


def ready_trigger_from_github(payload: Mapping[str, Any], action: str) -> ReviewTrigger | None:
    if action not in ("ready_for_review", "opened"):
        return None
    pr_obj = payload.get("pull_request")
    repository = _map(payload.get("repository"))
    repo = _str(repository.get("full_name"))
    if not isinstance(pr_obj, Mapping) or not repo:
        return None
    # Only an explicit `draft: false` makes `opened` a ready event.
    if action == "opened" and pr_obj.get("draft") is not False:
        return None
    pr = pr_from_github(pr_obj, repo=repo)
    if pr is None or not pr.is_open:
        return None
    return ReviewTrigger(
        pr=pr,
        trigger="ready",
        trigger_key=ready_key(repo, pr.number, pr.head_sha),
        actor=pr.author or actor_of(payload, "github"),
        author_association=_str(pr_obj.get("author_association")),
        author_type=_str(_map(pr_obj.get("user")).get("type")),
    )


def ready_trigger_from_gitlab(payload: Mapping[str, Any]) -> ReviewTrigger | None:
    if payload.get("object_kind") != "merge_request":
        return None
    attrs = payload.get("object_attributes")
    project = _map(payload.get("project"))
    repo = _str(project.get("path_with_namespace"))
    if not isinstance(attrs, Mapping) or not repo:
        return None
    changes = _map(payload.get("changes"))
    became_ready = any(
        _map(changes.get(key)).get("previous") is True and _map(changes.get(key)).get("current") is False
        for key in _DRAFT_KEYS
    )
    opened_ready = _str(attrs.get("action")) == "open" and not _gitlab_draft(attrs)
    if not (became_ready or opened_ready):
        return None
    pr = pr_from_gitlab(attrs, repo=repo)
    if pr is None or not pr.is_open:
        return None
    # The hook names the author only by id; `user` is who opened it or
    # marked it ready.
    return ReviewTrigger(
        pr=pr,
        trigger="ready",
        trigger_key=ready_key(repo, pr.number, pr.head_sha),
        actor=actor_of(payload, "gitlab") or pr.author,
        gitlab_author_id=_int(attrs.get("author_id")),
    )


def command_from_github(payload: Mapping[str, Any]) -> ReviewCommand | None:
    if payload.get("action") != "created":
        return None
    issue = _map(payload.get("issue"))
    comment = _map(payload.get("comment"))
    if not issue.get("pull_request") or not has_review_command(_str(comment.get("body"))):
        return None
    repository = _map(payload.get("repository"))
    repo = _str(repository.get("full_name"))
    number = _int(issue.get("number"))
    comment_id = comment.get("id")
    if not repo or number is None or comment_id is None:
        return None
    user = _map(comment.get("user"))
    return ReviewCommand(
        forge="github",
        repo=repo,
        number=number,
        comment_id=str(comment_id),
        author=_str(user.get("login")),
        github_comment=comment,
    )


def command_from_gitlab(payload: Mapping[str, Any]) -> ReviewCommand | None:
    if payload.get("object_kind") != "note":
        return None
    attrs = _map(payload.get("object_attributes"))
    if attrs.get("noteable_type") != "MergeRequest" or attrs.get("system") is True:
        return None
    if not has_review_command(_str(attrs.get("note"))):
        return None
    project = _map(payload.get("project"))
    mr = _map(payload.get("merge_request"))
    repo = _str(project.get("path_with_namespace"))
    number = _int(mr.get("iid"))
    note_id = attrs.get("id")
    if not repo or number is None or note_id is None:
        return None
    user = _map(payload.get("user"))
    return ReviewCommand(
        forge="gitlab",
        repo=repo,
        number=number,
        comment_id=str(note_id),
        author=_str(user.get("username")),
        gitlab_user_id=_int(user.get("id")),
        gitlab_mr=mr,
    )
