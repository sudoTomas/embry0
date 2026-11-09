"""salvage.enabled false (#182): embry0 commits nothing for a stopped coding run.

A repository that takes no commit embry0 wrote (signed commits only, or no
unreviewed work pushed in embry0's name) turns salvage off. Then a stopped
run's leftover changes are not committed: they stay in the job's sandbox,
which a paused job keeps, and the job pauses at ``dev_run_uncommitted``,
where "merge as is" is not offered. That is where the salvage commit would
have paused it, and also where salvage on could not make its commit (it
failed, or another branch is checked out while the job's branch exists) and
lets the job go on: with salvage off the changes are still in the sandbox.
Commits the agent made itself are still pushed, as for any run. ``true``,
the default, is pinned byte for byte by
``test_unmet_and_salvage_defaults.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures.sandbox_git_local import readable
from tests.unit.workflows.test_developer_salvage import (
    _BRANCH,
    _finished,
    _make_state,
    _pause_summary,
    _run,
    _Sandbox,
    _stopped,
)

OFF = {"job_settings": {"salvage": {"enabled": False}}}


def _git(sandbox: _Sandbox) -> list[str]:
    return [readable(c) for c in sandbox.calls]


def _wrote(sandbox: _Sandbox) -> list[str]:
    """The git commands that change the repository: what salvage off must never run."""
    return [g for g in _git(sandbox) if any(f"git {verb}" in g for verb in ("add", "commit", "switch", "stash"))]


@pytest.mark.asyncio
async def test_a_stopped_run_s_changes_stay_uncommitted_and_the_job_pauses_saying_where() -> None:
    from embry0.safety.error_codes import ErrorCode

    sandbox = _Sandbox()
    cmd = await _run(_stopped("reached max_turns (120)", error_code="ERR_AGENT_MAX_TURNS"), sandbox, **OFF)

    assert not sandbox.messages and not _wrote(sandbox), (
        f"salvage.enabled false: embry0 must commit nothing for the stopped run: {_wrote(sandbox)}"
    )
    assert any("status" in g and "--porcelain" in g for g in _git(sandbox)), "it still looks for uncommitted changes"
    assert cmd.goto == "max_retries", "a cut-off run is never a delivery, committed or not"
    assert cmd.update["current_stage"] == "dev_run_uncommitted", cmd.update.get("current_stage")
    assert cmd.update["error_code"] == ErrorCode.DEV_RUN_UNFINISHED.value
    assert cmd.update["unfinished_work"] == {
        "branch": _BRANCH,
        "agent": "developer",
        "reason": "turn cap",
        "committed": False,
        "uncommitted": True,
        "pushed": False,
    }
    assert cmd.update["errors"][-1] == (
        "The developer stopped (turn cap) before committing. This repository's salvage.enabled is false, so embry0 "
        "did not commit its work: the changes are UNCOMMITTED in this paused job's sandbox. Continue the job to let "
        "the next run finish them; stopping it, or letting the pause expire, discards them."
    )


@pytest.mark.asyncio
async def test_the_agent_s_own_commits_are_still_pushed() -> None:
    sandbox = _Sandbox()
    sandbox.local_commits = 2
    cmd = await _run(_stopped("Agent timed out", error_code="ERR_AGENT_TIMEOUT"), sandbox, **OFF)

    assert sandbox.on_origin and sandbox.local_commits == 0, "the agent's own commits are pushed, as for any run"
    assert not sandbox.messages, "but nothing of embry0's is committed"
    assert cmd.update["current_stage"] == "dev_run_uncommitted", "what is left uncommitted still pauses the job"
    assert cmd.update["unfinished_work"]["pushed"] is True


class _OtherBranch(_Sandbox):
    """Another branch checked out while the job's branch exists: salvage on refuses to guess where to commit."""

    async def run_cmd(self, cmd: list[str], timeout: float | None = None, **kwargs: Any) -> str:
        joined = readable(cmd)
        if "rev-parse" in joined and "--verify" in joined and "refs/heads/" in joined:
            self.calls.append(cmd)
            return "sha\n"
        return await super().run_cmd(cmd, timeout, **kwargs)


