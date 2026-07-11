"""Sub-task subgraph nodes — one per step in the per-app QA lifecycle.

Each sub-task runs as its own LangGraph subgraph against an isolated
sandbox container. Steps:

  acquire_sandbox → boot_app → seed (optional) → e2e (optional) →
  exploratory_qa → collect_artifacts → release_sandbox → emit_result

The integration is wired in subtask_graph.py (Task 17). This file defines:
  - The 8 node functions.

State shape and helpers live in subtask_state.py.
"""

from __future__ import annotations

import json
import time
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig

from embry0.workflows.qa._subtask_boot_phase import (
    _boot_phase_from_state,
    _read_boot_log_tail,
)
from embry0.workflows.qa._subtask_env import build_qa_sandbox_env
from embry0.workflows.qa.boot import run_boot_phase
from embry0.workflows.qa.cleanup import cleanup_qa_resources
from embry0.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)
from embry0.workflows.qa.subtask_state import (
    SubTaskState,
    _build_job_json_payload,
    _synth_v1_qa_yaml,
)

logger = structlog.get_logger(__name__)


async def acquire_sandbox_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Allocate a sandbox container for this sub-task; clone repo; write job.json.

    Per-sub-task: creates a fresh sandbox via SandboxManager.create(), invokes
    the shared prep helpers from _subtask_prep.py. Sub-task `job_id` is
    f"{parent_run_id}::{app_name}" so it never collides with the parent job.
    """
    from embry0.workflows.qa._subtask_prep import (
        prep_qa_sandbox_clone,
        prep_qa_sandbox_jobjson,
    )
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_mgr = configurable.get("sandbox_manager")
    profiles_repo = configurable.get("profiles_repo")
    minio_sandbox = configurable.get("qa_minio_sandbox")
    token_registry = configurable.get("qa_token_registry")
    proxy_mgr = configurable.get("proxy_manager")

    missing = [
        n
        for n, v in [
            ("docker", docker),
            ("sandbox_manager", sandbox_mgr),
            ("profiles_repo", profiles_repo),
            ("qa_minio_sandbox", minio_sandbox),
            ("qa_token_registry", token_registry),
        ]
        if v is None
    ]
    if missing:
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"acquire_sandbox missing config['configurable'] keys: {missing}",
            "completed_at": time.monotonic(),
        }

    # The `missing` guard above returned for any None dep, so all five are
    # present here — narrow for the type checker (always true at runtime).
    assert docker is not None
    assert sandbox_mgr is not None
    assert profiles_repo is not None
    assert minio_sandbox is not None
    assert token_registry is not None

    resolved: ResolvedAppConfig = state["resolved"]
    parent_job_id = state["parent_run_id"]
    sub_job_id = f"{parent_job_id}__{resolved.app_name}"
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

    prebaked_tag = state.get("prebaked_image_tag")
    if prebaked_tag:
        profile = {**profile, "base_image": prebaked_tag}

    # Phase-2 C3: shared-volume cache layer.
    # If a pre-warmed volume name is stashed in state, we'll mount it at
    # /workspace when creating the sandbox (so npm ci is already done) and
    # skip git clone inside prep_qa_sandbox_clone.
    shared_volume_name: str | None = state.get("shared_volume_name")

    # Create network for DinD mode (mirrors init_qa_node behavior)
    base: list[str] = docker._build_base_cmd() if hasattr(docker, "_build_base_cmd") else []
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

    # Build env from user_env_vars + sandbox-internal essentials.
    git_proxy_url = ""
    if proxy_mgr is not None:
        git_proxy_url = getattr(proxy_mgr, "git_proxy_url", "") or ""

    # Phase-2 E1: honor cache.turbo_remote.enabled.  When True, build a
    # TurboRemoteConfig from os.environ (TURBO_API/TURBO_TEAM/TURBO_TOKEN).
    # Actual secret plumbing via embry0 secret store is deferred to Phase 3;
    # for Phase 2 the boolean flag is the gate and env-var sourcing is the shim.
    turbo_remote_config = None
    if state.get("turbo_remote_enabled"):
        import os  # noqa: PLC0415

        from embry0.cache.turbo_remote import TurboRemoteConfig  # noqa: PLC0415

        api_url = os.environ.get("TURBO_API", "")
        team = os.environ.get("TURBO_TEAM", "")
        token = os.environ.get("TURBO_TOKEN", "")
        if api_url and token:
            turbo_remote_config = TurboRemoteConfig(api_url=api_url, team=team, token=token)

    env = build_qa_sandbox_env(
        user_env_vars=state.get("user_env_vars"),
        git_proxy_url=git_proxy_url,
        qa_job_id=sub_job_id,
        attempt_n=1,
        qa_network_name=qa_net,
        turbo_remote_config=turbo_remote_config,
    )

    # Allocate sandbox.  When the shared-volume cache layer is active, mount
    # the pre-warmed volume at /workspace so npm ci is already done.
    sandbox_volumes = [(shared_volume_name, "/workspace")] if shared_volume_name else None
    # When sub-tasks fan out across the SAME shared volume, the per-sub-task
    # `.qa/` directory (job.json, result.json, screenshots, playwright-tests)
    # would otherwise be a single physical directory shared by every container —
    # the last writer wins on `result.json` and every sub-task reads back the
    # same poisoned content (observed 2026-05-06 on job-f42d46553080: 11 of 14
    # sub-tasks reported byte-identical failure summaries copied from one
    # neighbouring sub-task's result.json). Overlay an empty tmpfs at
    # `/workspace/.qa` so each sub-task has its own ephemeral state without
    # losing the shared read-cache for `node_modules` and the cloned repo.
    sandbox_tmpfs = ["/workspace/.qa"] if shared_volume_name else None
    try:
        container_id, sandbox_token = await sandbox_mgr.create(
            sub_job_id,
            profile=profile,
            env=env,
            volumes=sandbox_volumes,
            tmpfs_mounts=sandbox_tmpfs,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"sandbox create failed: {exc}",
            "completed_at": time.monotonic(),
        }

    branch = state.get("branch_name") or "main"

    # Phase 1: clone + head_sha.
    # When shared_volume_name is set, the volume is already mounted at
    # /workspace — pass cached_volume so prep skips the git clone.
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
            cached_volume=shared_volume_name,
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
    # presign_refresh_url lets the QA agent mint additional presigned PUT
    # URLs at runtime for evidence files (screenshots, network HARs,
    # console logs) beyond the canonical result.json + logs/full.log
    # pair pre-minted in prep_qa_sandbox_jobjson. Without it, the agent
    # references screenshots in result.json's `evidence` array but the
    # files never make it to MinIO — observed on job-35ee75bd3e3e where
    # every sub-task reported real browser console errors but uploaded
    # zero screenshot/network/console artifacts. The presign-proxy is
    # already running on the sandbox-restricted network at port 9104.
    job_json_payload = _build_job_json_payload(
        sub_job_id=sub_job_id,
        attempt_n=attempt_n,
        qa_yaml=qa_yaml_for_app,
        resolved=resolved,
        sandbox_token=sandbox_token,
        presign_refresh_url="http://presign-proxy:9104/api/v1/internal/qa/presign",
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
            # SandboxTokenRegistry.unregister is synchronous — awaiting it
            # raises "object NoneType can't be used in 'await' expression".
            token_registry.unregister(sandbox_token)
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
        "cache_hits_partial": {
            "prebaked_image": bool(prebaked_tag),
            "shared_volume": bool(shared_volume_name),
        },
    }


async def boot_app_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Run startup.command + ready_checks via the existing run_boot_phase."""
    if state.get("status") is not None:
        return {}

    import httpx  # noqa: PLC0415

    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig  # noqa: PLC0415

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

    from embry0.cache.turbo_remote import parse_turbo_stdout_for_hits  # noqa: PLC0415

    boot_stdout = getattr(result, "stdout", "") or ""
    turbo_hits, turbo_misses = parse_turbo_stdout_for_hits(boot_stdout)

    if result.outcome == "passed":
        return {
            "boot_outcome": "passed",
            "boot_duration_ms": result.duration_ms,
            "boot_result": result,
            "cache_hits_partial": {
                **(state.get("cache_hits_partial") or {}),
                "turbo_remote_hits": turbo_hits,
                "turbo_remote_misses": turbo_misses,
            },
        }

    if result.outcome == "timeout":
        return {
            "status": SubTaskStatus.BOOT_FAILURE,
            "boot_outcome": "timeout",
            "boot_duration_ms": result.duration_ms,
            "boot_result": result,
            "failure_summary": (f"boot_command did not become ready within {resolved.boot_timeout_seconds}s"),
            "completed_at": time.monotonic(),
            "cache_hits_partial": {
                **(state.get("cache_hits_partial") or {}),
                "turbo_remote_hits": turbo_hits,
                "turbo_remote_misses": turbo_misses,
            },
        }

    # startup_failed
    return {
        "status": (SubTaskStatus.READY_CHECK_FAILED if result.failed_checks else SubTaskStatus.BOOT_FAILURE),
        "boot_outcome": "startup_failed",
        "boot_duration_ms": result.duration_ms,
        "boot_result": result,
        "failure_summary": result.error_message or "startup command failed",
        "completed_at": time.monotonic(),
    }


