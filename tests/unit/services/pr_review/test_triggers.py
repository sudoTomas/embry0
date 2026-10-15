"""Which deliveries ask for a pull request review (#30). Pure."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.services.pr_review.triggers import (
    REVIEW_MARKER,
    command_from_github,
    command_from_gitlab,
    command_key,
    has_review_command,
    is_embry0_branch,
    pr_from_github,
    ready_key,
    ready_trigger_from_github,
    ready_trigger_from_gitlab,
)
from tests.fixtures.webhooks import (
    github_issue_comment,
    github_review_pull_request,
    gitlab_issue,
    gitlab_merge_request,
    gitlab_mr_note,
)

SHA = "a" * 40

# ---- the command -----------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "/embry0 review",
        "/Embry0 REVIEW",
        "/embry0 review please, the auth part",
        "Thanks!\n/embry0 review",
        "   /embry0 review",
        "/embry0\treview",
    ],
)
def test_a_line_starting_with_the_command_asks_for_a_review(body: str) -> None:
    assert has_review_command(body) is True, f"{body!r} should ask for a review"


@pytest.mark.parametrize(
    "body",
    [
        "",
        "@embry0 please fix the typo",
        "please /embry0 review this",
        "/embry0 reviewer",
        "/embry0",
        "    /embry0 review",  # four spaces: a markdown code block
    ],
)
def test_anything_else_is_not_the_command(body: str) -> None:
    assert has_review_command(body) is False, f"{body!r} must not ask for a review"


@pytest.mark.parametrize(
    "body",
    [
        "```\n/embry0 review\n```",
        "~~~text\n/embry0 review\n~~~",
        "> /embry0 review",
        "Someone said:\n>  /embry0 review\nbut I disagree",
    ],
)
def test_command_inside_code_fence_or_quote_is_ignored(body: str) -> None:
    assert has_review_command(body) is False, f"{body!r} quotes the command; it is not a request"


def test_the_command_after_a_closed_fence_still_counts() -> None:
    assert has_review_command("```\ncode\n```\n/embry0 review") is True


def test_our_own_comment_never_triggers() -> None:
    body = f"/embry0 review\n{REVIEW_MARKER} job=job-1 sha={SHA} -->"
    assert has_review_command(body) is False, "embry0's own comments carry the marker and must never start a review"


# ---- GitHub ready ----------------------------------------------------------


def test_github_ready_for_review_is_a_trigger() -> None:
    trigger = ready_trigger_from_github(github_review_pull_request(), "ready_for_review")
    assert trigger is not None
    assert trigger.trigger == "ready"
    assert trigger.trigger_key == f"ready:acme/widgets#7@{SHA}"
    assert trigger.actor == "octocat"
    pr = trigger.pr
    assert (pr.forge, pr.repo, pr.number, pr.head_sha, pr.base_ref, pr.head_ref) == (
        "github",
        "acme/widgets",
        7,
        SHA,
        "main",
        "feature/login",
    )
    assert pr.from_fork is False and pr.is_open is True


def test_github_opened_non_draft_is_a_trigger() -> None:
    assert ready_trigger_from_github(github_review_pull_request(action="opened"), "opened") is not None


@pytest.mark.parametrize("draft", [True, None])
def test_github_opened_as_draft_or_without_the_flag_is_not(draft: bool | None) -> None:
    payload = github_review_pull_request(action="opened", draft=draft)
    assert ready_trigger_from_github(payload, "opened") is None


@pytest.mark.parametrize("action", ["synchronize", "edited", "reopened", "converted_to_draft", "closed"])
def test_other_github_actions_are_not(action: str) -> None:
    assert ready_trigger_from_github(github_review_pull_request(action=action), action) is None


def test_a_closed_pull_request_is_not_reviewed() -> None:
    payload = github_review_pull_request(state="closed")
    assert ready_trigger_from_github(payload, "ready_for_review") is None


@pytest.mark.parametrize("sha", ["", "not-a-sha", "A" * 3])
def test_a_github_pull_request_without_a_usable_head_sha_is_not(sha: str) -> None:
    assert ready_trigger_from_github(github_review_pull_request(head_sha=sha), "ready_for_review") is None


@pytest.mark.parametrize(
    ("head_repo", "expected"),
    [("acme/widgets", False), ("ACME/Widgets", False), ("someone/widgets", True), (None, True)],
)
def test_github_fork_detection_fails_closed(head_repo: str | None, expected: bool) -> None:
    pr = pr_from_github(github_review_pull_request(head_repo=head_repo)["pull_request"], repo="acme/widgets")
    assert pr is not None and pr.from_fork is expected, f"head repo {head_repo!r} → from_fork={expected}"


# ---- GitLab ready ----------------------------------------------------------


def test_gitlab_draft_to_ready_is_a_trigger() -> None:
    trigger = ready_trigger_from_gitlab(gitlab_merge_request())
    assert trigger is not None
    assert trigger.trigger_key == f"ready:acme/products/web#5@{'c' * 40}"
    assert (trigger.pr.forge, trigger.pr.repo, trigger.pr.number, trigger.pr.from_fork) == (
        "gitlab",
        "acme/products/web",
        5,
        False,
    )
    assert trigger.actor == "jdoe"


def test_gitlab_opened_non_draft_is_a_trigger() -> None:
    assert ready_trigger_from_gitlab(gitlab_merge_request(action="open", previous_draft=None)) is not None


def test_gitlab_opened_as_draft_is_not() -> None:
    assert ready_trigger_from_gitlab(gitlab_merge_request(action="open", draft=True, previous_draft=None)) is None


def test_gitlab_update_that_leaves_it_draft_is_not() -> None:
    assert ready_trigger_from_gitlab(gitlab_merge_request(draft=True, previous_draft=None)) is None
    assert ready_trigger_from_gitlab(gitlab_merge_request(draft=False, previous_draft=False)) is None


def test_gitlab_legacy_work_in_progress_to_ready_is_a_trigger() -> None:
    assert ready_trigger_from_gitlab(gitlab_merge_request(legacy_wip=True)) is not None, (
        "older GitLab spells the flag work_in_progress; marking ready must still trigger"
    )


def test_gitlab_legacy_work_in_progress_open_is_not() -> None:
    payload = gitlab_merge_request(action="open", legacy_wip=True, draft=True, previous_draft=None)
    assert ready_trigger_from_gitlab(payload) is None


def test_gitlab_fork_is_detected_from_project_ids() -> None:
    trigger = ready_trigger_from_gitlab(gitlab_merge_request(source_project_id=9, target_project_id=1))
    assert trigger is not None and trigger.pr.from_fork is True


def test_a_gitlab_issue_event_is_not_a_review() -> None:
    assert ready_trigger_from_gitlab(gitlab_issue()) is None


# ---- commands --------------------------------------------------------------


def test_github_pr_comment_command_is_parsed() -> None:
    command = command_from_github(
        github_issue_comment(body="/embry0 review", repo="acme/widgets", number=7, on_pull_request=True)
    )
    assert command is not None
    assert (command.forge, command.repo, command.number, command.comment_id, command.author) == (
        "github",
        "acme/widgets",
        7,
        "1001",
        "octocat",
    )
    assert command.github_comment is not None and command.github_comment["author_association"] == "OWNER"


def test_github_tag_only_comment_is_not_a_command() -> None:
    assert command_from_github(github_issue_comment(body="@embry0 fix it", on_pull_request=True)) is None


def test_github_issue_comment_command_on_an_issue_is_not() -> None:
    assert command_from_github(github_issue_comment(body="/embry0 review", on_pull_request=False)) is None


def test_gitlab_mr_note_command_is_parsed() -> None:
    command = command_from_gitlab(gitlab_mr_note())
    assert command is not None
    assert (
        command.forge,
        command.repo,
        command.number,
        command.comment_id,
        command.author,
        command.gitlab_user_id,
    ) == (
        "gitlab",
        "acme/products/web",
        5,
        "901",
        "jdoe",
        42,
    )
    assert command.gitlab_mr is not None and command.gitlab_mr["iid"] == 5


@pytest.mark.parametrize(
    "payload",
    [
        gitlab_mr_note(note="@embry0 fix it"),
        gitlab_mr_note(system=True),
        gitlab_mr_note(noteable_type="Issue"),
        gitlab_issue(),
    ],
    ids=["tag-only", "system-note", "issue-note", "issue-event"],
)
def test_other_gitlab_notes_are_not_commands(payload: dict[str, Any]) -> None:
    assert command_from_gitlab(payload) is None


# ---- keys and branches -----------------------------------------------------


def test_keys_have_the_documented_shapes() -> None:
    assert ready_key("acme/widgets", 7, SHA) == f"ready:acme/widgets#7@{SHA}"
    assert command_key("gitlab", "901") == "command:gitlab:901"


def test_embry0_branches_are_recognised() -> None:
    assert is_embry0_branch("embry0/iss-abcd1234-fix") is True
    assert is_embry0_branch("feature/embry0/x") is False
