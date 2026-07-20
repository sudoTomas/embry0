"""Orchestrator-side workspace-staging helpers.

Used by `init_orchestrator_node` to extract every package.json + the
lockfile from the bootstrap sandbox into a local /tmp directory the
workspace_provider can read.

Crash-tolerant: per-file read failures degrade gracefully to a warning +
skip, so a flaky monorepo doesn't block the whole QA run.

``compute_changed_files_via_diff`` moved to
``embry0.execution.workspace_diff`` (EMB-35 — the review node uses it
too); re-exported here for existing QA callers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from embry0.execution.workspace_diff import compute_changed_files_via_diff

__all__ = ["compute_changed_files_via_diff", "stage_workspace_for_provider"]

logger = structlog.get_logger(__name__)


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
        rel = in_sandbox_path[len("/workspace/") :]
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
