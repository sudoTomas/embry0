"""What a job's change request says when the job delivers unfinished work (#182 review).

- "Merge as is" at a pause where salvage committed the coding run's work
  completes the job with a branch that ends in embry0's "Do not merge it as it
  is" commit: its pull request is opened ``human-merge-only``, saying so.
- A job whose ``done_when.on_unmet`` is ``fail`` and that already has a pull
  request (a follow-up's, or an earlier failed job's it continued) gets that
  label and a sticky comment naming the unmet items there.
- A job whose ``done_when.on_unmet`` handed the change to review with items
  unmet lists them in the pull request it is delivered in.
- A coding run that ended in an error keeps its own failure; the pull request
  still lists the items it left unmet.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import embry0.workflows.issue_to_pr.nodes as nodes_module
from embry0.safety.error_codes import ErrorCode
from embry0.services.done_when import parse_done_when
from tests.unit.services.test_failed_job_pr import BRANCH, PR, _executor, _patches, _terminal

LIST = parse_done_when(
    {
        "items": [
            {"done": "The boot failure's cause is logged in full", "shown_by": "a test with a bad config"},
            {"done": "The cause is written down", "shown_by": "a PR section"},
        ]
    }
)
UNMET = {
    "items": [{"id": "d1", "met": True, "evidence": "a test"}, {"id": "d2", "met": False, "evidence": "not yet"}],
    "unmet": ["d2"],
    "reported": True,
}
SALVAGED = {"branch": BRANCH, "agent": "developer", "reason": "turn cap", "committed": True}
UNMET_LINE = "Done-when items not met:\n- The cause is written down (shown by: a PR section)"
EXISTING = "https://github.com/acme/app/pull/3"


class _Notes:
    """The sticky note's forge calls (``embry0.services.unfinished_note``), recorded."""

    def __init__(self) -> None:
        self.posted: list[dict[str, Any]] = []
        self.settled: list[tuple[str, str]] = []

    async def post(self, pr_url: str, parts: list[str], *, human_merge_only: bool, access: Any) -> bool:
        self.posted.append({"pr_url": pr_url, "body": "\n\n".join(parts), "human_merge_only": human_merge_only})
        return True

    async def settle(self, pr_url: str, job_id: str, *, access: Any) -> bool:
        self.settled.append((pr_url, job_id))
        return True

    @property
    def await_count(self) -> int:
        return len(self.posted)

    def assert_not_awaited(self) -> None:
        assert not self.posted, f"no note was to be posted: {self.posted}"


async def _outcome(result: dict[str, Any], row: dict[str, Any] | None = None) -> tuple[Any, AsyncMock, _Notes]:
    ex = _executor()
    if row is not None:
        ex._jobs.get = AsyncMock(return_value={"repo": "acme/app", "task": "Fix the idle timer", **row})
    opened = AsyncMock(return_value=PR)
    notes = _Notes()
    a, b, c, d = _patches(opened)
    with a, b, c, d, _note_calls(notes):
        await ex._handle_workflow_result("iss-1", "job-1", {"triage_decision": {"action": "proceed"}, **result})
    return ex, opened, notes


def _note_calls(notes: _Notes) -> Any:
    """Record the note's forge calls; on a tree from before ``unfinished_note``, the call that did its posting."""
    from contextlib import ExitStack
    from importlib.util import find_spec

    stack = ExitStack()
    if find_spec("embry0.services.unfinished_note") is not None:
        stack.enter_context(patch("embry0.services.unfinished_note.post_unfinished_note", new=notes.post))
        stack.enter_context(patch("embry0.services.unfinished_note.settle_unfinished_note", new=notes.settle))
    else:

        async def note_change_request(*, pr_url: str, body: str, human_merge_only: bool, **_kw: Any) -> bool:
            notes.posted.append({"pr_url": pr_url, "body": body, "human_merge_only": human_merge_only})
            return True

        stack.enter_context(
            patch("embry0.services.verification_publish.note_change_request", new=note_change_request, create=True)
        )
    return stack


def _merge_as_is(state: dict[str, Any]) -> dict[str, Any]:
    """What ``max_retries_node`` returns when a person answers "merge as is" at ``state``'s pause."""

    async def go() -> dict[str, Any]:
        with (
            patch.object(nodes_module, "get_stream_writer", return_value=lambda _: None),
            patch.object(nodes_module, "interrupt", new=lambda _summary: {"choice": "merge_as_is"}),
        ):
            return dict(await nodes_module.max_retries_node(state, config=None))

    return asyncio.run(go())


# --- "merge as is" after a salvage commit ---------------------------------------------------


