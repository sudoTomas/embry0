"""Reusable per-sandbox prep, shared by the QA, onboarding and cache-warming
flows that allocate a sandbox and clone into it.

Two-phase API:

  1. prep_qa_sandbox_clone() — networking + git creds + clone + head_sha capture.
     Caller may then read ``.embry0/qa.yaml`` from the sandbox if needed.

  2. prep_qa_sandbox_jobjson() — mint presigned URLs + write /workspace/.qa/job.json.
     Caller passes the full ``job_json`` dict (caller decides its contents and
     structure); the helper signs the canonical artifacts and writes the file.

The split lets a caller do its own work between (1) and (2); the sub-task
``acquire_sandbox_node`` passes a pre-synthesized dict straight to (2).

Neither function allocates the sandbox or destroys it — caller owns lifecycle.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from embry0.execution import sandbox_git
from embry0.workspace_init.git import clone_origin

if TYPE_CHECKING:
    from embry0.execution.runtime import AgentRuntime

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ClonedSandbox:
    head_sha: str
    """Output of ``git rev-parse HEAD`` after clone, stripped."""


async def prep_qa_sandbox_clone(
    *,
    docker: Any,
    proxy_mgr: Any | None,
    container_id: str,
    sandbox_token: str,
    job_id: str,
    repo: str,
    branch: str,
    is_dind: bool,
    qa_net: str,
    runtime: AgentRuntime | None = None,
    cached_volume: str | None = None,
    prefs_repo: Any | None = None,
    forge: str = "github",
    gitlab_url: str = "",
    forge_credential: Any | None = None,
) -> ClonedSandbox:
    """Phase 1 of sandbox prep.

    1. (DinD only) Connect container to ``qa_net`` so it can reach the app stack.
    2. Make the git-proxy the sandbox's git remote for ``repo`` (read only: QA never pushes).
    3a. If ``cached_volume`` is set: skip ``git clone`` (the volume already
        contains a cloned workspace from the warmer); run ``mkdir -p`` for
        ``.qa/{logs,pids}`` idempotently; capture HEAD sha from the volume.
    3b. Otherwise: clone {repo} @ {branch} into /workspace; create
        ``.qa/{logs,pids}``.
    4. Capture HEAD sha via ``git rev-parse HEAD``.

    Parameters match what a caller has in hand after sandbox creation.
    ``runtime`` attaches the sandbox to the QA network, so only a DinD-mode
    caller needs it; ``docker`` runs the commands inside the sandbox.

    ``cached_volume``: when set to a Docker volume name, signals that the
    shared-volume cache layer mounted a pre-warmed workspace at ``/workspace``.
    The git clone step is skipped; HEAD sha is read directly from the volume.
    Git proxy setup still runs so any in-sandbox git operations (e.g.
    differential-checkout) reach the repository.

    ``prefs_repo``: optional RepoPreferencesRepository — enables the per-repo
    git author identity override (EMB-51). Absent, the branding default
    (env-overridable, valid noreply address) applies.

    ``forge_credential``: the credential bound to this sandbox
    (``sandbox_mgr.last_forge_credential``); its author sits between the
    preference and the branding default (spec 2026-09-29 §3.3).

    ``forge`` / ``gitlab_url``: where to clone from (#166 / spec 3.6). The
    origin must match the host the git-proxy enrolled for this sandbox or git
    treats the credential as belonging to a different site and the clone fails
    as unauthenticated. ``gitlab_url`` covers self-managed instances and is
    ignored for GitHub.

    Raises whatever ``docker.run_cmd`` raises on failure; caller is responsible
    for cleanup via ``sandbox_mgr.destroy(container_id)``.
    """
    # 1. Network attach (DinD only)
    if is_dind:
        if runtime is None:
            raise RuntimeError("a DinD-mode QA sandbox needs the runtime to join its network")
        await runtime.connect_network(qa_net, container_id, timeout=10)

    # 2. Git: embry0's own git config, and the git-proxy as the remote for this
    # repo (#521; read only: QA never pushes).
    from embry0.sandbox.git_identity import resolve_git_identity
    from embry0.sandbox.github.git_ops import proxy_route

    git_proxy_url = getattr(proxy_mgr, "git_proxy_url", "") if proxy_mgr else ""
    route = None
    if git_proxy_url:
        route = proxy_route(git_proxy_url, origin=clone_origin(forge, gitlab_url), repo=repo)
    else:
        logger.warning(
            "qa_git_proxy_unavailable",
            job_id=job_id,
            msg="No git proxy URL — clone may fail for private repos",
        )
    identity = await resolve_git_identity(prefs_repo, repo, credential=forge_credential)
    await sandbox_git.configure(
        docker, container_id, route=route, sandbox_token=sandbox_token, identity=identity, job_id=job_id
    )

    # 2b. xAI proxy bearer (EMB-45 Phase C): when the xai-proxy is up, deliver the
    # per-sandbox bearer to the 0600 file the DirectXaiExecutor reads — same
    # delivery the issue_to_pr init node does for dev/review sandboxes. Without
    # this, a QA agent routed to grok has a proxy URL but no credential.
    xai_proxy_url = getattr(proxy_mgr, "xai_proxy_url", "") if proxy_mgr else ""
    if xai_proxy_url:
        from embry0.sandbox.xai_token import build_xai_token_write_cmd

        await docker.run_cmd(
            docker.build_exec_cmd(container_id, build_xai_token_write_cmd(), interactive=True),
            timeout=10,
            stdin_data=sandbox_token.encode(),
        )

    if cached_volume:
        # 3a. Volume path: workspace already present from warmer.  Skip clone;
        # ensure .qa dirs exist (idempotent mkdir -p is safe if already there).
        logger.info(
            "qa_sandbox_using_cached_volume",
            job_id=job_id,
            volume=cached_volume,
        )
        await docker.run_cmd(
            docker.build_exec_cmd(
                container_id,
                ["bash", "-c", "cd /workspace && mkdir -p .qa/logs .qa/pids"],
            ),
            timeout=10,
        )
    else:
        # 3b. Standard path: clone repo into /workspace — UNLESS /workspace is
        # already a git checkout. RAV-1400: sandboxes based on a pre-baked
        # image carry the bake-time workspace (and Docker copies it into any
        # fresh named volume mounted there), so a blind clone dies with
        # "destination path '/workspace' already exists". Reusing the baked
        # checkout is the point: fetch + checkout the requested branch on top
        # and the baked node_modules stays in place. That checkout was made
        # by another sandbox, so embry0 checks it before running git in it.
        #
        # -f (force): the baked workspace's bake-time `npm install` leaves
        # package-lock.json (a tracked file) dirty, so a plain checkout
        # aborts with "local changes would be overwritten" on any branch
        # switch. Forcing discards those bake-time tracked-file mutations
        # (the branch tree is the source of truth); node_modules is
        # gitignored and is preserved, so the baked deps stay in place.
        g = sandbox_git.git
        reuse = (
            f"{{ {g('fetch', 'origin', f'+refs/heads/{branch}:refs/remotes/origin/{branch}', '--depth=50')}"
            f" && {g('checkout', '-f', '-B', branch, f'refs/remotes/origin/{branch}')}; }}"
            f" || {{ {g('fetch', 'origin', branch, '--depth=50')} && {g('checkout', '-f', '--detach', 'FETCH_HEAD')}; }}"
        )
        clone = g(
            "clone", "--depth=50", "--branch", branch, f"{clone_origin(forge, gitlab_url)}/{repo}.git", "/workspace"
        )
        clone_cmd = sandbox_git.script(
            "set -e\n"
            "if [ -e /workspace/.git ] || [ -L /workspace/.git ]; then\n"
            f"  {sandbox_git.check_repo()}\n"
            f"  {reuse}\n"
            "else\n"
            f"  {clone}\n"
            "fi && cd /workspace && mkdir -p .qa/logs .qa/pids\n",
            repo=None,
        )
        # 300s (was 120s): matches the issue-path init clone budget — a large
        # repo via the git-proxy can exceed two minutes (a large monorepo
        # timed out at exactly 120s on job-06f63d7ba031, 2026-07-23), and the
        # issue path was bumped for the identical failure.
        await sandbox_git.run(
            docker, container_id, clone_cmd, step=sandbox_git.GitStep.QA_CHECKOUT, timeout=300, job_id=job_id
        )

    # 4. Capture head sha (works for both paths — volume has a real git repo)
    head_sha_raw = await sandbox_git.run(
        docker,
        container_id,
        sandbox_git.command("rev-parse", "HEAD"),
        step=sandbox_git.GitStep.QA_CHECKOUT,
        timeout=10,
        job_id=job_id,
    )
    head_sha = head_sha_raw.strip().splitlines()[-1] if head_sha_raw else ""

    return ClonedSandbox(head_sha=head_sha)


async def prep_qa_sandbox_jobjson(
    *,
    docker: Any,
    minio_sandbox: Any,
    token_registry: Any,
    container_id: str,
    sandbox_token: str,
    job_id: str,
    attempt_n: int,
    artifact_prefix: str,
    job_json: dict[str, Any],
) -> None:
    """Phase 2 of sandbox prep.

    1. Register sandbox token with the QA presign registry.
    2. Mint presigned PUT URLs for result.json and logs/full.log; merge them
       into ``job_json`` under the ``"artifact_uploads"`` key.
    3. Write base64-encoded /workspace/.qa/job.json.

    ``job_json`` is built by the caller (who knows all context-specific fields
    like acceptance_criteria, changed_files, presign_refresh_url, etc.).  This
    helper adds the two canonical presigned upload URLs and persists the file.
    """
    # 1. Register token
    token_registry.register(sandbox_token, job_id=job_id, attempt_n=attempt_n)

    # 2. Mint presigned URLs and inject into caller-provided dict
    presign_paths = ["result.json", "logs/full.log"]
    presigned: dict[str, str] = {}
    for p in presign_paths:
        url = await minio_sandbox.presign_put(
            "qa-artifacts",
            artifact_prefix + p,
            expires_seconds=21600,
        )
        presigned[p] = url
    job_json["artifact_uploads"] = presigned

    # 3. Write job.json via base64 round-trip (no heredoc — avoids shell injection
    # from acceptance_criteria or other string fields).
    encoded = base64.b64encode(json.dumps(job_json).encode()).decode()
    write_cmd = [
        "bash",
        "-c",
        f"echo '{encoded}' | base64 -d > /workspace/.qa/job.json",
    ]
    await docker.run_cmd(docker.build_exec_cmd(container_id, write_cmd), timeout=10)
