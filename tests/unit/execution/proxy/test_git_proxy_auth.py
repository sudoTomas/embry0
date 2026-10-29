"""git-proxy bearer auth and admin enrollment (#521: a bearer opens git routes, never a credential)."""

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from embry0.execution.proxy.git_proxy import create_git_proxy_app

from .git_forge import ADMIN as RIG_ADMIN
from .git_forge import ProxyRig, RecordingUpstream, basic

ADMIN = "test-admin-secret-not-real"
PAT = "ghp_PAT_TESTING_ONLY"

# Tokens used in tests must be >= 32 chars per enrollment validation
TOK_S1 = "tok-s1-" + "a" * 30  # 37 chars
TOK_S2 = "tok-s2-" + "b" * 30  # 37 chars
TOK_A = "token-A-" + "a" * 30  # 38 chars
TOK_B = "token-B-" + "b" * 30  # 38 chars


@pytest.fixture
async def client():
    app = create_git_proxy_app(github_token=PAT, admin_token=ADMIN)
    server = TestServer(app)
    async with TestClient(server) as c:
        yield c


@pytest.fixture
async def rig():
    async with ProxyRig(create_git_proxy_app(github_token=PAT, admin_token=RIG_ADMIN), RecordingUpstream()) as r:
        yield r


async def _rig_enroll(rig: ProxyRig, sandbox_id: str, token: str) -> None:
    await rig.enroll(sandbox_id, token, git_host="127.0.0.1")


async def _unenroll(rig: ProxyRig, sandbox_id: str) -> None:
    resp = await rig.client.delete(f"/admin/enroll/{sandbox_id}", headers={"X-Admin-Token": RIG_ADMIN})
    assert resp.status == 200


async def test_health_open(client):
    resp = await client.get("/health")
    assert resp.status == 200


async def test_git_route_without_bearer_rejected(rig):
    resp = await rig.client.get("/git/acme/widgets.git/info/refs?service=git-upload-pack")
    assert resp.status == 403
    assert "not enrolled" in await resp.text()
    assert rig.upstream.seen == []


async def test_git_route_with_unenrolled_bearer_rejected(rig):
    resp = await rig.fetch_refs("bogus")
    assert resp.status == 403
    assert rig.upstream.seen == []


async def test_enroll_requires_admin_header(client):
    resp = await client.post("/admin/enroll", json={"sandbox_id": "s1", "sandbox_token": TOK_S1})
    assert resp.status == 401


async def test_enroll_with_wrong_admin_rejected(client):
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": "wrong"},
    )
    assert resp.status == 401


async def test_enrolled_bearer_is_forwarded(rig):
    await _rig_enroll(rig, "s1", TOK_S1)
    resp = await rig.fetch_refs(TOK_S1)
    assert resp.status == 200
    assert len(rig.upstream.seen) == 1


async def test_multi_tenant_both_work(rig):
    for sid, tok in [("s1", TOK_S1), ("s2", TOK_S2)]:
        await _rig_enroll(rig, sid, tok)
    for tok in (TOK_S1, TOK_S2):
        resp = await rig.fetch_refs(tok)
        assert resp.status == 200


async def test_unenroll_revokes(rig):
    await _rig_enroll(rig, "s1", TOK_S1)
    await _unenroll(rig, "s1")

    resp = await rig.fetch_refs(TOK_S1)
    assert resp.status == 403
    assert rig.upstream.seen == []