@pytest.mark.asyncio
async def test_merge_as_is_after_a_salvage_commit_opens_a_human_merge_only_pull_request_saying_why() -> None:
    after = await asyncio.to_thread(
        _merge_as_is,
        {
            "job_id": "job-1",
            "current_stage": "dev_run_unfinished",
            "unfinished_work": SALVAGED,
            "pr_url": "",
            "branch_name": BRANCH,
        },
    )
    # The stage stays the pause's until deliver_as_is_node opens the change request and completes the job, or
    # sends it back to this pause (#459).
    assert after == {"merged_as_is": True, "unfinished_work": SALVAGED}, (
        f"a resume's result holds only the nodes' updates: the salvage record must go with this one: {after}"
    )
    # A resumed run's result: the resume's updates plus the carried keys (_execute_checkpointed).
    ex, opened, _ = await _outcome({**after, "branch_name": BRANCH})
    assert _terminal(ex)["status"] == "completed"
    kwargs = opened.await_args.kwargs
    assert kwargs["labels"] == ["embry0", "human-merge-only"], (
        f"a branch ending in embry0's 'Do not merge it as it is' commit must not be merged by anyone but a person: "
        f"{kwargs['labels']}"
    )
    assert (
        "**embry0 did not finish this job:** its developer stopped (turn cap) before it committed, embry0 committed "
        "what it left, and a person chose to deliver the branch as it is, ending in that unfinished commit. The work "
        "it pushed is here so it is not lost. Review it before merging; close it unmerged to let a later run start "
        "over."
    ) in kwargs["body"], kwargs["body"]


@pytest.mark.asyncio
async def test_merge_as_is_with_no_salvage_commit_is_delivered_as_before() -> None:
    after = await asyncio.to_thread(
        _merge_as_is,
        {
            "job_id": "job-1",
            "current_stage": "review_retries_exhausted",
            "retry_count": 2,
            "pr_url": "",
            "branch_name": BRANCH,
        },
    )
    assert after == {"merged_as_is": True}, after
    _, opened, noted = await _outcome({**after, "branch_name": BRANCH})
    assert opened.await_args.kwargs["labels"] == ["embry0"]
    assert "did not finish" not in opened.await_args.kwargs["body"]
    noted.assert_not_awaited()


# --- done_when.on_unmet fail on a job that already has a pull request ---------------------------


def _failed_at_the_finish_line(**extra: Any) -> dict[str, Any]:
    return {
        "current_stage": "done_when_unmet",
        "error_code": ErrorCode.DONE_WHEN_UNMET.value,
        "errors": ["Done-when items still not met after a retry: The cause is written down"],
        "branch_name": BRANCH,
        "done_when": {**LIST, "report": UNMET},
        "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "done"}],
        **extra,
    }


@pytest.mark.asyncio
async def test_fail_on_a_job_that_has_a_pull_request_labels_it_and_comments_the_unmet_items() -> None:
    ex, opened, noted = await _outcome(_failed_at_the_finish_line(pr_url=EXISTING))

    opened.assert_not_awaited()
    assert noted.await_count == 1, "the pull request the job already had must say the work is unfinished"
    kwargs = noted.posted[-1]
    assert kwargs["pr_url"] == EXISTING and kwargs["human_merge_only"] is True, kwargs
    body = kwargs["body"]
    assert "**embry0 did not finish this job:** its coding runs did not meet every item of its done-when list." in body
    assert UNMET_LINE in body, body
    assert _terminal(ex)["error_code"] == ErrorCode.DONE_WHEN_UNMET.value


@pytest.mark.asyncio
async def test_a_resumed_job_whose_pull_request_the_forge_finds_again_gets_the_comment_too() -> None:
    """A resume's result no longer names the pull request; the job row does, and the forge finds it again."""
    _, opened, noted = await _outcome(_failed_at_the_finish_line(), row={"pr_url": PR})

    assert opened.await_count == 1 and noted.await_count == 1, (opened.await_count, noted.await_count)
    assert noted.posted[-1]["pr_url"] == PR and UNMET_LINE in noted.posted[-1]["body"]


@pytest.mark.asyncio
async def test_a_failed_job_s_new_pull_request_gets_no_comment_its_body_says_it() -> None:
    _, opened, noted = await _outcome(_failed_at_the_finish_line())

    assert UNMET_LINE in opened.await_args.kwargs["body"]
    noted.assert_not_awaited()


# --- done_when.on_unmet review: the delivered pull request lists the unmet items -------------------


