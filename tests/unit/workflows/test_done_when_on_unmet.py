"""done_when.on_unmet (#182): what a job does once its done-when retries are spent and items are still unmet.

``pause`` is what every job did before the key, pinned byte for byte by
``test_unmet_and_salvage_defaults.py``. ``review`` hands the change to
review as it stands, which judges it against the list and the report; a
route with no review step left pauses instead, and the developer is told
what will really happen. ``fail`` ends the job with ``ERR_DONE_WHEN_UNMET``:
its branch is on origin, so the pull request of a job that did not finish
is opened for it, naming the items not met.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph import END

import embry0.workflows.issue_to_pr.nodes as nodes_module
from embry0.services.done_when import parse_done_when
from tests.unit.workflows.test_developer_salvage import _BRANCH, _Sandbox

LIST = parse_done_when(
    {
        "items": [
            {"done": "The boot failure's cause is logged in full", "shown_by": "a test with a bad config"},
            {"done": "The cause is written down", "shown_by": "a PR section"},
        ]
    }
)
UNMET = [
    {"id": "d1", "met": True, "evidence": "test_bad_config passes"},
    {"id": "d2", "met": False, "evidence": "not written yet"},
]
STEP = {"developer": "developer", "reviewer": "reviewer", "qa": "qa", "slopmaster": "slopmaster", "ops": "ops"}


def _plan(*agents: str) -> list[dict[str, Any]]:
    return [{"agent_type": STEP[a], "node_id": a, "config": {}} for a in agents]


def _settings(on_unmet: str | None = None, retries: int | None = None) -> dict[str, Any]:
    section: dict[str, Any] = {}
    if on_unmet is not None:
        section["on_unmet"] = on_unmet
    if retries is not None:
        section["retries"] = retries
    return {"job_settings": {"done_when": section}}


def _result(report: Any) -> dict[str, Any]:
    final: dict[str, Any] = {"pr_url": None, "branch": _BRANCH, "summary": "s"}
    if report is not None:
        final["done_when"] = report
    out = {"agent_type": "developer", "is_error": False, "output": "done\n" + json.dumps(final), "messages": []}
    return {"agent_outputs": [out], "events": [], "total_cost_usd": 0.05}


def _state(**over: Any) -> dict[str, Any]:
    return {
        "job_id": "J",
        "issue_id": "iss-abc12345",
        "repo": "o/r",
        "task": "test task",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
        "branch_name": _BRANCH,
        **over,
    }


def _check(state: dict[str, Any], report: Any = None) -> tuple[dict[str, Any], Any, list[dict[str, Any]]]:
    from embry0.workflows.issue_to_pr.nodes._done_when import check_done_when

    seen: list[dict[str, Any]] = []
    updates, goto = asyncio.run(
        check_done_when(state, {}, _result(UNMET if report is None else report), LIST, writer=seen.append)
    )
    return updates, goto, seen


async def _developer(state: dict[str, Any], report: Any = None) -> tuple[Any, str]:
    from embry0.workflows.issue_to_pr.nodes import developer_node

    run = AsyncMock(return_value=_result(UNMET if report is None else report))
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            state,
            {
                "configurable": {
                    "agent_runner": object(),
                    "credentials": {},
                    "docker": _Sandbox(dirty=False, on_origin=True),
                }
            },
        )
    return cmd, str(run.await_args.kwargs["prompt"])


def _pause(state: dict[str, Any]) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _interrupt(summary: dict[str, Any]) -> str:
        captured.update(summary)
        return "abandon"

    async def go() -> None:
        with (
            patch.object(nodes_module, "get_stream_writer", return_value=lambda _: None),
            patch.object(nodes_module, "interrupt", new=_interrupt),
        ):
            await nodes_module.max_retries_node(state, config=None)

    asyncio.run(go())
    return captured


# --- which way the route goes --------------------------------------------------------------


@pytest.mark.parametrize(
    ("plan", "cursor", "ahead"),
    [
        (None, 0, True),
        (_plan("developer", "reviewer", "qa"), 0, True),
        (_plan("developer", "qa", "reviewer"), 0, True),
        (_plan("developer", "qa"), 0, False),
        (_plan("developer"), 0, False),
        (_plan("developer", "ops"), 0, False),
        (_plan("slopmaster", "reviewer"), 0, True),
        # The review retry loop parks the cursor AT the review it re-enters.
        (_plan("developer", "reviewer", "qa"), 1, True),
        # Past every review: a QA failure's developer retry moves the cursor back to the coding step.
        (_plan("developer", "reviewer", "qa"), 2, False),
    ],
)
def test_a_review_is_ahead_only_when_the_route_still_holds_one(plan: Any, cursor: int, ahead: bool) -> None:
    from embry0.workflows.issue_to_pr import route_plan

    review_ahead = getattr(route_plan, "review_ahead", None)
    assert review_ahead is not None, "route_plan.review_ahead is missing: on_unmet review cannot tell a review is left"
    state = {"route_plan": plan, "route_cursor": cursor} if plan else {}
    assert review_ahead(state) is ahead, f"{plan and [s['agent_type'] for s in plan]} at {cursor}: review ahead?"


@pytest.mark.parametrize(
    ("on_unmet", "plan", "action"),
    [
        (None, None, "pause"),
        ("pause", _plan("developer", "reviewer"), "pause"),
        ("fail", _plan("developer"), "fail"),
        ("review", _plan("developer", "reviewer", "qa"), "review"),
        ("review", None, "review"),
        ("review", _plan("developer", "qa"), "pause"),
    ],
)
def test_the_action_is_the_job_s_on_unmet_and_review_needs_a_review_left(
    on_unmet: str | None, plan: Any, action: str
) -> None:
    try:
        from embry0.workflows.issue_to_pr.nodes._done_when import unmet_action
    except ImportError:
        pytest.fail("nodes._done_when.unmet_action is missing: what an unmet finish line does is still fixed")
    routed = {"route_plan": plan, "route_cursor": 0} if plan else {}
    assert unmet_action({**_settings(on_unmet), **routed}) == action


# --- the check ------------------------------------------------------------------------------


def test_fail_ends_the_job_with_its_own_code_once_the_retries_are_spent() -> None:
    from embry0.safety.error_codes import ErrorCode

    code = getattr(ErrorCode, "DONE_WHEN_UNMET", None)
    assert code is not None, "ErrorCode.DONE_WHEN_UNMET is missing: a job cannot fail on an unmet finish line"
    updates, goto, events = _check(_state(done_when_retries=1, errors=["an earlier error"], **_settings("fail")))
    assert goto == END, f"done_when.on_unmet fail must end the job, not pause it: {goto}"
    assert updates["current_stage"] == "done_when_unmet" and updates["error_code"] == code.value, updates
    assert updates["errors"] == ["Done-when items still not met after a retry: The cause is written down"], (
        f"JobState.errors appends: the fail returns its own entry, never the stored list again: {updates['errors']}"
    )
    assert [e["message"] for e in events][-1] == (
        "Done when: still not met after a retry: The cause is written down. The job fails, as its "
        "done_when.on_unmet says."
    )


@pytest.mark.parametrize("on_unmet", ["fail", "review"])
def test_the_retries_still_come_first(on_unmet: str) -> None:
    updates, goto, _ = _check(_state(done_when_retries=0, **_settings(on_unmet)))
    assert goto == "developer" and updates["done_when_retries"] == 1, (
        f"on_unmet {on_unmet} applies only once the retries are spent: {goto}"
    )


def test_review_lets_the_route_go_on_with_the_report_and_no_error() -> None:
    plan = {"route_plan": _plan("developer", "reviewer", "qa"), "route_cursor": 0}
    updates, goto, events = _check(_state(done_when_retries=1, **plan, **_settings("review")))
    assert goto is None, f"done_when.on_unmet review must let the route go on: {goto}"
    assert "current_stage" not in updates and "errors" not in updates and "error_code" not in updates, updates
    assert updates["done_when"]["report"]["unmet"] == ["d2"], "review reads the report naming the unmet item"
    assert updates["done_when"]["report"].get("on_unmet") == "review", (
        "the report must say the change went on with these items unmet, so its pull request lists them"
    )
    assert events[-1]["message"] == (
        "Done when: still not met after a retry: The cause is written down. The change goes on along its route as "
        "it stands, and review judges it against the list."
    ), "the event never says review comes next: a route may put QA first"


def test_a_report_handed_on_is_marked_on_the_job_row_too() -> None:
    from unittest.mock import MagicMock

    from embry0.workflows.issue_to_pr.nodes._done_when import check_done_when

    jobs = MagicMock(update=AsyncMock())
    plan = {"route_plan": _plan("developer", "qa", "reviewer"), "route_cursor": 0}
    asyncio.run(
        check_done_when(
            _state(done_when_retries=1, **plan, **_settings("review")),
            {"jobs_repo": jobs},
            _result(UNMET),
            LIST,
            writer=lambda _e: None,
        )
    )
    stored = jobs.update.await_args.kwargs["done_when"]
    assert stored["report"].get("on_unmet") == "review", f"a resumed job's pull request reads the row: {stored}"


@pytest.mark.parametrize("on_unmet", ["pause", "fail"])
def test_only_a_report_handed_to_review_is_marked(on_unmet: str) -> None:
    updates, _, _ = _check(_state(done_when_retries=1, **_settings(on_unmet)))
    assert "on_unmet" not in updates["done_when"]["report"], updates["done_when"]["report"]


def test_a_run_that_ended_in_an_error_keeps_its_own_failure() -> None:
    """N3: a turn-cap death's report is missing; failing the job as a missed finish line would hide the turn cap."""
    errored = {
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": True,
                "error_code": "ERR_AGENT_MAX_TURNS",
                "error_message": "reached max_turns (120)",
                "output": "",
            }
        ]
    }
    from embry0.workflows.issue_to_pr.nodes._done_when import check_done_when

    updates, goto = asyncio.run(
        check_done_when(_state(done_when_retries=1, **_settings("fail")), {}, errored, LIST, writer=lambda _e: None)
    )
    assert goto == END and updates["current_stage"] == "done_when_unmet", (goto, updates)
    assert "error_code" not in updates, (
        f"the job must be classified by its run's own error, not as ERR_DONE_WHEN_UNMET: {updates.get('error_code')}"
    )


