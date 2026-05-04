"""Sub-task subgraph nodes — one per step in the per-app QA lifecycle.

Each sub-task runs as its own LangGraph subgraph against an isolated
sandbox container. Steps:

  acquire_sandbox → boot_app → seed (optional) → e2e (optional) →
  exploratory_qa → collect_artifacts → release_sandbox → emit_result

The integration is wired in subtask_graph.py (Task 17). This file defines:
  - SubTaskState (TypedDict)
  - initial_state_for_app(...) helper
  - _synth_v1_qa_yaml(resolved) helper — maps v2 ResolvedAppConfig onto the
    v1-shaped qa.yaml dict that run_boot_phase + the agent pipeline consume.
  - The 8 node functions.
"""

from __future__ import annotations

import json
import time
from typing import Any, TypedDict

import structlog
from langchain_core.runnables import RunnableConfig

from athanor.workflows.qa.boot import run_boot_phase
from athanor.workflows.qa.cleanup import cleanup_qa_resources
from athanor.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
from athanor.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)

logger = structlog.get_logger(__name__)


class SubTaskState(TypedDict, total=False):
    """LangGraph state for one sub-task subgraph instance.

    `total=False` because the subgraph mutates the dict step by step. By
    `emit_result_node`, every key has been populated exactly once.
    """

    # Inputs (set in initial_state_for_app)
    app_name: str
    parent_run_id: str
    resolved: ResolvedAppConfig
    repo: str
    branch_name: str | None
    user_env_vars: list[dict[str, str]] | dict[str, str] | None

    # Set by acquire_sandbox_node
    sandbox_id: str | None
    sandbox_token: str | None
    artifact_prefix: str | None
    head_sha: str | None

    # Set by boot_app_node on success
    boot_outcome: str | None        # "passed" | "timeout" | "startup_failed"
    boot_duration_ms: int | None

    # Set by exploratory_qa_node (forwarded from legacy qa_node return)
    agent_outputs: list[dict[str, Any]]

    # Set by any failing node
    status: SubTaskStatus | None
    failure_summary: str | None
    completed_at: float | None

    # Set by collect_artifacts_node on success path
    trace_url: str | None
    raw_result: dict[str, Any]

    # Set by emit_result_node
    started_at: float
    subtask_result: SubTaskResult


def initial_state_for_app(
    *,
    resolved: ResolvedAppConfig,
    parent_run_id: str,
    repo: str,
    branch_name: str | None = None,
    user_env_vars: Any = None,
) -> SubTaskState:
    return {
        "app_name": resolved.app_name,
        "parent_run_id": parent_run_id,
        "resolved": resolved,
        "repo": repo,
        "branch_name": branch_name,
        "user_env_vars": user_env_vars,
        "status": None,
        "started_at": time.monotonic(),
        "completed_at": None,
        "trace_url": None,
        "failure_summary": None,
        "raw_result": {},
        "sandbox_id": None,
        "sandbox_token": None,
        "artifact_prefix": None,
        "head_sha": None,
        "boot_outcome": None,
        "boot_duration_ms": None,
        "agent_outputs": [],
    }


def _synth_v1_qa_yaml(resolved: ResolvedAppConfig) -> dict[str, Any]:
    """Construct the v1-shaped qa.yaml dict that run_boot_phase + the
    agent pipeline consume. Maps v2 ResolvedAppConfig fields onto v1 keys.
    """
    qa_yaml: dict[str, Any] = {
        "version": 1,
        "mode": resolved.mode,
        "sandbox_profile": resolved.sandbox_profile,
        "frontend_url": resolved.frontend_url,
        "startup": {
            "command": resolved.boot_command,
            "ready_checks": [rc.model_dump() for rc in resolved.ready_checks],
            "boot_timeout_seconds": resolved.boot_timeout_seconds,
        },
        "acceptance_criteria_template": list(resolved.acceptance_criteria),
        "qa_required": "always",
    }
    if resolved.seed_command:
        qa_yaml["seed"] = {
            "command": resolved.seed_command,
            "timeout_seconds": 120,
        }
    if resolved.e2e is not None:
        qa_yaml["e2e"] = {
            "command": resolved.e2e.command,
            "timeout_seconds": resolved.e2e.timeout_seconds,
        }
    return qa_yaml


