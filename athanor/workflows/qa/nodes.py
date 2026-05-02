"""QA pipeline nodes: init_qa, qa, report, retry.

Dependency injection: each node reads runtime dependencies from
``config["configurable"]`` (LangGraph standard ``RunnableConfig`` injection),
matching the pattern used by ``athanor/workflows/issue_to_pr/nodes.py``.

Phase 1.5 architecture: configurable carries TWO MinIO clients —
- ``qa_minio_sandbox``: endpoint=minio-proxy:9100, used to mint URLs the
  sandbox will hit through Docker DNS in DinD.
- ``qa_minio_internal``: endpoint=minio:9000, used by report_node for direct
  orchestrator-side uploads.
"""

from __future__ import annotations

import base64
import json
import shlex
from datetime import UTC, datetime

import structlog
from langchain_core.runnables import RunnableConfig

from athanor.workflows.qa.qa_yaml import parse_qa_yaml

logger = structlog.get_logger(__name__)


async def init_qa_node(state: dict, config: RunnableConfig) -> dict:
    """Initialize a QA attempt: resolve profile, start sandbox, clone repo,
    parse qa.yaml, register sandbox token, mint presigned URLs, write job.json.

    Dependencies are read from ``config["configurable"]``:
      - ``docker``, ``sandbox_manager``, ``profiles_repo``
      - ``qa_minio_sandbox``, ``qa_token_registry``
      - ``proxy_manager`` (optional but required for git auth into private repos)

    On any failure during steps 2-9, all partially-allocated resources
    (sandbox token registration, sandbox container, qa-net) are rolled back
    in reverse order before the exception is re-raised.
    """
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_mgr = configurable.get("sandbox_manager")
    profiles_repo = configurable.get("profiles_repo")
    minio_sandbox = configurable.get("qa_minio_sandbox")
    token_registry = configurable.get("qa_token_registry")
    proxy_mgr = configurable.get("proxy_manager")

    if not all([docker, sandbox_mgr, profiles_repo, minio_sandbox, token_registry]):
        raise RuntimeError("init_qa_node missing required dependencies in config['configurable']")

    job_id = state["job_id"]
    repo = state["repo"]
    branch = state.get("branch") or "main"
    qa = state.setdefault("qa", {})

    attempt_n = len(qa.get("attempts", [])) + 1
    base = docker._build_base_cmd()  # noqa: SLF001
    qa_net = f"qa-net-{job_id}"

    # 1. Resolve sandbox profile.
    profile_name = qa.get("sandbox_profile_name") or qa.get("qa_yaml_parsed", {}).get("sandbox_profile") or "qa-jvm"
    if profile_name == "qa-jvm" and not qa.get("sandbox_profile_name") and not qa.get("qa_yaml_parsed"):
        logger.warning(
            "qa_profile_fallback_to_default",
            job_id=job_id,
            msg="Neither qa.sandbox_profile_name nor qa.qa_yaml_parsed.sandbox_profile set; "
            "defaulting to 'qa-jvm'. Set sandbox_profile in .athanor/qa.yaml.",
        )
    profile = await profiles_repo.get(profile_name)
    if profile is None:
        msg = f"qa: sandbox profile {profile_name!r} does not exist"
        raise RuntimeError(msg)

    is_dind = bool(profile.get("dind_enabled", False))

    # Track allocated resources for rollback on failure.
    container_id: str | None = None
    sandbox_token: str | None = None
    qa_net_created = False

    try:
        # 2. (Mode 2) create per-job network.
        if is_dind:
            await docker.run_cmd(
                base + ["network", "create", "--label", f"athanor.qa_job_id={job_id}", qa_net],
                timeout=15,
            )
            qa_net_created = True
            logger.info("qa_net_created", job_id=job_id, network=qa_net)

        # 3. Build sandbox env (filtered user env + infra vars) and start sandbox.
        # _filter_user_env_for_sandbox provides defense-in-depth vs the API-layer
        # validators, dropping reserved keys/prefixes. qa_active=True includes
        # scope='qa' rows.
        from athanor.workflows.issue_to_pr.nodes import _filter_user_env_for_sandbox

        git_proxy_url = getattr(proxy_mgr, "git_proxy_url", "") if proxy_mgr else ""
        env = _filter_user_env_for_sandbox(
            state.get("user_env_vars") or [],
            qa_active=True,
        )
        if git_proxy_url:
            env["ATHANOR_GIT_PROXY_URL"] = git_proxy_url
        env.update(
            {
                "QA_JOB_ID": job_id,
                "QA_ATTEMPT_N": str(attempt_n),
                "QA_NETWORK_NAME": qa_net,
            }
        )

        container_id, sandbox_token = await sandbox_mgr.create(
            job_id,
            profile=profile,
            env=env,
        )

        # 4. (Mode 2) attach sandbox to qa-net so it can reach app stack by DNS.
        if is_dind:
            await docker.run_cmd(
                base + ["network", "connect", qa_net, container_id],
                timeout=10,
            )

        # 4b. Set up git credential helper so clone (and any later git
        # operations) flow through the credential proxy. Without this,
        # private repos fail to clone.
        if git_proxy_url:
            from athanor.sandbox.github.git_ops import build_sandbox_credential_config_cmd

            cred_cmd = build_sandbox_credential_config_cmd(git_proxy_url, sandbox_token)
            setup_cmd = [
                "bash",
                "-c",
                f"{cred_cmd} && "
                'git config --global user.email "qa-agent@athanor.local" && '
                'git config --global user.name "Athanor QA"',
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

        # 5. Clone the target repo. shlex.quote on branch/repo prevents shell
        # metacharacter injection from API input.
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
        # Only the canonical end-of-run artifacts are pre-minted here; anything
        # else (per-criterion screenshots, traces, per-service logs) the agent
        # mints on demand via the presign_refresh_url.
        artifact_prefix = f"{job_id}/{attempt_n}/"
        presign_paths = ["result.json", "logs/full.log"]
        presigned: dict[str, str] = {}
        for p in presign_paths:
            url = await minio_sandbox.presign_put(
                "qa-artifacts",
                artifact_prefix + p,
                expires_seconds=21600,
            )
            presigned[p] = url

        # 9. Drop /workspace/.qa/job.json into the sandbox.
        # Refresh URL points at presign-proxy in DinD (sandbox-restricted), NOT at
        # the orchestrator directly — sandboxes can't reach the host backend network.
        # Use base64 round-trip to avoid heredoc-injection from acceptance_criteria
        # or any other string field that might contain shell-active characters.
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
        encoded = base64.b64encode(json.dumps(job_json).encode()).decode()
        write_cmd = [
            "bash",
            "-c",
            f"echo '{encoded}' | base64 -d > /workspace/.qa/job.json",
        ]
        await docker.run_cmd(docker.build_exec_cmd(container_id, write_cmd), timeout=10)

        # 10. Update state with the new attempt.
        qa.setdefault("attempts", []).append(
            {
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
            }
        )
        qa["sandbox_token"] = sandbox_token
        qa["qa_yaml_raw"] = yaml_text
        qa["qa_yaml_parsed"] = qa_yaml.model_dump()
        state["qa"] = qa

        logger.info("init_qa_done", job_id=job_id, attempt_n=attempt_n, sandbox=container_id)
        return state

    except Exception as exc:
        logger.error("init_qa_failed", job_id=job_id, error=str(exc), exc_info=True)
        # Roll back in reverse order of allocation.
        if sandbox_token:
            try:
                token_registry.unregister(sandbox_token)
            except Exception:
                logger.warning("init_qa_rollback_token_unregister_failed", exc_info=True)
        if container_id:
            try:
                await sandbox_mgr.destroy(container_id)
            except Exception:
                logger.warning("init_qa_rollback_destroy_failed", exc_info=True)
        if qa_net_created:
            from athanor.workflows.qa.cleanup import cleanup_qa_resources

            try:
                await cleanup_qa_resources(docker, job_id=job_id)
            except Exception:
                logger.warning("init_qa_rollback_cleanup_failed", exc_info=True)
        raise