def _sandbox(kind: str, kwargs: dict[str, Any]) -> _Sandbox:
    return _OtherBranch(**kwargs) if kind == "other branch" else _Sandbox(**kwargs)


_UNCOMMITTED = ("max_retries", "dev_run_uncommitted")


@pytest.mark.parametrize(
    ("label", "kind", "sandbox_kwargs", "local_commits", "result", "differs"),
    [
        ("dirty, never pushed", "", {}, 0, _stopped("max turns", error_code="ERR_AGENT_MAX_TURNS"), None),
        (
            "dirty, pushed earlier",
            "",
            {"on_origin": True},
            0,
            _stopped("timed out", error_code="ERR_AGENT_TIMEOUT"),
            None,
        ),
        ("dirty, own commits unpushed", "", {}, 1, _stopped("Agent timed out"), None),
        ("clean, pushed", "", {"dirty": False, "on_origin": True}, 0, _stopped("Agent timed out"), None),
        ("clean, own commits unpushed", "", {"dirty": False}, 2, _stopped("reached max_turns (120)"), None),
        ("clean, nothing", "", {"dirty": False}, 0, _stopped("reached max_turns (120)"), None),
        ("dirty, on the base branch", "", {"head": "main"}, 0, _stopped("reached max_turns (120)"), None),
        ("budget, dirty", "", {}, 0, _stopped("reached max_turns (120)", cost=60.0), None),
        ("clean finish, dirty, pushed", "", {"on_origin": True}, 0, _finished(), None),
        ("clean finish, own commits unpushed", "", {"dirty": False}, 1, _finished(), None),
        # Where salvage on could not make its commit, it goes on as if the run had left
        # nothing; salvage off pauses, since the changes are still in the sandbox.
        (
            "dirty, the commit fails, pushed earlier",
            "",
            {"fail_commit": True, "on_origin": True},
            0,
            _stopped("max turns", error_code="ERR_AGENT_MAX_TURNS"),
            (("review", "developer_complete"), _UNCOMMITTED),
        ),
        (
            "dirty, the commit fails, own commits unpushed",
            "",
            {"fail_commit": True},
            1,
            _stopped("max turns", error_code="ERR_AGENT_MAX_TURNS"),
            (("review", "developer_complete"), _UNCOMMITTED),
        ),
        (
            "dirty, another branch checked out, pushed earlier",
            "other branch",
            {"head": "other", "on_origin": True},
            0,
            _stopped("max turns", error_code="ERR_AGENT_MAX_TURNS"),
            (("review", "developer_complete"), _UNCOMMITTED),
        ),
        (
            "dirty, another branch checked out, never pushed",
            "other branch",
            {"head": "other"},
            1,
            _stopped("max turns", error_code="ERR_AGENT_MAX_TURNS"),
            (("max_retries", "dev_branch_missing"), _UNCOMMITTED),
        ),
    ],
)
@pytest.mark.asyncio
async def test_the_route_is_the_salvage_commit_s_but_where_it_could_not_be_made(
    label: str,
    kind: str,
    sandbox_kwargs: dict[str, Any],
    local_commits: int,
    result: dict[str, Any],
    differs: tuple[tuple[str, str], tuple[str, str]] | None,
) -> None:
    routes = {}
    for enabled in (True, False):
        sandbox = _sandbox(kind, sandbox_kwargs)
        sandbox.local_commits = local_commits
        cmd = await _run(result, sandbox, job_settings={"salvage": {"enabled": enabled}})
        routes[enabled] = (cmd.goto, cmd.update.get("current_stage"))
        if not enabled:
            assert not sandbox.messages and not _wrote(sandbox), f"{label}: salvage off committed {_wrote(sandbox)}"
    if differs is None:
        on_stage = "dev_run_uncommitted" if routes[True][1] == "dev_run_unfinished" else routes[True][1]
        assert routes[False] == (routes[True][0], on_stage), (
            f"{label}: salvage off must pause where the salvage commit pauses and go on where it goes on: {routes}"
        )
    else:
        assert (routes[True], routes[False]) == differs, (
            f"{label}: where salvage on could not commit it goes on as if nothing was left, and salvage off "
            f"still pauses with the changes in the sandbox: {routes}"
        )