def _build_job_json_payload(
    *,
    sub_job_id: str,
    attempt_n: int,
    qa_yaml: dict[str, Any],
    resolved: ResolvedAppConfig,
    sandbox_token: str,
    presign_refresh_url: str | None = None,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    """Construct the job.json content the in-sandbox agent reads.

    Mirrors the field set the legacy init_qa_node assembles, but parameterized
    so sub-tasks can produce equivalent job.json content without sharing
    init_qa_node's local variables.
    """
    return {
        "schema_version": 1,
        "job_id": sub_job_id,
        "attempt_n": attempt_n,
        "mode": qa_yaml["mode"],
        "frontend_url": qa_yaml["frontend_url"],
        "qa_yaml": qa_yaml,
        "acceptance_criteria": list(resolved.acceptance_criteria),
        "changed_files": list(changed_files or []),
        "sandbox_token": sandbox_token,
        "presign_refresh_url": presign_refresh_url,
    }


async def acquire_sandbox_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Allocate a sandbox container for this sub-task; clone repo; write job.json.

    Per-sub-task: creates a fresh sandbox via SandboxManager.create(), invokes
    the shared prep helpers from _subtask_prep.py. Sub-task `job_id` is
    f"{parent_run_id}::{app_name}" so it never collides with the parent job.
    """
    from athanor.workflows.qa._subtask_prep import (
        prep_qa_sandbox_clone,
        prep_qa_sandbox_jobjson,
    )

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_mgr = configurable.get("sandbox_manager")
    profiles_repo = configurable.get("profiles_repo")
    minio_sandbox = configurable.get("qa_minio_sandbox")
    token_registry = configurable.get("qa_token_registry")
    proxy_mgr = configurable.get("proxy_manager")

    missing = [
        n for n, v in [
            ("docker", docker),
            ("sandbox_manager", sandbox_mgr),
            ("profiles_repo", profiles_repo),
            ("qa_minio_sandbox", minio_sandbox),
            ("qa_token_registry", token_registry),
        ] if v is None
    ]
    if missing:
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"acquire_sandbox missing config['configurable'] keys: {missing}",
            "completed_at": time.monotonic(),
        }

    resolved: ResolvedAppConfig = state["resolved"]
    parent_job_id = state["parent_run_id"]
    sub_job_id = f"{parent_job_id}::{resolved.app_name}"
    artifact_prefix = f"{parent_job_id}/{resolved.app_name}/"
    attempt_n = 1
    is_dind = resolved.mode == "dind"
    qa_net = f"qa-net-{sub_job_id}" if is_dind else ""

    profile = await profiles_repo.get(resolved.sandbox_profile)
    if profile is None:
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"sandbox profile {resolved.sandbox_profile!r} not found",
            "completed_at": time.monotonic(),
        }

    # Create network for DinD mode (mirrors init_qa_node behavior)
    base: list[str] = (
        docker._build_base_cmd() if hasattr(docker, "_build_base_cmd") else []
    )
    if is_dind:
        try:
            await docker.run_cmd(
                base + ["network", "create", qa_net],
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "subtask_qa_net_create_failed",
                qa_net=qa_net,
                error=str(exc),
            )
            # Continue: network may already exist; clone will surface real errors

    # Build env from user_env_vars + sandbox-internal essentials. Mirror the
    # env-construction shape used by init_qa_node — read that function for
    # the real key set if anything is unclear.
    env: dict[str, str] = {}
    user_env = state.get("user_env_vars") or {}
    if isinstance(user_env, list):
        for item in user_env:
            if isinstance(item, dict) and "name" in item and "value" in item:
                env[item["name"]] = item["value"]
    elif isinstance(user_env, dict):
        env.update({k: str(v) for k, v in user_env.items()})

    # Allocate sandbox
    try:
        container_id, sandbox_token = await sandbox_mgr.create(
            sub_job_id, profile=profile, env=env
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"sandbox create failed: {exc}",
            "completed_at": time.monotonic(),
        }

    branch = state.get("branch_name") or "main"

    # Phase 1: clone + head_sha
    try:
        cloned = await prep_qa_sandbox_clone(
            docker=docker,
            proxy_mgr=proxy_mgr,
            container_id=container_id,
            sandbox_token=sandbox_token,
            job_id=sub_job_id,
            repo=state["repo"],
            branch=branch,
            is_dind=is_dind,
            qa_net=qa_net,
            base=base,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            await sandbox_mgr.destroy(container_id)
        except Exception:
            pass
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"sandbox clone failed: {exc}",
            "completed_at": time.monotonic(),
        }

    # Phase 2: token register + presign + write job.json
    qa_yaml_for_app = _synth_v1_qa_yaml(resolved)
    job_json_payload = _build_job_json_payload(
        sub_job_id=sub_job_id,
        attempt_n=attempt_n,
        qa_yaml=qa_yaml_for_app,
        resolved=resolved,
        sandbox_token=sandbox_token,
    )

    try:
        await prep_qa_sandbox_jobjson(
            docker=docker,
            minio_sandbox=minio_sandbox,
            token_registry=token_registry,
            container_id=container_id,
            sandbox_token=sandbox_token,
            job_id=sub_job_id,
            attempt_n=attempt_n,
            artifact_prefix=artifact_prefix,
            job_json=job_json_payload,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            await token_registry.unregister(sandbox_token)
        except Exception:
            pass
        try:
            await sandbox_mgr.destroy(container_id)
        except Exception:
            pass
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"sandbox job.json prep failed: {exc}",
            "completed_at": time.monotonic(),
        }

    return {
        "sandbox_id": container_id,
        "sandbox_token": sandbox_token,
        "artifact_prefix": artifact_prefix,
        "head_sha": cloned.head_sha,
    }


async def boot_app_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Run startup.command + ready_checks via the existing run_boot_phase."""
    if state.get("status") is not None:
        return {}

    import httpx

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_id = state.get("sandbox_id")
    if docker is None or sandbox_id is None:
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": "boot_app missing docker or sandbox_id",
            "completed_at": time.monotonic(),
        }

    resolved: ResolvedAppConfig = state["resolved"]
    qa_yaml = _synth_v1_qa_yaml(resolved)

    async with httpx.AsyncClient(timeout=10.0) as http:
        try:
            result = await run_boot_phase(
                qa_yaml=qa_yaml,
                container_id=sandbox_id,
                docker=docker,
                http=http,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "status": SubTaskStatus.INFRA_FAILURE,
                "failure_summary": f"run_boot_phase crashed: {exc}",
                "completed_at": time.monotonic(),
            }

    if result.outcome == "passed":
        return {
            "boot_outcome": "passed",
            "boot_duration_ms": result.duration_ms,
        }

    if result.outcome == "timeout":
        return {
            "status": SubTaskStatus.BOOT_FAILURE,
            "boot_outcome": "timeout",
            "boot_duration_ms": result.duration_ms,
            "failure_summary": (
                f"boot_command did not become ready within "
                f"{resolved.boot_timeout_seconds}s"
            ),
            "completed_at": time.monotonic(),
        }

    # startup_failed
    return {
        "status": (
            SubTaskStatus.READY_CHECK_FAILED if result.failed_checks
            else SubTaskStatus.BOOT_FAILURE
        ),
        "boot_outcome": "startup_failed",
        "boot_duration_ms": result.duration_ms,
        "failure_summary": result.error_message or "startup command failed",
        "completed_at": time.monotonic(),
    }


