"""Enrollment hardening tests (RAV-1070): TTL, registry cap, admin backoff.

The registry/guard units are tested directly with an injected clock; the
end-to-end paths (TTL via enroll body, backoff over HTTP, expiry rejection at
the credentials endpoint) go through a real git-proxy app.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from embry0.execution.proxy.enrollment import (
    AdminGuard,
    EnrollmentRegistry,
    RegistryFullError,
)
from embry0.execution.proxy.git_proxy import create_git_proxy_app

ADMIN = "test-admin-secret-not-real"
PAT = "ghp_PAT_TESTING_ONLY"
TOK = "tok-ttl-" + "a" * 30


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# ---- EnrollmentRegistry unit ----


def test_enrollment_expires_after_ttl():
    clock = Clock()
    reg = EnrollmentRegistry(default_ttl_seconds=100, now=clock)
    reg.enroll("s1", TOK)
    assert reg.lookup(TOK) == "s1"
    clock.t += 101
    assert reg.lookup(TOK) is None
    assert len(reg) == 0  # expired entry dropped, not just hidden


def test_explicit_ttl_overrides_default():
    clock = Clock()
    reg = EnrollmentRegistry(default_ttl_seconds=100, now=clock)
    reg.enroll("s1", TOK, ttl_seconds=10)
    clock.t += 50
    assert reg.lookup(TOK) is None


def test_ttl_clamped_to_max():
    from embry0.execution.proxy.enrollment import MAX_TTL_SECONDS

    clock = Clock()
    reg = EnrollmentRegistry(now=clock)
    reg.enroll("s1", TOK, ttl_seconds=MAX_TTL_SECONDS * 100)
    clock.t += MAX_TTL_SECONDS + 1
    assert reg.lookup(TOK) is None


def test_reenroll_refreshes_ttl_and_replaces_token():
    clock = Clock()
    reg = EnrollmentRegistry(default_ttl_seconds=100, now=clock)
    reg.enroll("s1", TOK)
    clock.t += 90
    reg.enroll("s1", "tok-new-" + "b" * 30)
    clock.t += 90  # 180 past the first enroll, 90 past the second
    assert reg.lookup(TOK) is None  # old token replaced
    assert reg.lookup("tok-new-" + "b" * 30) == "s1"


def test_registry_cap_rejects_when_full():
    reg = EnrollmentRegistry(max_entries=2)
    reg.enroll("s1", "tok-1-" + "a" * 30)
    reg.enroll("s2", "tok-2-" + "b" * 30)
    with pytest.raises(RegistryFullError):
        reg.enroll("s3", "tok-3-" + "c" * 30)
    # Re-enrolling an existing id is NOT a new entry — always allowed.
    reg.enroll("s1", "tok-1b-" + "d" * 30)


def test_registry_cap_purges_expired_first():
    clock = Clock()
    reg = EnrollmentRegistry(max_entries=1, default_ttl_seconds=100, now=clock)
    reg.enroll("s1", "tok-1-" + "a" * 30)
    clock.t += 101
    reg.enroll("s2", "tok-2-" + "b" * 30)  # s1 expired → slot free
    assert reg.lookup("tok-2-" + "b" * 30) == "s2"


# ---- AdminGuard unit ----


def test_admin_guard_locks_after_repeated_failures():
    clock = Clock()
    guard = AdminGuard("secret", now=clock)
    for _ in range(9):
        assert guard.check("wrong") == "unauthorized"
    assert guard.check("wrong") == "locked"  # 10th failure trips the lock
    assert guard.check("secret") == "locked"  # even the right token waits out the cooldown
    clock.t += 61
    assert guard.check("secret") == "ok"


def test_admin_guard_success_clears_failure_window():
    clock = Clock()
    guard = AdminGuard("secret", now=clock)
    for _ in range(9):
        guard.check("wrong")
    assert guard.check("secret") == "ok"
    for _ in range(9):
        assert guard.check("wrong") == "unauthorized"  # counter restarted


def test_admin_guard_empty_token_required():
    with pytest.raises(ValueError):
        AdminGuard("")


# ---- End-to-end through a real proxy app ----


@pytest.fixture
async def client():
    app = create_git_proxy_app(github_token=PAT, admin_token=ADMIN)
    server = TestServer(app)
    async with TestClient(server) as c:
        yield c


async def test_enroll_accepts_ttl_and_expiry_cuts_credentials(client):
    clock = Clock()
    client.app["enrollment"]._now = clock  # inject the clock post-construction
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK, "ttl_seconds": 60},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 200
    # 410 is the answer to an accepted bearer (#521: no credential is handed
    # out); 401 is the answer to one the proxy no longer knows.
    ok = await client.get("/git-credentials", headers={"Authorization": f"Bearer {TOK}"})
    assert ok.status == 410
    clock.t += 61
    expired = await client.get("/git-credentials", headers={"Authorization": f"Bearer {TOK}"})
    assert expired.status == 401


async def test_enroll_rejects_bad_ttl(client):
    for bad in (0, -5, "sixty", True):
        resp = await client.post(
            "/admin/enroll",
            json={"sandbox_id": "s1", "sandbox_token": TOK, "ttl_seconds": bad},
            headers={"X-Admin-Token": ADMIN},
        )
        assert resp.status == 400, f"ttl_seconds={bad!r} should 400"


async def test_admin_backoff_over_http(client):
    for _ in range(9):
        resp = await client.post(
            "/admin/enroll",
            json={"sandbox_id": "s1", "sandbox_token": TOK},
            headers={"X-Admin-Token": "wrong"},
        )
        assert resp.status == 401
    locked = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK},
        headers={"X-Admin-Token": "wrong"},
    )
    assert locked.status == 429
    assert "Retry-After" in locked.headers
    # Valid admin is locked out too until the cooldown passes.
    also_locked = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK},
        headers={"X-Admin-Token": ADMIN},
    )
    assert also_locked.status == 429


async def test_registry_full_maps_to_429(client):
    client.app["enrollment"]._max_entries = 1
    first = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK},
        headers={"X-Admin-Token": ADMIN},
    )
    assert first.status == 200
    second = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s2", "sandbox_token": "tok-2-" + "b" * 30},
        headers={"X-Admin-Token": ADMIN},
    )
    assert second.status == 429


async def test_a_sandbox_id_ending_in_a_newline_is_refused(client):
    """Python's ``$`` also matches just before a final newline, so ``match`` took ``sbx-1\\n``."""
    enroll = await client.post(
        "/admin/enroll", json={"sandbox_id": "sbx-1\n", "sandbox_token": TOK}, headers={"X-Admin-Token": ADMIN}
    )
    unenroll = await client.delete("/admin/enroll/sbx-1%0A", headers={"X-Admin-Token": ADMIN})
    assert enroll.status == 400, f"enrolling a sandbox id ending in a newline must be refused: {enroll.status}"
    assert unenroll.status == 400, f"unenrolling a sandbox id ending in a newline must be refused: {unenroll.status}"
    assert len(client.server.app["enrollment"]) == 0, "nothing may be enrolled under a sandbox id ending in a newline"
