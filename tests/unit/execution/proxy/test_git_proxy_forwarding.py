"""The git-proxy is the sandbox's git remote; the forge token never reaches the sandbox (#521).

Before #521 the proxy's ``/git-credentials`` answered the sandbox's credential
helper with the forge token, so anything holding the sandbox bearer had the
token's full scope on every repository it could reach.

These tests run real git, configured as a sandbox is (ADR 0007: the job's
repository rewritten to the proxy, the bearer sent to the proxy), against the
proxy, which forwards to ``git http-backend`` serving bare repositories. The
fake forge accepts only the forge credential. It records every request for its
repositories or carrying its credential; what other processes on the host send
to its port is not counted.
"""

from __future__ import annotations

import asyncio
import base64
import shutil
from pathlib import Path

import aiohttp
import pytest
import structlog
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from embry0.execution.proxy.git_proxy import create_git_proxy_app

from .git_forge import (
    ADMIN,
    FORGE_TOKEN,
    FORGE_USER,
    FakeForge,
    basic,
    configure_sandbox_git,
    enroll,
    run_git,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs the git binary")

BEARER = "sandbox-bearer-" + "b" * 40
REPO = "acme/widgets"
FORGE_AUTH = basic(FORGE_USER, FORGE_TOKEN)
SECRETS = (FORGE_TOKEN, base64.b64encode(f"{FORGE_USER}:{FORGE_TOKEN}".encode()).decode())


@pytest.fixture
async def forge(tmp_path: Path):
    f = FakeForge(tmp_path / "forge", expected_auth=FORGE_AUTH)
    f.root.mkdir()
    f.create_repo(REPO, files={"README.md": "widgets\n"})
    f.create_repo("acme/other", files={"SECRET.md": "another repository\n"})
    server = TestServer(f.app(), host="127.0.0.1")
    await server.start_server()
    f.origin = f"http://127.0.0.1:{server.port}"  # type: ignore[attr-defined]
    yield f
    await server.close()


@pytest.fixture
async def proxy():
    client = TestClient(TestServer(create_git_proxy_app(github_token="", admin_token=ADMIN), host="127.0.0.1"))
    await client.start_server()
    yield client
    await client.close()


def _url(proxy: TestClient) -> str:
    return f"http://127.0.0.1:{proxy.server.port}"


async def _enroll_job(proxy: TestClient, forge: FakeForge, *, push: bool, **overrides) -> dict:
    fields = {
        "github_token": FORGE_TOKEN,
        "git_host": "127.0.0.1",
        "git_username": FORGE_USER,
        "allowed_repos": [REPO],
        "git_upstream": forge.origin,  # type: ignore[attr-defined]
        "git_push": push,
        "git_push_branches": ["feature"] if push else None,
        **overrides,
    }
    return await enroll(proxy, "sbx-1", BEARER, **fields)


async def _sandbox(tmp_path: Path, proxy: TestClient, forge: FakeForge, bearer: str = BEARER) -> Path:
    home = tmp_path / "sandbox-home"
    home.mkdir(exist_ok=True)
    await configure_sandbox_git(
        home,
        proxy_url=_url(proxy),
        origin=forge.origin,  # type: ignore[attr-defined]
        repo=REPO,
        bearer=bearer,
    )
    return home


async def _clone(forge: FakeForge, home: Path, dest: Path) -> tuple[int, str]:
    return await run_git("clone", f"{forge.origin}/{REPO}.git", str(dest), home=home)  # type: ignore[attr-defined]


async def _commit(home: Path, work: Path, name: str) -> None:
    (work / name).write_text(f"{name}\n")
    for args in (("add", "."), ("commit", "-m", f"add {name}")):
        rc, out = await run_git(*args, home=home, cwd=work)
        assert rc == 0, out


async def test_clone_fetch_and_push_of_the_jobs_repository_go_through_the_proxy(proxy, forge, tmp_path):
    await _enroll_job(proxy, forge, push=True)
    home = await _sandbox(tmp_path, proxy, forge)
    work = tmp_path / "workspace"

    rc, out = await _clone(forge, home, work)
    assert rc == 0, f"a clone of the job's repository through the git-proxy failed:\n{out}"
    assert (work / "README.md").read_text() == "widgets\n"
    # `git remote get-url` would expand insteadOf; the stored URL is what other code reads.
    _, origin_url = await run_git("config", "remote.origin.url", home=home, cwd=work)
    assert origin_url.strip() == f"{forge.origin}/{REPO}.git", "origin keeps the forge URL; only the transport moves"

    sha = forge.commit_upstream(REPO, "NEW.md", "new\n")
    rc, out = await run_git("fetch", "origin", home=home, cwd=work)
    assert rc == 0, f"a fetch through the git-proxy failed:\n{out}"
    _, fetched = await run_git("rev-parse", "origin/main", home=home, cwd=work)
    assert fetched.strip() == sha, "the fetch did not bring the forge's new commit"

    await _commit(home, work, "CHANGE.md")
    rc, out = await run_git("push", "origin", "HEAD:refs/heads/feature", home=home, cwd=work)
    assert rc == 0, f"a push through the git-proxy with the push right failed:\n{out}"
    assert "refs/heads/feature" in forge.refs(REPO), "the push did not reach the forge"

    services = {s.path.rsplit("/", 1)[-1] for s in forge.seen} | {s.query for s in forge.seen}
    assert {"git-upload-pack", "git-receive-pack"} <= services, services
    assert all(s.headers.get("Authorization") == FORGE_AUTH for s in forge.seen), "the proxy did not add the credential"
    leaked = [s.path for s in forge.seen if any(BEARER in v for v in s.headers.values())]
    assert not leaked, f"the sandbox bearer reached the forge on {leaked}"


async def test_a_fetch_whose_request_git_gzips_goes_through_the_proxy(proxy, forge, tmp_path):
    """Git gzips an upload-pack request body over 1 KiB. The proxy's server
    hands the handler that body already decompressed, so it must not tell the
    forge the body is still gzip: the forge answers 400 and git stops with
    "expected 'acknowledgments'". A first clone sends a small request and
    passes either way; a later fetch that negotiates with many local commits
    does not (seen on the first deploy of #521)."""
    await _enroll_job(proxy, forge, push=False)
    home = await _sandbox(tmp_path, proxy, forge)
    work = tmp_path / "workspace"
    rc, out = await _clone(forge, home, work)
    assert rc == 0, f"a clone of the job's repository through the git-proxy failed:\n{out}"

    # Commits the forge has never seen: the fetch sends them as `have` lines,
    # which takes its request body past git's gzip threshold.
    for i in range(64):
        await _commit(home, work, f"local-{i}.md")
    sha = forge.commit_upstream(REPO, "NEW.md", "new\n")

    rc, out = await run_git("fetch", "origin", "main:refs/remotes/origin/main", "--depth=50", home=home, cwd=work)
    assert rc == 0, f"a fetch whose request git gzips failed through the git-proxy:\n{out}"
    _, fetched = await run_git("rev-parse", "refs/remotes/origin/main", home=home, cwd=work)
    assert fetched.strip() == sha, "the fetch did not bring the forge's new commit"


async def test_push_is_refused_without_the_push_right(proxy, forge, tmp_path):
    await _enroll_job(proxy, forge, push=False)
    home = await _sandbox(tmp_path, proxy, forge)
    work = tmp_path / "workspace"
    rc, out = await _clone(forge, home, work)
    assert rc == 0, f"a read-only sandbox must still clone its repository:\n{out}"

    await _commit(home, work, "CHANGE.md")
    rc, out = await run_git("push", "origin", "HEAD:refs/heads/feature", home=home, cwd=work)
    assert rc != 0, f"a sandbox without the push right pushed:\n{out}"
    assert "push_not_allowed" in out, f"the refusal must say why:\n{out}"
    assert "refs/heads/feature" not in forge.refs(REPO)
    pushes = [s for s in forge.seen if "receive-pack" in s.path or "receive-pack" in s.query]
    assert not pushes, "a refused push must not reach the forge at all"


async def test_another_repository_is_refused_and_never_gets_the_credential(proxy, forge, tmp_path):
    await _enroll_job(proxy, forge, push=True)
    home = await _sandbox(tmp_path, proxy, forge)

    # Through the proxy by its own URL, as a process holding the bearer would try.
    rc, out = await run_git("clone", f"{_url(proxy)}/git/acme/other.git", str(tmp_path / "via-proxy"), home=home)
    assert rc != 0, f"the proxy served a repository outside the job's scope:\n{out}"
    assert "repo_out_of_scope" in out, f"the refusal must say why:\n{out}"

    # Straight to the forge: not rewritten, so it goes without the credential.
    rc, out = await run_git("clone", f"{forge.origin}/acme/other.git", str(tmp_path / "direct"), home=home)
    assert rc != 0, f"another private repository was cloned:\n{out}"

    other = forge.requests_for("acme/other")
    assert other, "the direct clone should have reached the forge (and been refused there)"
    assert not [s for s in other if s.headers.get("Authorization") == FORGE_AUTH], (
        "the forge credential was used for a repository outside the job's scope"
    )
    assert not (tmp_path / "via-proxy" / "SECRET.md").exists()
    assert not (tmp_path / "direct" / "SECRET.md").exists()


async def test_no_proxy_endpoint_hands_the_forge_token_to_the_sandbox(proxy, forge):
    await _enroll_job(proxy, forge, push=True)
    auth = {"Authorization": f"Bearer {BEARER}"}
    probes = [
        ("GET", "/git-credentials"),
        ("GET", "/health"),
        ("GET", f"/git/{REPO}.git/info/refs?service=git-upload-pack"),
        ("GET", f"/git/{REPO}.git/info/refs?service=git-receive-pack"),
        ("GET", f"/git/{REPO}.git/info/refs"),
        ("POST", f"/git/{REPO}.git/git-upload-pack"),
        ("POST", f"/git/{REPO}.git/info/lfs/objects/batch"),
        ("GET", f"/git/{REPO}.git/HEAD"),
        ("GET", "/git/acme/other.git/info/refs?service=git-upload-pack"),
        ("POST", "/admin/enroll"),
    ]
    for method, path in probes:
        resp = await proxy.request(method, path, headers=auth)
        body = await resp.read()
        leaked = [s for s in SECRETS if s.encode() in body or any(s in v for v in resp.headers.values())]
        assert not leaked, f"{method} {path} handed the forge token to the sandbox (HTTP {resp.status})"

    resp = await proxy.get("/git-credentials", headers=auth)
    assert resp.status == 410, f"the retired credential-helper endpoint must refuse (got HTTP {resp.status})"


async def test_an_enrollment_without_a_grant_is_refused_every_git_request(proxy, forge, tmp_path):
    """An orchestrator older than the proxy image enrolls without ``allowed_repos``: fail closed, loudly."""
    with structlog.testing.capture_logs() as logs:
        await enroll(proxy, "sbx-1", BEARER, github_token=FORGE_TOKEN, git_host="127.0.0.1")
        home = await _sandbox(tmp_path, proxy, forge)
        rc, out = await _clone(forge, home, tmp_path / "workspace")
        helper = await proxy.get("/git-credentials", headers={"Authorization": f"Bearer {BEARER}"})
    assert rc != 0 and "unscoped_enrollment" in out, f"an unscoped enrollment was served:\n{out}"
    assert helper.status == 410, "an old credential helper must get nothing"
    events = [e["event"] for e in logs]
    for event in ("git_proxy_enrollment_unscoped", "git_proxy_policy_denied", "git_proxy_credentials_refused"):
        assert event in events, f"{event} not logged: an operator could not see why git fails ({events})"
    assert forge.seen == [], "nothing may reach the forge for an unscoped enrollment"


async def test_an_unknown_bearer_is_refused_before_the_forge(proxy, forge, tmp_path):
    await _enroll_job(proxy, forge, push=True)
    home = await _sandbox(tmp_path, proxy, forge, bearer="not-enrolled-" + "x" * 40)
    rc, out = await _clone(forge, home, tmp_path / "workspace")
    assert rc != 0 and "not enrolled" in out, f"an unknown bearer was served:\n{out}"
    assert forge.seen == [], f"an unknown bearer's request reached the forge: {forge.seen}"


async def _get_from_forge(forge: FakeForge, path: str, headers: dict[str, str] | None = None) -> int:
    """GET ``path`` from the forge's port directly, not through the proxy."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{forge.origin}{path}", headers=headers) as resp:  # type: ignore[attr-defined]
            await resp.read()
            return resp.status


async def test_a_stray_request_to_the_forge_changes_no_result(proxy, forge, tmp_path):
    """Any process on the host can reach the forge's port. A ``GET /`` from an
    unrelated client once failed the unknown-bearer test above while three
    re-runs passed (#214). Counted, such a request fails a test that expects
    nothing to reach the forge, or passes one that expects something to."""
    await _enroll_job(proxy, forge, push=True)
    home = await _sandbox(tmp_path, proxy, forge, bearer="not-enrolled-" + "x" * 40)
    assert await _get_from_forge(forge, "/") == 401, "the stray request did not reach the forge"
    rc, out = await _clone(forge, home, tmp_path / "workspace")
    assert await _get_from_forge(forge, "/favicon.ico") == 401, "the stray request did not reach the forge"

    assert rc != 0 and "not enrolled" in out, f"an unknown bearer was served:\n{out}"
    assert forge.seen == [], f"only the stray requests should reach the forge, and they must not count: {forge.seen}"
    # A subset: a real stray from another process lands here too.
    strays = {(s.method, s.path) for s in forge.ignored}
    assert {("GET", "/"), ("GET", "/favicon.ico")} <= strays, f"the strays were not kept aside: {strays}"


async def test_a_request_with_the_forge_credential_counts_on_any_path(forge):
    """A proxy that leaks need not use the repository's path. Forwarding the
    sandbox's ``/git/<repo>.git/info/refs`` as it came, with the credential, is
    a leak too, and the unknown-bearer and refused-push tests must see it. No
    other process has the credential, so a request that carries it counts
    wherever it goes."""
    leaked = f"/git/{REPO}.git/info/refs"
    await _get_from_forge(forge, f"{leaked}?service=git-upload-pack", {"Authorization": FORGE_AUTH})
    assert [(s.method, s.path) for s in forge.seen] == [("GET", leaked)], (
        f"a request carrying the forge credential to {leaked} was not counted: a proxy leaking there passes every test"
    )


async def test_the_credential_goes_only_to_the_host_it_was_enrolled_for(proxy, forge, tmp_path):
    """A credential for another host is not sent; git then fails as unauthenticated, as with a helper."""
    with structlog.testing.capture_logs() as logs:
        await _enroll_job(proxy, forge, push=False, git_host="forge.example.com")
        home = await _sandbox(tmp_path, proxy, forge)
        rc, out = await _clone(forge, home, tmp_path / "workspace")
    assert rc != 0, f"the clone should fail without a credential:\n{out}"
    assert forge.seen, "the request should reach the forge, only without the credential"
    assert all("Authorization" not in s.headers for s in forge.seen), "the credential was sent to another host"
    assert "git_proxy_credential_host_mismatch" in [e["event"] for e in logs]


async def test_both_bodies_are_streamed_not_buffered(proxy):
    """A pack can be gigabytes: the proxy must pass bytes on as they come, both ways."""
    got_request_start = asyncio.Event()
    release_response = asyncio.Event()

    async def upload_pack(request: web.Request) -> web.StreamResponse:
        await request.content.readany()
        got_request_start.set()
        response = web.StreamResponse()
        response.content_type = "application/x-git-upload-pack-result"
        await response.prepare(request)
        await response.write(b"PART-ONE")
        await asyncio.wait_for(release_response.wait(), 10)
        await request.content.read()
        await response.write(b"PART-TWO")
        await response.write_eof()
        return response

    upstream_app = web.Application()
    upstream_app.router.add_post(f"/{REPO}.git/git-upload-pack", upload_pack)
    upstream = TestServer(upstream_app, host="127.0.0.1")
    await upstream.start_server()
    try:
        await enroll(
            proxy,
            "sbx-1",
            BEARER,
            github_token=FORGE_TOKEN,
            git_host="127.0.0.1",
            allowed_repos=[REPO],
            git_upstream=f"http://127.0.0.1:{upstream.port}",
        )

        async def request_body():
            yield b"REQ-ONE"
            try:
                await asyncio.wait_for(got_request_start.wait(), 5)
            except TimeoutError:
                pytest.fail("the proxy held the request body back: the upstream never saw its first bytes")
            yield b"REQ-TWO"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_url(proxy)}/git/{REPO}.git/git-upload-pack",
                data=request_body(),
                headers={"Authorization": f"Bearer {BEARER}", "Content-Type": "application/x-git-upload-pack-request"},
            ) as resp:
                assert resp.status == 200, f"the proxy did not forward git-upload-pack (HTTP {resp.status})"
                try:
                    first = await asyncio.wait_for(resp.content.readexactly(len(b"PART-ONE")), 5)
                except TimeoutError:
                    pytest.fail("the proxy held the response back: its first bytes never arrived")
                release_response.set()
                rest = await resp.content.read()
        assert first + rest == b"PART-ONEPART-TWO"
    finally:
        await upstream.close()