async def _get_from_upstream(rig: ProxyRig, path: str, **headers: str) -> int:
    """GET ``path`` from the upstream's port directly, as another process on the host can."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{rig.upstream_url}{path}", headers=headers) as resp:
            await resp.read()
            return resp.status


async def test_a_stray_request_to_the_upstream_changes_no_refusal(rig):
    """Any process on the host can reach the upstream's port, and a ``GET /``
    from one fails the refusal tests above when it counts (#221). It could as
    well pass a test that expects something to reach the upstream."""
    await _rig_enroll(rig, "s1", TOK_S1)
    assert await _get_from_upstream(rig, "/") == 200, "the stray request did not reach the upstream"
    resp = await rig.fetch_refs("not-enrolled-" + "x" * 40)
    assert await _get_from_upstream(rig, "/favicon.ico") == 200, "the stray request did not reach the upstream"

    assert resp.status == 403
    assert rig.upstream.seen == [], (
        f"only the stray requests reached the upstream, and they must not count: {rig.upstream.seen}"
    )
    # A subset: a real stray from another process lands here too.
    strays = {(s.method, s.path) for s in rig.upstream.ignored}
    assert {("GET", "/"), ("GET", "/favicon.ico")} <= strays, f"the strays were not kept aside: {strays}"


async def test_a_stray_request_is_not_taken_for_the_credential_sent(rig, monkeypatch):
    """``credential_sent`` reports the Authorization of one fetch's request
    upstream. Read off the upstream's last request, it reported a stray's that
    landed right after the proxy's instead (#221)."""
    await rig.enroll("s1", TOK_S1, git_host="127.0.0.1", git_username="x-access-token")
    fetch_refs = rig.fetch_refs

    async def fetch_refs_then_a_stray(*args, **kwargs):
        resp = await fetch_refs(*args, **kwargs)
        await resp.read()
        await _get_from_upstream(rig, "/", Authorization=basic("someone", "else"))
        return resp

    monkeypatch.setattr(rig, "fetch_refs", fetch_refs_then_a_stray)
    sent = await rig.credential_sent(TOK_S1)
    assert sent == basic("x-access-token", PAT), f"the stray's Authorization was taken for the proxy's: {sent!r}"


async def test_a_request_carrying_a_token_the_proxy_holds_counts_on_any_path(rig):
    """A proxy that leaks need not use the repository's path: forwarding the
    sandbox's own ``/git/<repo>.git/...`` with a forge token is a leak too, and
    the refusal tests must see it. No other process holds the proxy's global
    token or an enrolled one, so a request carrying either counts wherever it
    goes; one carrying neither, off the repository's path, does not."""
    await rig.enroll("s1", TOK_S1, git_host="127.0.0.1", github_token="ghp_PER_SANDBOX_TESTING_ONLY")
    leaked = "/git/acme/widgets.git/info/refs"
    for token in (PAT, "ghp_PER_SANDBOX_TESTING_ONLY"):
        await _get_from_upstream(rig, leaked, Authorization=basic("x-access-token", token))
    await _get_from_upstream(rig, leaked, Authorization=basic("someone", "else"))

    counted = [(s.path, s.headers["Authorization"]) for s in rig.upstream.seen]
    assert counted == [
        (leaked, basic("x-access-token", PAT)),
        (leaked, basic("x-access-token", "ghp_PER_SANDBOX_TESTING_ONLY")),
    ], f"a request carrying a token the proxy holds must count on any path, and no other: {counted}"


async def test_create_app_rejects_empty_admin():
    with pytest.raises(ValueError, match="admin_token is required"):
        create_git_proxy_app(github_token="x", admin_token="")


# --- I-1: re-enrollment test ---


async def test_re_enroll_replaces_old_token(rig):
    """Enrolling s1 with token B replaces token A; A → 403, B → 200; unenroll removes both and the grant."""
    await _rig_enroll(rig, "s1", TOK_A)
    assert (await rig.fetch_refs(TOK_A)).status == 200

    await _rig_enroll(rig, "s1", TOK_B)
    assert (await rig.fetch_refs(TOK_A)).status == 403
    assert (await rig.fetch_refs(TOK_B)).status == 200

    await _unenroll(rig, "s1")
    assert (await rig.fetch_refs(TOK_B)).status == 403
    assert rig.client.app["git_grant_by_id"] == {}, "the grant must end with its enrollment"


# --- I-2: token length floor ---


async def test_enroll_rejects_short_sandbox_token(client):
    """sandbox_token shorter than 32 chars must be rejected with 400."""
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": "x"},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 400


# --- I-3: sandbox_id format validation ---


async def test_enroll_rejects_malformed_sandbox_id(client):
    """sandbox_id with path-traversal characters must be rejected with 400."""
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "../../../etc/passwd", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 400

    # Also reject a sandbox_id longer than 64 characters
    long_id = "a" * 65
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": long_id, "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 400