@pytest.mark.asyncio
async def test_a_change_review_approved_with_items_unmet_lists_them_in_its_pull_request() -> None:
    report = {**UNMET, "on_unmet": "review"}
    # A resumed job's state does not hold the list: the job row does.
    ex, opened, noted = await _outcome(
        {"current_stage": "review_complete", "branch_name": BRANCH}, row={"done_when": {**LIST, "report": report}}
    )
    assert _terminal(ex)["status"] == "completed"
    kwargs = opened.await_args.kwargs
    assert UNMET_LINE in kwargs["body"], (
        f"the delivered pull request must list the items review let through:\n{kwargs['body']}"
    )
    assert kwargs["labels"] == ["embry0"], "review approved it: it is a delivery, not a failed job's"
    assert "did not finish" not in kwargs["body"]
    noted.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_an_existing_pull_request_the_list_is_a_comment_without_the_label() -> None:
    report = {**UNMET, "on_unmet": "review"}
    _, opened, noted = await _outcome(
        {
            "current_stage": "review_complete",
            "branch_name": BRANCH,
            "pr_url": EXISTING,
            "done_when": {**LIST, "report": report},
        }
    )
    opened.assert_not_awaited()
    assert noted.await_count == 1, "the pull request the job already had must list the items review let through"
    kwargs = noted.posted[-1]
    assert kwargs["human_merge_only"] is False and UNMET_LINE in kwargs["body"], kwargs
    assert "did not finish" not in kwargs["body"]


@pytest.mark.asyncio
async def test_a_report_with_items_unmet_but_not_handed_on_changes_nothing() -> None:
    """A person's "merge as is" at a done_when_unmet pause is delivered as it always was."""
    _, opened, noted = await _outcome(
        {"current_stage": "completed", "branch_name": BRANCH}, row={"done_when": {**LIST, "report": UNMET}}
    )
    assert "Done-when items not met" not in opened.await_args.kwargs["body"]
    assert opened.await_args.kwargs["labels"] == ["embry0"]
    noted.assert_not_awaited()


# --- a run that ended in an error keeps its own failure --------------------------------------------


@pytest.mark.asyncio
async def test_a_turn_cap_death_at_the_finish_line_keeps_its_code_and_the_pull_request_lists_the_items() -> None:
    from embry0.workflows.issue_to_pr.nodes._done_when import check_done_when

    errored = {
        "agent_type": "developer",
        "is_error": True,
        "error_code": ErrorCode.AGENT_MAX_TURNS.value,
        "error_message": "reached max_turns (120)",
        "output": "",
    }
    state = {"job_id": "job-1", "done_when_retries": 1, "job_settings": {"done_when": {"on_unmet": "fail"}}}
    updates, _goto = await check_done_when(state, {}, {"agent_outputs": [errored]}, LIST, writer=lambda _e: None)
    ex, opened, _ = await _outcome({**updates, "branch_name": BRANCH, "agent_outputs": [errored]})

    terminal = _terminal(ex)
    assert (terminal["status"], terminal["error_code"]) == ("failed", ErrorCode.AGENT_MAX_TURNS.value), (
        f"the turn cap stopped the run: the job is classified by it, not as a missed finish line: {terminal}"
    )
    assert opened.await_args.kwargs["labels"] == ["embry0", "human-merge-only"]
    assert "Done-when items not met:" in opened.await_args.kwargs["body"]


# --- never raising, and which list ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("pr_url", [EXISTING, None])
async def test_a_report_of_another_shape_never_stops_the_job_being_recorded(pr_url: str | None) -> None:
    """N-a: a stored report that is no mapping (a hand edit, an older release) reads as listing nothing."""
    result = _failed_at_the_finish_line(done_when={**LIST, "report": ["not", "a", "mapping"]})
    if pr_url:
        result["pr_url"] = pr_url
    ex, opened, noted = await _outcome(result)
    assert _terminal(ex)["status"] == "failed", "the job's outcome is written whatever its report holds"
    if pr_url is None:
        assert opened.await_count == 1, "the failed job's pull request is still opened, listing nothing"
        assert "Done-when items not met" not in opened.await_args.kwargs["body"]
    noted.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_job_row_s_list_wins_over_the_state_s() -> None:
    """N-b: the row holds the latest report (a person's edit, a resumed job); the state may hold an older one."""
    older = {**UNMET, "unmet": ["d1"]}
    _, opened, _ = await _outcome(
        _failed_at_the_finish_line(done_when={**LIST, "report": older}),
        row={"done_when": {**LIST, "report": UNMET}},
    )
    body = opened.await_args.kwargs["body"]
    assert UNMET_LINE in body, f"the row's report names d2 unmet: {body}"
    assert "The boot failure's cause" not in body, f"the state's older report must not be listed: {body}"