async def seed_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Optional pre-test data seeding."""
    if state.get("status") is not None:
        return {}
    resolved: ResolvedAppConfig = state["resolved"]
    if resolved.seed_command is None:
        return {}

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_id = state.get("sandbox_id")
    if docker is None or sandbox_id is None:
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": "seed missing docker or sandbox_id",
            "completed_at": time.monotonic(),
        }

    try:
        out = await docker.run_cmd(
            docker.build_exec_cmd(sandbox_id, ["bash", "-c", resolved.seed_command]),
            timeout=300,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"seed_command failed: {exc}",
            "completed_at": time.monotonic(),
        }

    return {"raw_result": {**state.get("raw_result", {}), "seed_stdout": out}}


async def e2e_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Optional e2e test suite — runs *before* exploratory QA."""
    if state.get("status") is not None:
        return {}
    resolved: ResolvedAppConfig = state["resolved"]
    if resolved.e2e is None:
        return {}

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_id = state.get("sandbox_id")
    if docker is None or sandbox_id is None:
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": "e2e missing docker or sandbox_id",
            "completed_at": time.monotonic(),
        }

    try:
        out = await docker.run_cmd(
            docker.build_exec_cmd(sandbox_id, ["bash", "-c", resolved.e2e.command]),
            timeout=resolved.e2e.timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": SubTaskStatus.E2E_FAILURE,
            "failure_summary": f"e2e command failed: {exc}",
            "completed_at": time.monotonic(),
        }

    return {"raw_result": {**state.get("raw_result", {}), "e2e_stdout": out}}