def test_review_with_no_review_left_pauses_and_the_pause_says_why() -> None:
    plan = {"route_plan": _plan("developer", "qa"), "route_cursor": 0}
    updates, goto, _ = _check(_state(done_when_retries=1, **plan, **_settings("review")))
    assert goto == "max_retries" and updates["current_stage"] == "done_when_unmet", (
        f"with no review step left, unmet work must not go on unjudged: {goto}"
    )
    listed = {**LIST, "report": {"items": [], "unmet": ["d2"], "reported": True}}
    summary = _pause(
        _state(current_stage="done_when_unmet", done_when=listed, retry_count=0, **plan, **_settings("review"))
    )
    assert summary["message"] == (
        "Done-when items still not met after a retry: The cause is written down. done_when.on_unmet hands such a "
        "change to review, but this job's route has no review step left"
    ), summary["message"]
    assert summary["options"] == ["continue_retrying", "merge_as_is", "abandon"]


# --- through the developer node ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_goes_to_review_through_the_developer_node() -> None:
    state = _state(
        done_when=LIST,
        done_when_retries=1,
        route_plan=_plan("developer", "reviewer", "qa"),
        route_cursor=0,
        **_settings("review"),
    )
    cmd, _ = await _developer(state)
    assert cmd.goto == "review", f"done_when.on_unmet review: the next step is the review: {cmd.goto}"
    assert cmd.update["route_cursor"] == 1 and cmd.update["current_stage"] == "developer_complete"
    assert cmd.update["done_when"]["report"]["unmet"] == ["d2"]


