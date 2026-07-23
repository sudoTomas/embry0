"""Git workspace initializer — credential helper + identity + shallow clone.

Absorbs the logic previously inlined in ``init_node`` (EMB-era). Git auth
flows via the credential proxy: the sandbox's helper curls
``$EMBRY0_GIT_PROXY_URL/git-credentials`` with the per-sandbox bearer, so
the orchestrator's GitHub token never enters the sandbox.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from embry0.workspace_init.base import InitContext, WorkspaceInitError

logger = structlog.get_logger(__name__)

# Branch/sha names are passed into a shell line; keep the vocabulary strict
# (same defensive posture as the git-proxy URL regex).
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")


def build_clone_shell(repo: str, ref: str | None = None) -> str:
    """Shell for the init clone; ONLY the main:main fetch is best-effort.

    The ``|| true`` must be brace-scoped to the fetch: ``a && b || true``
    rescues the WHOLE chain, which let a failed clone exit 0 and report
    "Repository cloned" over an empty /workspace (2026-07-06 access
    smoke). The fetch stays best-effort because it fails benignly
    when the clone already checked out main.
    """
    shell = (
        f"git clone --depth=50 https://github.com/{repo}.git /workspace"
        f" && {{ git -C /workspace fetch origin main:main --depth=50 || true; }}"
    )
    if ref:
        # JobContext.ref: check out a specific branch/sha after the clone.
        # Fetched explicitly first — a --depth=50 clone of the default branch
        # doesn't carry other branches.
        shell += (
            f" && {{ git -C /workspace fetch origin {ref} --depth=50 || true; }} && git -C /workspace checkout {ref}"
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
            from embry0.sandbox.github.git_ops import build_sandbox_credential_config_cmd

            identity = await resolve_git_identity(ctx.repo_preferences_repo, repo)
            cred_cmd = build_sandbox_credential_config_cmd(ctx.git_proxy_url, ctx.sandbox_token)
            setup_cmd = [
                "bash",
                "-c",
                f"{cred_cmd} && {build_git_identity_cmd(identity)}",
            ]
            await docker.run_cmd(
                docker.build_exec_cmd(ctx.container_id, setup_cmd),
                timeout=10,
            )
        else:
            logger.warning(
                "git_proxy_unavailable",
                job_id=ctx.job_id,
                msg="No git proxy URL — skipping credential helper setup; clone and push to private repos will fail.",
            )

        # Fail loudly on clone error — if /workspace isn't a git repo, every
        # downstream agent call silently misbehaves. A non-zero exit from
        # `git clone` makes DockerClient.run_cmd raise RuntimeError.
        clone_cmd = ["bash", "-c", build_clone_shell(repo, ref)]
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
            ctx.emit({"type": "error", "message": f"Repository clone failed for {repo}: {exc}"})
            raise WorkspaceInitError(f"Repository clone failed for {repo}: {exc}") from exc
        ctx.emit({"type": "progress", "message": f"Repository {repo} cloned"})
        logger.info("repo_cloned", job_id=ctx.job_id, repo=repo, ref=ref)
        return {}