async def exploratory_qa_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Invoke the existing qa_node against this sub-task's sandbox.

    qa_node reads state["qa"]["attempts"][-1] for sandbox_id + artifact_prefix
    and state["sandbox_container_id"]. We synthesize a minimal JobState-shaped
    dict that satisfies that contract and forward the result through. The
    verdict (acceptance pass/fail) is read from /workspace/.qa/result.json
    by collect_artifacts_node — qa_node only runs the agent.
    """
    if state.get("status") is not None:
        return {}

    from athanor.workflows.qa.nodes import qa_node as legacy_qa_node

    resolved: ResolvedAppConfig = state["resolved"]
    sub_job_id = f"{state['parent_run_id']}::{resolved.app_name}"
    sandbox_id = state.get("sandbox_id")
    if sandbox_id is None:
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": "exploratory_qa missing sandbox_id",
            "completed_at": time.monotonic(),
        }

    pseudo_state = {
        "job_id": sub_job_id,
        "sandbox_container_id": sandbox_id,
        "qa": {
            "attempts": [
                {
                    "attempt_n": 1,
                    "sandbox_id": sandbox_id,
                    "qa_net_name": f"qa-net-{sub_job_id}",
                    "artifact_prefix": state.get("artifact_prefix") or f"{state['parent_run_id']}/{resolved.app_name}/",
                    "started_at": "1970-01-01T00:00:00Z",
                    "last_phase": "boot",
                }
            ],
            "qa_yaml_parsed": _synth_v1_qa_yaml(resolved),
            "budget_seconds": 7200,
        },
        "repo": state["repo"],
        "branch_name": state.get("branch_name") or "main",
        "agent_outputs": [],
    }

    try:
        out = await legacy_qa_node(pseudo_state, config)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"qa agent crashed: {exc}",
            "completed_at": time.monotonic(),
        }

    return {"agent_outputs": out.get("agent_outputs", []) or []}


async def collect_artifacts_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Read /workspace/.qa/result.json from the sandbox; produce SubTaskStatus.

    Synthesizes the boot section from sub-task state (the agent never writes
    boot — that's the orchestrator's responsibility, mirroring the legacy
    report_node behavior).
    """
    if state.get("status") is not None:
        return {}

    from athanor.workflows.qa.result_schema import (
        QABootResult,
        QAReadyCheckResult,
        QAResult,
    )

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_id = state.get("sandbox_id")
    resolved: ResolvedAppConfig = state["resolved"]

    if docker is None or sandbox_id is None:
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": "collect_artifacts missing docker or sandbox_id",
            "completed_at": time.monotonic(),
        }

    try:
        result_json_str = await docker.run_cmd(
            docker.build_exec_cmd(sandbox_id, ["cat", "/workspace/.qa/result.json"]),
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"result.json not readable: {exc}",
            "completed_at": time.monotonic(),
        }

    try:
        agent_dump = json.loads(result_json_str)
    except json.JSONDecodeError as exc:
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"result.json not valid JSON: {exc}",
            "completed_at": time.monotonic(),
        }

    # Synthesize the boot section from sub-task state — agent never writes it.
    # QABootResult requires min_length=1 for ready_checks; only inject when
    # there is at least one ready check configured.
    if state.get("boot_outcome") == "passed" and resolved.ready_checks:
        agent_dump["boot"] = QABootResult(
            command=resolved.boot_command,
            duration_ms=int(state.get("boot_duration_ms") or 0),
            ready_checks=[
                QAReadyCheckResult(url=rc.http, status=rc.expect_status, duration_ms=0)
                for rc in resolved.ready_checks
            ],
        ).model_dump()

    try:
        validated = QAResult.model_validate(agent_dump)
    except Exception as exc:  # noqa: BLE001  — pydantic.ValidationError or similar
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"result.json failed schema validation: {exc}",
            "completed_at": time.monotonic(),
        }

    if validated.overall == "passed":
        sub_status = SubTaskStatus.PASSED
        failure_summary: str | None = None
    elif validated.overall == "failed":
        sub_status = SubTaskStatus.QA_FAILURE
        failed = [r for r in validated.acceptance_results if r.status == "failed"]
        failure_summary = (
            f"{failed[0].criterion}: {failed[0].notes or 'no notes'}"
            if failed
            else "agent reported overall=failed with no failed acceptance_results"
        )
    else:
        sub_status = SubTaskStatus.INFRA_FAILURE
        failure_summary = "agent reported overall=inconclusive"

    return {
        "status": sub_status,
        "failure_summary": failure_summary,
        "raw_result": validated.model_dump(),
        "completed_at": time.monotonic(),
    }


