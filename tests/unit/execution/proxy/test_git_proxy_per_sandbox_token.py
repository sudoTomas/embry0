"""git-proxy authenticates each sandbox with its own enrolled token, else the global one (#521: upstream only)."""

import pytest

from embry0.execution.proxy.git_proxy import create_git_proxy_app

from .git_forge import ADMIN, ProxyRig, RecordingUpstream, basic

GLOBAL = "ghp_GLOBAL"
TOK = "tok-" + "a" * 32


@pytest.fixture
async def rig():
    async with ProxyRig(create_git_proxy_app(github_token=GLOBAL, admin_token=ADMIN), RecordingUpstream()) as r:
        yield r


async def _enroll(rig: ProxyRig, sid: str, tok: str, github_token: str | None = None) -> None:
    extra = {"github_token": github_token} if github_token else {}
    await rig.enroll(sid, tok, git_host="127.0.0.1", git_username="x-access-token", **extra)


async def test_per_sandbox_token_used(rig):
    await _enroll(rig, "s1", TOK, github_token="ghp_ACME")
    assert await rig.credential_sent(TOK) == basic("x-access-token", "ghp_ACME")


async def test_falls_back_to_global_without_per_sandbox_token(rig):
    await _enroll(rig, "s1", TOK)
    assert await rig.credential_sent(TOK) == basic("x-access-token", GLOBAL)


async def test_two_sandboxes_get_their_own_tokens(rig):
    t1, t2 = "tok-1-" + "a" * 32, "tok-2-" + "b" * 32
    await _enroll(rig, "s1", t1, github_token="ghp_A")
    await _enroll(rig, "s2", t2, github_token="ghp_B")
    assert await rig.credential_sent(t1) == basic("x-access-token", "ghp_A")
    assert await rig.credential_sent(t2) == basic("x-access-token", "ghp_B")


async def test_unenroll_clears_per_sandbox_token(rig):
    await _enroll(rig, "s1", TOK, github_token="ghp_ACME")
    await rig.client.delete("/admin/enroll/s1", headers={"X-Admin-Token": ADMIN})
    assert await rig.credential_sent(TOK) is None


async def test_reenroll_overwrites_per_sandbox_token(rig):
    t1, t2 = "tok-a-" + "a" * 32, "tok-b-" + "b" * 32
    await _enroll(rig, "s1", t1, github_token="ghp_A")
    await _enroll(rig, "s1", t2, github_token="ghp_B")  # re-enroll same sandbox_id
    assert await rig.credential_sent(t2) == basic("x-access-token", "ghp_B")


async def test_reenroll_without_token_resets_to_global(rig):
    t1, t2 = "tok-c-" + "c" * 32, "tok-d-" + "d" * 32
    await _enroll(rig, "s1", t1, github_token="ghp_A")
    await _enroll(rig, "s1", t2)  # re-enroll with NO github_token
    assert await rig.credential_sent(t2) == basic("x-access-token", GLOBAL)


async def test_no_credential_at_all_forwards_anonymously():
    """A deployment with no credential for this forge still clones public repositories."""
    async with ProxyRig(create_git_proxy_app(github_token="", admin_token=ADMIN), RecordingUpstream()) as rig:
        await _enroll(rig, "s1", TOK)
        assert await rig.credential_sent(TOK) == ""
