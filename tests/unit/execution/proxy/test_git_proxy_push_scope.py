"""A push through the git-proxy may touch only the job's branch (#559, ADR 0009).

Before #559 the proxy streamed a receive-pack body without reading it, so a
sandbox with the push right could create, rewrite or delete any ref of its
repository: the default branch, tags, another job's branch. These tests push
with real git, configured as a sandbox is, through the proxy to ``git
http-backend`` serving a bare repository, and read the forge's refs afterwards.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import structlog
from aiohttp.test_utils import TestClient, TestServer

from embry0.execution.proxy.git_proxy import create_git_proxy_app

from .git_forge import ADMIN, FORGE_TOKEN, FORGE_USER, FakeForge, basic, configure_sandbox_git, enroll, run_git

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs the git binary")

BEARER = "sandbox-bearer-" + "p" * 40
REPO = "acme/widgets"
JOB_BRANCH = "embry0/iss-1-fix-widgets-1a2b3c4d"


@pytest.fixture
async def forge(tmp_path: Path):
    f = FakeForge(tmp_path / "forge", expected_auth=basic(FORGE_USER, FORGE_TOKEN))
    f.root.mkdir()
    f.create_repo(REPO, files={"README.md": "widgets\n"})
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


async def _enroll(proxy: TestClient, forge: FakeForge, **overrides) -> dict:
    fields = {
        "github_token": FORGE_TOKEN,
        "git_host": "127.0.0.1",
        "git_username": FORGE_USER,
        "allowed_repos": [REPO],
        "git_upstream": forge.origin,  # type: ignore[attr-defined]
        "git_push": True,
        "git_push_branches": [JOB_BRANCH],
        "job_id": "job-1a2b3c4d5e6f",
        **overrides,
    }
    return await enroll(proxy, "sbx-1", BEARER, **fields)


async def _workspace(tmp_path: Path, proxy: TestClient, forge: FakeForge) -> tuple[Path, Path]:
    home = tmp_path / "sandbox-home"
    home.mkdir(exist_ok=True)
    await configure_sandbox_git(
        home,
        proxy_url=f"http://127.0.0.1:{proxy.server.port}",
        origin=forge.origin,  # type: ignore[attr-defined]
        repo=REPO,
        bearer=BEARER,
    )
    work = tmp_path / "workspace"
    rc, out = await run_git("clone", f"{forge.origin}/{REPO}.git", str(work), home=home)  # type: ignore[attr-defined]
    assert rc == 0, f"clone through the git-proxy failed:\n{out}"
    return home, work


async def _commit(home: Path, work: Path, name: str, content: str | bytes | None = None) -> None:
    path = work / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content if content is not None else f"{name}\n")
    for args in (("add", "."), ("commit", "-m", f"add {name}")):
        rc, out = await run_git(*args, home=home, cwd=work)
        assert rc == 0, out


def _ref(forge: FakeForge, ref: str) -> str | None:
    for line in forge.refs(REPO).splitlines():
        sha, _, name = line.partition(" ")
        if name == ref:
            return sha
    return None


def _receive_pack_posts(forge: FakeForge) -> list:
    return [s for s in forge.seen if s.method == "POST" and s.path.endswith("/git-receive-pack")]


async def test_the_jobs_branch_is_created_and_updated_with_a_large_pack(proxy, forge, tmp_path):
    """A pack over git's 1 MiB post buffer makes git probe first and send the body chunked."""
    await _enroll(proxy, forge)
    home, work = await _workspace(tmp_path, proxy, forge)
    rc, out = await run_git("checkout", "-b", JOB_BRANCH, home=home, cwd=work)
    assert rc == 0, out
    await _commit(home, work, "blob.bin", os.urandom(3 * 1024 * 1024))

    rc, out = await run_git("push", "-u", "origin", JOB_BRANCH, home=home, cwd=work)
    assert rc == 0, f"a push of the job's own branch was refused:\n{out}"
    _, head = await run_git("rev-parse", "HEAD", home=home, cwd=work)
    assert _ref(forge, f"refs/heads/{JOB_BRANCH}") == head.strip(), "the job's branch did not reach the forge"
    bodies = [s.body for s in _receive_pack_posts(forge)]
    assert b"0000" in bodies, "git did not probe first: the pack was too small to exercise the large-push path"
    assert max(len(b) for b in bodies) > 3 * 1024 * 1024, "the pack did not reach the forge whole"

    await _commit(home, work, "second.md")
    rc, out = await run_git("push", "origin", JOB_BRANCH, home=home, cwd=work)
    assert rc == 0, f"a fast-forward of the job's own branch was refused:\n{out}"
    _, head = await run_git("rev-parse", "HEAD", home=home, cwd=work)
    assert _ref(forge, f"refs/heads/{JOB_BRANCH}") == head.strip()


