"""A fresh job does not inherit an earlier job's branch (#478).

The working branch is derived per issue, so a job dispatched again after an
abandoned one (the documented recovery for #475/#476) got the SAME name. The
remote still held the abandoned job's commit; the new developer built its own
fix, found that branch at the end of its run, and spent its last turns
deciding whose design wins (job-5f12020ec6a7). Now the first developer run of
a job probes origin once and, when the name is taken, works on a branch of its
own; the prompt names the earlier branch as read-only.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from embry0.orchestration.branch_names import job_branch_name
from tests.fixtures.sandbox_git_local import readable

ISSUE = "iss-81a36c858147"
TASK = "[ENG-2435] [watcher] Fix vendor display names"
JOB = "job-5f12020ec6a7"
DERIVED = job_branch_name(issue_id=ISSUE, task=TASK, job_id=JOB)
OWN = f"{DERIVED}-5f12020e"
EARLIER_SHA = "ef115b14c0ffee00ef115b14c0ffee00ef115b14"


class _Docker:
    """Answers the two git commands the probe runs inside the sandbox."""

    def __init__(self, *, heads: str = "", detail: str = "", fail: bool = False) -> None:
        self.heads = heads
        self.detail = detail
        self.fail = fail
        self.commands: list[list[str]] = []

    def build_exec_cmd(self, container_id: str, cmd: list[str]) -> list[str]:
        return ["docker", "exec", container_id, *cmd]

    async def run_cmd(self, cmd: list[str], timeout: int = 60) -> str:
        self.commands.append(cmd)
        if self.fail:
            raise RuntimeError("Cannot connect to the Docker daemon")
        if "ls-remote" in cmd:
            return self.heads
        if "git log -1" in readable(cmd):
            return self.detail
        return ""


def _taken(ref: str = DERIVED) -> str:
    return f"{EARLIER_SHA}\trefs/heads/{ref}\n"


def _state(**extra: Any) -> dict[str, Any]:
    return {
        "job_id": JOB,
        "issue_id": ISSUE,
        "repo": "acme/backend",
        "task": TASK,
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
        **extra,
    }


def _final_json(branch: str) -> str:
    return f'Done.\n{{"pr_url": null, "branch": "{branch}", "summary": "s", "files_changed": []}}'


async def _run(state: dict[str, Any], docker: _Docker | None, *, reports: str | None = None) -> tuple[str, dict]:
    """Run developer_node; return (the prompt it sent, its state update)."""
    import embry0.workflows.issue_to_pr.nodes as nodes

    captured: dict[str, Any] = {}

    async def _agent(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        output = _final_json(reports) if reports else "done"
        return {
            "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": output}],
            "events": [],
            "total_cost_usd": 0.01,
        }

    sessions = AsyncMock()
    sessions.get = AsyncMock(return_value=None)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_agent)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch("embry0.workflows.issue_to_pr.nodes._verify_branch_pushed", new=AsyncMock(return_value=True)),
    ):
        command = await nodes.developer_node(
            state,
            {
                "configurable": {
                    "agent_runner": object(),
                    "credentials": {},
                    "agent_sessions_repo": sessions,
                    "docker": docker,
                }
            },
        )
    return captured["prompt"], command.update


def _probed(docker: _Docker) -> bool:
    return any("ls-remote" in cmd for cmd in docker.commands)


@pytest.mark.asyncio
async def test_a_fresh_job_whose_branch_is_taken_works_on_its_own() -> None:
    docker = _Docker(heads=_taken(), detail="2026-10-03\tstop oversized company names from wedging Blujay")

    prompt, update = await _run(_state(), docker)

    assert f"1. Create branch: {OWN}" in prompt, (
        f"a fresh job must not be told to create a branch an earlier job already pushed:\n{prompt}"
    )
    assert update["branch_name"] == OWN, f"the job's own branch must be recorded in state: {update['branch_name']!r}"


@pytest.mark.asyncio
async def test_the_prompt_names_the_earlier_branch_as_read_only() -> None:
    docker = _Docker(heads=_taken(), detail="2026-10-03\tstop oversized company names from wedging Blujay")

    prompt, _ = await _run(_state(), docker)

    assert f"Branch {DERIVED} already exists on origin" in prompt, f"the developer must be told up front:\n{prompt}"
    assert "ef115b14c0ff 2026-10-03: stop oversized company names from wedging Blujay" in prompt, prompt
    assert "never push to it" in prompt
    assert f"Your branch is {OWN}" in prompt


@pytest.mark.asyncio
async def test_the_note_survives_a_probe_that_only_finds_the_sha() -> None:
    """The commit detail is a second, optional fetch; losing it must not lose the warning."""
    docker = _Docker(heads=_taken())

    prompt, update = await _run(_state(), docker)

    assert f"Branch {DERIVED} already exists on origin (last commit ef115b14c0ff)" in prompt, prompt
    assert update["branch_name"] == OWN


@pytest.mark.asyncio
async def test_a_free_branch_keeps_its_derived_name_and_no_note() -> None:
    docker = _Docker(heads="")

    prompt, update = await _run(_state(), docker)

    assert _probed(docker), "the first developer run of a fresh job must look at origin"
    assert f"1. Create branch: {DERIVED}" in prompt
    assert "already exists on origin" not in prompt
    assert update["branch_name"] == DERIVED


@pytest.mark.asyncio
async def test_only_the_exact_ref_counts_as_taken() -> None:
    """ls-remote matches by tail: a branch that merely ENDS with the name is someone else's."""
    docker = _Docker(heads=_taken(f"someone/{DERIVED}"))

    prompt, update = await _run(_state(), docker)

    assert update["branch_name"] == DERIVED, f"a tail match is not a collision: {update['branch_name']!r}"
    assert "already exists on origin" not in prompt


