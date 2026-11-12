"""A resumed job gets its sandbox's proxy access back before any agent runs (#476, #475).

Proxies keep enrollments in memory, for 24 h at most, and are recreated on every
orchestrator start. Resuming a paused job used to send every agent call to a
proxy that no longer knew the sandbox: 401 on each, retries spent in seconds.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.types import Command

from embry0.execution.sandbox_manager import SandboxInitError, SandboxReenrollError
from embry0.safety.error_codes import ErrorCode
from embry0.services.credentials.resolver import ResolvedCredential
from embry0.services.issue_executor import IssueExecutor

PAUSED_STATE = {
    "sandbox_container_id": "cid-1",
    "repo": "acme/widgets",
    "case_log_containers": ["widgets-backend"],
    "branch_name": "embry0/iss-1-fix-widgets",
}


def _resolved(provider: str, kind: str) -> ResolvedCredential:
    return ResolvedCredential(
        id=f"cred-{provider}",
        kind=kind,
        provider=provider,
        host="api.x.ai" if provider == "xai" else "api.anthropic.com",
        label=provider,
        secret="super-secret-token",
        git_author_name=None,
        git_author_email=None,
        source="registry",
        trace=(),
    )


class _Saver:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _executor(order: list[Any], *, state: dict[str, Any] = PAUSED_STATE, xai_kind: str = "llm_oauth") -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    resolver = MagicMock()

    async def resolve_llm_for_job(repo, providers, *, job_id, parent_job_id=None):
        return {"anthropic": _resolved("anthropic", "llm_oauth"), "xai": _resolved("xai", xai_kind)}

    resolver.resolve_llm_for_job = resolve_llm_for_job
    ex._credential_resolver = resolver
    ex._db = None
    ex._jobs = MagicMock()
    ex._jobs.get = AsyncMock(return_value={"repo": "acme/widgets"})
    ex._config = SimpleNamespace(default_auth_mode="oauth", default_execution_mode="sdk")
    ex._build_graph_config = lambda job_id, **_kw: {"configurable": {"thread_id": job_id}}  # type: ignore[method-assign]
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values=dict(state), next=()))
    workflow = MagicMock()
    workflow.compile = MagicMock(return_value=graph)
    ex._select_workflow = lambda _pipeline: workflow  # type: ignore[method-assign]

    async def process_stream(*_args, **_kwargs):
        order.append("stream")
        return None, False, None

    ex._process_stream = process_stream  # type: ignore[method-assign]
    ex._database_url = ""
    sandbox = MagicMock()

    async def reenroll(container, **kwargs):
        order.append(("reenroll", container, kwargs))

    sandbox.reenroll = reenroll
    ex._sandbox = sandbox
    ex._forge_for = AsyncMock(return_value="github")  # type: ignore[method-assign]
    return ex


@pytest.fixture(autouse=True)
def _no_checkpointer(monkeypatch):
    monkeypatch.setattr("embry0.services._executor_run.checkpointer_context", lambda _url: _Saver())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resume_value",
    [{"choice": "continue_retrying", "guidance": ""}, [{"input_id": "q-1", "answer": "yes"}]],
    ids=["continue_retrying", "answers"],
)
async def test_a_resume_reenrolls_the_sandbox_before_the_graph_runs(resume_value):
    order: list[Any] = []
    ex = _executor(order)

    await ex._execute_workflow_stream(Command(resume=resume_value), "iss-1", "job-1")

    assert order and order[0][0] == "reenroll", f"the sandbox must be re-enrolled before the graph runs: {order}"
    assert order[-1] == "stream"
    _, container, kwargs = order[0]
    assert container == "cid-1"
    assert kwargs == {
        "job_id": "job-1",
        "repo": "acme/widgets",
        "forge": "github",
        "xai_credential_id": "cred-xai",
        "context_scope": ["widgets-backend"],
        # #521: an issue-to-pr job's sandbox keeps the push right it was created with,
        # #559: for the branch its developer chose.
        "git_push": True,
        "git_push_branches": ["embry0/iss-1-fix-widgets"],
        # embry0's own git config is rewritten with the identity as at init.
        "prefs_repo": None,
    }, kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["abandon", "merge_as_is"])
async def test_a_resume_that_runs_no_agent_does_not_reenroll(choice):
    """Abandoning must still work when the sandbox is gone; neither choice runs an agent."""
    order: list[Any] = []
    ex = _executor(order)

    await ex._execute_workflow_stream(Command(resume={"choice": choice, "guidance": ""}), "iss-1", "job-1")

    assert order == ["stream"], f"{choice} must not touch the sandbox: {order}"


@pytest.mark.asyncio
async def test_a_fresh_run_does_not_reenroll():
    order: list[Any] = []
    ex = _executor(order)

    await ex._execute_workflow_stream({"repo": "acme/widgets", "job_id": "job-1"}, "iss-1", "job-1")

    assert order == ["stream"], f"a fresh run creates its own sandbox: {order}"


@pytest.mark.asyncio
async def test_an_xai_api_key_account_is_not_enrolled_as_an_oauth_credential():
    """The proxy injects an account's token only for oauth accounts, as at create."""
    order: list[Any] = []
    ex = _executor(order, xai_kind="llm_api_key")

    await ex._execute_workflow_stream(Command(resume={"choice": "continue_retrying"}), "iss-1", "job-1")

    assert order[0][2]["xai_credential_id"] is None


