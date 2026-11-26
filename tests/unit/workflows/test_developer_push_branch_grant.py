"""The git-proxy lets a job push the branch its developer chose, and no other (#559).

init_node enrolls the sandbox for the job's own branch (``job_push_branch``).
When the developer finds the issue's shared name free and chooses it, the
git-proxy must be told, or the push is refused; when it cannot be told, the
developer keeps the job's own branch. A branch the agent reports instead of the
granted one is never recorded, since a resume would enroll what state names.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from embry0.orchestration.branch_names import job_push_branch, shared_branch_name

ISSUE = "iss-81a36c858147"
TASK = "Fix widget display names"
JOB = "job-5f12020ec6a7"
SHARED = shared_branch_name(issue_id=ISSUE, task=TASK)
OWN = f"{SHARED}-5f12020e"


class _Docker:
    """Answers the #478 probe: ``heads`` is what ``git ls-remote`` prints."""

    def __init__(self, heads: str = "") -> None:
        self.heads = heads

    def build_exec_cmd(self, container_id: str, cmd: list[str]) -> list[str]:
        return ["docker", "exec", container_id, *cmd]

    async def run_cmd(self, cmd: list[str], timeout: int = 60) -> str:
        return self.heads if "ls-remote" in cmd else ""


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


def _proxy(fail: bool = False) -> MagicMock:
    proxy = MagicMock()
    proxy.git_proxy_url = "http://git-proxy:9101"
    proxy.set_git_push_branches = AsyncMock(side_effect=RuntimeError("HTTP 405") if fail else None)
    return proxy


async def _run(state: dict[str, Any], *, docker: _Docker, proxy: MagicMock | None, reports: str | None = None):
    import embry0.workflows.issue_to_pr.nodes as nodes

    captured: dict[str, Any] = {}

    async def _agent(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        output = f'{{"pr_url": null, "branch": "{reports}", "summary": "s", "files_changed": []}}' if reports else "ok"
        return {
            "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": output}],
            "events": [],
            "total_cost_usd": 0.01,
        }

    sessions = AsyncMock()
    sessions.get = AsyncMock(return_value=None)
    configurable = {"agent_runner": object(), "credentials": {}, "agent_sessions_repo": sessions, "docker": docker}
    if proxy is not None:
        configurable["proxy_manager"] = proxy
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_agent)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch("embry0.workflows.issue_to_pr.nodes._verify_branch_pushed", new=AsyncMock(return_value=True)),
    ):
        command = await nodes.developer_node(state, {"configurable": configurable})
    return captured["prompt"], command.update


def test_a_job_is_enrolled_for_a_branch_no_other_job_derives():
    """Before the developer chooses, the issue's shared name is not granted: an earlier job may hold it (#478)."""
    assert job_push_branch(issue_id=ISSUE, task=TASK, job_id=JOB) == OWN
    assert job_push_branch(issue_id=ISSUE, task=TASK, job_id=JOB, chosen=SHARED) == SHARED
    assert job_push_branch(issue_id=ISSUE, task=TASK, job_id=JOB, context={"ref": "feature/pr"}) == "feature/pr"
    assert (
        job_push_branch(issue_id=None, task=TASK, job_id=JOB)
        == f"{shared_branch_name(issue_id=None, task=TASK)}-5f12020e"
    )


@pytest.mark.asyncio
async def test_a_free_shared_branch_is_granted_before_the_agent_runs():
    proxy = _proxy()

    prompt, update = await _run(_state(), docker=_Docker(), proxy=proxy)

    proxy.set_git_push_branches.assert_awaited_once_with("C", [SHARED])
    assert f"1. Create branch: {SHARED}" in prompt
    assert f"The git remote accepts pushes to {SHARED} only" in prompt, prompt
    assert update["branch_name"] == SHARED


@pytest.mark.asyncio
async def test_a_taken_shared_branch_keeps_the_enrolled_branch_and_changes_nothing():
    proxy = _proxy()
    taken = f"{'e' * 40}\trefs/heads/{SHARED}\n"

    prompt, update = await _run(_state(), docker=_Docker(taken), proxy=proxy)

    proxy.set_git_push_branches.assert_not_awaited()
    assert update["branch_name"] == OWN


@pytest.mark.asyncio
async def test_when_the_proxy_cannot_be_told_the_job_keeps_its_own_branch():
    """An older proxy image has no route: the developer must not be sent to a branch it cannot push."""
    proxy = _proxy(fail=True)

    with structlog.testing.capture_logs() as logs:
        prompt, update = await _run(_state(), docker=_Docker(), proxy=proxy)

    assert f"1. Create branch: {OWN}" in prompt, prompt
    assert update["branch_name"] == OWN
    assert "developer_push_branch_not_granted" in [e["event"] for e in logs]


@pytest.mark.asyncio
async def test_a_later_run_reads_its_branch_back_and_asks_nothing():
    proxy = _proxy()

    _, update = await _run(_state(branch_name=SHARED), docker=_Docker(), proxy=proxy)

    proxy.set_git_push_branches.assert_not_awaited()
    assert update["branch_name"] == SHARED


@pytest.mark.asyncio
async def test_a_branch_the_agent_reports_instead_of_its_own_is_not_recorded():
    """A resume enrolls the branch state names, so recording ``main`` would grant a push to main."""
    with structlog.testing.capture_logs() as logs:
        _, update = await _run(_state(branch_name=OWN), docker=_Docker(), proxy=_proxy(), reports="main")

    assert update["branch_name"] == OWN, f"the agent's reported branch was recorded: {update['branch_name']!r}"
    assert "developer_reported_other_branch" in [e["event"] for e in logs]


@pytest.mark.asyncio
@pytest.mark.parametrize(("push_open", "granted"), [(True, True), (None, True), (False, False)])
async def test_the_shared_branch_is_named_in_place_of_the_jobs_own_only_while_the_branch_is_open(
    push_open: bool | None, granted: bool
) -> None:
    """#395: the step start opens the job's branch; one it did not open stays closed, and naming another branch in
    its place would open it. A checkpoint from before the flag (None) was enrolled with its branch, as before."""
    proxy = _proxy()

    prompt, update = await _run(_state(push_open=push_open), docker=_Docker(), proxy=proxy)

    if granted:
        proxy.set_git_push_branches.assert_awaited_once_with("C", [SHARED])
        assert update["branch_name"] == SHARED
    else:
        proxy.set_git_push_branches.assert_not_awaited()
        assert update["branch_name"] == OWN and f"1. Create branch: {OWN}" in prompt, prompt
