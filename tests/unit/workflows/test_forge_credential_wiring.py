"""Production passes the sandbox's bound forge credential to git identity setup.

``forge_credential`` is optional everywhere it is accepted, so a caller that
forgets it still constructs and runs — commits silently fall back to the
branding author (#369). These tests pin that each sandbox setup site hands
over ``sandbox_mgr.last_forge_credential(container_id)``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.runtime import DockerRuntime
from embry0.services.credentials.resolver import ResolvedCredential

_TOKEN = "a" * 43
_CID = "container-cred"


def _cred() -> ResolvedCredential:
    return ResolvedCredential(
        id="cred-1",
        kind="forge_token",
        provider="github",
        host="github.com",
        label="alice",
        secret="s",
        git_author_name="Alice",
        git_author_email="alice@example.com",
        source="registry",
        trace=(),
    )


class _Stop(Exception):
    """Ends the node right after the call under test."""


def _sandbox_mgr(cred: ResolvedCredential) -> MagicMock:
    mgr = MagicMock()
    mgr.create = AsyncMock(return_value=(_CID, _TOKEN))
    mgr.destroy = AsyncMock(return_value=None)
    mgr.last_forge_credential = MagicMock(return_value=cred)
    return mgr


def _docker() -> MagicMock:
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.build_exec_cmd = MagicMock(side_effect=lambda c, cmd, *a, **kw: ["docker", "exec", c, *cmd])
    docker.run_cmd = AsyncMock(return_value="")
    return docker


def _profiles(profile: dict) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=profile)
    return repo


@pytest.fixture
def captured_clone(monkeypatch) -> dict:
    """Patch prep_qa_sandbox_clone: record its kwargs, then stop the node."""
    seen: dict = {}

    async def fake_clone(**kwargs):
        seen.update(kwargs)
        raise _Stop

    monkeypatch.setattr("embry0.workflows.qa._subtask_prep.prep_qa_sandbox_clone", fake_clone)
    return seen


async def test_prep_clone_configures_the_credential_author():
    from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone

    docker = _docker()
    docker.run_cmd = AsyncMock(side_effect=["", "", "", "abc123\n"])
    proxy_mgr = MagicMock(git_proxy_url="http://git-proxy:9101", xai_proxy_url="")
    await prep_qa_sandbox_clone(
        docker=docker,
        proxy_mgr=proxy_mgr,
        container_id=_CID,
        sandbox_token=_TOKEN,
        job_id="job-1",
        repo="acme/widgets",
        branch="main",
        is_dind=False,
        qa_net="",
        forge_credential=_cred(),
    )
    first = docker.run_cmd.await_args_list[0].kwargs
    assert first.get("stdin_data") is not None, "embry0 must write its own git config on stdin (as root)"
    embry0s = first["stdin_data"].decode()
    assert '\temail = "alice@example.com"' in embry0s and '\tname = "Alice"' in embry0s, embry0s
    setup = " ".join(docker.run_cmd.await_args_list[1].args[0])
    assert "user.email alice@example.com" in setup, setup
    assert "user.name Alice" in setup, setup


async def test_init_orchestrator_node_passes_the_bound_credential(captured_clone):
    from embry0.workflows.qa.orchestrator import init_orchestrator_node

    cred = _cred()
    mgr = _sandbox_mgr(cred)
    config = {
        "configurable": {
            "docker": _docker(),
            "runtime": DockerRuntime(_docker()),
            "sandbox_manager": mgr,
            "profiles_repo": _profiles({"name": "slim"}),
        }
    }
    await init_orchestrator_node({"job_id": "job-orch", "repo": "acme/widgets"}, config)
    assert captured_clone.get("forge_credential") is cred, captured_clone.get("forge_credential")
    mgr.last_forge_credential.assert_called_with(_CID)


async def test_init_onboard_node_passes_the_bound_credential(captured_clone):
    from embry0.workflows.onboard.nodes import init_onboard_node

    cred = _cred()
    mgr = _sandbox_mgr(cred)
    config = {
        "configurable": {
            "docker": _docker(),
            "runtime": DockerRuntime(_docker()),
            "sandbox_manager": mgr,
            "profiles_repo": _profiles({"name": "slim"}),
        }
    }
    await init_onboard_node({"job_id": "job-ob", "repo": "acme/widgets"}, config)
    assert captured_clone.get("forge_credential") is cred, captured_clone.get("forge_credential")
    mgr.last_forge_credential.assert_called_with(_CID)


async def test_init_node_passes_the_bound_credential(monkeypatch):
    import embry0.workflows.issue_to_pr.nodes as nodes_module
    from embry0.workflows.issue_to_pr.nodes import init_node

    seen: dict = {}

    class _Initializer:
        def validate(self, context, config):
            return None

        async def initialize(self, ctx):
            seen["ctx"] = ctx
            raise _Stop

    monkeypatch.setattr("embry0.workspace_init.load_initializer", lambda _type: _Initializer())
    monkeypatch.setattr(nodes_module, "get_stream_writer", lambda: lambda _event: None, raising=False)

    cred = _cred()
    mgr = _sandbox_mgr(cred)
    configurable = {
        "sandbox_manager": mgr,
        "docker": _docker(),
        "runtime": DockerRuntime(_docker()),
        "proxy_manager": MagicMock(git_proxy_url=""),
    }
    # init_node wraps initializer failures in RuntimeError; the ctx is what matters.
    with pytest.raises(RuntimeError):
        await init_node(state={"job_id": "job-init", "repo": "acme/widgets"}, config={"configurable": configurable})
    assert getattr(seen.get("ctx"), "forge_credential", None) is cred, seen.get("ctx")
    mgr.last_forge_credential.assert_called_with(_CID)