async def test_the_job_may_rewrite_its_own_branch(proxy, forge, tmp_path):
    """After a rebase in review, a job force-pushes its own branch."""
    await _enroll(proxy, forge)
    home, work = await _workspace(tmp_path, proxy, forge)
    await run_git("checkout", "-b", JOB_BRANCH, home=home, cwd=work)
    await _commit(home, work, "one.md")
    rc, out = await run_git("push", "origin", JOB_BRANCH, home=home, cwd=work)
    assert rc == 0, out
    await run_git("commit", "--amend", "-m", "rewritten", home=home, cwd=work)

    rc, out = await run_git("push", "--force", "origin", JOB_BRANCH, home=home, cwd=work)
    assert rc == 0, f"a force-push of the job's own branch was refused:\n{out}"
    _, head = await run_git("rev-parse", "HEAD", home=home, cwd=work)
    assert _ref(forge, f"refs/heads/{JOB_BRANCH}") == head.strip()


async def test_a_force_push_to_the_default_branch_is_refused_before_the_forge(proxy, forge, tmp_path):
    await _enroll(proxy, forge)
    home, work = await _workspace(tmp_path, proxy, forge)
    main_before = _ref(forge, "refs/heads/main")
    await run_git("commit", "--amend", "-m", "rewritten history", home=home, cwd=work)

    with structlog.testing.capture_logs() as logs:
        rc, out = await run_git("push", "--force", "origin", "main", home=home, cwd=work)
    assert rc != 0, f"a force-push to main through the git-proxy was accepted:\n{out}"
    assert "refs/heads/main" in out or "main -> main" in out, f"git did not name the refused ref:\n{out}"
    assert "not a branch this job may push" in out, f"git did not print the proxy's reason:\n{out}"
    assert _ref(forge, "refs/heads/main") == main_before, "main was rewritten on the forge"
    assert not _receive_pack_posts(forge), "a refused push must not reach the forge"
    refused = [e for e in logs if e["event"] == "git_proxy_push_refused"]
    assert refused and refused[0]["job_id"] == "job-1a2b3c4d5e6f", f"the refusal was not logged with the job: {logs}"
    assert "refs/heads/main" in refused[0]["refused"], refused[0]


async def test_another_branch_is_refused_even_as_a_new_branch(proxy, forge, tmp_path):
    await _enroll(proxy, forge)
    home, work = await _workspace(tmp_path, proxy, forge)
    await _commit(home, work, "x.md")
    rc, out = await run_git("push", "origin", "HEAD:refs/heads/some-other-branch", home=home, cwd=work)
    assert rc != 0, f"a push creating another branch was accepted:\n{out}"
    assert "not a branch this job may push" in out, out
    assert _ref(forge, "refs/heads/some-other-branch") is None


async def test_a_tag_is_refused(proxy, forge, tmp_path):
    await _enroll(proxy, forge)
    home, work = await _workspace(tmp_path, proxy, forge)
    await run_git("tag", "v9.9.9", home=home, cwd=work)
    rc, out = await run_git("push", "origin", "v9.9.9", home=home, cwd=work)
    assert rc != 0, f"a tag push was accepted:\n{out}"
    assert _ref(forge, "refs/tags/v9.9.9") is None


async def test_deleting_a_branch_is_refused_the_jobs_own_included(proxy, forge, tmp_path):
    await _enroll(proxy, forge)
    home, work = await _workspace(tmp_path, proxy, forge)
    await run_git("checkout", "-b", JOB_BRANCH, home=home, cwd=work)
    await _commit(home, work, "one.md")
    rc, out = await run_git("push", "origin", JOB_BRANCH, home=home, cwd=work)
    assert rc == 0, out

    for ref in ("main", JOB_BRANCH):
        rc, out = await run_git("push", "origin", "--delete", ref, home=home, cwd=work)
        assert rc != 0, f"deleting {ref} through the git-proxy was accepted:\n{out}"
        assert _ref(forge, f"refs/heads/{ref}") is not None, f"{ref} was deleted on the forge"
    assert "deleting the job's branch is not allowed" in out, out


async def test_one_refused_ref_refuses_the_whole_push(proxy, forge, tmp_path):
    await _enroll(proxy, forge)
    home, work = await _workspace(tmp_path, proxy, forge)
    await _commit(home, work, "x.md")
    rc, out = await run_git(
        "push", "origin", f"HEAD:refs/heads/{JOB_BRANCH}", "HEAD:refs/heads/main", home=home, cwd=work
    )
    assert rc != 0, out
    assert _ref(forge, f"refs/heads/{JOB_BRANCH}") is None, "half of a refused push landed"
    assert "refused with the rest of this push" in out, out