@pytest.mark.asyncio
async def test_a_failed_job_lists_the_items_its_last_report_left_unmet_whatever_stopped_it() -> None:
    """N-c: a job that failed in QA after going on with items unmet says which, as one failed at its finish line."""
    _, opened, _ = await _outcome(
        {
            "current_stage": "qa_failed",
            "error_code": ErrorCode.QA_FAILURES_UNRESOLVED.value,
            "branch_name": BRANCH,
            "done_when": {**LIST, "report": {**UNMET, "on_unmet": "review"}},
            "agent_outputs": [{"agent_type": "qa", "is_error": False, "output": "2 failures"}],
        }
    )
    body = opened.await_args.kwargs["body"]
    assert "**embry0 did not finish this job:** the change did not pass review or QA." in body
    assert UNMET_LINE in body, f"the items the last report left unmet: {body}"


# --- a later job that finishes the work -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_later_job_that_finishes_on_the_pull_request_settles_the_note() -> None:
    """N-d: a job continuing the pull request an earlier one left unfinished edits that note when it finishes."""
    ex, opened, notes = await _outcome(
        {"current_stage": "review_complete", "branch_name": BRANCH, "pr_url": EXISTING}, row={"pr_url": EXISTING}
    )
    assert _terminal(ex)["status"] == "completed"
    opened.assert_not_awaited()
    assert notes.settled == [(EXISTING, "job-1")], (
        f"the earlier job's note must say the work is finished: {notes.settled}"
    )
    notes.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_pull_request_the_job_opened_itself_is_not_searched_for_a_note() -> None:
    """Only a change request the job had from its start (on its row) can carry an earlier job's note."""
    _, _, notes = await _outcome({"current_stage": "review_complete", "branch_name": BRANCH, "pr_url": EXISTING})
    assert notes.settled == [] and notes.posted == [], (notes.settled, notes.posted)


@pytest.mark.asyncio
async def test_a_later_job_that_fails_too_does_not_settle_the_note() -> None:
    _, _, notes = await _outcome(
        {
            "current_stage": "qa_failed",
            "error_code": ErrorCode.QA_FAILURES_UNRESOLVED.value,
            "branch_name": BRANCH,
            "pr_url": EXISTING,
            "agent_outputs": [{"agent_type": "qa", "is_error": False, "output": "2 failures"}],
        },
        row={"pr_url": EXISTING},
    )
    assert notes.settled == [], f"a job that did not finish leaves the note as it is: {notes.settled}"


@pytest.mark.asyncio
async def test_a_resumed_job_that_finishes_on_a_pull_request_the_forge_finds_again_settles_the_note() -> None:
    ex, opened, notes = await _outcome({"current_stage": "completed", "branch_name": BRANCH}, row={"pr_url": PR})
    assert opened.await_count == 1 and notes.settled == [(PR, "job-1")], (
        f"the pull request the forge found again is the job's from before: its note must be settled: {notes.settled}"
    )


# --- "merge as is" is not a finish --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["done_when_unmet", "review_retries_exhausted", "review_errored", "dev_branch_missing"]
)
async def test_a_person_s_merge_as_is_never_settles_an_earlier_job_s_note(stage: str) -> None:
    """S-1: "merge as is" delivers what stood; it does not finish what an earlier job left, so the note stays."""
    after = await asyncio.to_thread(
        _merge_as_is,
        {"job_id": "job-1", "current_stage": stage, "retry_count": 1, "pr_url": EXISTING, "done_when": None},
    )
    # The resume's result (no pull request named; the forge finds it again from the row) and a state that names it.
    for result, row in (
        ({**after, "branch_name": BRANCH}, {"pr_url": PR}),
        ({**after, "branch_name": BRANCH, "pr_url": EXISTING}, {"pr_url": EXISTING}),
    ):
        ex, _opened, notes = await _outcome(result, row=row)
        assert _terminal(ex)["status"] == "completed"
        assert notes.settled == [], (
            f"{stage}: a person's merge as is must not say the work was finished: {notes.settled}"
        )
    assert after.get("merged_as_is") is True, f"the pause says the job was merged as it stood: {after}"


@pytest.mark.asyncio
async def test_a_failed_job_whose_pull_request_the_forge_finds_again_does_not_settle_the_note() -> None:
    _, opened, notes = await _outcome(
        {
            "current_stage": "qa_failed",
            "error_code": ErrorCode.QA_FAILURES_UNRESOLVED.value,
            "branch_name": BRANCH,
            "agent_outputs": [{"agent_type": "qa", "is_error": False, "output": "2 failures"}],
        },
        row={"pr_url": PR},
    )
    assert opened.await_count == 1
    assert notes.settled == [] and notes.posted == [], (notes.settled, notes.posted)
