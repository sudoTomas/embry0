"""Reusable per-sandbox prep — extracted from init_qa_node so single-app and
multi-app QA flows share one implementation.

Two-phase API:

  1. prep_qa_sandbox_clone() — networking + git creds + clone + head_sha capture.
     Caller may then read ``.embry0/qa.yaml`` from the sandbox if needed.

  2. prep_qa_sandbox_jobjson() — mint presigned URLs + write /workspace/.qa/job.json.
     Caller passes the full ``job_json`` dict (caller decides its contents and
     structure); the helper signs the canonical artifacts and writes the file.

The split lets ``init_qa_node`` read+parse qa.yaml between (1) and (2), while
the multi-app sub-task ``acquire_sandbox_node`` passes a pre-synthesized dict
straight to (2).

Neither function allocates the sandbox or destroys it — caller owns lifecycle.
"""

from __future__ import annotations

import base64
import json
import shlex
from dataclasses import dataclass
from typing import Any

import structlog

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
    base: list[str],
    cached_volume: str | None = None,
) -> ClonedSandbox:
    """Phase 1 of sandbox prep.

    1. (DinD only) Connect container to ``qa_net`` so it can reach the app stack.
    2. Configure git credential helper (proxy-aware) inside the sandbox.
    3a. If ``cached_volume`` is set: skip ``git clone`` (the volume already
        contains a cloned workspace from the warmer); run ``mkdir -p`` for
        ``.qa/{logs,pids}`` idempotently; capture HEAD sha from the volume.
    3b. Otherwise: clone {repo} @ {branch} into /workspace; create
        ``.qa/{logs,pids}``.
    4. Capture HEAD sha via ``git rev-parse HEAD``.

    Parameters match what ``init_qa_node`` has in hand after sandbox creation.
    ``base`` is ``docker._build_base_cmd()`` — the docker CLI prefix for raw
    docker commands (distinct from ``docker.build_exec_cmd`` which builds
    ``docker exec`` invocations).

    ``cached_volume``: when set to a Docker volume name, signals that the
    shared-volume cache layer mounted a pre-warmed workspace at ``/workspace``.
    The git clone step is skipped; HEAD sha is read directly from the volume.
    Git credential setup still runs so any in-sandbox git operations (e.g.
    differential-checkout) can authenticate.

    Raises whatever ``docker.run_cmd`` raises on failure; caller is responsible
    for cleanup via ``sandbox_mgr.destroy(container_id)``.
    """
    # 1. Network attach (DinD only)
    if is_dind:
        await docker.run_cmd(
            base + ["network", "connect", qa_net, container_id],
            timeout=10,
        )

    # 2. Git creds
    git_proxy_url = getattr(proxy_mgr, "git_proxy_url", "") if proxy_mgr else ""
    if git_proxy_url:
        from embry0.sandbox.github.git_ops import build_sandbox_credential_config_cmd

        cred_cmd = build_sandbox_credential_config_cmd(git_proxy_url, sandbox_token)
        setup_cmd = [
            "bash",
            "-c",
            f"{cred_cmd} && "
            'git config --global user.email "qa-agent@embry0.local" && '
            'git config --global user.name "embry0 QA"',
        ]
        await docker.run_cmd(
            docker.build_exec_cmd(container_id, setup_cmd),
            timeout=10,
        )
    else:
        logger.warning(
            "qa_git_proxy_unavailable",
            job_id=job_id,
            msg="No git proxy URL — clone may fail for private repos",
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
        # 3b. Standard path: clone repo into /workspace.
        clone_cmd = [
            "bash",
            "-c",
            (
                f"set -e && git clone --depth=50 --branch {shlex.quote(branch)} "
                f"https://github.com/{shlex.quote(repo)}.git /workspace && "
                "cd /workspace && mkdir -p .qa/logs .qa/pids"
            ),
        ]
        await docker.run_cmd(docker.build_exec_cmd(container_id, clone_cmd), timeout=120)

    # 4. Capture head sha (works for both paths — volume has a real git repo)
    head_sha_raw = await docker.run_cmd(
        docker.build_exec_cmd(
            container_id,
            ["bash", "-c", "cd /workspace && git rev-parse HEAD"],
        ),
        timeout=10,
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
