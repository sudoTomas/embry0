"""Salvage (spec 2026-10-25): a coding run that stops before it pushes keeps its work.

#358 kept a turn-cap death's uncommitted work. Any other stop still lost it: an
error ending (a proxy's request cap, a timeout, a crash) skipped the commit, and
a run that took the job over its budget went to the pause before the push check
ran at all. Observed on a grok developer: 120 turns, 21 edits and 7 new files,
all left uncommitted in the sandbox, behind a pause that said "Review failed".
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.fixtures.sandbox_git_local import MAIN_SHA, readable, reads_branch_tip, reads_head

_BRANCH = "embry0/iss-abc12345-test-task"


def _make_state(**over: Any) -> dict[str, Any]:
    base = {
        "job_id": "J",
        "issue_id": "iss-abc12345",
        "repo": "o/r",
        "task": "test task",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
    }
    base.update(over)
    return base


def _stopped(error_message: str, *, error_code: str | None = None, cost: float = 1.0) -> dict[str, Any]:
    out: dict[str, Any] = {
        "agent_type": "developer",
        "is_error": True,
        "error_message": error_message,
        "output": "mid-stride",
        "messages": [],
    }
    if error_code:
        out["error_code"] = error_code
    return {"agent_outputs": [out], "events": [], "total_cost_usd": cost}


def _finished(cost: float = 1.0) -> dict[str, Any]:
    return {
        "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "done", "messages": []}],
        "events": [],
        "total_cost_usd": cost,
    }


class _Sandbox:
    """A fake sandbox git: uncommitted edits, a branch that may already be on origin."""

    def __init__(self, *, dirty: bool = True, on_origin: bool = False, head: str = _BRANCH, fail_commit: bool = False):
        self.dirty = dirty
        self.on_origin = on_origin
        self.head = head
        self.fail_commit = fail_commit
        self.local_commits = 0
        # Each local branch's tip; HEAD is the checked-out one's, another commit when that is main. The push
        # check compares the job's branch's tip with the forge's (#435).
        self.tips = {head: "abc123" if head == _BRANCH else MAIN_SHA}
        self.forge_tip = self.tips.get(_BRANCH, "") if on_origin else ""
        self.calls: list[list[str]] = []
        self.messages: list[str] = []

    def build_exec_cmd(self, container_id: str, cmd: list[str], **_kw: Any) -> list[str]:
        return ["docker", "exec", container_id, *cmd]

    async def run_cmd(self, cmd: list[str], timeout: float | None = None, **_kwargs: Any) -> str:  # noqa: ARG002
        self.calls.append(cmd)
        joined = readable(cmd)
        if reads_branch_tip(cmd):
            from embry0.workflows.issue_to_pr.nodes._developer import _PROBE_NO_BRANCH

            return f"{self.tips.get(_BRANCH, _PROBE_NO_BRANCH)}\n"
        if reads_head(cmd):
            return f"{self.tips[self.head]}\n"
        if "status" in joined and "--porcelain" in joined:
            return " M src/a.py\n?? src/new.py\n" if self.dirty else ""
        if "rev-list" in joined:
            # The unpushed-commit probe (one bash script that also names rev-parse).
            return f"{self.local_commits}\n"
        if "--abbrev-ref" in joined:
            return f"{self.head}\n"
        if "switch" in joined and "-c" in cmd:
            self.tips[cmd[-1]] = self.tips[self.head]
            self.head = cmd[-1]
            return ""
        if "rev-parse" in joined and "--verify" in joined and "refs/heads/" in joined:
            return "sha\n" if _BRANCH in self.tips else ""
        if " add -A" in f" {joined}":
            return ""
        if "commit" in cmd:
            if self.fail_commit:
                raise RuntimeError("Docker command failed: git commit: no identity")
            self.messages.append(cmd[cmd.index("-m") + 1])
            self.local_commits += 1
            self.tips[self.head] = f"c0ffee{self.local_commits}"
            self.dirty = False
            return "[branch abc1234] wip"
        if "push" in cmd:
            assert "--force" not in cmd and "-f" not in cmd, "salvage never force-pushes"
            self.on_origin = True
            self.forge_tip = self.tips.get(_BRANCH, self.forge_tip)
            self.local_commits = 0
            return ""
        if "ls-remote" in joined:
            return f"{self.forge_tip}\trefs/heads/{_BRANCH}" if self.on_origin else ""
        return ""


async def _run(result: dict[str, Any], sandbox: _Sandbox, **state_over: Any) -> Any:
    from embry0.workflows.issue_to_pr.nodes import developer_node

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=result)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        return await developer_node(
            # The job's branch, as every run after the first has it in state.
            _make_state(**{"branch_name": _BRANCH, **state_over}),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": sandbox}},
        )


@pytest.mark.asyncio
async def test_an_error_ending_commits_and_pushes_the_work_as_unfinished():
    from embry0.safety.error_codes import ErrorCode

    sandbox = _Sandbox()
    cmd = await _run(
        _stopped("Maximum request body size 1048576 exceeded.", error_code="ERR_CONTEXT_WINDOW_EXCEEDED"), sandbox
    )

    assert sandbox.messages, "the stopped run's work was never committed"
    subject = sandbox.messages[0].splitlines()[0]
    assert subject == "wip: unfinished — developer stopped (error ERR_CONTEXT_WINDOW_EXCEEDED)"
    assert "not by the agent" in sandbox.messages[0]
    assert sandbox.on_origin, "committed but never pushed"
    assert cmd.goto == "max_retries"
    assert cmd.update["current_stage"] == "dev_run_unfinished"
    assert cmd.update["error_code"] == ErrorCode.DEV_RUN_UNFINISHED.value
    assert cmd.update["unfinished_work"] == {
        "branch": _BRANCH,
        "agent": "developer",
        "reason": "error ERR_CONTEXT_WINDOW_EXCEEDED",
        "committed": True,
    }


@pytest.mark.asyncio
async def test_a_turn_cap_names_its_reason():
    sandbox = _Sandbox()
    await _run(_stopped("reached max_turns (120)", error_code="ERR_AGENT_MAX_TURNS"), sandbox)
    assert sandbox.messages[0].splitlines()[0] == "wip: unfinished — developer stopped (turn cap)"


@pytest.mark.asyncio
async def test_a_run_that_takes_the_job_over_budget_keeps_its_work_and_the_pause_says_where():
    sandbox = _Sandbox()
    cmd = await _run(_stopped("reached max_turns (120)", cost=60.0), sandbox)

    assert cmd.goto == "max_retries"
    assert sandbox.on_origin and sandbox.messages, "the budget route skipped the salvage"
    assert sandbox.messages[0].splitlines()[0] == "wip: unfinished — developer stopped (budget reached)"
    assert cmd.update["unfinished_work"]["branch"] == _BRANCH
    assert cmd.update["unfinished_work"]["reason"] == "budget reached"


@pytest.mark.asyncio
async def test_a_stopped_run_that_pushed_earlier_pushes_what_it_left_since_and_pauses():
    """The branch is on origin from an earlier push; the edits after it are not."""
    sandbox = _Sandbox(on_origin=True)
    cmd = await _run(_stopped("Agent timed out after 1800s", error_code="ERR_AGENT_TIMEOUT"), sandbox)

    assert sandbox.messages and sandbox.local_commits == 0, "the remainder was not committed and pushed"
    assert cmd.goto == "max_retries", "a salvage commit must not go to review as finished work"
    assert cmd.update["current_stage"] == "dev_run_unfinished"


@pytest.mark.asyncio
async def test_a_stopped_run_with_nothing_uncommitted_takes_its_old_path():
    sandbox = _Sandbox(dirty=False, on_origin=True)
    cmd = await _run(_stopped("Agent timed out after 1800s", error_code="ERR_AGENT_TIMEOUT"), sandbox)

    assert not sandbox.messages
    assert cmd.goto == "review"
    assert "unfinished_work" not in cmd.update or cmd.update["unfinished_work"] is None


@pytest.mark.asyncio
async def test_a_clean_finish_that_pushed_is_untouched_even_with_a_dirty_tree():
    sandbox = _Sandbox(on_origin=True)
    cmd = await _run(_finished(), sandbox)

    assert not sandbox.messages, "a run that finished and pushed got a wip commit"
    assert cmd.goto == "review"


@pytest.mark.asyncio
async def test_the_commit_goes_on_the_job_branch_when_the_agent_never_created_it():
    sandbox = _Sandbox(head="main")
    await _run(_stopped("reached max_turns (120)", error_code="ERR_AGENT_MAX_TURNS"), sandbox)

    joined = [readable(c) for c in sandbox.calls]
    assert any(f"switch -c {_BRANCH}" in j for j in joined), "committed on the base branch"
    assert sandbox.messages and sandbox.on_origin


@pytest.mark.asyncio
async def test_a_failed_salvage_changes_nothing_but_the_log():
    from embry0.safety.error_codes import ErrorCode

    sandbox = _Sandbox(fail_commit=True)
    cmd = await _run(_stopped("reached max_turns (120)", error_code="ERR_AGENT_MAX_TURNS"), sandbox)

    assert cmd.goto == "max_retries"
    assert cmd.update["error_code"] == ErrorCode.DEV_BRANCH_NOT_PUSHED.value
    assert not cmd.update.get("unfinished_work")


def test_the_next_attempt_is_told_to_finish_the_unfinished_commit_not_redo_it():
    from embry0.workflows.issue_to_pr.nodes import _developer_delta_sections

    record = {"branch": _BRANCH, "agent": "developer", "reason": "turn cap", "committed": True}
    text = "\n".join(_developer_delta_sections(_make_state(unfinished_work=record)))
    assert _BRANCH in text and "wip: unfinished" in text
    assert "Do not start over" in text
    assert "turn cap" in text
    assert _developer_delta_sections(_make_state()) == []


@pytest.mark.asyncio
async def test_a_run_after_the_salvage_clears_the_record():
    sandbox = _Sandbox(dirty=False, on_origin=True)
    record = {"branch": _BRANCH, "agent": "developer", "reason": "turn cap", "committed": True}
    cmd = await _run(_finished(), sandbox, unfinished_work=record, branch_name=_BRANCH)
    assert cmd.update["unfinished_work"] is None
    assert cmd.goto == "review"


async def _pause_summary(state: dict[str, Any]) -> dict[str, Any]:
    import embry0.workflows.issue_to_pr.nodes as nodes_module

    captured: dict[str, Any] = {}

    def _interrupt(summary: dict[str, Any]) -> str:
        captured.update(summary)
        return "abandon"

    with (
        patch.object(nodes_module, "get_stream_writer", return_value=lambda _: None),
        patch.object(nodes_module, "interrupt", new=_interrupt),
    ):
        await nodes_module.max_retries_node(state, config=None)
    return captured


@pytest.mark.asyncio
async def test_the_unfinished_pause_names_the_agent_the_reason_and_the_branch():
    record = {"branch": _BRANCH, "agent": "developer", "reason": "error ERR_AGENT_TIMEOUT", "committed": True}
    summary = await _pause_summary(
        _make_state(current_stage="dev_run_unfinished", branch_name=_BRANCH, unfinished_work=record, pr_url="")
    )
    assert "Developer stopped (error ERR_AGENT_TIMEOUT) before committing" in summary["message"]
    assert _BRANCH in summary["message"] and "UNFINISHED" in summary["message"]


@pytest.mark.asyncio
async def test_a_budget_pause_says_so_and_where_the_work_is():
    record = {"branch": _BRANCH, "agent": "developer", "reason": "budget reached", "committed": True}
    summary = await _pause_summary(
        _make_state(
            current_stage="developer_failed",
            total_cost_usd=60.0,
            retry_count=1,
            branch_name=_BRANCH,
            unfinished_work=record,
            pr_url="",
        )
    )
    assert "Review failed" not in summary["message"]
    assert "budget" in summary["message"] and "$60.00" in summary["message"]
    assert _BRANCH in summary["message"]


CONVENTIONAL = "chore(wip): {agent} stopped, {reason}"


def _default_subject() -> str:
    from embry0.settings.sections.salvage import SALVAGE

    template = getattr(SALVAGE.defaults(), "commit_subject", None)
    if template is None:
        pytest.fail("the salvage section has no commit_subject template: the subject is a prefix and the code's words")
    return str(template)


def _subject(agent: str, reason: str, template: str) -> str:
    import inspect

    from embry0.workflows.issue_to_pr.nodes import _salvage

    if "template" not in inspect.signature(_salvage.commit_subject).parameters:
        pytest.fail("the salvage subject is not rendered from the section's commit_subject template")
    return str(_salvage.commit_subject(agent, reason, template=template))


def _reasons() -> list[str]:
    """Every reason a salvage commit names: the turn cap, budget, question rounds, and an error with or without a code."""
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import _salvage

    return ["turn cap", _salvage.BUDGET, _salvage.QUESTIONS, "error"] + [f"error {code.value}" for code in ErrorCode]


def test_the_default_subject_is_the_old_one_for_every_reason():
    from embry0.workflows.row_behaviour import CODING_AGENT_TYPES

    template = _default_subject()
    for agent in sorted(CODING_AGENT_TYPES):
        for reason in _reasons():
            old = f"wip: unfinished — {agent} stopped ({reason})"
            assert _subject(agent, reason, template) == old, f"the default must render the code's old subject: {old!r}"


def test_a_subject_over_100_characters_shortens_only_its_reason():
    template = "chore(salvage): unfinished work kept by embry0 — {agent} stopped ({reason})"
    full = template.format(agent="slopmaster", reason="error ERR_SANDBOX_AGENT_START_FAILED")
    assert len(full) == 106, len(full)
    subject = _subject("slopmaster", "error ERR_SANDBOX_AGENT_START_FAILED", template)
    assert subject == (
        "chore(salvage): unfinished work kept by embry0 — slopmaster stopped (error ERR_SANDBOX_AGENT_START…)"
    ), f"only the reason is shortened, ending in an ellipsis: {subject!r}"
    assert len(subject) == 100
    # A subject of exactly 100 characters is kept whole; one more and its reason is shortened.
    exactly = "x" * (100 - len(template.format(agent="developer", reason="")))
    assert _subject("developer", exactly, template) == template.format(agent="developer", reason=exactly)
    one_more = _subject("developer", exactly + "y", template)
    assert len(one_more) == 100 and one_more.endswith("…)"), one_more
    # Named twice, the reason is shortened in both places and the subject still fits.
    twice = _subject("developer", "error " + "E" * 60, "{reason}: {agent} stopped ({reason})")
    assert len(twice) <= 100 and twice.count("…") == 2, twice


@pytest.mark.asyncio
async def test_the_salvage_commit_reads_the_job_s_commit_subject():
    """#182: a repository whose commit rules refuse wip: names the subject they accept in its salvage section."""
    sandbox = _Sandbox()
    await _run(
        _stopped("Reached maximum number of turns", error_code="ERR_AGENT_MAX_TURNS"),
        sandbox,
        job_settings={"salvage": {"commit_subject": CONVENTIONAL}},
    )
    assert sandbox.messages, "the stopped run's work was never committed"
    assert sandbox.messages[0].splitlines()[0] == "chore(wip): developer stopped, turn cap", (
        f"the subject must be the job's salvage.commit_subject: {sandbox.messages[0].splitlines()[0]!r}"
    )