@pytest.mark.asyncio
async def test_a_budget_stop_keeps_its_route_and_the_pause_says_the_work_is_in_the_sandbox() -> None:
    sandbox = _Sandbox()
    cmd = await _run(_stopped("reached max_turns (120)", cost=60.0), sandbox, **OFF)

    assert cmd.goto == "max_retries" and not sandbox.messages
    record = cmd.update["unfinished_work"]
    assert record["uncommitted"] is True and record["reason"] == "budget reached"
    summary = await _pause_summary(
        _make_state(current_stage=cmd.update["current_stage"], total_cost_usd=60.0, unfinished_work=record, **OFF)
    )
    assert summary["message"].endswith(
        " The developer's changes are UNCOMMITTED in this paused job's sandbox, not on its branch: this "
        "repository's salvage.enabled is false, so embry0 did not commit them. Stopping the job, or letting the "
        "pause expire, discards them."
    ), summary["message"]
    assert "budget" in summary["message"]


@pytest.mark.asyncio
async def test_a_questions_stop_commits_nothing_and_says_so() -> None:
    sandbox = _Sandbox()
    cmd = await _run(_finished(), sandbox, agent_questions_exhausted=True, **OFF)

    assert cmd.goto == "max_retries" and not sandbox.messages and not _wrote(sandbox)
    record = cmd.update["unfinished_work"]
    assert record["uncommitted"] is True and record["reason"] == "question rounds used up"
    summary = await _pause_summary(_make_state(agent_questions_exhausted=True, unfinished_work=record, pr_url=""))
    assert summary["message"].startswith("Agent exceeded the ask_user round cap — cannot continue. ")
    assert "UNCOMMITTED in this paused job's sandbox" in summary["message"]
    assert summary["options"] == ["abandon"]


@pytest.mark.asyncio
async def test_the_uncommitted_pause_names_the_agent_and_the_reason_and_offers_to_continue() -> None:
    record = {
        "branch": _BRANCH,
        "agent": "developer",
        "reason": "error ERR_AGENT_TIMEOUT",
        "committed": False,
        "uncommitted": True,
        "pushed": False,
    }
    summary = await _pause_summary(
        _make_state(current_stage="dev_run_uncommitted", branch_name=_BRANCH, unfinished_work=record, pr_url="")
    )
    assert summary["message"] == (
        "Developer stopped (error ERR_AGENT_TIMEOUT) before committing. This repository's salvage.enabled is false, "
        "so embry0 did not commit its work: the changes are UNCOMMITTED in this paused job's sandbox, not on its "
        "branch. Continue to let the next run finish them; stopping the job, or letting the pause expire, discards "
        "them."
    ), summary["message"]
    assert summary["options"] == ["continue_retrying", "abandon"], (
        "merge as is would complete the job and destroy the sandbox holding the changes: it is not offered"
    )


@pytest.mark.asyncio
async def test_merge_as_is_sent_anyway_is_read_as_a_stop() -> None:
    """The API takes any choice: merge as is where it is not offered is a stop, as a continue that is not offered is."""
    from unittest.mock import patch

    import embry0.workflows.issue_to_pr.nodes as nodes_module

    record = {"branch": _BRANCH, "agent": "developer", "reason": "turn cap", "committed": False, "uncommitted": True}
    state = _make_state(current_stage="dev_run_uncommitted", unfinished_work=record, pr_url="", **OFF)
    with (
        patch.object(nodes_module, "get_stream_writer", return_value=lambda _: None),
        patch.object(nodes_module, "interrupt", new=lambda _summary: {"choice": "merge_as_is"}),
    ):
        after = await nodes_module.max_retries_node(state, config=None)
    assert after["current_stage"] == "abandoned", f"a job whose work is only in its sandbox is never completed: {after}"


