"""init_node: fail loud on an unregistered repo; publish state['base_branch']."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.safety.error_codes import ErrorCode, error_code_for_exception
from embry0.storage.repositories.repos import UnregisteredRepoError


class FakeRepos:
    def __init__(self, forge: str = "github", *, raises: Exception | None = None) -> None:
        self._forge = forge
        self._raises = raises

    async def forge_for(self, repo: str) -> str:
        if self._raises is not None:
            raise self._raises
        return self._forge


class FakePrefs:
    def __init__(self, base_branch: str | None) -> None:
        self._base_branch = base_branch

    async def get(self, repo: str):
        return {"base_branch": self._base_branch}


def _sandbox_mgr():
    mgr = MagicMock()
    mgr.create = AsyncMock(return_value=("container-abc", "a" * 43))
    mgr.destroy = AsyncMock()
    return mgr


def _docker():
    d = MagicMock()
    d.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    d.run_cmd = AsyncMock(return_value="")
    return d


async def _run(state, configurable):
    """Call init_node with the stream writer stubbed (test_nodes_init pattern)."""
    import embry0.workflows.issue_to_pr.nodes as nodes_module
    from embry0.workflows.issue_to_pr.nodes import init_node

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = lambda: lambda _event: None
    try:
        return await init_node(state=state, config={"configurable": configurable})
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig


@pytest.mark.asyncio
async def test_unregistered_repo_fails_before_any_sandbox_is_created():
    sandbox_mgr = _sandbox_mgr()
    with pytest.raises(UnregisteredRepoError):
        await _run(
            {"job_id": "job-1", "repo": "acme/group/backend"},
            {
                "sandbox_manager": sandbox_mgr,
                "docker": _docker(),
                "proxy_manager": MagicMock(git_proxy_url=""),
                "repos_repo": FakeRepos(raises=UnregisteredRepoError("not registered")),
            },
        )
    sandbox_mgr.create.assert_not_awaited()
    sandbox_mgr.destroy.assert_not_awaited()


@pytest.mark.asyncio
async def test_unregistered_repo_failure_carries_its_own_error_code():
    """Not ERR_SANDBOX_INIT — the operator must see what to fix."""
    exc = UnregisteredRepoError("repo 'x/y' is not registered")
    assert error_code_for_exception(exc) is ErrorCode.REPO_UNREGISTERED


@pytest.mark.asyncio
async def test_registry_outage_still_falls_back_to_github():
    """A DB blip must not change the forge (pre-#162 behaviour)."""
    update = await _run(
        {"job_id": "job-2", "repo": "o/r"},
        {
            "sandbox_manager": _sandbox_mgr(),
            "docker": _docker(),
            "proxy_manager": MagicMock(git_proxy_url=""),
            "repos_repo": FakeRepos(raises=RuntimeError("db down")),
        },
    )
    assert update["base_branch"] == "main"


@pytest.mark.asyncio
async def test_base_branch_preference_is_published_on_state():
    update = await _run(
        {"job_id": "job-3", "repo": "acme/group/backend"},
        {
            "sandbox_manager": _sandbox_mgr(),
            "docker": _docker(),
            "proxy_manager": MagicMock(git_proxy_url=""),
            "repos_repo": FakeRepos("gitlab"),
            "repo_preferences_repo": FakePrefs("dev"),
            "embry0_config": SimpleNamespace(gitlab_url="https://gitlab.example.com", gitlab_token=""),
        },
    )
    assert update["base_branch"] == "dev"


@pytest.mark.asyncio
async def test_job_override_beats_the_preference():
    update = await _run(
        {"job_id": "job-4", "repo": "o/r", "base_branch": "hotfix/urgent"},
        {
            "sandbox_manager": _sandbox_mgr(),
            "docker": _docker(),
            "proxy_manager": MagicMock(git_proxy_url=""),
            "repos_repo": FakeRepos("github"),
            "repo_preferences_repo": FakePrefs("dev"),
        },
    )
    assert update["base_branch"] == "hotfix/urgent"


@pytest.mark.asyncio
async def test_resolved_base_branch_reaches_the_clone_shell():
    docker = _docker()
    await _run(
        {"job_id": "job-5", "repo": "o/r"},
        {
            "sandbox_manager": _sandbox_mgr(),
            "docker": docker,
            "proxy_manager": MagicMock(git_proxy_url="http://git-proxy:9101"),
            "repos_repo": FakeRepos("github"),
            "repo_preferences_repo": FakePrefs("dev"),
        },
    )
    # build_exec_cmd(container_id, cmd_list) — the shell line is the last
    # element of the LAST argument, not of the container id.
    shell_lines = [
        call.args[-1][-1]
        for call in docker.build_exec_cmd.call_args_list
        if call.args
        and isinstance(call.args[-1], list)
        and call.args[-1]
        and "embry0_git clone" in str(call.args[-1][-1])
    ]
    assert shell_lines, "expected a clone command"
    assert "fetch --no-recurse-submodules origin dev:dev" in shell_lines[0]


@pytest.mark.asyncio
async def test_base_branch_source_is_published_alongside_the_branch():
    """Downstream nodes must be able to tell "main, because the project says
    so" from "main, because nobody knew" — see JobState.base_branch_source."""
    update = await _run(
        {"job_id": "job-6", "repo": "acme/group/backend"},
        {
            "sandbox_manager": _sandbox_mgr(),
            "docker": _docker(),
            "proxy_manager": MagicMock(git_proxy_url=""),
            "repos_repo": FakeRepos("gitlab"),
            "repo_preferences_repo": FakePrefs("dev"),
            "embry0_config": SimpleNamespace(gitlab_url="https://gitlab.example.com", gitlab_token=""),
        },
    )
    assert (update["base_branch"], update["base_branch_source"]) == ("dev", "preference")


@pytest.mark.asyncio
async def test_a_guessed_base_branch_is_labelled_fallback_not_forge():
    """No token, so no forge client is built and nothing answers."""
    from embry0.services.base_branch import FALLBACK_SOURCE

    update = await _run(
        {"job_id": "job-7", "repo": "acme/group/backend"},
        {
            "sandbox_manager": _sandbox_mgr(),
            "docker": _docker(),
            "proxy_manager": MagicMock(git_proxy_url=""),
            "repos_repo": FakeRepos("gitlab"),
            "repo_preferences_repo": FakePrefs(None),
            "embry0_config": SimpleNamespace(gitlab_url="https://gitlab.example.com", gitlab_token=""),
        },
    )
    assert (update["base_branch"], update["base_branch_source"]) == ("main", FALLBACK_SOURCE)