def test_the_next_attempt_is_told_the_commit_by_its_subject():
    from embry0.workflows.issue_to_pr.nodes import _developer_delta_sections

    record = {"branch": _BRANCH, "agent": "developer", "reason": "turn cap", "committed": True}
    state = _make_state(unfinished_work=record, job_settings={"salvage": {"commit_subject": CONVENTIONAL}})
    text = "\n".join(_developer_delta_sections(state))
    assert "as `chore(wip): developer stopped, turn cap`" in text, f"the prompt must name the commit it made:\n{text}"


def test_the_commit_helper_has_no_subject_of_its_own():
    import inspect

    from embry0.workflows.issue_to_pr.nodes import _commit_unfinished_work

    message = inspect.signature(_commit_unfinished_work).parameters["message"]
    assert message.default is inspect.Parameter.empty, (
        "the salvage commit's message must come from the job's salvage.commit_subject, never a wip: default in code"
    )


class _ForgeReadsFail(_Sandbox):
    """The salvage commit is made and pushed, and then every read of the forge fails."""

    async def run_cmd(self, cmd: list[str], timeout: float | None = None, **kwargs: Any) -> str:
        if "ls-remote" in readable(cmd) and self.on_origin:
            self.calls.append(cmd)
            raise RuntimeError("Docker command failed: fatal: unable to access the forge: 502")
        return await super().run_cmd(cmd, timeout, **kwargs)


