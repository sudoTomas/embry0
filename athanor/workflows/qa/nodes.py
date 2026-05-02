"""QA pipeline nodes: init_qa, qa, report, retry.

Dependency injection: each node reads runtime dependencies from
state["__deps"] (set by IssueExecutor at job start). Same pattern as
issue_to_pr/nodes.py uses to access docker, proxy_mgr, etc.

Phase 1.5 architecture: __deps carries TWO MinIO clients —
- minio_sandbox: endpoint=minio-proxy:9100, used to mint URLs the
  sandbox will hit through Docker DNS in DinD.
- minio_internal: endpoint=minio:9000, used by report_node for direct
  orchestrator-side uploads.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import structlog

from athanor.workflows.qa.qa_yaml import parse_qa_yaml

logger = structlog.get_logger(__name__)


async def init_qa_node(state: dict) -> dict:
    """Initialize a QA attempt: resolve profile, start sandbox, clone repo,
    parse qa.yaml, register sandbox token, mint presigned URLs, write job.json.
    """
    deps = state["__deps"]
    docker = deps["docker"]
    sandbox_mgr = deps["sandbox_manager"]
    profiles_repo = deps["profiles_repo"]
    minio_sandbox = deps["minio_sandbox"]
    token_registry = deps["token_registry"]

    job_id = state["job_id"]
    repo = state["repo"]
    branch = state.get("branch") or "main"
    qa = state.setdefault("qa", {})

    attempt_n = len(qa.get("attempts", [])) + 1
    base = docker._build_base_cmd()  # noqa: SLF001
    qa_net = f"qa-net-{job_id}"

    # 1. Resolve sandbox profile.
    profile_name = (
        qa.get("sandbox_profile_name")
        or qa.get("qa_yaml_parsed", {}).get("sandbox_profile")
        or "qa-jvm"
    )
    profile = await profiles_repo.get(profile_name)
    if profile is None:
        msg = f"qa: sandbox profile {profile_name!r} does not exist"
        raise RuntimeError(msg)

    is_dind = bool(profile.get("dind_enabled", False))

    # 2. (Mode 2) create per-job network.
    if is_dind:
        await docker.run_cmd(
            base + ["network", "create", "--label", f"athanor.qa_job_id={job_id}", qa_net],
            timeout=15,
        )
        logger.info("qa_net_created", job_id=job_id, network=qa_net)

    # 3. Start the sandbox (Phase 1.5 wiring is in SandboxManager).
    container_id, sandbox_token = await sandbox_mgr.create(
        job_id, profile=profile,
        env={
            "QA_JOB_ID": job_id,
            "QA_ATTEMPT_N": str(attempt_n),
            "QA_NETWORK_NAME": qa_net,
        },
    )

    # 4. (Mode 2) attach sandbox to qa-net so it can reach app stack by DNS.
    if is_dind:
        await docker.run_cmd(base + ["network", "connect", qa_net, container_id], timeout=10)

    # 5. Clone the target repo.
    clone_cmd = [
        "bash", "-c",
        (
            f"set -e && git clone --depth=50 --branch {branch} "
            f"https://github.com/{repo}.git /workspace && "
            "cd /workspace && mkdir -p .qa/logs .qa/pids"
        ),
    ]
    await docker.run_cmd(docker.build_exec_cmd(container_id, clone_cmd), timeout=120)

    # 6. Read + parse .athanor/qa.yaml from inside the sandbox.
    yaml_text = await docker.run_cmd(
        docker.build_exec_cmd(container_id, ["cat", "/workspace/.athanor/qa.yaml"]),
        timeout=10,
    )
    qa_yaml = parse_qa_yaml(yaml_text)

    # 7. Register sandbox token with the QA presign registry.
    token_registry.register(sandbox_token, job_id=job_id, attempt_n=attempt_n)

    # 8. Mint presigned URLs via the SANDBOX-FACING client (Phase 1.5).
    # URLs are signed for endpoint=minio-proxy:9100 so the signature validates
    # when the sandbox makes the request through that proxy.
    artifact_prefix = f"{job_id}/{attempt_n}/"
    presign_paths = ["result.json", "logs/full.log"]
    presigned: dict[str, str] = {}
    for p in presign_paths:
        url = await minio_sandbox.presign_put(
            "qa-artifacts", artifact_prefix + p, expires_seconds=21600,
        )
        presigned[p] = url

    # 9. Drop /workspace/.qa/job.json into the sandbox.
    # Refresh URL points at presign-proxy in DinD (sandbox-restricted), NOT at
    # the orchestrator directly — sandboxes can't reach the host backend network.
    job_json = {
        "schema_version": 1,
        "job_id": job_id,
        "attempt_n": attempt_n,
        "mode": qa_yaml.mode,
        "qa_yaml": qa_yaml.model_dump(),
        "acceptance_criteria": qa.get("acceptance_criteria") or qa_yaml.acceptance_criteria_template,
        "changed_files": qa.get("changed_files", []),
        "frontend_url": qa_yaml.frontend_url,
        "artifact_uploads": presigned,
        "presign_refresh_url": "http://presign-proxy:9104/api/v1/internal/qa/presign",
        "sandbox_token": sandbox_token,
    }
    write_cmd = [
        "bash", "-c",
        f"cat > /workspace/.qa/job.json <<'EOF'\n{json.dumps(job_json, indent=2)}\nEOF",
    ]
    await docker.run_cmd(docker.build_exec_cmd(container_id, write_cmd), timeout=10)

    # 10. Update state with the new attempt.
    qa.setdefault("attempts", []).append({
        "attempt_n": attempt_n,
        "started_at": datetime.now(UTC).isoformat(),
        "ended_at": None,
        "sandbox_id": container_id,
        "qa_net_name": qa_net if is_dind else None,
        "artifact_prefix": artifact_prefix,
        "last_phase": None,
        "exit_reason": None,
        "result_summary": None,
        "log_artifact_url": None,
    })
    qa["sandbox_token"] = sandbox_token
    qa["qa_yaml_raw"] = yaml_text
    qa["qa_yaml_parsed"] = qa_yaml.model_dump()
    state["qa"] = qa

    logger.info("init_qa_done", job_id=job_id, attempt_n=attempt_n, sandbox=container_id)
    return state
