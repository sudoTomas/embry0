"""Sandbox git-diff helper — compute a job's changed-file set.

Promoted from ``embry0/workflows/qa/_orchestrator_workspace.py`` (EMB-35):
the review node scopes its input by changed files too, so the helper is no
longer QA-only. The QA module re-exports it for its existing callers.
"""

from __future__ import annotations

import shlex
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def compute_changed_files_via_diff(
    *,
    docker: Any,
    container_id: str,
    base_branch: str,
) -> list[str]:
    """Run `git diff --name-only origin/<base_branch>..HEAD` in the sandbox.

    Returns repo-root-relative paths as strings. Empty list on any failure
    (logged at WARNING) — callers treat empty as "no scoping available"
    (QA: every app affected; review: unscoped prompt).

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
        return []

    # Step 2: actual diff.
    diff_cmd = [
        "bash",
        "-c",
        f"cd /workspace && git diff --name-only origin/{shlex.quote(base_branch)}..HEAD || true",
    ]
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