@pytest.mark.asyncio
async def test_a_salvage_commit_keeps_its_record_when_the_push_cannot_be_confirmed(monkeypatch: Any) -> None:
    """The run stopped, embry0 committed and pushed its work, and the forge could not be read after (#435, review).

    The pause must still be the unfinished one, naming the commit: without the record, "merge as is" would
    deliver a branch ending in a "Do not merge it as it is" commit with no human-merge-only note.
    """
    import embry0.workflows.issue_to_pr.nodes as nodes_module
    from embry0.execution import sandbox_git
    from embry0.workflows.issue_to_pr.nodes import max_retries_node

    async def no_pause(_seconds: float) -> None:
        return None

    monkeypatch.setattr(sandbox_git, "_pause", no_pause, raising=False)
    sandbox = _ForgeReadsFail()
    cmd = await _run(_stopped("reached max_turns (120)", error_code="ERR_AGENT_MAX_TURNS"), sandbox)
    assert sandbox.messages and sandbox.on_origin, "the salvage commit was not made and pushed"
    assert cmd.update["current_stage"] == "dev_run_unfinished", (
        f"an unfinished run whose push could not be confirmed lost its unfinished pause: {cmd.update['current_stage']}"
    )
    record = cmd.update["unfinished_work"]
    assert record and record["committed"] and record["pushed"] is False, f"the salvage record was lost: {record}"
    assert "could not confirm" in cmd.update["errors"][-1], cmd.update["errors"][-1]

    summary: dict[str, Any] = {}

    def _interrupt(value: dict[str, Any]) -> str:
        summary.update(value)
        return "abandon"

    with (
        patch.object(nodes_module, "interrupt", _interrupt),
        patch.object(nodes_module, "get_stream_writer", return_value=lambda _event: None),
    ):
        await max_retries_node({**_make_state(branch_name=_BRANCH), **cmd.update}, config=None)
    assert "could not confirm the forge has it" in summary["message"], summary["message"]
    assert "UNFINISHED" in summary["message"], summary["message"]
    assert "merge_as_is" not in summary["options"], (
        f"a salvage commit the forge was not shown to have was offered to merge as is: {summary['options']}"
    )


