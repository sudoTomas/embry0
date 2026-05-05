"""Orchestrator-side workspace-staging and diff helpers.

Used by `init_orchestrator_node` to:
  1. Extract every package.json + the lockfile from the bootstrap sandbox
     into a local /tmp directory the workspace_provider can read.
  2. Compute `changed_files` via `git diff --name-only origin/<base>..HEAD`
     inside the bootstrap sandbox.

Both helpers are crash-tolerant: per-file read failures degrade gracefully
to a warning + skip, so a flaky monorepo doesn't block the whole QA run.
"""

from __future__ import annotations

import shlex
from pathlib import Path
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
    (logged at WARNING) — caller treats empty as "every app affected" via
    the existing fallback.

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
            "qa_orchestrator_diff_fetch_failed",
            base_branch=base_branch,
            error=str(exc),
            msg="git fetch failed; skipping diff (falls back to all-apps)",
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
            "qa_orchestrator_diff_failed",
            base_branch=base_branch,
            error=str(exc),
        )
        return []

    if not raw:
        return []

    files = [line.strip() for line in raw.splitlines() if line.strip()]
    logger.info(
        "qa_orchestrator_diff_computed",
        base_branch=base_branch,
        n_files=len(files),
    )
    return files


async def stage_workspace_for_provider(
    *,
    docker: Any,
    container_id: str,
    job_id: str,
    target_root: Path,
) -> Path:
    """Copy workspace metadata from the sandbox into a local directory.

    Specifically: every package.json reachable within 3 levels of /workspace
    (covers `apps/<name>/package.json` and `packages/<name>/package.json`)
    plus the root lockfile (best-effort: package-lock.json, pnpm-lock.yaml,
    or yarn.lock).

    Returns the local target_root path. Caller is responsible for cleanup
    (the existing janitor reaps orphan staging dirs after 1h).

    The staging dir layout mirrors the in-sandbox structure relative to
    /workspace, so `provider.discover()` against `target_root` produces
    identical results to running it against `/workspace`.
    """
    target_root.mkdir(parents=True, exist_ok=True)

    # 1. Find every package.json (max-depth 3 covers `<root>`, `<glob>/<name>/`,
    #    and `<glob>/<name>/<sub>/` — the latter is rare but legal).
    find_cmd = [
        "bash",
        "-c",
        "find /workspace -maxdepth 3 -name 'package.json' -not -path '*/node_modules/*'",
    ]
    try:
        listing = await docker.run_cmd(
            docker.build_exec_cmd(container_id, find_cmd),
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "qa_orchestrator_workspace_listing_failed",
            job_id=job_id,
            error=str(exc),
        )
        listing = ""

    pkg_paths = [line.strip() for line in listing.splitlines() if line.strip()]

    for in_sandbox_path in pkg_paths:
        # Translate /workspace/foo/bar/package.json → target_root/foo/bar/package.json.
        if not in_sandbox_path.startswith("/workspace/"):
            continue
        rel = in_sandbox_path[len("/workspace/"):]
        if rel == "package.json":
            local_path = target_root / "package.json"
        else:
            local_path = target_root / rel
        local_path.parent.mkdir(parents=True, exist_ok=True)
        cat_cmd = ["cat", in_sandbox_path]
        try:
            content = await docker.run_cmd(
                docker.build_exec_cmd(container_id, cat_cmd),
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qa_orchestrator_pkgjson_read_failed",
                in_sandbox_path=in_sandbox_path,
                error=str(exc),
            )
            continue
        local_path.write_text(content, encoding="utf-8")

    # 2. Best-effort lockfile read. Try the three common variants.
    for lockfile_name in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock"):
        cat_lock = ["cat", f"/workspace/{lockfile_name}"]
        try:
            content = await docker.run_cmd(
                docker.build_exec_cmd(container_id, cat_lock),
                timeout=10,
            )
        except Exception:
            continue
        if content:
            (target_root / lockfile_name).write_text(content, encoding="utf-8")
            break  # one lockfile is enough

    logger.info(
        "qa_orchestrator_workspace_staged",
        job_id=job_id,
        target_root=str(target_root),
        n_package_jsons=len(pkg_paths),
    )
    return target_root