async def test_a_follow_up_job_pushes_the_pull_requests_existing_branch(proxy, forge, tmp_path):
    """A PR follow-up job's branch was created by someone else; it is the one it is enrolled for."""
    pr_branch = "feature/existing-pr"
    await _enroll(proxy, forge, git_push_branches=[pr_branch])
    home, work = await _workspace(tmp_path, proxy, forge)
    await run_git("checkout", "-b", pr_branch, home=home, cwd=work)
    await _commit(home, work, "pr.md")
    # The branch exists on the forge before the follow-up job runs.
    rc, out = await run_git("push", "origin", pr_branch, home=home, cwd=work)
    assert rc == 0, out
    await _commit(home, work, "follow-up.md")
    rc, out = await run_git("push", "origin", pr_branch, home=home, cwd=work)
    assert rc == 0, f"a follow-up job could not push its PR's branch:\n{out}"


async def test_an_enrollment_without_push_branches_refuses_every_push(proxy, forge, tmp_path):
    """An orchestrator older than the proxy image sends git_push without branches: fail closed."""
    with structlog.testing.capture_logs() as logs:
        echoed = await _enroll(proxy, forge, git_push_branches=None)
    assert echoed["git_push_branches"] == []
    assert "git_proxy_enrollment_without_push_branches" in [e["event"] for e in logs]
    home, work = await _workspace(tmp_path, proxy, forge)
    await run_git("checkout", "-b", JOB_BRANCH, home=home, cwd=work)
    await _commit(home, work, "x.md")
    rc, out = await run_git("push", "origin", JOB_BRANCH, home=home, cwd=work)
    assert rc != 0, f"an enrollment naming no branch pushed:\n{out}"
    assert "none (the enrollment names no branch)" in out, out
    assert _ref(forge, f"refs/heads/{JOB_BRANCH}") is None


async def test_the_orchestrator_can_swap_the_branch_after_the_developer_chooses(proxy, forge, tmp_path):
    await _enroll(proxy, forge)
    home, work = await _workspace(tmp_path, proxy, forge)
    chosen = "embry0/iss-1-fix-widgets"

    resp = await proxy.put(
        "/admin/git-push-branches/sbx-1", json={"git_push_branches": [chosen]}, headers={"X-Admin-Token": ADMIN}
    )
    assert resp.status == 200, await resp.text()
    assert (await resp.json())["git_push_branches"] == [chosen]

    await _commit(home, work, "x.md")
    rc, out = await run_git("push", "origin", f"HEAD:refs/heads/{chosen}", home=home, cwd=work)
    assert rc == 0, f"the swapped-in branch was refused:\n{out}"
    rc, out = await run_git("push", "origin", f"HEAD:refs/heads/{JOB_BRANCH}", home=home, cwd=work)
    assert rc != 0, f"the branch swapped out is still writable:\n{out}"


async def test_the_swap_route_is_admin_only_and_checks_its_input(proxy, forge):
    await _enroll(proxy, forge)
    path = "/admin/git-push-branches/sbx-1"
    resp = await proxy.put(path, json={"git_push_branches": ["main"]}, headers={"Authorization": f"Bearer {BEARER}"})
    assert resp.status == 401, "the sandbox bearer must not change its own push branches"
    for bad in (["refs/heads/main"], ["a..b"], ["-x"], "main", [1]):
        resp = await proxy.put(path, json={"git_push_branches": bad}, headers={"X-Admin-Token": ADMIN})
        assert resp.status == 400, f"{bad!r} was accepted (HTTP {resp.status})"
    resp = await proxy.put(
        "/admin/git-push-branches/sbx-unknown", json={"git_push_branches": ["x"]}, headers={"X-Admin-Token": ADMIN}
    )
    assert resp.status == 404
    await enroll(
        proxy, "sbx-ro", "ro-bearer-" + "r" * 40, allowed_repos=[REPO], git_upstream=forge.origin, git_push=False
    )  # type: ignore[attr-defined]
    resp = await proxy.put(
        "/admin/git-push-branches/sbx-ro", json={"git_push_branches": ["x"]}, headers={"X-Admin-Token": ADMIN}
    )
    assert resp.status == 409, "a read-only sandbox must not gain push branches"


async def test_an_enrollment_naming_no_branch_yet_refuses_every_push_quietly(proxy, forge, tmp_path):
    """#395: a job's sandbox is enrolled with an empty list until its route is planned with a coder step. Every push
    is refused, as for an older orchestrator, but the proxy does not warn: this is the expected state."""
    with structlog.testing.capture_logs() as logs:
        echoed = await _enroll(proxy, forge, git_push_branches=[])
    assert echoed["git_push_branches"] == []
    events = [e["event"] for e in logs]
    assert "git_proxy_enrollment_without_push_branches" not in events, events
    assert "git_proxy_enrollment_no_push_branch_yet" in events, events
    home, work = await _workspace(tmp_path, proxy, forge)
    await run_git("checkout", "-b", JOB_BRANCH, home=home, cwd=work)
    await _commit(home, work, "x.md")
    rc, out = await run_git("push", "origin", JOB_BRANCH, home=home, cwd=work)
    assert rc != 0 and "none (the enrollment names no branch)" in out, out
    assert _ref(forge, f"refs/heads/{JOB_BRANCH}") is None