class _PushFails(_Sandbox):
    """The salvage commit is made, and embry0's push of it is refused: the branch is not on the forge."""

    def __init__(self, *, forge_unreadable: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.forge_unreadable = forge_unreadable

    async def run_cmd(self, cmd: list[str], timeout: float | None = None, **kwargs: Any) -> str:
        if "push" in cmd:
            self.calls.append(cmd)
            raise RuntimeError("Docker command failed: remote: rejected by the git proxy (403)")
        if self.forge_unreadable and "ls-remote" in readable(cmd):
            self.calls.append(cmd)
            raise RuntimeError("Docker command failed: fatal: unable to access the forge: 502")
        return await super().run_cmd(cmd, timeout, **kwargs)


async def _pause(state: dict[str, Any], answer: str = "abandon") -> tuple[dict[str, Any], dict[str, Any]]:
    """The summary ``max_retries_node`` pauses ``state`` with, and what it returns for ``answer``."""
    import embry0.workflows.issue_to_pr.nodes as nodes_module
    from embry0.workflows.issue_to_pr.nodes import max_retries_node

    summary: dict[str, Any] = {}

    def _interrupt(value: dict[str, Any]) -> str:
        summary.update(value)
        return answer

    with (
        patch.object(nodes_module, "interrupt", _interrupt),
        patch.object(nodes_module, "get_stream_writer", return_value=lambda _event: None),
    ):
        after = await max_retries_node(state, config=None)
    return summary, dict(after or {})


@pytest.mark.parametrize("forge_unreadable", [False, True], ids=["forge read", "forge unreadable from the start"])
@pytest.mark.asyncio
async def test_a_failed_salvage_push_says_so_and_offers_no_merge(monkeypatch: Any, forge_unreadable: bool) -> None:
    """embry0's own push of the salvage commit is refused (#435, second review).

    The commit is then only in the sandbox: the error and the pause say the push failed, not that it went
    through unconfirmed; "merge as is", which would end the job and destroy the sandbox, is not offered, and
    choosing it anyway is a stop. When the forge could not be read either, the push failure is still named.
    """
    from embry0.execution import sandbox_git

    async def no_pause(_seconds: float) -> None:
        return None

    monkeypatch.setattr(sandbox_git, "_pause", no_pause, raising=False)
    sandbox = _PushFails(forge_unreadable=forge_unreadable)
    cmd = await _run(_stopped("reached max_turns (120)", error_code="ERR_AGENT_MAX_TURNS"), sandbox)
    assert sandbox.messages and not sandbox.on_origin, "the salvage commit was not made, or reached the forge"
    record = cmd.update["unfinished_work"]
    assert record["committed"] and record["pushed"] is False and "403" in str(record.get("push_error")), (
        f"the record does not say the push failed: {record}"
    )
    error = cmd.update["errors"][-1]
    assert "could not push it" in error and "403" in error and "pushed 'embry0" not in error, (
        f"the run's error does not say the push failed: {error}"
    )
    summary, after = await _pause({**_make_state(branch_name=_BRANCH), **cmd.update, "pr_url": ""}, "merge_as_is")
    assert "could not push it" in summary["message"] and "403" in summary["message"], (
        f"the pause does not say the push failed: {summary['message']}"
    )
    assert "merge_as_is" not in summary["options"], f"merge as is was offered for an unpushed commit: {summary}"
    assert after.get("current_stage") == "abandoned" and not after.get("merged_as_is"), (
        f"a merge as is the pause did not offer was not read as a stop: {after}"
    )


def test_the_next_run_is_told_whether_the_commit_was_pushed() -> None:
    """The prompt said "and pushed it" for every salvage commit, even one whose push failed (#435)."""
    from embry0.settings.sections.salvage import DEFAULT_SUBJECT as OLD_SUBJECT
    from embry0.workflows.issue_to_pr.nodes._salvage import prompt_section

    base = {"branch": _BRANCH, "agent": "developer", "reason": "turn cap", "committed": True}
    pushed = prompt_section(base, subject_template=OLD_SUBJECT) or ""
    failed = prompt_section({**base, "pushed": False, "push_error": "403"}, subject_template=OLD_SUBJECT) or ""
    unconfirmed = prompt_section({**base, "pushed": False}, subject_template=OLD_SUBJECT) or ""
    assert "and pushed it, so it is checked out here" in pushed, pushed
    assert "could not push it" in failed and "and pushed it" not in failed, (
        f"the next run is told a failed push went through: {failed.strip()}"
    )
    assert "not shown to have it" in unconfirmed, f"the next run is not told the push was unconfirmed: {unconfirmed}"


@pytest.mark.asyncio
async def test_a_budget_pause_names_an_unpushed_salvage_commit_and_offers_no_merge() -> None:
    """A budget or question stop's pause adds the salvage note: it says the push failed, and merge is withheld."""
    record = {
        "branch": _BRANCH,
        "agent": "developer",
        "reason": "budget reached",
        "committed": True,
        "pushed": False,
        "push_error": "remote: rejected by the git proxy (403)",
    }
    state = {**_make_state(branch_name=_BRANCH), "current_stage": "max_retries", "unfinished_work": record}
    state["retry_count"] = 2
    summary, _after = await _pause(state)
    assert "could not push it" in summary["message"] and "403" in summary["message"], (
        f"the pause's salvage note does not say the push failed: {summary['message']}"
    )
    assert "merge_as_is" not in summary["options"], f"merge as is was offered for an unpushed commit: {summary}"
