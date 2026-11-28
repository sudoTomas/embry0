"""ProxyManager sends an xAI credential id only to the xai-proxy."""

from unittest.mock import AsyncMock

import pytest

from embry0.execution.proxy.manager import ProxyManager

_BARE = {"sandbox_id", "sandbox_token", "ttl_seconds"}


async def _manager(monkeypatch):
    pm = ProxyManager(docker=AsyncMock(), proxy_admin_token="x" * 20)
    pm._http = object()
    pm.git_proxy_url = "http://git-proxy:9101"
    pm.github_proxy_url = "http://github-proxy:9103"
    pm.gitlab_proxy_url = "http://gitlab-proxy:9105"
    pm.xai_proxy_url = "http://xai-proxy:9106"
    pm.auth_proxy_url = "http://auth-proxy:9100"
    captured: list[dict] = []

    async def fake_exec(**kwargs):
        captured.append(kwargs)
        body = kwargs.get("body") or {}
        if "allowed_repos" in body:  # a current forge API proxy echoes its scope (#520), git-proxy its grant (#521)
            return {k: body[k] for k in ("allowed_repos", "git_upstream", "git_push", "git_push_branches") if k in body}
        return {"credentials": {}}

    monkeypatch.setattr(pm, "_exec_admin_request", fake_exec)
    return pm, captured


def _body(captured, container):
    return next(c["body"] for c in captured if c["container"] == container)


@pytest.mark.asyncio
async def test_xai_credential_id_is_only_on_the_xai_proxy_body(monkeypatch) -> None:
    pm, captured = await _manager(monkeypatch)
    await pm.enroll_sandbox(
        "sb-1",
        github_token="ghp_SECRET",
        forge="github",
        xai_credential_id="cred-a",
    )
    assert _body(captured, "xai-proxy").get("xai_credential_id") == "cred-a", (
        "the xai-proxy enroll body must carry xai_credential_id"
    )
    for name in ("git-proxy", "github-proxy", "gitlab-proxy", "auth-proxy"):
        assert "xai_credential_id" not in _body(captured, name), f"{name} must not receive xai_credential_id"
    assert "github_token" not in _body(captured, "xai-proxy")
    assert set(_body(captured, "gitlab-proxy")) == _BARE | {"allowed_repos"}


@pytest.mark.asyncio
async def test_push_xai_token_names_the_credential(monkeypatch) -> None:
    pm, captured = await _manager(monkeypatch)
    await pm.push_xai_token("cred-a", "access-1", "2030-01-01T00:00:00Z")
    assert captured[-1]["path"] == "/admin/token"
    assert captured[-1]["body"] == {
        "credential_id": "cred-a",
        "access_token": "access-1",
        "expires_at": "2030-01-01T00:00:00Z",
    }, f"per-credential push body was {captured[-1]['body']!r}"


@pytest.mark.asyncio
async def test_xai_proxy_health_reads_admin_health(monkeypatch) -> None:
    pm, captured = await _manager(monkeypatch)
    report = await pm.xai_proxy_health()
    assert captured[-1]["path"] == "/admin/health", f"health must GET /admin/health, got {captured[-1]!r}"
    assert report == {"credentials": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [True, False])
async def test_the_metering_proxies_alone_are_told_whether_the_sandbox_is_held_to_runs(monkeypatch, required) -> None:
    """budget.refuse_calls_outside_runs goes with the enrollment to each metering proxy, and to no other."""
    pm, captured = await _manager(monkeypatch)
    try:
        await pm.enroll_sandbox("sb-1", github_token="ghp_SECRET", forge="github", require_run=required)
    except TypeError as exc:
        pytest.fail(f"an enrollment cannot say whether the sandbox is held to runs: {exc}")
    assert _body(captured, "xai-proxy").get("require_run") is required, _body(captured, "xai-proxy")
    for name in ("git-proxy", "github-proxy", "gitlab-proxy", "auth-proxy"):
        assert "require_run" not in _body(captured, name), f"{name} was told whether the sandbox is held to runs"


@pytest.mark.asyncio
async def test_a_metering_proxy_that_does_not_echo_the_requirement_is_logged(monkeypatch) -> None:
    """An image from before forwards a call outside a run, counted under no run: the orchestrator says so."""
    from structlog.testing import capture_logs

    pm, _captured = await _manager(monkeypatch)
    with capture_logs() as logs:
        try:
            await pm.enroll_sandbox("sb-1", github_token="ghp_SECRET", forge="github", require_run=True)
        except TypeError as exc:
            pytest.fail(f"an enrollment cannot say whether the sandbox is held to runs: {exc}")
    events = [e for e in logs if e.get("event") == "proxy_run_requirement_not_held"]
    assert [e.get("proxy") for e in events] == ["xai-proxy"], f"what was logged: {[e.get('event') for e in logs]}"