async def seed_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Optional pre-test data seeding."""
    if state.get("status") is not None:
        return {}
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig

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
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig

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

    from embry0.workflows.qa.nodes import qa_node as legacy_qa_node
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig

    resolved: ResolvedAppConfig = state["resolved"]
    sub_job_id = f"{state['parent_run_id']}__{resolved.app_name}"
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

    Phase 5A: when boot did NOT pass we still run, just to populate
    ``boot_phase`` for the dashboard drill-down — but we DO NOT touch
    status/failure_summary (those are owned by boot_app_node on the
    failure path). The legacy short-circuit (``return {}``) is preserved
    on the boot-passed-but-status-set path (e.g. e2e/seed failures).
    """
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
    from embry0.workflows.qa.result_schema import (
        QABootResult,
        QAReadyCheckResult,
        QAResult,
    )

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_id = state.get("sandbox_id")
    resolved: ResolvedAppConfig = state["resolved"]

    # Phase 5A: when boot did not pass, this node's only remaining job is to
    # capture boot_phase for the dashboard drill-down. Skip the result.json
    # read (the agent never ran) and don't touch status/failure_summary.
    if state.get("status") is not None:
        if state.get("boot_outcome") not in (None, "passed"):
            stdout_tail = ""
            if docker is not None and sandbox_id is not None:
                stdout_tail = await _read_boot_log_tail(docker=docker, sandbox_id=sandbox_id)
            boot_phase = _boot_phase_from_state(state, stdout_tail=stdout_tail)
            return {"boot_phase": boot_phase} if boot_phase is not None else {}
        return {}

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
    if state.get("boot_outcome") == "passed":
        if resolved.ready_checks:
            # ``expect_status`` may be int or list[int] (the latter introduced
            # 2026-05-06 for auth-gated apps). On a passed boot we record the
            # *first* acceptable code as a representative success status —
            # boot.py only logs the actual code on failure, and the schema
            # field here is a single int.
            ready_check_results = [
                QAReadyCheckResult(url=rc.http, status=rc.status_codes()[0], duration_ms=0)
                for rc in resolved.ready_checks
            ]
        else:
            # No ready_checks configured — inject a sentinel so the schema accepts.
            ready_check_results = [
                QAReadyCheckResult(
                    url="<no-ready-checks-configured>",
                    status=200,
                    duration_ms=0,
                )
            ]
        agent_dump["boot"] = QABootResult(
            command=resolved.boot_command,
            duration_ms=int(state.get("boot_duration_ms") or 0),
            ready_checks=ready_check_results,
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
        sub_status = SubTaskStatus.INCONCLUSIVE
        failure_summary = "agent reported overall=inconclusive"

    return {
        "status": sub_status,
        "failure_summary": failure_summary,
        "raw_result": validated.model_dump(),
        "completed_at": time.monotonic(),
    }


async def release_sandbox_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Always-runs cleanup. Failures logged but never override sub-task status."""
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_mgr = configurable.get("sandbox_manager")
    qa_token_registry = configurable.get("qa_token_registry")

    sandbox_id = state.get("sandbox_id")
    sandbox_token = state.get("sandbox_token")
    resolved: ResolvedAppConfig = state["resolved"]
    sub_job_id = f"{state['parent_run_id']}__{resolved.app_name}"

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
            # SandboxTokenRegistry.unregister is synchronous — awaiting it
            # raises "object NoneType can't be used in 'await' expression",
            # silently leaking the token entry (caught + warned below).
            qa_token_registry.unregister(sandbox_token)
        except Exception as exc:  # noqa: BLE001
            logger.warning("subtask_token_unregister_failed", error=str(exc))

    if docker is not None:
        try:
            await cleanup_qa_resources(docker, job_id=sub_job_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("subtask_cleanup_failed", error=str(exc))

    return {}


async def emit_result_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Final node: build the SubTaskResult the orchestrator collects.

    Phase 5C: also publishes a ``subtask_status`` event to the QA event bus
    (when one is wired into ``config['configurable']['qa_event_bus']``) so
    the SSE route at ``/api/v1/qa/runs/{run_id}/events`` can stream
    per-app state changes to the dashboard. The publish is best-effort —
    bus errors are logged and swallowed so the orchestrator never
    fails because of a flaky subscriber.
    """
    started = state.get("started_at") or 0.0
    completed = state.get("completed_at") or time.monotonic()
    duration_ms = max(0, int((completed - started) * 1000))

    status = state.get("status")
    failure_summary = state.get("failure_summary")
    if status is None:
        status = SubTaskStatus.INFRA_FAILURE
        failure_summary = "sub-task graph terminated with no status emitted"

    partial = state.get("cache_hits_partial") or {}
    cache_hits = CacheHits(
        prebaked_image=bool(partial.get("prebaked_image", False)),
        shared_volume=bool(partial.get("shared_volume", False)),
        turbo_remote_hits=list(partial.get("turbo_remote_hits", [])),
        turbo_remote_misses=list(partial.get("turbo_remote_misses", [])),
    )
    result = SubTaskResult(
        app_name=state["app_name"],
        status=status,
        duration_ms=duration_ms,
        cache_hits=cache_hits,
        trace_url=state.get("trace_url"),
        failure_summary=failure_summary,
        raw_result=state.get("raw_result", {}) or {},
        # Phase 5A: thread boot_phase through to the orchestrator so it gets
        # persisted via QAAppResultsRepository.upsert_with_boot_phase. None
        # when boot passed (legacy behavior preserved) or never ran.
        boot_phase=state.get("boot_phase"),
    )

    # Phase 5C: publish to the QA event bus for SSE liveness. Absent when
    # tests / legacy callers don't wire one in — that's intentional.
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    bus = configurable.get("qa_event_bus")
    if bus is not None:
        try:
            await bus.publish(
                state["parent_run_id"],
                {
                    "type": "subtask_status",
                    "run_id": state["parent_run_id"],
                    "app": state["app_name"],
                    "status": str(status),
                    "duration_ms": duration_ms,
                    "failure_summary": failure_summary,
                    "boot_phase": state.get("boot_phase"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qa_event_bus_publish_subtask_status_failed",
                parent_run_id=state["parent_run_id"],
                app=state["app_name"],
                error=str(exc),
            )

    return {"subtask_result": result}