@pytest.mark.asyncio
async def test_review_on_a_job_with_no_plan_takes_the_fixed_hop_to_review() -> None:
    cmd, _ = await _developer(_state(done_when=LIST, done_when_retries=1, **_settings("review")))
    assert cmd.goto == "review", cmd.goto


@pytest.mark.asyncio
async def test_fail_ends_the_graph_from_the_developer_node() -> None:
    from embry0.safety.error_codes import ErrorCode

    state = _state(done_when=LIST, done_when_retries=1, route_plan=_plan("developer", "reviewer"), **_settings("fail"))
    cmd, _ = await _developer(state)
    assert cmd.goto == END, f"done_when.on_unmet fail ends the job: {cmd.goto}"
    assert cmd.update["error_code"] == ErrorCode.DONE_WHEN_UNMET.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("on_unmet", "retries", "plan", "words"),
    [
        ("review", 1, ("developer", "reviewer"), "sends the work back once, then hands it to review as it stands."),
        ("review", 0, ("developer", "reviewer"), "hands the work to review as it stands."),
        ("fail", 2, ("developer", "reviewer"), "sends the work back up to 2 times, then fails the job."),
        ("fail", 0, ("developer",), "fails the job."),
        # The contract never promises a review the route does not have.
        ("review", 1, ("developer", "qa"), "sends the work back once, then pauses the job."),
        ("pause", 1, ("developer", "reviewer"), "sends the work back once, then pauses the job."),
    ],
)
async def test_the_developer_is_told_what_an_unmet_report_will_do(
    on_unmet: str, retries: int, plan: tuple[str, ...], words: str
) -> None:
    state = _state(done_when=LIST, route_plan=_plan(*plan), route_cursor=0, **_settings(on_unmet, retries=retries))
    _, prompt = await _developer(state, report=[{"id": "d1", "met": True, "evidence": "e"}])
    assert prompt.endswith(f"embry0 checks this report: an unmet or unreported item {words}"), (
        f"the contract must say what done_when.on_unmet {on_unmet} does after {retries} retries:\n{prompt[-400:]}"
    )


