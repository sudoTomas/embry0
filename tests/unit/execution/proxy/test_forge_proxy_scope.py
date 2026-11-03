"""The forge API proxies forward only read calls on the job's own repository (#520).

Before #520 both proxies forwarded any method and any path with the forge
credential, so every refusal below used to reach the forge. The upstream here
is an httpx MockTransport that records what actually leaves the proxy, after
httpx's own URL normalisation: that normalisation is what turns
``/repos/acme/widgets/../../other/repo`` into a call on another repository.

Requests are written to the socket by hand, so no client-side URL handling
cleans up a traversal before the proxy sees it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import structlog
from aiohttp.test_utils import TestClient, TestServer

from embry0.execution.proxy import app_keys
from embry0.execution.proxy.enrollment import ENROLLMENT
from embry0.execution.proxy.forge_scope import REPO_SCOPE_BY_ID
from embry0.execution.proxy.github_proxy import create_github_proxy_app
from embry0.execution.proxy.gitlab_proxy import create_gitlab_proxy_app

ADMIN = "admin-secret-for-scope-tests"
BEARER = "sandbox-bearer-" + "s" * 32
PAT = "ghp_SCOPE_TEST_ONLY"
GLPAT = "glpat-SCOPE_TEST_ONLY"
GITLAB_BASE = "https://gitlab.example.com/api/v4"


class _Upstream:
    """Stands in for the forge: records every request that left the proxy."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(200, json={"ok": True})

    def seen(self) -> list[str]:
        return [f"{r.method} {r.url.raw_path.decode()}" for r in self.calls]


async def _serve(app) -> AsyncIterator[tuple[TestClient, _Upstream]]:
    upstream = _Upstream()

    async def _mock_forge(app) -> None:
        # Runs after the proxy's own startup hook; its cleanup hook closes this client.
        await app[app_keys.HTTP_CLIENT].aclose()
        app[app_keys.HTTP_CLIENT] = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle))

    app.on_startup.append(_mock_forge)
    async with TestClient(TestServer(app)) as client:
        yield client, upstream


@pytest.fixture
async def github() -> AsyncIterator[tuple[TestClient, _Upstream]]:
    async for pair in _serve(create_github_proxy_app(github_token=PAT, admin_token=ADMIN)):
        yield pair


@pytest.fixture
async def gitlab() -> AsyncIterator[tuple[TestClient, _Upstream]]:
    async for pair in _serve(create_gitlab_proxy_app(GLPAT, ADMIN, api_base=GITLAB_BASE)):
        yield pair


async def _enroll(client: TestClient, scope: list[str] | None, *, sandbox_id: str = "sbx-1", bearer: str = BEARER):
    body: dict = {"sandbox_id": sandbox_id, "sandbox_token": bearer}
    if scope is not None:
        body["allowed_repos"] = scope
    return await client.post("/admin/enroll", json=body, headers={"X-Admin-Token": ADMIN})


