"""Done when (spec 2026-10-26): the developer works to the list, embry0 checks its report, review and the pause use it."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import embry0.workflows.issue_to_pr.nodes as nodes_module
from embry0.services.done_when import parse_done_when
from embry0.workflows.issue_to_pr.nodes._done_when import RETRY_STAGE, UNMET_STAGE, check_done_when

LIST = parse_done_when(
    {
        "items": [
            {"done": "The boot failure's cause is logged in full", "shown_by": "a test with a bad config"},
            {"done": "The cause is written down", "shown_by": "a PR section"},
        ],
        "out_of_scope": ["Deploy tooling"],
    }
)


def _result(report: Any) -> dict[str, Any]:
    final = {"pr_url": None, "branch": "b", "summary": "s"}
    if report is not None:
        final["done_when"] = report
    return {"agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "done\n" + json.dumps(final)}]}


MET = [
    {"id": "d1", "met": True, "evidence": "test_bad_config passes"},
    {"id": "d2", "met": True, "evidence": "PR 'Cause'"},
]


@pytest.mark.asyncio
async def test_every_item_met_lets_the_route_go_on_and_keeps_the_report() -> None:
    jobs = MagicMock(update=AsyncMock())
    updates, goto = await check_done_when(
        {"job_id": "J"}, {"jobs_repo": jobs}, _result(MET), LIST, writer=lambda _e: None
    )
    assert goto is None
    assert updates["done_when"]["report"]["unmet"] == [] and updates["done_when_followup"] is None
    jobs.update.assert_awaited_once_with("J", done_when=updates["done_when"])


@pytest.mark.asyncio
async def test_an_unmet_item_sends_the_coding_step_once_more_naming_it() -> None:
    report = [MET[0], {"id": "d2", "met": False, "evidence": "not written yet"}]
    updates, goto = await check_done_when({"job_id": "J"}, {}, _result(report), LIST, writer=lambda _e: None)
    assert goto == "developer" and updates["current_stage"] == RETRY_STAGE
    assert updates["done_when_retries"] == 1
    assert "[d2] The cause is written down" in updates["done_when_followup"]
    assert "[d1]" not in updates["done_when_followup"]


@pytest.mark.asyncio
async def test_still_unmet_after_the_retry_pauses_the_job_naming_the_items() -> None:
    updates, goto = await check_done_when(
        {"job_id": "J", "done_when_retries": 1, "errors": []}, {}, _result(None), LIST, writer=lambda _e: None
    )
    assert goto == "max_retries" and updates["current_stage"] == UNMET_STAGE
    assert "The boot failure's cause is logged in full; The cause is written down" in updates["errors"][-1]
    assert updates["done_when"]["report"]["reported"] is False


@pytest.mark.asyncio
async def test_a_job_without_a_list_changes_nothing() -> None:
    assert await check_done_when({"job_id": "J"}, {}, _result(None), None, writer=lambda _e: None) == ({}, None)


def _dev_state(**over: Any) -> dict[str, Any]:
    return {
        "job_id": "J",
        "issue_id": "iss-abc12345",
        "repo": "o/r",
        "task": "test task",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
        **over,
    }


@pytest.mark.asyncio
async def test_the_developer_gets_the_list_as_its_contract_and_an_unreported_run_goes_again() -> None:
    from embry0.workflows.issue_to_pr.nodes import developer_node

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=["", "abc\trefs/heads/embry0/iss-abc12345-test-task"])
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    run = AsyncMock(return_value={**_result(None), "events": [], "total_cost_usd": 0.05})
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _dev_state(done_when=LIST),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )
    prompt = run.await_args.kwargs["prompt"]
    assert "## Done when (your finish line)" in prompt and "[d2] The cause is written down" in prompt
    assert run.await_args.kwargs["state"]["done_when"] == LIST
    assert cmd.goto == "developer" and cmd.update["current_stage"] == RETRY_STAGE
    assert "no done_when report" in cmd.update["done_when_followup"]


@pytest.mark.asyncio
async def test_a_person_s_edit_on_the_row_is_the_next_run_s_contract() -> None:
    from embry0.workflows.issue_to_pr.nodes import developer_node

    mine = parse_done_when({"items": [{"done": "Only the header row"}]}, source="person")
    jobs = MagicMock(get=AsyncMock(return_value={"done_when": mine}), update=AsyncMock())
    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=["", "abc\trefs/heads/embry0/iss-abc12345-test-task"])
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    met = [{"id": "d1", "met": True, "evidence": "tests/test_export.py passes"}]
    run = AsyncMock(return_value={**_result(met), "events": [], "total_cost_usd": 0.05})
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _dev_state(done_when=LIST),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker, "jobs_repo": jobs}},
        )
    prompt = run.await_args.kwargs["prompt"]
    assert "Only the header row" in prompt and "The cause is written down" not in prompt
    assert cmd.goto == "review" and cmd.update["done_when"]["report"]["unmet"] == []


def test_the_pause_names_the_unmet_items() -> None:
    import asyncio

    seen: dict[str, Any] = {}

    def _interrupt(summary: dict[str, Any]) -> Any:
        seen.update(summary)
        return {"choice": "abandon"}

    listed = {**LIST, "report": {"items": [], "unmet": ["d2"], "reported": True}}
    state = {"job_id": "J", "current_stage": UNMET_STAGE, "done_when": listed, "retry_count": 0}

    async def go() -> None:
        with (
            patch.object(nodes_module, "get_stream_writer", return_value=lambda _: None),
            patch.object(nodes_module, "interrupt", new=_interrupt),
        ):
            await nodes_module.max_retries_node(state, config=None)

    asyncio.run(go())
    assert seen["message"] == "Done-when items still not met after a retry: The cause is written down"


def test_review_judges_against_the_list_and_the_report() -> None:
    from embry0.workflows.issue_to_pr.nodes._review import format_review_task

    listed = {**LIST, "report": {"items": [{"id": "d1", "met": True, "evidence": "a test"}], "unmet": ["d2"]}}
    block, _step, _field = format_review_task({"task": "Fix\n\n## Acceptance criteria\n- old", "done_when": listed})
    assert "Done when (the job's finish line" in block and "[d1] met: a test" in block
    assert "Acceptance criteria:" not in block
    plain, _s, _f = format_review_task({"task": "Fix\n\n## Acceptance criteria\n- old"})
    assert "Acceptance criteria:\n- old" in plain


def test_only_the_coding_step_with_a_list_gets_the_pacing() -> None:
    from embry0.orchestration.nodes.agent import _done_when_pacing

    assert "by turn 30 commit" in _done_when_pacing({"done_when": LIST}, "developer", 60)
    assert "by turn 45 stop exploring" in _done_when_pacing({"done_when": LIST}, "developer", 60)
    assert _done_when_pacing({"done_when": LIST, "coding_agent": "slopmaster"}, "slopmaster", 40) != ""
    assert _done_when_pacing({"done_when": LIST}, "review", 60) == ""
    assert _done_when_pacing({}, "developer", 60) == ""


# --- #182: the job's done_when section, as it read it when it started --------------------


def _with(retries: int | None = None, headings: list[str] | None = None) -> dict[str, Any]:
    """A state holding the done_when section a job read at its start."""
    section: dict[str, Any] = {}
    if retries is not None:
        section["retries"] = retries
    if headings is not None:
        section["ticket_sections"] = headings
    return {"job_settings": {"done_when": section}}


_UNMET = [MET[0], {"id": "d2", "met": False, "evidence": "not written yet"}]


@pytest.mark.asyncio
async def test_with_no_retries_the_first_unmet_report_pauses_the_job() -> None:
    updates, goto = await check_done_when(
        {"job_id": "J", "errors": [], **_with(retries=0)}, {}, _result(_UNMET), LIST, writer=lambda _e: None
    )
    assert goto == "max_retries" and updates.get("current_stage") == UNMET_STAGE, (
        f"done_when.retries 0 must pause at the first unmet report, not send the work back: {goto}"
    )
    assert updates["errors"][-1] == "Done-when items still not met: The cause is written down"


@pytest.mark.asyncio
async def test_the_job_s_retries_send_the_work_back_that_many_times() -> None:
    gotos = []
    for done in range(3):
        updates, goto = await check_done_when(
            {"job_id": "J", "errors": [], "done_when_retries": done, **_with(retries=2)},
            {},
            _result(_UNMET),
            LIST,
            writer=lambda _e: None,
        )
        gotos.append(goto)
    assert gotos == ["developer", "developer", "max_retries"], f"done_when.retries 2 sends the work back twice: {gotos}"
    assert updates["errors"][-1] == "Done-when items still not met after 2 retries: The cause is written down"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retries", "words"),
    [
        (0, "an unmet or unreported item pauses the job."),
        (3, "an unmet or unreported item sends the work back up to 3 times, then pauses the job."),
    ],
)
async def test_the_developer_s_contract_says_how_often_an_unmet_report_goes_back(retries: int, words: str) -> None:
    from embry0.workflows.issue_to_pr.nodes import developer_node

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=["", "abc\trefs/heads/embry0/iss-abc12345-test-task"])
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    run = AsyncMock(return_value={**_result(MET), "events": [], "total_cost_usd": 0.05})
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        await developer_node(
            _dev_state(done_when=LIST, **_with(retries=retries)),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )
    prompt = run.await_args.kwargs["prompt"]
    assert words in prompt, f"the contract must state the job's done_when.retries ({retries}):\n{prompt[-600:]}"


def test_the_pause_says_how_many_retries_ran() -> None:
    import asyncio

    seen: dict[str, Any] = {}

    def _interrupt(summary: dict[str, Any]) -> Any:
        seen.update(summary)
        return {"choice": "abandon"}

    listed = {**LIST, "report": {"items": [], "unmet": ["d2"], "reported": True}}
    state = {"job_id": "J", "current_stage": UNMET_STAGE, "done_when": listed, "retry_count": 0, **_with(retries=2)}

    async def go() -> None:
        with (
            patch.object(nodes_module, "get_stream_writer", return_value=lambda _: None),
            patch.object(nodes_module, "interrupt", new=_interrupt),
        ):
            await nodes_module.max_retries_node(state, config=None)

    asyncio.run(go())
    assert seen["message"] == "Done-when items still not met after 2 retries: The cause is written down"


def test_review_reads_the_ticket_s_criteria_under_the_job_s_headings() -> None:
    from embry0.workflows.issue_to_pr.nodes._review import format_review_task

    task = "Fix\n\n## Acceptance criteria\n- old\n\n## Definition of done\n- new"
    block, _step, _field = format_review_task({"task": task, **_with(headings=["Definition of done"])})
    assert "Acceptance criteria:\n- new" in block, f"review must read the job's heading, Definition of done:\n{block}"
    plain, _s, _f = format_review_task({"task": task})
    assert "Acceptance criteria:\n- old" in plain, "a job that read no settings reads Acceptance criteria"


def test_review_reads_the_section_the_ticket_s_list_comes_from() -> None:
    """#182 review: one rule for the ticket's list and review's criteria, the first heading with bullets."""
    from embry0.workflows.issue_to_pr.nodes._review import format_review_task

    task = "Fix\n\n## Definition of done\nWhen the export works.\n\n## Acceptance criteria\n- CSV has a header\n"
    block, _step, _field = format_review_task(
        {"task": task, **_with(headings=["Definition of done", "Acceptance criteria"])}
    )
    assert "Acceptance criteria:\n- CSV has a header" in block, (
        f"review must read the first heading whose section lists bullets, as the ticket's list does:\n{block}"
    )
    prose = "Fix\n\n## Acceptance criteria\nThe export has a header row.\n"
    plain, _s, _f = format_review_task({"task": prose})
    assert "Acceptance criteria:\nThe export has a header row." in plain, "criteria written as prose still reach review"