# --- a failed job: its code, its words and its pull request -------------------------------------


def test_the_code_is_a_verification_failure_with_words_of_its_own() -> None:
    from embry0.notifications.public_errors import public_error_message
    from embry0.safety.error_codes import ErrorCode, FailureCategory, category_for_code
    from embry0.services._executor_outcomes import _stopped_because
    from embry0.services.world import failure_words

    code = getattr(ErrorCode, "DONE_WHEN_UNMET", None)
    assert code is not None, "ErrorCode.DONE_WHEN_UNMET is missing"
    assert category_for_code(code.value) is FailureCategory.VERIFICATION
    words = "its coding runs did not meet every item of its done-when list"
    assert _stopped_because(code.value) == words, "not 'did not pass review or QA': neither ran"
    assert failure_words(code.value) == words
    assert public_error_message(code.value) == (
        "the change did not meet every item of its done-when list within the allowed retries"
    )


@pytest.mark.asyncio
async def test_a_job_failed_on_its_finish_line_gets_a_human_merge_only_pull_request_naming_the_items() -> None:
    from embry0.safety.error_codes import ErrorCode
    from tests.unit.services.test_failed_job_pr import BRANCH, _result_outcome, _terminal

    code = getattr(ErrorCode, "DONE_WHEN_UNMET", None)
    assert code is not None, "ErrorCode.DONE_WHEN_UNMET is missing"
    listed = {**LIST, "report": {"items": UNMET, "unmet": ["d2"], "reported": True}}
    ex, opened = await _result_outcome(
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "done_when_unmet",
            "error_code": code.value,
            "errors": ["Done-when items still not met after a retry: The cause is written down"],
            "branch_name": BRANCH,
            "done_when": listed,
            "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "done"}],
        }
    )
    terminal = _terminal(ex)
    assert (terminal["status"], terminal["error_code"]) == ("failed", code.value), terminal
    assert terminal["error_message"] == "Done-when items still not met after a retry: The cause is written down"
    kwargs = opened.await_args.kwargs
    assert kwargs["labels"] == ["embry0", "human-merge-only"], kwargs
    body = kwargs["body"]
    assert "**embry0 did not finish this job:** its coding runs did not meet every item of its done-when list." in body
    assert "Done-when items not met:\n- The cause is written down (shown by: a PR section)" in body, body
    assert "The boot failure's cause" not in body, f"a met item is not listed as unmet:\n{body}"
