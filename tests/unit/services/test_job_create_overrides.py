"""RAV-1058: _run_workflow surfaces job-create overrides on initial_state.

JobCreateRequest.max_budget_usd / execution_mode_override / auth_mode_override
ride the job row's pipeline_config (written by POST /jobs); sandbox_profile and
pipeline_template ride their own job-row columns. All five were previously
accepted by the schema and silently dropped on the direct-jobs path.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.services.issue_executor import IssueExecutor


def _executor_with_job_row(job_row: dict) -> tuple[IssueExecutor, dict]:
    jobs = MagicMock()
    jobs.get = AsyncMock(return_value=job_row)
    jobs.update = AsyncMock()

    captured: dict = {}

    async def fake_execute_workflow_stream(input_value, issue_id, job_id, state_sink=None):
        captured["initial_state"] = input_value
        return None, False, None

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    executor._issues = MagicMock()
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
    executor._background_tasks = set()
    executor._tasks_by_job = {}
    executor._execute_workflow_stream = fake_execute_workflow_stream  # type: ignore[assignment]
    executor._cleanup_sandbox = AsyncMock()  # type: ignore[assignment]
    return executor, captured


_ISSUE = {"title": "t", "body": "", "repo": "foo/bar", "github_number": None}


@pytest.mark.asyncio
async def test_run_workflow_surfaces_pipeline_config_overrides():
    executor, captured = _executor_with_job_row(
        {
            "trace_id": None,
            "pipeline_config": {
                "agent_models_override": {"developer": "claude-opus-4-6"},
                "max_budget_usd": 42.5,
                "budget_overrun_mode": "hard",
                "execution_mode_override": "cli",
                "auth_mode_override": "oauth",
                "open_pr": True,
            },
        }
    )
    await executor._run_workflow(None, "job-1", _ISSUE)

    state = captured["initial_state"]
    assert state["agent_models_override"] == {"developer": "claude-opus-4-6"}
    assert state["max_budget_usd"] == 42.5
    assert state["budget_overrun_mode_override"] == "hard"
    assert state["execution_mode_override"] == "cli"
    assert state["auth_mode_override"] == "oauth"
    assert state["open_pr"] is True, "a context audit's open_pr reaches the output step (ADR 0022)"


@pytest.mark.asyncio
async def test_run_workflow_surfaces_profile_and_template_columns():
    executor, captured = _executor_with_job_row(
        {
            "trace_id": None,
            "pipeline_config": None,
            "sandbox_profile": "dev-python",
            "pipeline_template": "quick-fix",
        }
    )
    await executor._run_workflow(None, "job-2", _ISSUE)

    state = captured["initial_state"]
    assert state["sandbox_profile"] == "dev-python"
    assert state["pipeline_template_request"] == "quick-fix"


@pytest.mark.asyncio
async def test_run_workflow_omits_absent_overrides():
    executor, captured = _executor_with_job_row({"trace_id": None, "pipeline_config": None})
    await executor._run_workflow(None, "job-3", _ISSUE)

    state = captured["initial_state"]
    for key in (
        "max_budget_usd",
        "budget_overrun_mode_override",
        "execution_mode_override",
        "auth_mode_override",
        "sandbox_profile",
        "pipeline_template_request",
        "open_pr",
    ):
        assert key not in state, f"{key} must not be set when the job row carries no override"


@pytest.mark.asyncio
async def test_run_workflow_opens_no_pr_for_an_open_pr_that_is_not_true():
    executor, captured = _executor_with_job_row({"trace_id": None, "pipeline_config": {"open_pr": "yes"}})
    await executor._run_workflow(None, "job-4", _ISSUE)
    assert "open_pr" not in captured["initial_state"], "only a stored true asks for a pull request"


# ---------------------------------------------------------------------------
# RAV-1246: repo_preferences.sandbox_profile must be resolved on the executor
# path (the graph is init→triage, so a triage-side override runs after the
# sandbox already exists — the executor is the only place early enough).
# ---------------------------------------------------------------------------


def _prefs_repo(row):
    prefs = MagicMock()
    prefs.get = AsyncMock(return_value=row)
    return prefs


@pytest.mark.asyncio
async def test_run_workflow_seeds_sandbox_profile_from_repo_preferences():
    """Label-dispatch path (no job-create override): the per-repo preference
    reaches initial_state, so init_node builds the right container."""
    executor, captured = _executor_with_job_row({"trace_id": None, "pipeline_config": None})
    executor._repo_prefs = _prefs_repo({"repo": "foo/bar", "sandbox_profile": "dev-python"})

    await executor._run_workflow(None, "job-4", _ISSUE)

    executor._repo_prefs.get.assert_awaited_once_with("foo/bar")
    assert captured["initial_state"]["sandbox_profile"] == "dev-python"


@pytest.mark.asyncio
async def test_run_workflow_job_create_profile_outranks_repo_preferences():
    """An explicit job-create sandbox_profile wins over the repo preference.

    (Prefs are still fetched — RAV-1254 reads dev_checks off the same row —
    but the profile they carry must not override the job-create choice.)"""
    executor, captured = _executor_with_job_row(
        {"trace_id": None, "pipeline_config": None, "sandbox_profile": "qa-jvm"}
    )
    executor._repo_prefs = _prefs_repo({"repo": "foo/bar", "sandbox_profile": "dev-python"})

    await executor._run_workflow(None, "job-5", _ISSUE)

    assert captured["initial_state"]["sandbox_profile"] == "qa-jvm"


@pytest.mark.asyncio
async def test_run_workflow_repo_preferences_null_profile_leaves_default():
    """A prefs row without sandbox_profile must not set the key (SandboxManager
    falls back to its default profile)."""
    executor, captured = _executor_with_job_row({"trace_id": None, "pipeline_config": None})
    executor._repo_prefs = _prefs_repo({"repo": "foo/bar", "sandbox_profile": None})

    await executor._run_workflow(None, "job-6", _ISSUE)

    assert "sandbox_profile" not in captured["initial_state"]


@pytest.mark.asyncio
async def test_run_workflow_repo_preferences_fetch_failure_is_fail_open():
    """A prefs read failure degrades to the default profile, not a dead job."""
    executor, captured = _executor_with_job_row({"trace_id": None, "pipeline_config": None})
    prefs = MagicMock()
    prefs.get = AsyncMock(side_effect=RuntimeError("db down"))
    executor._repo_prefs = prefs

    await executor._run_workflow(None, "job-7", _ISSUE)

    assert "sandbox_profile" not in captured["initial_state"]


@pytest.mark.asyncio
async def test_run_workflow_seeds_dev_checks_from_repo_preferences():
    """RAV-1254: repo_preferences.dev_checks ride state into the developer prompt."""
    checks = [{"name": "python", "paths": ["ml"], "command": "cd ml && poetry run pytest -q"}]
    executor, captured = _executor_with_job_row({"trace_id": None, "pipeline_config": None})
    executor._repo_prefs = _prefs_repo({"repo": "foo/bar", "sandbox_profile": None, "dev_checks": checks})

    await executor._run_workflow(None, "job-8", _ISSUE)

    assert captured["initial_state"]["dev_checks"] == checks
    assert "sandbox_profile" not in captured["initial_state"]


@pytest.mark.asyncio
async def test_run_workflow_omits_dev_checks_when_empty():
    executor, captured = _executor_with_job_row({"trace_id": None, "pipeline_config": None})
    executor._repo_prefs = _prefs_repo({"repo": "foo/bar", "sandbox_profile": "dev-python", "dev_checks": []})

    await executor._run_workflow(None, "job-9", _ISSUE)

    assert "dev_checks" not in captured["initial_state"]
    assert captured["initial_state"]["sandbox_profile"] == "dev-python"
