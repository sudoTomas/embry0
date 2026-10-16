"""SandboxManager.reenroll: a paused job's sandbox gets proxy access back (#476, #475).

The credential proxies hold enrollments in memory with a 24 h TTL, and the
orchestrator recreates them on every start. A paused sandbox therefore loses
its bearer either way, and every agent call of the resumed job gets 401.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.sandbox_manager import SandboxInitError, SandboxManager, SandboxReenrollError
from embry0.safety.error_codes import ErrorCode
from embry0.services.credentials.resolver import ResolvedCredential

# Autouse: every sandbox on sandbox-restricted gets the isolation chain (#554),
# which the docker mock cannot write.
from tests.unit.execution.test_sandbox_manager import _stub_isolation_unless_overridden  # noqa: F401

NEW_TOKEN = "fresh-bearer-" + "y" * 40


def _resolved() -> ResolvedCredential:
    return ResolvedCredential(
        id="cred-gh",
        kind="forge_token",
        provider="github",
        host="github.com",
        label="alice",
        secret="tok-reg",
        git_author_name="Alice",
        git_author_email="alice@example.com",
        source="registry",
        trace=("repo:acme/widgets -> alice",),
    )


def _docker(running: str | Exception = "true") -> tuple[MagicMock, list[list[str]]]:
    calls: list[list[str]] = []
    docker = MagicMock()
    docker.build_exec_cmd = lambda container, cmd: ["exec", container, *cmd]
    docker.build_inspect_running_cmd = lambda container: ["inspect-running", container]

    async def run_cmd(cmd, timeout=60, **_kw):
        calls.append(list(cmd))
        if cmd and cmd[0] == "inspect-running":
            if isinstance(running, Exception):
                raise running
            return running
        return ""

    docker.run_cmd = run_cmd
    return docker, calls


def _proxy() -> MagicMock:
    pm = MagicMock()
    pm.enroll_sandbox = AsyncMock(return_value=NEW_TOKEN)
    pm.enroll_context = AsyncMock(return_value=["widgets-backend"])
    pm.git_proxy_url = "http://git-proxy:9101"
    pm.xai_proxy_url = "http://xai-proxy:9106"
    return pm


def _manager(docker: MagicMock, pm: MagicMock) -> SandboxManager:
    resolver = MagicMock()
    resolver.resolve_forge = AsyncMock(return_value=_resolved())
    return SandboxManager(docker=docker, proxy_manager=pm, credential_resolver=resolver)


async def test_reenroll_issues_a_fresh_bearer_to_every_proxy_and_into_the_sandbox():
    docker, calls = _docker()
    pm = _proxy()
    mgr = _manager(docker, pm)

    await mgr.reenroll(
        "cid-1", job_id="job-1", repo="acme/widgets", forge="github", xai_credential_id="cred-xai", context_scope=None
    )

    pm.enroll_sandbox.assert_awaited_once()
    args, kwargs = pm.enroll_sandbox.await_args
    assert args == ("cid-1",)
    assert kwargs["github_token"] == "tok-reg", "the forge payload must be the job's account, as at create"
    assert kwargs["forge"] == "github"
    assert kwargs["xai_credential_id"] == "cred-xai", "the proxy must serve the account the resume resolved"
    execs = [" ".join(c) for c in calls if c and c[0] == "exec"]
    assert any(NEW_TOKEN in c and "xai_proxy_token" in c for c in execs), f"xAI token file not rewritten: {execs}"
    assert any(NEW_TOKEN in c and "extraHeader" in c for c in execs), f"git's proxy bearer not rewritten: {execs}"
    pm.enroll_context.assert_not_awaited()


async def test_reenroll_makes_the_git_proxy_the_remote_and_keeps_the_push_right():
    """#521: a resumed sandbox reaches its repository through the git-proxy, with the right it was created with.

    A sandbox created before #521 still has a credential helper that asks the
    proxy for the forge token; the rewrite removes it.
    """
    docker, calls = _docker()
    pm = _proxy()
    mgr = _manager(docker, pm)

    await mgr.reenroll(
        "cid-1",
        job_id="job-1",
        repo="acme/widgets",
        forge="github",
        xai_credential_id=None,
        context_scope=None,
        git_push=True,
    )

    kwargs = pm.enroll_sandbox.await_args.kwargs
    assert kwargs["git_push"] is True and kwargs["git_upstream"] == "https://github.com", kwargs
    git = [" ".join(c) for c in calls if c and c[0] == "exec" and "insteadOf" in " ".join(c)]
    assert git, f"git's proxy config was not rewritten: {calls}"
    assert "'url.http://git-proxy:9101/git/acme/widgets.git.insteadOf' 'https://github.com/acme/widgets.git'" in git[0]
    assert "--unset-all credential.helper" in git[0] and "/git-credentials" not in git[0]


async def test_reenroll_is_read_only_unless_asked_and_uses_the_gitlab_origin():
    docker, calls = _docker()
    pm = _proxy()
    resolver = MagicMock()
    resolver.resolve_forge = AsyncMock(return_value=_resolved())
    mgr = SandboxManager(
        docker=docker, proxy_manager=pm, credential_resolver=resolver, gitlab_url="https://git.example.com/"
    )

    await mgr.reenroll(
        "cid-1", job_id="job-1", repo="group/proj", forge="gitlab", xai_credential_id=None, context_scope=None
    )

    kwargs = pm.enroll_sandbox.await_args.kwargs
    assert kwargs["git_push"] is False and kwargs["git_upstream"] == "https://git.example.com", kwargs
    git = [" ".join(c) for c in calls if c and c[0] == "exec" and "insteadOf" in " ".join(c)]
    assert "'https://git.example.com/group/proj.git'" in git[0]


async def test_reenroll_scopes_the_forge_api_proxies_to_the_job_repo():
    """#520: a resumed sandbox gets the same repository scope as at create."""
    docker, _ = _docker()
    pm = _proxy()
    mgr = _manager(docker, pm)

    await mgr.reenroll(
        "cid-1", job_id="job-1", repo="acme/widgets", forge="github", xai_credential_id=None, context_scope=None
    )

    kwargs = pm.enroll_sandbox.await_args.kwargs
    assert kwargs.get("repo") == "acme/widgets", f"reenroll must pass the job's repo to enroll_sandbox, got {kwargs}"


