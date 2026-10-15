"""Git workspace initializer — git-proxy remote + identity + shallow clone.

Absorbs the logic previously inlined in ``init_node`` (EMB-era). Git reaches
the job's repository through the git-proxy (#521, ADR 0007): the sandbox's git
config rewrites ``<clone origin>/<repo>.git`` to the proxy, which adds the
forge credential upstream, so the orchestrator's forge token never enters the
sandbox. The proxy forwards to the base URL enrolled for that sandbox, which
``SandboxManager`` takes from :func:`clone_origin` too, so the two agree.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from embry0.execution import events
from embry0.workspace_init.base import InitContext, WorkspaceInitError

logger = structlog.get_logger(__name__)

# Branch/sha names are passed into a shell line; keep the vocabulary strict
# (same defensive posture as the git-proxy URL regex).
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")


# Clone origins per forge (#166). The git-proxy's enrolled upstream is this
# same origin (SandboxManager), and the sandbox's git config rewrites exactly
# `<origin>/<repo>.git` to the proxy.
FORGE_CLONE_HOSTS: dict[str, str] = {
    "github": "https://github.com",
    "gitlab": "https://gitlab.com",
}
DEFAULT_CLONE_HOST = FORGE_CLONE_HOSTS["github"]


def clone_origin(forge: str = "github", gitlab_url: str = "") -> str:
    """Base URL to clone from.

    ``gitlab_url`` overrides the gitlab.com default so self-managed instances
    work; it is ignored for GitHub, which has no equivalent knob.
    """
    if forge == "gitlab" and gitlab_url:
        return gitlab_url.rstrip("/")
    return FORGE_CLONE_HOSTS.get(forge, DEFAULT_CLONE_HOST)


def build_clone_shell(
    repo: str,
    ref: str | None = None,
    *,
    forge: str = "github",
    gitlab_url: str = "",
    base_branch: str = "main",
) -> str:
    """Shell for the init clone; ONLY the base-branch fetch is best-effort.

    The ``|| true`` must be brace-scoped to the fetch: ``a && b || true``
    rescues the WHOLE chain, which let a failed clone exit 0 and report
    "Repository cloned" over an empty /workspace (2026-07-06 access
    smoke). The fetch stays best-effort because it fails benignly
    when the clone already checked out the base branch.

    ``base_branch`` (Spec 1A §3.7) is the repo's integration branch — the
    same ref the review diff and the MR/PR target use. It used to be the
    literal ``main``, which left a ``dev``-based repo with no local base to
    diff against. A value that fails ``_REF_RE`` never reaches the shell
    line: ``main`` is fetched in its place and the rejection is logged.
    The fallback does not raise — this fetch is best-effort and a bad
    preference must not fail the clone — so that warning is the only trace
    of a job quietly diffing against the wrong branch.
    """
    if _REF_RE.match(base_branch or ""):
        base = base_branch
    else:
        base = "main"
        logger.warning(
            "base_branch_rejected",
            base_branch=base_branch,
            msg="base_branch is not a usable branch name; fetching main instead",
        )
    shell = (
        f"git clone --depth=50 {clone_origin(forge, gitlab_url)}/{repo}.git /workspace"
        f" && {{ git -C /workspace fetch origin {base}:{base} --depth=50 || true; }}"
    )
    if ref:
        # JobContext.ref: check out a specific branch/sha after the clone.
        # A --depth clone is single-branch, so other refs must be fetched
        # explicitly — and a bare `fetch origin <branch>` lands only in
        # FETCH_HEAD, so the old `checkout <branch>` died with "pathspec did
        # not match" (first live hit: RAV-1092's first pr-followup job).
        # Branch path: fetch into a remote-tracking ref and check out a LOCAL
        # branch of the same name (follow-up jobs must push back to it).
        # Fallback path (SHAs, tags): fetch the raw ref and detach onto
        # FETCH_HEAD.
        shell += (
            f" && {{ {{ git -C /workspace fetch origin"
            f" +refs/heads/{ref}:refs/remotes/origin/{ref} --depth=50"
            f" && git -C /workspace checkout -B {ref} refs/remotes/origin/{ref}; }}"
            f" || {{ git -C /workspace fetch origin {ref} --depth=50"
            f" && git -C /workspace checkout --detach FETCH_HEAD; }}; }}"
        )
    return shell


class GitWorkspaceInitializer:
    name = "git"

    def validate(self, context: dict[str, Any], config: Any | None) -> None:
        # JobContext already guarantees repo for type=git at the API boundary;
        # re-check defensively for rows written outside the API.
        if not context.get("repo"):
            raise WorkspaceInitError("git context requires 'repo'")
        ref = context.get("ref")
        if ref and not _REF_RE.match(str(ref)):
            raise WorkspaceInitError(f"git context ref {ref!r} contains disallowed characters")

    async def initialize(self, ctx: InitContext) -> dict[str, Any]:
        repo = ctx.context.get("repo") or ""
        ref = ctx.context.get("ref")
        docker = ctx.docker
        if not docker:
            raise WorkspaceInitError("git initializer requires a docker client")

        if ctx.git_proxy_url:
            from embry0.sandbox.git_identity import build_git_identity_cmd, resolve_git_identity
            from embry0.sandbox.github.git_ops import build_sandbox_git_proxy_config_cmd

            identity = await resolve_git_identity(ctx.repo_preferences_repo, repo, credential=ctx.forge_credential)
            git_cmd = build_sandbox_git_proxy_config_cmd(
                ctx.git_proxy_url,
                ctx.sandbox_token,
                origin=clone_origin(ctx.forge, ctx.gitlab_url),
                repo=repo,
            )
            setup_cmd = [
                "bash",
                "-c",
                f"{git_cmd} && {build_git_identity_cmd(identity)}",
            ]
            await docker.run_cmd(
                docker.build_exec_cmd(ctx.container_id, setup_cmd),
                timeout=10,
            )
        else:
            logger.warning(
                "git_proxy_unavailable",
                job_id=ctx.job_id,
                msg="No git proxy URL — skipping git proxy setup; clone and push to private repos will fail.",
            )

        # Fail loudly on clone error — if /workspace isn't a git repo, every
        # downstream agent call silently misbehaves. A non-zero exit from
        # `git clone` makes DockerClient.run_cmd raise RuntimeError.
        clone_cmd = [
            "bash",
            "-c",
            build_clone_shell(repo, ref, forge=ctx.forge, gitlab_url=ctx.gitlab_url, base_branch=ctx.base_branch),
        ]
        try:
            # 300s (was 120s): large monorepos cloned via the git-proxy at
            # --depth=50 can exceed two minutes when the proxy adds latency
            # or the repo has many large files. Verified against a large repo
            # which timed out at exactly 120s on first attempt.
            await docker.run_cmd(
                docker.build_exec_cmd(ctx.container_id, clone_cmd),
                timeout=300,
            )
        except RuntimeError as exc:
            logger.error("repo_clone_failed", job_id=ctx.job_id, repo=repo, error=str(exc))
            ctx.emit(events.error(message=f"Repository clone failed for {repo}: {exc}", node="init"))
            raise WorkspaceInitError(f"Repository clone failed for {repo}: {exc}") from exc
        ctx.emit(events.progress(message=f"Repository {repo} cloned", node="init"))
        logger.info("repo_cloned", job_id=ctx.job_id, repo=repo, ref=ref)
        return {}
