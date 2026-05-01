"""Test that IssueExecutor._run_workflow injects merged env vars into initial_state."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.services.issue_executor import IssueExecutor
from athanor.storage.encryption import FernetSecretsProvider


@pytest.mark.asyncio
async def test_run_workflow_injects_env_vars_from_repo():
    """_run_workflow must merge global + repo env and attach to initial_state.user_env_vars."""
    # Build a fake env_repo. Global has MODE=prod (config) and API_TOKEN (secret).
    # Repo overrides MODE=dev (config) and adds DATABASE_URL=pg://... (config).
    secret_key = "test-injection-key"
    provider = FernetSecretsProvider(secret_key=secret_key)
    encrypted_token = await provider.encrypt("secret-token-value")

    env_repo = MagicMock()
    env_repo.get_global = AsyncMock(
        return_value=[
            {"key": "MODE", "value": "prod", "var_type": "config", "description": ""},
            {
                "key": "API_TOKEN",
                "value": encrypted_token,
                "var_type": "secret",
                "description": "",
            },
        ]
    )
    env_repo.get_repo = AsyncMock(
        return_value=[
            {"key": "MODE", "value": "dev", "var_type": "config", "description": ""},
            {
                "key": "DATABASE_URL",
                "value": "pg://x",
                "var_type": "config",
                "description": "",
            },
        ]
    )

    jobs = MagicMock()
    jobs.get = AsyncMock(return_value={"trace_id": None, "pipeline_config": None})
    jobs.update = AsyncMock()

    issues = MagicMock()
    issues.update = AsyncMock()

    captured: dict = {}

    async def fake_execute_workflow_stream(input_value, issue_id, job_id):
        captured["initial_state"] = input_value
        # Return "no interrupt, no final_state" to short-circuit downstream logic
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
    executor._config = SimpleNamespace(environment_secret_key=secret_key)
    executor._sandbox = None
    executor._agent_runner = None
    executor._proxy = None
    executor._event_bus = None
    executor._env_repo = env_repo
    executor._background_tasks = set()
    executor._tasks_by_job = {}
    executor._execute_workflow_stream = fake_execute_workflow_stream  # type: ignore[assignment]
    # _cleanup_sandbox is called after the short-circuit return; stub it.
    executor._cleanup_sandbox = AsyncMock()  # type: ignore[assignment]

    issue = {"title": "Test issue", "body": "body text", "repo": "foo/bar", "github_number": 7}
    await executor._run_workflow("iss-1", "job-1", issue)

    state = captured["initial_state"]
    assert "user_env_vars" in state
    # New shape: list of {"key", "value", "scope"} dicts.
    envs = {row["key"]: row["value"] for row in state["user_env_vars"]}
    # Repo MODE wins over global MODE
    assert envs["MODE"] == "dev"
    # Repo-only var present
    assert envs["DATABASE_URL"] == "pg://x"
    # Secret was decrypted
    assert envs["API_TOKEN"] == "secret-token-value"
    # Scope defaults to 'app' when not provided by repo.
    scopes = {row["key"]: row.get("scope") for row in state["user_env_vars"]}
    assert scopes["MODE"] == "app"


@pytest.mark.asyncio
async def test_run_workflow_no_env_vars_when_env_repo_none():
    """If env_repo is None, no user_env_vars key is set on initial_state."""
    jobs = MagicMock()
    jobs.get = AsyncMock(return_value={"trace_id": None, "pipeline_config": None})
    jobs.update = AsyncMock()

    issues = MagicMock()
    issues.update = AsyncMock()

    captured: dict = {}

    async def fake_execute_workflow_stream(input_value, issue_id, job_id):
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
    executor._config = SimpleNamespace(environment_secret_key="")
    executor._sandbox = None
    executor._agent_runner = None
    executor._proxy = None
    executor._event_bus = None
    executor._env_repo = None
    executor._background_tasks = set()
    executor._tasks_by_job = {}
    executor._execute_workflow_stream = fake_execute_workflow_stream  # type: ignore[assignment]
    executor._cleanup_sandbox = AsyncMock()  # type: ignore[assignment]

    issue = {"title": "Test", "body": "", "repo": "foo/bar", "github_number": None}
    await executor._run_workflow("iss-1", "job-1", issue)

    state = captured["initial_state"]
    assert "user_env_vars" not in state