async def _raw(client: TestClient, method: str, target: str, *, bearer: str = BEARER) -> tuple[int, dict]:
    """Send one request with ``target`` byte for byte as written."""
    reader, writer = await asyncio.open_connection(client.server.host, client.server.port)
    writer.write(
        (
            f"{method} {target} HTTP/1.1\r\nHost: proxy\r\nAuthorization: Bearer {bearer}\r\nConnection: close\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    data = await reader.read()
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    try:
        parsed = json.loads(body) if body else {}
    except ValueError:
        parsed = {"raw": body.decode(errors="replace")}
    return status, parsed if isinstance(parsed, dict) else {"raw": parsed}


def _assert_refused(status: int, body: dict, upstream: _Upstream, reason: str, call: str) -> None:
    assert status == 403 and body.get("error") == "policy_denied", (
        f"{call} must be refused with 403 policy_denied, got {status} {body}; the forge saw {upstream.seen()}"
    )
    assert body.get("reason") == reason, f"{call}: expected reason {reason!r}, got {body.get('reason')!r}"
    assert upstream.calls == [], f"{call} was refused but still reached the forge: {upstream.seen()}"


# --- GitHub -------------------------------------------------------------------

GITHUB_SCOPE = ["acme/widgets"]


@pytest.mark.parametrize(
    ("method", "target", "upstream_path"),
    [
        ("GET", "/repos/acme/widgets", "/repos/acme/widgets"),
        ("GET", "/repos/acme/widgets/issues/7/comments", "/repos/acme/widgets/issues/7/comments"),
        ("GET", "/repos/acme/widgets/pulls/3/files?per_page=100", "/repos/acme/widgets/pulls/3/files?per_page=100"),
        ("GET", "/repos/Acme/Widgets/pulls/3", "/repos/Acme/Widgets/pulls/3"),
        (
            "GET",
            "/repos/acme/widgets/contents/docs/read%20me.md?ref=main",
            "/repos/acme/widgets/contents/docs/read%20me.md?ref=main",
        ),
        ("HEAD", "/repos/acme/widgets/commits/abc123/check-runs", "/repos/acme/widgets/commits/abc123/check-runs"),
        ("GET", "/repos/acme/widgets/compare/main...feature/x", "/repos/acme/widgets/compare/main...feature/x"),
    ],
)
async def test_github_read_on_the_job_repo_is_forwarded(github, method, target, upstream_path):
    client, upstream = github
    assert (await _enroll(client, GITHUB_SCOPE)).status == 200
    status, body = await _raw(client, method, target)
    assert status == 200, f"{method} {target} is an allowed read, got {status} {body}"
    assert upstream.seen() == [f"{method} {upstream_path}"], f"{method} {target} reached the forge as {upstream.seen()}"
    assert upstream.calls[0].headers["Authorization"] == f"Bearer {PAT}", "the PAT must be injected upstream"
    assert BEARER not in str(dict(upstream.calls[0].headers)), "the sandbox bearer must not reach the forge"


@pytest.mark.parametrize(
    ("method", "target", "reason"),
    [
        # Another repository.
        ("GET", "/repos/other/repo/issues", "repo_out_of_scope"),
        ("GET", "/repos/acme/secrets/contents/.env", "repo_out_of_scope"),
        ("DELETE", "/repos/x/y", "repo_out_of_scope"),
        # A path outside the allowlist.
        ("GET", "/user", "path_not_allowed"),
        ("GET", "/user/repos", "path_not_allowed"),
        ("GET", "/orgs/acme/members", "path_not_allowed"),
        ("POST", "/graphql", "path_not_allowed"),
        ("GET", "/repos/acme/widgets/hooks", "path_not_allowed"),
        ("GET", "/repos/acme/widgets/actions/secrets", "path_not_allowed"),
        ("PUT", "/repos/acme/widgets/pulls/3/merge", "path_not_allowed"),
        # A write, or any other method, on an allowlisted path of the job's repo.
        ("DELETE", "/repos/acme/widgets", "method_not_allowed"),
        ("POST", "/repos/acme/widgets/issues", "method_not_allowed"),
        ("PATCH", "/repos/acme/widgets/pulls/3", "method_not_allowed"),
        ("PUT", "/repos/acme/widgets/contents/README.md", "method_not_allowed"),
        ("OPTIONS", "/repos/acme/widgets", "method_not_allowed"),
        # Traversal and encoding tricks aimed at another repository.
        ("GET", "/repos/acme/widgets/../../other/repo/issues", "malformed_path"),
        ("GET", "/repos/acme/widgets/%2e%2e/%2E%2E/other/repo/issues", "malformed_path"),
        # Through a rule that takes any number of trailing segments: only the
        # dot-segment check stands between these and /repos/other/repo/issues.
        ("GET", "/repos/acme/widgets/contents/../../../other/repo/issues", "malformed_path"),
        ("GET", "/repos/acme/widgets/compare/%2e%2e/%2e%2e/%2e%2e/other/repo/issues", "malformed_path"),
        ("GET", "/repos/acme/widgets%2F..%2F..%2Fother%2Frepo/issues", "malformed_path"),
        ("GET", "/repos/acme/widgets/contents/%252e%252e/x", "malformed_path"),
        ("GET", "/repos/acme/widgets/contents/a\\..\\b", "malformed_path"),
        ("GET", "/repos//acme/widgets", "malformed_path"),
        ("GET", "//repos/other/repo", "malformed_path"),
        ("GET", "/repos/acme/widgets/issues/", "malformed_path"),
        # Case: the literal segments are exact.
        ("GET", "/REPOS/other/repo", "path_not_allowed"),
        ("GET", "/Repos/acme/widgets", "path_not_allowed"),
        ("GET", "/repos/acme/widgets/ISSUES", "path_not_allowed"),
    ],
)
async def test_github_call_outside_the_scope_is_refused(github, method, target, reason):
    client, upstream = github
    assert (await _enroll(client, GITHUB_SCOPE)).status == 200
    status, body = await _raw(client, method, target)
    _assert_refused(status, body, upstream, reason, f"{method} {target}")


async def test_github_enrollment_echoes_the_scope_it_stored(github):
    """The orchestrator reads the echo to tell a current proxy from an older image."""
    client, _ = github
    resp = await _enroll(client, ["Acme/Widgets"])
    assert resp.status == 200
    granted = (await resp.json()).get("allowed_repos")
    assert granted == ["acme/widgets"], f"enrollment must echo the stored scope, got {granted!r}"


async def test_github_unscoped_enrollment_forwards_nothing(github):
    """An orchestrator older than this image enrolls without a scope: fail closed, loudly."""
    client, upstream = github
    with structlog.testing.capture_logs() as logs:
        assert (await _enroll(client, None)).status == 200
        status, body = await _raw(client, "GET", "/repos/acme/widgets")
    _assert_refused(status, body, upstream, "unscoped_enrollment", "GET /repos/acme/widgets (unscoped)")
    events = [e["event"] for e in logs]
    assert "github_proxy_enrollment_unscoped" in events, f"no warning at enrollment: {events}"


async def test_github_empty_scope_forwards_nothing(github):
    """The orchestrator sends [] to the other forge's proxy and when a job has no repo."""
    client, upstream = github
    assert (await _enroll(client, [])).status == 200
    status, body = await _raw(client, "GET", "/repos/acme/widgets")
    _assert_refused(status, body, upstream, "repo_out_of_scope", "GET /repos/acme/widgets (empty scope)")


@pytest.mark.parametrize(
    "scope",
    [
        "acme/widgets",
        ["acme"],
        ["acme/widgets/extra"],
        ["../widgets"],
        ["acme/.."],
        [""],
        [1],
        [f"acme/r{i}" for i in range(9)],
    ],
)
async def test_github_malformed_scope_refuses_the_enrollment(github, scope):
    client, upstream = github
    resp = await _enroll(client, scope)
    assert resp.status == 400, f"allowed_repos={scope!r} must be refused, enrollment answered {resp.status}"
    status, _ = await _raw(client, "GET", "/repos/acme/widgets")
    assert status == 401, f"a refused enrollment must leave the sandbox unenrolled, got {status}"
    assert upstream.calls == []


async def test_github_re_enrollment_replaces_the_scope(github):
    client, upstream = github
    await _enroll(client, ["acme/widgets"])
    await _enroll(client, ["acme/gadgets"])
    status, body = await _raw(client, "GET", "/repos/acme/widgets")
    _assert_refused(status, body, upstream, "repo_out_of_scope", "GET /repos/acme/widgets after re-scoping")


async def test_github_scope_does_not_survive_unenrollment(github):
    """A recycled sandbox id enrolled without a scope must not inherit the old one."""
    client, upstream = github
    await _enroll(client, ["acme/widgets"])
    await client.delete("/admin/enroll/sbx-1", headers={"X-Admin-Token": ADMIN})
    await _enroll(client, None)
    status, body = await _raw(client, "GET", "/repos/acme/widgets")
    _assert_refused(status, body, upstream, "unscoped_enrollment", "GET /repos/acme/widgets after unenroll")


async def test_github_scope_of_an_expired_enrollment_is_dropped(github):
    """An ended enrollment takes its scope with it; the map must not outgrow the registry (#536)."""
    client, _ = github
    await _enroll(client, ["acme/widgets"], sandbox_id="old")
    client.app[ENROLLMENT].unenroll("old")  # what purge_expired() does
    await _enroll(client, ["acme/widgets"], sandbox_id="new", bearer="another-bearer-" + "n" * 32)
    scopes = client.app.get(REPO_SCOPE_BY_ID)
    assert scopes is not None, "the proxy keeps no per-sandbox repo scope"
    assert set(scopes) == {"new"}, f"scope of an expired enrollment kept: {sorted(scopes)}"


async def test_github_refusal_is_logged_as_a_policy_denial(github):
    client, _ = github
    await _enroll(client, GITHUB_SCOPE)
    with structlog.testing.capture_logs() as logs:
        await _raw(client, "DELETE", "/repos/other/repo")
    denials = [e for e in logs if e["event"] == "github_proxy_policy_denied"]
    assert denials, f"no github_proxy_policy_denied line: {[e['event'] for e in logs]}"
    assert denials[0]["reason"] == "repo_out_of_scope"
    assert denials[0]["method"] == "DELETE"
    assert denials[0]["path"] == "/repos/other/repo"
    assert denials[0]["sandbox_id"] == "sbx-1"


# --- GitLab -------------------------------------------------------------------

GITLAB_SCOPE = ["group/sub/proj"]


@pytest.mark.parametrize(
    ("method", "target", "upstream_path"),
    [
        ("GET", "/projects/group%2Fsub%2Fproj", "/api/v4/projects/group%2Fsub%2Fproj"),
        (
            "GET",
            "/projects/GROUP%2Fsub%2Fproj/merge_requests/4/notes",
            "/api/v4/projects/GROUP%2Fsub%2Fproj/merge_requests/4/notes",
        ),
        (
            "GET",
            "/projects/group%2Fsub%2Fproj/repository/files/src%2Fapp.py/raw?ref=main",
            "/api/v4/projects/group%2Fsub%2Fproj/repository/files/src%2Fapp.py/raw?ref=main",
        ),
        (
            "HEAD",
            "/projects/group%2Fsub%2Fproj/pipelines/9/jobs",
            "/api/v4/projects/group%2Fsub%2Fproj/pipelines/9/jobs",
        ),
    ],
)
async def test_gitlab_read_on_the_job_project_is_forwarded(gitlab, method, target, upstream_path):
    client, upstream = gitlab
    assert (await _enroll(client, GITLAB_SCOPE)).status == 200
    status, body = await _raw(client, method, target)
    assert status == 200, f"{method} {target} is an allowed read, got {status} {body}"
    assert upstream.seen() == [f"{method} {upstream_path}"], f"{method} {target} reached the forge as {upstream.seen()}"
    assert upstream.calls[0].headers["PRIVATE-TOKEN"] == GLPAT, "the token must be injected upstream"


@pytest.mark.parametrize(
    ("method", "target", "reason"),
    [
        ("GET", "/projects/other%2Frepo", "repo_out_of_scope"),
        ("DELETE", "/projects/other%2Frepo", "repo_out_of_scope"),
        ("GET", "/projects/42", "project_id_not_a_path"),
        ("GET", "/user", "path_not_allowed"),
        ("GET", "/groups/group/members", "path_not_allowed"),
        ("GET", "/projects/group%2Fsub%2Fproj/variables", "path_not_allowed"),
        ("PUT", "/projects/group%2Fsub%2Fproj", "method_not_allowed"),
        ("POST", "/projects/group%2Fsub%2Fproj/merge_requests", "method_not_allowed"),
        ("DELETE", "/projects/group%2Fsub%2Fproj/repository/branches/main", "method_not_allowed"),
        ("GET", "/projects/group%2Fsub%2Fproj/../../other%2Frepo", "malformed_path"),
        ("GET", "/projects/group%2Fsub%2F..%2F..%2Fother%2Frepo", "malformed_path"),
        ("GET", "/projects/group%252Fsub%252Fproj", "malformed_path"),
        ("GET", "/projects//group%2Fsub%2Fproj", "malformed_path"),
        ("GET", "/PROJECTS/group%2Fsub%2Fproj", "path_not_allowed"),
    ],
)
async def test_gitlab_call_outside_the_scope_is_refused(gitlab, method, target, reason):
    client, upstream = gitlab
    assert (await _enroll(client, GITLAB_SCOPE)).status == 200
    status, body = await _raw(client, method, target)
    _assert_refused(status, body, upstream, reason, f"{method} {target}")


async def test_gitlab_enrollment_echoes_the_scope_it_stored(gitlab):
    client, _ = gitlab
    resp = await _enroll(client, ["Group/Sub/Proj"])
    granted = (await resp.json()).get("allowed_repos")
    assert granted == ["group/sub/proj"], f"enrollment must echo the stored scope, got {granted!r}"


async def test_gitlab_unscoped_enrollment_forwards_nothing(gitlab):
    client, upstream = gitlab
    with structlog.testing.capture_logs() as logs:
        assert (await _enroll(client, None)).status == 200
        status, body = await _raw(client, "GET", "/projects/group%2Fsub%2Fproj")
    _assert_refused(status, body, upstream, "unscoped_enrollment", "GET /projects/group%2Fsub%2Fproj (unscoped)")
    assert "gitlab_proxy_enrollment_unscoped" in [e["event"] for e in logs]
