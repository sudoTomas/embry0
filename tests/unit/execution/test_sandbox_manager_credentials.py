"""SandboxManager enrolls the credential the registry resolves (spec §3.2)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.sandbox_manager import SandboxInitError, SandboxManager
from embry0.safety.error_codes import ErrorCode
from embry0.services.credentials.resolver import (
    CredentialMissingError,
    CredentialUnavailableError,
    ResolvedCredential,
)

# Autouse: create() installs the private-range egress drop, which the docker mock cannot run.
from tests.unit.execution.test_sandbox_manager import (  # noqa: F401
    _stub_egress_unless_overridden,
    _stub_isolation_unless_overridden,
)


def _resolved(secret="tok-reg", host="github.com", provider="github", author="Alice"):
    return ResolvedCredential(
        id="cred-1",
        kind="forge_token",
        provider=provider,
        host=host,
        label="alice",
        secret=secret,
        git_author_name=author,
        git_author_email="alice@example.com",
        source="registry",
        trace=("repo:acme/widgets -> alice",),
    )


def _docker():
    # Copied from the `manager` fixture in test_sandbox_manager.py.
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-abc123")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])
    docker.build_stop_cmd = MagicMock(return_value=["docker", "stop", "..."])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "..."])
    docker.build_network_cmd = MagicMock(return_value=["docker", "network", "..."])
    return docker


def _proxy():
    pm = MagicMock()
    pm.enroll_sandbox = AsyncMock(return_value="bearer-token-" + "x" * 30)
    pm.unenroll_sandbox = AsyncMock()
    pm.git_proxy_url = "http://git-proxy:9101"
    pm.xai_proxy_url = ""
    return pm


async def test_create_enrolls_resolved_credential_and_remembers_it():
    resolver = MagicMock()
    resolver.resolve_forge = AsyncMock(return_value=_resolved())
    pm = _proxy()
    mgr = SandboxManager(docker=_docker(), proxy_manager=pm, credential_resolver=resolver)
    container_id, _ = await mgr.create("job-1", repo="acme/widgets", forge="github")
    resolver.resolve_forge.assert_awaited_once_with("acme/widgets", "github", job_id="job-1")
    kwargs = pm.enroll_sandbox.await_args.kwargs
    assert kwargs["github_token"] == "tok-reg" and kwargs["forge"] == "github"
    assert kwargs["git_host"] is None and kwargs["git_username"] is None
    assert mgr.last_forge_credential(container_id).label == "alice"


async def test_destroy_forgets_the_remembered_credential():
    resolver = MagicMock()
    resolver.resolve_forge = AsyncMock(return_value=_resolved())
    mgr = SandboxManager(docker=_docker(), proxy_manager=_proxy(), credential_resolver=resolver)
    container_id, _ = await mgr.create("job-1", repo="acme/widgets", forge="github")
    assert mgr.last_forge_credential(container_id) is not None
    await mgr.destroy(container_id)
    assert mgr.last_forge_credential(container_id) is None


async def test_gitlab_uses_credential_host():
    resolver = MagicMock()
    resolver.resolve_forge = AsyncMock(return_value=_resolved(host="gitlab.example.com", provider="gitlab"))
    pm = _proxy()
    mgr = SandboxManager(docker=_docker(), proxy_manager=pm, credential_resolver=resolver)
    await mgr.create("job-2", repo="group/proj", forge="gitlab")
    kwargs = pm.enroll_sandbox.await_args.kwargs
    assert kwargs["git_host"] == "gitlab.example.com"
    assert kwargs["git_username"] == "oauth2"
    assert kwargs["forge"] == "gitlab"


async def test_create_scopes_the_forge_api_proxies_to_the_job_repo():
    """#520: the enrollment carries the job's repo, or the forge API proxies forward nothing."""
    resolver = MagicMock()
    resolver.resolve_forge = AsyncMock(return_value=_resolved(host="gitlab.example.com", provider="gitlab"))
    pm = _proxy()
    mgr = SandboxManager(docker=_docker(), proxy_manager=pm, credential_resolver=resolver)
    await mgr.create("job-4", repo="group/sub/proj", forge="gitlab")
    kwargs = pm.enroll_sandbox.await_args.kwargs
    assert kwargs.get("repo") == "group/sub/proj", f"create must pass the job's repo to enroll_sandbox, got {kwargs}"


async def test_missing_credential_enrolls_without_payload():
    resolver = MagicMock()
    resolver.resolve_forge = AsyncMock(side_effect=CredentialMissingError("none"))
    pm = _proxy()
    mgr = SandboxManager(docker=_docker(), proxy_manager=pm, credential_resolver=resolver)
    container_id, _ = await mgr.create("job-3", repo="acme/widgets", forge="github")
    assert pm.enroll_sandbox.await_args.kwargs["github_token"] == ""
    assert pm.enroll_sandbox.await_args.kwargs["forge"] == "github"
    assert mgr.last_forge_credential(container_id) is None


async def test_unavailable_pinned_credential_fails_before_container():
    resolver = MagicMock()
    resolver.resolve_forge = AsyncMock(side_effect=CredentialUnavailableError("alice is pinned but disabled"))
    docker = _docker()
    mgr = SandboxManager(docker=docker, proxy_manager=_proxy(), credential_resolver=resolver)
    with pytest.raises(SandboxInitError) as exc:
        await mgr.create("job-4", repo="acme/widgets", forge="github")
    assert exc.value.error_code == ErrorCode.CREDENTIAL_UNAVAILABLE
    docker.build_run_cmd.assert_not_called()


async def test_without_resolver_env_path_unchanged(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN__ACME", "ghp_env")
    pm = _proxy()
    mgr = SandboxManager(docker=_docker(), proxy_manager=pm, github_token="ghp_default")
    await mgr.create("job-5", repo="acme/widgets", forge="github")
    assert pm.enroll_sandbox.await_args.kwargs["github_token"] == "ghp_env"


async def test_create_registers_the_sandbox_bearer_with_the_scrubber():
    """spec 3.6: the per-sandbox bearer must be an exact-value scrub target; it
    matches no pattern on its own."""
    from embry0.safety.redaction import known_secret_values, register_known_secrets

    register_known_secrets([], reset=True)
    try:
        resolver = MagicMock()
        resolver.resolve_forge = AsyncMock(return_value=_resolved())
        pm = _proxy()
        mgr = SandboxManager(docker=_docker(), proxy_manager=pm, credential_resolver=resolver)
        await mgr.create("job-1", repo="acme/widgets", forge="github")
        assert "bearer-token-" + "x" * 30 in known_secret_values(), "the bearer is registered as a known secret"
    finally:
        register_known_secrets([], reset=True)
