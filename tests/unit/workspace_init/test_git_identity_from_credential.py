"""The git initializer configures the bound account's author (spec §3.3)."""

from unittest.mock import AsyncMock, MagicMock

from embry0.services.credentials.resolver import ResolvedCredential
from embry0.workspace_init.base import InitContext
from embry0.workspace_init.git import GitWorkspaceInitializer


async def test_initializer_sets_credential_author():
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="")
    docker.build_exec_cmd = MagicMock(side_effect=lambda c, cmd, **_kw: ["exec", c, *cmd])
    cred = ResolvedCredential(
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
    ctx = InitContext(
        job_id="job-1",
        context={"type": "git", "repo": "acme/widgets"},
        container_id="c1",
        sandbox_token="t" * 43,
        docker=docker,
        git_proxy_url="http://git-proxy:9101",
        forge_credential=cred,
    )
    await GitWorkspaceInitializer().initialize(ctx)
    first = docker.run_cmd.await_args_list[0].kwargs
    assert first.get("stdin_data") is not None, "embry0 must write its own git config on stdin (as root)"
    embry0s = first["stdin_data"].decode()
    assert '\tname = "Alice"' in embry0s and '\temail = "alice@example.com"' in embry0s, "embry0's own commits"
    agents = " ".join(docker.run_cmd.await_args_list[1].args[0])
    assert "user.email alice@example.com" in agents and "user.name Alice" in agents, "the agent's own commits"