@pytest.mark.parametrize("running", [RuntimeError("Error: No such object: cid-1"), "false"])
async def test_a_missing_or_stopped_sandbox_is_sandbox_gone(running):
    """#475: resuming into a container that is gone used to burn every retry on
    'No such container' in milliseconds and record 0-token agent runs."""
    docker, _ = _docker(running)
    pm = _proxy()
    mgr = _manager(docker, pm)

    with pytest.raises(SandboxInitError) as exc:
        await mgr.reenroll(
            "cid-1", job_id="job-1", repo="acme/widgets", forge="github", xai_credential_id=None, context_scope=None
        )
    assert exc.value.error_code == ErrorCode.SANDBOX_GONE, f"expected ERR_SANDBOX_GONE, got {exc.value.error_code}"
    assert not isinstance(exc.value, SandboxReenrollError), "a gone sandbox is not retryable"
    pm.enroll_sandbox.assert_not_awaited()


@pytest.mark.parametrize(
    ("scope", "expected_kwargs"),
    [(True, {}), (["widgets-backend"], {"containers": ["widgets-backend"]})],
    ids=["watcher", "case-scope"],
)
async def test_reenroll_restores_the_context_proxy_scope(scope, expected_kwargs):
    docker, calls = _docker()
    pm = _proxy()
    mgr = _manager(docker, pm)

    await mgr.reenroll(
        "cid-1", job_id="job-1", repo="acme/widgets", forge="github", xai_credential_id=None, context_scope=scope
    )

    pm.enroll_context.assert_awaited_once_with("cid-1", NEW_TOKEN, **expected_kwargs)
    execs = [" ".join(c) for c in calls if c and c[0] == "exec"]
    assert any(NEW_TOKEN in c and "context_proxy_token" in c for c in execs), f"context token not rewritten: {execs}"


async def test_a_failed_enrollment_is_retryable():
    """A proxy that is restarting must not cost the paused job its sandbox."""
    docker, _ = _docker()
    pm = _proxy()
    pm.enroll_sandbox = AsyncMock(side_effect=RuntimeError("enroll git-proxy /admin/enroll returned HTTP 503"))
    mgr = _manager(docker, pm)

    with pytest.raises(SandboxReenrollError) as exc:
        await mgr.reenroll(
            "cid-1", job_id="job-1", repo="acme/widgets", forge="github", xai_credential_id=None, context_scope=None
        )
    assert exc.value.error_code == ErrorCode.SANDBOX_INIT


async def test_destroy_unregisters_the_bearer_reenroll_registered():
    """#39: the fresh bearer is a scrub target until the sandbox is destroyed, then released."""
    from embry0.safety.redaction import known_secret_values

    docker, _calls = _docker()
    pm = _proxy()
    pm.unenroll_sandbox = AsyncMock()
    mgr = _manager(docker, pm)
    await mgr.reenroll(
        "cid-1", job_id="job-1", repo="acme/widgets", forge="github", xai_credential_id=None, context_scope=None
    )
    assert NEW_TOKEN in known_secret_values(), "precondition: reenroll registered the fresh bearer"
    await mgr.destroy("cid-1")
    assert NEW_TOKEN not in known_secret_values(), "destroy unregisters the reenrolled bearer"