@pytest.mark.asyncio
async def test_a_probe_that_fails_keeps_the_derived_name() -> None:
    docker = _Docker(fail=True)

    _, update = await _run(_state(), docker)

    assert update["branch_name"] == DERIVED, "a probe that cannot run must leave the job as it always was"


@pytest.mark.asyncio
async def test_a_later_run_keeps_the_branch_the_job_started_on() -> None:
    """A retry, resume or answered question must not re-probe and find its OWN pushed branch."""
    docker = _Docker(heads=_taken(OWN))

    prompt, update = await _run(_state(branch_name=OWN), docker)

    assert not _probed(docker), f"a job that already has a branch must not probe again: {docker.commands}"
    assert f"1. Create branch: {OWN}" in prompt, prompt
    assert update["branch_name"] == OWN


@pytest.mark.asyncio
async def test_a_follow_up_job_is_never_probed() -> None:
    docker = _Docker(heads=_taken("feature/existing"))
    state = _state(context={"type": "git", "repo": "acme/backend", "ref": "feature/existing"})

    _, update = await _run(state, docker)

    assert not _probed(docker), "a PR follow-up works on its PR's branch, which always exists"
    assert update["branch_name"] == "feature/existing"


@pytest.mark.asyncio
async def test_a_developer_that_reports_the_earlier_branch_cannot_claim_it() -> None:
    """It exists on origin, so the push check would pass and the PR fallback would deliver the earlier work."""
    docker = _Docker(heads=_taken())

    _, update = await _run(_state(), docker, reports=DERIVED)

    assert update["branch_name"] == OWN, (
        f"a job named away from {DERIVED} must not record it as its branch: {update['branch_name']!r}"
    )


@pytest.mark.asyncio
async def test_no_reported_branch_replaces_the_jobs_own() -> None:
    """Since #559 no reported branch is taken at its word, not only the earlier one.

    The git-proxy lets this sandbox push only its own branch, so another
    branch the developer names holds none of its work, and a resume would
    enroll whatever state records.
    """
    docker = _Docker(heads=_taken())

    _, update = await _run(_state(), docker, reports="embry0/its-own-idea")

    assert update["branch_name"] == OWN, update["branch_name"]