@pytest.mark.asyncio
async def test_a_paused_job_without_a_sandbox_is_left_alone():
    order: list[Any] = []
    ex = _executor(order, state={"repo": "acme/widgets"})

    await ex._execute_workflow_stream(Command(resume={"choice": "continue_retrying"}), "iss-1", "job-1")

    assert order == ["stream"]


def _resume_executor(error: BaseException) -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.get = AsyncMock(return_value={"job_id": "job-1", "status": "paused"})
    ex._jobs.update = AsyncMock()
    ex._issues = MagicMock()
    ex._issues.get = AsyncMock(return_value={"id": "iss-1", "status": "open"})
    ex._issues.update = AsyncMock()
    ex._execute_workflow_stream = AsyncMock(side_effect=error)  # type: ignore[method-assign]
    ex._cleanup_sandbox = AsyncMock()  # type: ignore[method-assign]
    ex._finish_credential_uses = AsyncMock()  # type: ignore[method-assign]
    ex._post_tracker_outcome = AsyncMock()  # type: ignore[method-assign]
    ex._traces = None
    return ex


@pytest.mark.asyncio
async def test_a_transient_reenroll_failure_leaves_the_job_paused_with_its_sandbox():
    """A proxy that is restarting must not cost the job its sandbox or its pause."""
    ex = _resume_executor(SandboxReenrollError("enroll returned HTTP 503", error_code=ErrorCode.SANDBOX_INIT))

    await ex.resume("iss-1", "job-1", {"choice": "continue_retrying"})

    last_job = ex._jobs.update.await_args_list[-1]
    assert last_job.kwargs.get("status") == "paused", f"job must go back to paused: {ex._jobs.update.await_args_list}"
    assert ex._issues.update.await_args_list[-1].kwargs.get("status") == "open"
    ex._cleanup_sandbox.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_gone_sandbox_fails_the_resume_with_its_own_code():
    ex = _resume_executor(SandboxInitError("sandbox cid-1 no longer exists", error_code=ErrorCode.SANDBOX_GONE))

    await ex.resume("iss-1", "job-1", {"choice": "continue_retrying"})

    failed = [c.kwargs for c in ex._jobs.update.await_args_list if c.kwargs.get("status") == "failed"]
    assert failed, "a resume into a gone sandbox must fail the job"
    assert failed[-1].get("error_code") == ErrorCode.SANDBOX_GONE.value, failed[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(("pipeline", "push"), [("issue-to-pr", True), ("qa", False), ("onboard", False)])
async def test_a_resume_restores_the_push_right_of_the_pipeline_that_made_the_sandbox(pipeline, push):
    """#521: only issue-to-pr's init node creates a sandbox that pushes; QA and onboarding only read."""
    order: list[Any] = []
    ex = _executor(order)

    await ex._execute_workflow_stream(Command(resume={"q1": "yes"}), "iss-1", "job-1", pipeline=pipeline)

    assert order[0][0] == "reenroll"
    assert order[0][2]["git_push"] is push, f"{pipeline}: {order[0][2]}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra", "branch"),
    [
        ({"branch_name": "embry0/iss-1-fix-widgets"}, "embry0/iss-1-fix-widgets"),
        ({"branch_name": None, "context": {"type": "git", "ref": "feature/pr-branch"}}, "feature/pr-branch"),
        ({"branch_name": None, "issue_id": "iss-1", "task": "Fix widgets", "job_id": "job-1a2b3c4d"}, None),
    ],
    ids=["chosen", "follow-up", "not-yet-chosen"],
)
async def test_a_resume_enrolls_the_one_branch_the_checkpoint_names(extra, branch):
    """#559: the push branch survives a proxy restart because it is re-derived from the checkpoint.

    Before the developer chooses, the job's own name, never the issue's shared
    name another job may hold (#478).
    """
    order: list[Any] = []
    ex = _executor(order, state={**PAUSED_STATE, **extra})

    await ex._execute_workflow_stream(Command(resume={"q1": "yes"}), "iss-1", "job-1a2b3c4d")

    expected = branch or "embry0/iss-1-fix-widgets-1a2b3c4d"
    assert order[0][2]["git_push_branches"] == [expected], order[0][2]


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["qa", "onboard"])
async def test_a_read_only_resume_enrolls_no_push_branch(pipeline):
    order: list[Any] = []
    ex = _executor(order)

    await ex._execute_workflow_stream(Command(resume={"q1": "yes"}), "iss-1", "job-1", pipeline=pipeline)

    assert order[0][2]["git_push_branches"] == [], order[0][2]


@pytest.mark.asyncio
async def test_a_claimed_resume_knows_the_job_was_paused_and_gives_the_pause_back():
    """#470: the route took the pause (it reads ``running``); a failed re-enroll still puts it back to ``paused``."""
    ex = _resume_executor(SandboxReenrollError("enroll returned HTTP 503", error_code=ErrorCode.SANDBOX_INIT))
    ex._jobs.get = AsyncMock(return_value={"job_id": "job-1", "status": "running"})

    try:
        await ex.resume("iss-1", "job-1", {"choice": "continue_retrying"}, claimed=True)
    except TypeError as exc:
        pytest.fail(f"a resume cannot be told the route already took its pause: {exc}")

    writes = [c.kwargs.get("status") for c in ex._jobs.update.await_args_list if c.kwargs.get("status")]
    assert "running" not in writes, f"the claimed resume wrote running again: {writes}"
    assert writes[-1:] == ["paused"], f"a claimed pause the resume could not run must be given back: {writes}"