async def release_sandbox_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Always-runs cleanup. Failures logged but never override sub-task status."""
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_mgr = configurable.get("sandbox_manager")
    qa_token_registry = configurable.get("qa_token_registry")

    sandbox_id = state.get("sandbox_id")
    sandbox_token = state.get("sandbox_token")
    resolved: ResolvedAppConfig = state["resolved"]
    sub_job_id = f"{state['parent_run_id']}::{resolved.app_name}"

    if sandbox_mgr is not None and sandbox_id is not None:
        try:
            await sandbox_mgr.destroy(sandbox_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "subtask_sandbox_destroy_failed",
                sandbox_id=sandbox_id,
                app_name=state.get("app_name"),
                error=str(exc),
            )

    if qa_token_registry is not None and sandbox_token is not None:
        try:
            await qa_token_registry.unregister(sandbox_token)
        except Exception as exc:  # noqa: BLE001
            logger.warning("subtask_token_unregister_failed", error=str(exc))

    if docker is not None:
        try:
            await cleanup_qa_resources(docker, job_id=sub_job_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("subtask_cleanup_failed", error=str(exc))

    return {}


async def emit_result_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Final node: build the SubTaskResult the orchestrator collects."""
    started = state.get("started_at") or 0.0
    completed = state.get("completed_at") or time.monotonic()
    duration_ms = max(0, int((completed - started) * 1000))

    status = state.get("status")
    failure_summary = state.get("failure_summary")
    if status is None:
        status = SubTaskStatus.INFRA_FAILURE
        failure_summary = "sub-task graph terminated with no status emitted"

    result = SubTaskResult(
        app_name=state["app_name"],
        status=status,
        duration_ms=duration_ms,
        cache_hits=CacheHits(),  # Phase 1 always all-False
        trace_url=state.get("trace_url"),
        failure_summary=failure_summary,
        raw_result=state.get("raw_result", {}) or {},
    )
    return {"subtask_result": result}