@pytest.mark.asyncio
async def test_no_pause_that_holds_uncommitted_work_offers_merge_as_is() -> None:
    record = {
        "branch": _BRANCH,
        "agent": "developer",
        "reason": "budget reached",
        "committed": False,
        "uncommitted": True,
    }
    budget = await _pause_summary(
        _make_state(current_stage="developer_failed", total_cost_usd=60.0, unfinished_work=record, pr_url="", **OFF)
    )
    asked = await _pause_summary(
        _make_state(
            agent_questions_exhausted=True, unfinished_work=record, pr_url="https://github.com/acme/widgets/pull/7"
        )
    )
    assert "merge_as_is" not in budget["options"] and "merge_as_is" not in asked["options"], (budget, asked)


@pytest.mark.asyncio
async def test_the_uncommitted_pause_returns_only_its_own_error() -> None:
    """JobState.errors appends: the node returns this run's new entry, never the stored list again."""
    cmd = await _run(
        _stopped("reached max_turns (120)", error_code="ERR_AGENT_MAX_TURNS"), _Sandbox(), errors=["earlier"], **OFF
    )
    assert cmd.update["current_stage"] == "dev_run_uncommitted"
    assert "earlier" not in cmd.update["errors"] and len(cmd.update["errors"]) == 1, cmd.update["errors"]


def test_the_job_page_and_the_failure_code_know_the_stage() -> None:
    from embry0.safety.error_codes import ErrorCode, classify_failure
    from embry0.services.job_status_words import status_words

    interrupt = {"summary": {"options": ["continue_retrying", "abandon"], "reason": "max_retries"}}
    words = status_words({"status": "paused", "current_stage": "dev_run_uncommitted"}, interrupt)
    assert words["next"] == "Continue or stop.", f"the job page offers no merge for this pause: {words}"
    assert "merge" not in words["headline"].lower()
    assert words["headline"] == (
        "Paused: the developer stopped before committing; its work is uncommitted in the job's sandbox, not on its "
        "branch."
    ), words["headline"]
    assert classify_failure(stage="dev_run_uncommitted") is ErrorCode.DEV_RUN_UNFINISHED


def test_the_next_attempt_is_told_its_changes_are_in_the_working_tree() -> None:
    from embry0.workflows.issue_to_pr.nodes import _developer_delta_sections

    record = {"branch": _BRANCH, "agent": "developer", "reason": "turn cap", "committed": False, "uncommitted": True}
    sections = _developer_delta_sections(_make_state(unfinished_work=record))
    assert sections, "the next run must be told its earlier run's changes are uncommitted in its working tree"
    assert sections == [
        "\nUnfinished work from your earlier run:\nThat run stopped (turn cap) before it committed. This repository's "
        "salvage policy keeps embry0 from committing it, so what it left is still uncommitted in this working tree. "
        "Do not start over: review those changes against the task, finish what is missing, run the checks, then "
        "commit and push."
    ]


@pytest.mark.asyncio
async def test_the_next_run_clears_the_record() -> None:
    record = {"branch": _BRANCH, "agent": "developer", "reason": "turn cap", "committed": False, "uncommitted": True}
    cmd = await _run(_finished(), _Sandbox(dirty=False, on_origin=True), unfinished_work=record, **OFF)
    assert cmd.update["unfinished_work"] is None and cmd.goto == "review"


@pytest.mark.asyncio
async def test_production_passes_the_job_s_salvage_enabled_to_salvage() -> None:
    """The salvage call takes ``commit`` with no default: a caller that forgets it fails, never commits silently."""
    import inspect

    from embry0.workflows.issue_to_pr.nodes import _salvage

    commit = inspect.signature(_salvage.preserve_unfinished_work).parameters.get("commit")
    assert commit is not None and commit.default is inspect.Parameter.empty, (
        "preserve_unfinished_work must take the job's salvage.enabled as a required argument"
    )
