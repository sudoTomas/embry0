"""RAV-1079: _run_workflow's pre-graph open-PR dedup (skip+link policy).

Harness mirrors test_job_create_overrides: executor via __new__, fake
_execute_workflow_stream capturing initial_state. The OpenPRService fetch
is patched at the class method so no network is ever touched.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.services.issue_executor import IssueExecutor

_OPEN_PRS = [
    {
        "number": 366,
        "title": "fix(msgraph): stop stub deals",
        "head_ref": "embry0/iss-eafd1fec-eng-1066-watcher",
        "url": "https://github.com/o/r/pull/366",
        "author": "someone",
        "issue_refs": ["ENG-1066"],
        "closes_numbers": [],
        "issue_prefix": "iss-eafd1fec",
    }
]


def _executor(job_row: dict) -> tuple[IssueExecutor, dict]:
    jobs = MagicMock()
    jobs.get = AsyncMock(return_value=job_row)
    jobs.update = AsyncMock()

    issues = MagicMock()
    issues.get = AsyncMock(return_value=None)
    issues.update = AsyncMock()
    issues.update_parent_status = AsyncMock()

    captured: dict = {}

    async def fake_execute_workflow_stream(input_value, issue_id, job_id, state_sink=None):
        captured["initial_state"] = input_value
        return None, False, None

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    executor._issues = issues
    executor._traces = MagicMock()
    executor._registry = MagicMock()
    executor._database_url = ""
    executor._audit_log_path = None
    executor._db = None
    executor._inputs = None
    executor._config = SimpleNamespace(environment_secret_key="k")
    executor._sandbox = None
    executor._agent_runner = None
    executor._proxy = None
    executor._event_bus = None
    executor._env_repo = None
    executor._github_token = "tok"
    executor._background_tasks = set()
    executor._tasks_by_job = {}
    executor._execute_workflow_stream = fake_execute_workflow_stream  # type: ignore[assignment]
    executor._cleanup_sandbox = AsyncMock()  # type: ignore[assignment]
    return executor, captured


def _patch_open_prs(prs):
    return patch(
        "embry0.services.github_prs.OpenPRService.list_open_prs",
        new=AsyncMock(return_value=prs),
    )


@pytest.mark.asyncio
async def test_open_prs_injected_into_state_when_no_duplicate():
    executor, captured = _executor({"trace_id": None, "pipeline_config": None})
    issue = {"title": "t", "body": "", "repo": "o/r", "tracker": "linear", "tracker_key": "ENG-2000"}
    with _patch_open_prs(list(_OPEN_PRS)):
        await executor._run_workflow("iss-00000000ffff", "job-1", issue)

    state = captured["initial_state"]
    assert state["open_prs"] == _OPEN_PRS
    # No duplicate → the graph ran (the harness's None final_state then marks
    # the job completed via the pre-existing path — WITHOUT a linked PR).
    skip_calls = [c for c in executor._jobs.update.call_args_list if c.kwargs.get("pr_url")]
    assert not skip_calls


@pytest.mark.asyncio
async def test_duplicate_ticket_skips_graph_and_links_pr():
    executor, captured = _executor({"trace_id": None, "pipeline_config": None})
    issue = {"title": "t", "body": "", "repo": "o/r", "tracker": "linear", "tracker_key": "ENG-1066"}
    with _patch_open_prs(list(_OPEN_PRS)):
        await executor._run_workflow("iss-00000000ffff", "job-2", issue)

    # The graph never ran…
    assert "initial_state" not in captured
    # …and the job ended skipped (#474), naming the existing PR in its summary
    # without claiming it: no pr_url, the issue stays open.
    final = next(c for c in executor._jobs.update.call_args_list if c.kwargs.get("status") == "skipped")
    assert "pr_url" not in final.kwargs, "the skipped job recorded someone else's PR as its own"
    assert "already exists: #366" in final.kwargs["result_summary"]
    assert "https://github.com/o/r/pull/366" in final.kwargs["result_summary"]
    assert not [c for c in executor._jobs.update.call_args_list if c.kwargs.get("status") == "completed"]
    executor._issues.update.assert_any_call("iss-00000000ffff", status="open")
    assert ("iss-00000000ffff",) not in [
        c.args for c in executor._issues.update.call_args_list if c.kwargs.get("status") == "closed"
    ], "a skip closed the issue although the fix is not merged"


@pytest.mark.asyncio
async def test_followup_jobs_with_existing_pr_are_exempt():
    """A pr-followup job (pr_url set at dispatch, context.ref carries the
    branch) targets its PR deliberately — no fetch, no skip, and the known
    PR is seeded onto state for the developer prompt."""
    executor, captured = _executor(
        {
            "trace_id": None,
            "pipeline_config": None,
            "pr_url": "https://github.com/o/r/pull/366",
            "context": {"type": "git", "repo": "o/r", "ref": "embry0/iss-eafd1fec-eng-1066-watcher"},
        }
    )
    issue = {"title": "t", "body": "", "repo": "o/r", "tracker": "linear", "tracker_key": "ENG-1066"}
    fetch = AsyncMock(return_value=list(_OPEN_PRS))
    with patch("embry0.services.github_prs.OpenPRService.list_open_prs", new=fetch):
        await executor._run_workflow(None, "job-3", issue)

    fetch.assert_not_awaited()
    state = captured["initial_state"]
    assert state["pr_url"] == "https://github.com/o/r/pull/366"
    assert "open_prs" not in state


@pytest.mark.asyncio
async def test_a_failed_listing_fails_the_job_closed_before_the_graph():
    """#305: a listing error used to read as "no open PRs", and the job ran
    on and could open a duplicate. It now stops the job before the graph,
    with its own code, and hands the issue back."""
    import httpx

    from embry0.safety.error_codes import ErrorCode

    executor, captured = _executor({"trace_id": None, "pipeline_config": None})
    executor._post_tracker_outcome = AsyncMock()  # type: ignore[method-assign]
    executor._finish_credential_uses = AsyncMock()  # type: ignore[method-assign]
    executor._emit_audit = AsyncMock()  # type: ignore[method-assign]
    executor._traces = None
    issue = {"title": "t", "body": "", "repo": "o/r", "tracker": "linear", "tracker_key": "ENG-2000"}
    with (
        patch("embry0.services.github_prs.GitHubClient.get", new=AsyncMock(side_effect=httpx.ConnectError("down"))),
        patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
    ):
        await executor._run_workflow("iss-00000000ffff", "job-7", issue)

    assert "initial_state" not in captured, "the graph ran although the open PRs could not be listed"
    failed = [c for c in executor._jobs.update.call_args_list if c.kwargs.get("status") == "failed"]
    assert failed, "a failed listing did not fail the job"
    assert failed[-1].kwargs["error_code"] == ErrorCode.PR_LISTING_FAILED.value
    assert "duplicate-PR check cannot run" in failed[-1].kwargs["error_message"]
    executor._issues.update.assert_any_call("iss-00000000ffff", status="open")


@pytest.mark.asyncio
async def test_no_open_prs_runs_the_graph_without_a_list():
    executor, captured = _executor({"trace_id": None, "pipeline_config": None})
    issue = {"title": "t", "body": "", "repo": "o/r", "tracker": "linear", "tracker_key": "ENG-1066"}
    with _patch_open_prs([]):
        await executor._run_workflow("iss-00000000ffff", "job-4", issue)

    state = captured["initial_state"]
    assert "open_prs" not in state


@pytest.mark.asyncio
async def test_dedup_disabled_by_config_flag():
    executor, captured = _executor({"trace_id": None, "pipeline_config": None})
    executor._config = SimpleNamespace(environment_secret_key="k", pr_dedup_enabled=False)
    issue = {"title": "t", "body": "", "repo": "o/r", "tracker": "linear", "tracker_key": "ENG-1066"}
    fetch = AsyncMock(return_value=list(_OPEN_PRS))
    with patch("embry0.services.github_prs.OpenPRService.list_open_prs", new=fetch):
        await executor._run_workflow("iss-00000000ffff", "job-5", issue)

    fetch.assert_not_awaited()
    assert "open_prs" not in captured["initial_state"]


@pytest.mark.asyncio
async def test_no_github_token_skips_fetch():
    """The __new__-construction contract: executors without _github_token
    (or with an empty one) never attempt a fetch."""
    executor, captured = _executor({"trace_id": None, "pipeline_config": None})
    executor._github_token = ""
    issue = {"title": "t", "body": "", "repo": "o/r", "tracker": "linear", "tracker_key": "ENG-1066"}
    fetch = AsyncMock(return_value=list(_OPEN_PRS))
    with patch("embry0.services.github_prs.OpenPRService.list_open_prs", new=fetch):
        await executor._run_workflow("iss-00000000ffff", "job-6", issue)

    fetch.assert_not_awaited()
    assert "initial_state" in captured


# ---- only a repository on GitHub has its open pull requests listed there ------------------------------------------


class _Repos:
    """The repository registry: ``o/r`` registered on ``forge``; ``fails`` makes every lookup fail. Its configured
    default is GitHub, as shipped, so a resolver falling back to it would read a failed lookup as GitHub."""

    default_forge = "github"

    def __init__(self, forge: str = "gitlab", *, fails: bool = False) -> None:
        self.forge, self.fails = forge, fails

    async def forge_for(self, repo: str) -> str:
        if self.fails:
            raise RuntimeError("registry down")
        return self.forge


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repos",
    [_Repos("gitlab"), _Repos(fails=True)],
    ids=["on-gitlab", "forge-unread-default-github"],
)
async def test_a_repository_not_on_github_lists_no_github_pull_requests(repos: _Repos):
    """A GitLab repository with a GitHub token at hand: github.com/<its path> is another repository, whose pull
    request naming the job's ticket must neither end the job as its duplicate nor reach its prompt."""
    executor, captured = _executor({"trace_id": None, "pipeline_config": None})
    executor._repos = repos
    issue = {"title": "t", "body": "", "repo": "o/r", "tracker": "linear", "tracker_key": "ENG-1066"}
    listed = AsyncMock(return_value=list(_OPEN_PRS))
    with patch("embry0.services.github_prs.OpenPRService.list_open_prs", new=listed):
        await executor._run_workflow("iss-00000000ffff", "job-9", issue)

    listed.assert_not_awaited()
    state = captured.get("initial_state")
    assert state is not None, "the job runs: no duplicate was found on another forge"
    assert "open_prs" not in state, "no other repository's pull request titles reach the prompt"
    assert not [c for c in executor._jobs.update.call_args_list if c.kwargs.get("status") == "skipped"]


@pytest.mark.asyncio
async def test_a_repository_registered_on_github_is_still_deduplicated():
    executor, captured = _executor({"trace_id": None, "pipeline_config": None})
    executor._repos = _Repos("github")
    issue = {"title": "t", "body": "", "repo": "o/r", "tracker": "linear", "tracker_key": "ENG-1066"}
    with _patch_open_prs(list(_OPEN_PRS)):
        await executor._run_workflow("iss-00000000ffff", "job-10", issue)

    assert "initial_state" not in captured
    assert [c for c in executor._jobs.update.call_args_list if c.kwargs.get("status") == "skipped"]
