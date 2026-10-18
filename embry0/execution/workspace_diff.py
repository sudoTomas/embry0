"""Sandbox git-diff helper — compute a job's changed-file set.

Promoted from ``embry0/workflows/qa/_orchestrator_workspace.py`` (EMB-35):
the review node scopes its input by changed files too, so the helper is no
longer QA-only. The QA module re-exports it for its existing callers.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Same defensive vocabulary as workspace_init/git.py: the resolved name is
# interpolated into shell lines (shlex-quoted) and agent prompts downstream.
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")


class WorkspaceDiffUnavailable(RuntimeError):
    """A strict diff could not be computed: the base fetch or ``git diff`` failed.

    Distinct from an empty diff, which is an answer: the branch changed
    nothing against its base.
    """


async def resolve_base_branch(
    *,
    docker: Any,
    container_id: str,
    configured: str | None = None,
) -> str:
    """Resolve the diff base for a job's workspace (RAV-1405).

    Precedence: an explicitly configured base (``state.base_branch``, set via
    QA job overrides) → the clone's ``origin/HEAD`` symbolic ref (set by
    default-branch clones; no network) → ``git ls-remote --symref origin
    HEAD`` (RAV-1400: the QA bootstrap clones ``--branch <ref>
    --single-branch``, which never sets a local origin/HEAD — the remote
    lookup goes through the sandbox's git-proxy remote) → ``"main"``. The old
    hardcoded-"main" fallback diffed one repo's branches against an
    unrelated orphan ``main`` (3994 files), which blew the review prompt
    past ARG_MAX and killed the job.
    """
    if configured:
        return str(configured)
    cmd = [
        "bash",
        "-c",
        # Local first (free), remote second: ls-remote prints
        # "ref: refs/heads/<name>\tHEAD" for the symref.
        "cd /workspace && { git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null"
        " || git ls-remote --symref origin HEAD 2>/dev/null"
        " | sed -n 's|^ref: refs/heads/\\(.*\\)\\tHEAD$|\\1|p'; } || true",
    ]
    try:
        raw = await docker.run_cmd(docker.build_exec_cmd(container_id, cmd), timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.warning("base_branch_resolve_failed", error=str(exc))
        return "main"
    name = (raw or "").strip().splitlines()[0] if (raw or "").strip() else ""
    name = name.removeprefix("origin/")
    if name and _BRANCH_RE.match(name):
        return name
    return "main"


async def compute_changed_files_via_diff(
    *,
    docker: Any,
    container_id: str,
    base_branch: str,
    strict: bool = False,
) -> list[str]:
    """Run `git diff --name-only origin/<base_branch>...HEAD` in the sandbox.

    Three dots, deliberately: for ``git diff`` two dots is a plain tree
    comparison, so every commit the base has gained since the branch forked
    reads as a change *in the branch*. The verify node's tier and the review
    scope both come from this list; with two dots a frontend commit landing
    on main mid-job made an infra-only PR a "frontend change with no browser
    QA" (#398). The merge-base diff is the branch's own change whatever main
    did meanwhile.

    Returns repo-root-relative paths as strings. Empty list on any failure
    (logged at WARNING) — callers treat empty as "no scoping available"
    (QA: every app affected; review: unscoped prompt).

    ``strict=True`` is for a caller that must tell a failed diff from an
    empty one (QA app selection, #65): a fetch failure or a non-zero
    ``git diff`` exit (no merge base, unknown ref) raises
    ``WorkspaceDiffUnavailable`` instead of returning ``[]``.

    The base branch must exist remotely. We `git fetch origin <base_branch>
    --depth=50` first (depth covers most short-lived PRs).
    """
    # Step 1: fetch base ref (best-effort; failures fall through to empty diff).
    fetch_cmd = [
        "bash",
        "-c",
        f"cd /workspace && git fetch origin {shlex.quote(base_branch)}:refs/remotes/origin/{shlex.quote(base_branch)} --depth=50",
    ]
    try:
        await docker.run_cmd(
            docker.build_exec_cmd(container_id, fetch_cmd),
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "workspace_diff_fetch_failed",
            base_branch=base_branch,
            error=str(exc),
            msg="git fetch failed; skipping diff",
        )
        if strict:
            raise WorkspaceDiffUnavailable(f"git fetch origin {base_branch} failed: {exc}") from exc
        return []

    # Step 2: actual diff. Strict mode drops `|| true` so a git failure
    # surfaces as run_cmd's non-zero-exit RuntimeError.
    diff_shell = f"cd /workspace && git diff --name-only origin/{shlex.quote(base_branch)}...HEAD"
    diff_cmd = ["bash", "-c", diff_shell if strict else f"{diff_shell} || true"]
    try:
        raw = await docker.run_cmd(
            docker.build_exec_cmd(container_id, diff_cmd),
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "workspace_diff_failed",
            base_branch=base_branch,
            error=str(exc),
        )
        if strict:
            raise WorkspaceDiffUnavailable(f"git diff against origin/{base_branch} failed: {exc}") from exc
        return []

    if not raw:
        return []

    files = [line.strip() for line in raw.splitlines() if line.strip()]
    logger.info(
        "workspace_diff_computed",
        base_branch=base_branch,
        n_files=len(files),
    )
    return files
