"""Sub-task subgraph nodes — one per step in the per-app QA lifecycle.

Each sub-task runs as its own LangGraph subgraph against an isolated
sandbox container. Steps:

  acquire_sandbox → boot_app → seed (optional) → auth_setup (optional) →
  e2e (optional) → exploratory_qa → collect_artifacts → release_sandbox →
  emit_result

The integration is wired in subtask_graph.py (Task 17). This file defines:
  - The 9 node functions.

State shape and helpers live in subtask_state.py.
"""

from __future__ import annotations

import copy
import json
import time
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig

from embry0.execution import events
from embry0.execution.runtime.egress import derive_deployed_egress_allowlist
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


def _parent_writer(config: RunnableConfig) -> Any:
    """The orchestrator's live stream writer, threaded via configurable.

    Sub-task graphs run under ainvoke() where get_stream_writer() is dead
    (custom events only flow under the parent's astream), so lifecycle
    events go through this explicit channel. No-op when absent (tests,
    legacy callers).
    """
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    return configurable.get("qa_parent_writer") or (lambda _event: None)


async def acquire_sandbox_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Allocate a sandbox container for this sub-task; clone repo; write job.json.

    Per-sub-task: creates a fresh sandbox via SandboxManager.create(), invokes
    the shared prep helpers from _subtask_prep.py. Sub-task `job_id` is
    f"{parent_run_id}::{app_name}" so it never collides with the parent job.
    """
    from embry0.safety.error_codes import ErrorCode
    from embry0.services.forge_resolver import resolve_forge
    from embry0.storage.repositories.repos import UnregisteredRepoError
    from embry0.workflows.qa._subtask_prep import (
        prep_qa_sandbox_clone,
        prep_qa_sandbox_jobjson,
    )
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig

    _app = str(state.get("app_name") or "")
    _parent_writer(config)(
        events.progress(
            message=f"[{_app}] acquiring sandbox",
            node="acquire_sandbox",
            app=_app,
        )
    )

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    runtime = configurable.get("runtime")
    sandbox_mgr = configurable.get("sandbox_manager")
    profiles_repo = configurable.get("profiles_repo")
    minio_sandbox = configurable.get("qa_minio_sandbox")
    token_registry = configurable.get("qa_token_registry")
    proxy_mgr = configurable.get("proxy_manager")

    missing = [
        n
        for n, v in [
            ("docker", docker),
            ("runtime", runtime),
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

    # The `missing` guard above returned for any None dep, so all six are
    # present here — narrow for the type checker (always true at runtime).
    assert docker is not None
    assert runtime is not None
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

    # #268: before the qa-net or any sandbox exists.
    from embry0.workflows.qa.runtime_gate import dind_mode_refusal

    refusal = dind_mode_refusal(runtime, mode=resolved.mode, dind_profile=bool(profile.get("dind_enabled", False)))
    if refusal is not None:
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": refusal,
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

    # Create network for DinD mode
    if is_dind:
        try:
            await runtime.create_network(qa_net, timeout=10)
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
        storage_state=resolved.auth is not None,
        browser_backend=resolved.browser_backend,
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

    # EMB-29: deployed-target sandboxes on the NAT-egress network get their
    # declared LAN targets (profile extra_hosts IPs / a private
    # frontend_url) allowlisted; every OTHER private-range destination is
    # dropped in DinD's DOCKER-USER chain. Public egress stays open (clone,
    # Auth0, CDN frontends, Anthropic API). Managed apps are unaffected.
    egress_allowlist: list[str] | None = None
    if resolved.target == "deployed" and "sandbox-internet" in (profile.get("extra_networks") or []):
        egress_allowlist = derive_deployed_egress_allowlist(
            frontend_url=resolved.frontend_url,
            extra_hosts=profile.get("extra_hosts") or {},
        )

    # Spec 3.6: the forge decides the clone origin and the git-proxy
    # credential host enrolled with the sandbox. Resolved before the sandbox
    # is created so an unregistered repo starts no container and reports its
    # own error code instead of a generic "sandbox clone failed".
    gitlab_url = getattr(configurable.get("embry0_config"), "gitlab_url", "") or ""
    try:
        forge = await resolve_forge(configurable.get("repos_repo"), state["repo"], job_id=sub_job_id)
    except UnregisteredRepoError as exc:
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"{ErrorCode.REPO_UNREGISTERED}: {exc}",
            "completed_at": time.monotonic(),
        }

    try:
        container_id, sandbox_token = await sandbox_mgr.create(
            sub_job_id,
            profile=profile,
            env=env,
            volumes=sandbox_volumes,
            tmpfs_mounts=sandbox_tmpfs,
            repo=state["repo"],
            egress_allowlist=egress_allowlist,
            forge=forge,
            llm_providers=tuple(configurable.get("llm_providers") or ()),
            parent_job_id=parent_job_id,
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
            runtime=runtime,
            cached_volume=shared_volume_name,
            prefs_repo=configurable.get("repo_preferences_repo"),
            forge=forge,
            gitlab_url=gitlab_url,
            forge_credential=(
                sandbox_mgr.last_forge_credential(container_id)
                if hasattr(sandbox_mgr, "last_forge_credential")
                else None
            ),
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

    _app = str(state.get("app_name") or "")
    _parent_writer(config)(
        events.progress(
            message=f"[{_app}] booting app (target={resolved.target})",
            node="boot_app",
            app=_app,
        )
    )

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

    # #238: boot failed — capture a diagnostic screenshot. Best-effort by
    # contract: any failure here degrades
    # to "boot failure without screenshot", never an infra crash.
    screenshot_key = await _capture_boot_failure_screenshot(
        state, configurable, docker=docker, sandbox_id=sandbox_id, resolved=resolved
    )

    if result.outcome == "timeout":
        return {
            "status": SubTaskStatus.BOOT_FAILURE,
            "boot_outcome": "timeout",
            "boot_duration_ms": result.duration_ms,
            "boot_result": result,
            "boot_diagnostic_screenshot_path": screenshot_key,
            "failure_summary": (
                f"deployed target did not answer ready_checks within {resolved.boot_timeout_seconds}s"
                if resolved.target == "deployed"
                else f"boot_command did not become ready within {resolved.boot_timeout_seconds}s"
            ),
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
        "boot_diagnostic_screenshot_path": screenshot_key,
        "failure_summary": result.error_message or "startup command failed",
        "completed_at": time.monotonic(),
    }


async def _capture_boot_failure_screenshot(
    state: SubTaskState,
    configurable: dict[str, Any],
    *,
    docker: Any,
    sandbox_id: str,
    resolved: Any,
) -> str | None:
    """Best-effort boot-failure screenshot for a sub-task (#238).

    Drive the sub-task's browser backend inside the sandbox, upload under
    the sub-task's artifact prefix at ``screenshots/boot-failure.png`` (the
    dashboard's latest-screenshot endpoint surfaces anything under
    ``screenshots/``). Returns the MinIO
    key, or None when anything fails — the boot failure must always report,
    with or without the picture.
    """
    from embry0.workflows.qa.screenshot import take_diagnostic_screenshot  # noqa: PLC0415

    minio = configurable.get("qa_minio")
    frontend_url = getattr(resolved, "frontend_url", "") or ""
    artifact_prefix = state.get("artifact_prefix")
    if minio is None or not frontend_url or not artifact_prefix:
        return None
    try:
        screenshot_bytes = await take_diagnostic_screenshot(
            docker=docker,
            container_id=sandbox_id,
            frontend_url=frontend_url,
            browser_backend=getattr(resolved, "browser_backend", None) or "playwright",
        )
        if not screenshot_bytes:
            return None
        key = f"{artifact_prefix}screenshots/boot-failure.png"
        await minio.put_object("qa-artifacts", key, screenshot_bytes, content_type="image/png")
        logger.info(
            "qa_subtask_boot_screenshot_uploaded",
            app_name=getattr(resolved, "app_name", ""),
            key=key,
        )
        return key
    except Exception as exc:  # noqa: BLE001 — diagnostics never block the boot report
        logger.warning("qa_subtask_boot_screenshot_failed", error=str(exc))
        return None


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
            timeout=resolved.seed_timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"seed_command failed: {exc}",
            "completed_at": time.monotonic(),
        }

    return {"raw_result": {**state.get("raw_result", {}), "seed_stdout": out}}


async def auth_setup_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Produce /workspace/.qa/storage-state.json for pre-authenticated QA (EMB-40).

    Skipped unless the resolved config declares an ``auth:`` block. Two source
    modes (schema-enforced exactly-one):

      - ``secret``: a qa-scoped env var (already injected into the sandbox by
        build_qa_sandbox_env) holds the storageState JSON; write it to the
        canonical path.
      - ``command``: run the repo-provided login script in-sandbox with
        QA_STORAGE_STATE_PATH in its env; the script writes the file itself.

    Either way the file is then read back and sanity-checked as Playwright
    storageState (JSON object with cookies/origins). Failures report as
    INCONCLUSIVE — "couldn't authenticate, so couldn't verify" — never as
    QA_FAILURE (the app didn't fail) and never as INFRA_FAILURE (the login
    script / stored session is repo-owned, not embry0's fault).
    """
    if state.get("status") is not None:
        return {}
    from embry0.workflows.qa._subtask_env import QA_STORAGE_STATE_PATH
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig

    resolved: ResolvedAppConfig = state["resolved"]
    if resolved.auth is None:
        return {}

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_id = state.get("sandbox_id")
    if docker is None or sandbox_id is None:
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": "auth_setup missing docker or sandbox_id",
            "completed_at": time.monotonic(),
        }

    src = resolved.auth.storage_state_from

    def _inconclusive(reason: str) -> dict[str, Any]:
        return {
            "status": SubTaskStatus.INCONCLUSIVE,
            "failure_summary": f"auth setup failed: {reason}",
            "completed_at": time.monotonic(),
        }

    if src.secret is not None:
        source = "secret"
        script = (
            f'if [ -z "${{{src.secret}:-}}" ]; then '
            f'echo "storageState secret {src.secret} is empty or not injected into the sandbox '
            f'(is it set with scope=qa in /environments?)" >&2; exit 1; fi; '
            f"printf '%s' \"${{{src.secret}}}\" > {QA_STORAGE_STATE_PATH}"
        )
        exec_cmd = docker.build_exec_cmd(sandbox_id, ["bash", "-c", script])
    else:
        source = "command"
        exec_cmd = docker.build_exec_cmd(
            sandbox_id,
            ["bash", "-c", src.command],
            env={"QA_STORAGE_STATE_PATH": QA_STORAGE_STATE_PATH},
        )

    try:
        await docker.run_cmd(exec_cmd, timeout=src.timeout_seconds)
    except Exception as exc:  # noqa: BLE001
        return _inconclusive(f"{source} step failed: {exc}")

    try:
        raw = await docker.run_cmd(
            docker.build_exec_cmd(sandbox_id, ["cat", QA_STORAGE_STATE_PATH]),
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return _inconclusive(f"storage-state file not readable after {source} step: {exc}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _inconclusive(f"storage-state file is not valid JSON: {exc}")

    if not isinstance(data, dict) or ("cookies" not in data and "origins" not in data):
        return _inconclusive("storage-state JSON has neither `cookies` nor `origins` — not a Playwright storageState")

    # #234: vibium's restore applies web storage only from its NATIVE
    # single-origin shape — a Playwright-shape file silently restores
    # cookies-only (verified against the pinned 26.8.21 binary). Convert
    # in place; the login-command/secret contract stays Playwright-shape,
    # and nothing else reads this file on a vibium run.
    if resolved.browser_backend == "vibium":
        import base64

        from embry0.workflows.qa.browser_backends import to_vibium_storage_state

        origins = data.get("origins") or []
        if len(origins) > 1:
            logger.warning(
                "qa_auth_vibium_multi_origin_storage",
                app_name=resolved.app_name,
                origins=[o.get("origin") for o in origins],
                detail="vibium restore is single-origin; keeping the frontend_url match, dropping the rest",
            )
        converted = to_vibium_storage_state(data, resolved.frontend_url)
        encoded = base64.b64encode(json.dumps(converted).encode()).decode()
        try:
            await docker.run_cmd(
                docker.build_exec_cmd(
                    sandbox_id,
                    ["bash", "-c", f"printf '%s' '{encoded}' | base64 -d > {QA_STORAGE_STATE_PATH}"],
                ),
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            return _inconclusive(f"vibium storage-state conversion write failed: {exc}")

    logger.info(
        "qa_auth_setup_complete",
        app_name=resolved.app_name,
        source=source,
        browser_backend=resolved.browser_backend,
        cookies=len(data.get("cookies") or []),
        origins=len(data.get("origins") or []),
    )
    return {
        "raw_result": {
            **state.get("raw_result", {}),
            "auth_setup": {
                "source": source,
                "cookies": len(data.get("cookies") or []),
                "origins": len(data.get("origins") or []),
            },
        }
    }


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


# #643 (EMB-34): runs of the QA agent added after an environmental failure.
_MAX_AGENT_TRANSIENT_RETRIES = 1


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

    pseudo_state: dict[str, Any] = {
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
            # EMB-37: deployed targets are read-only UI QA against an
            # already-running instance — nothing builds or boots, so 2h of
            # wall-clock is pure runaway budget (green runs finish in
            # ~10 min). Managed targets keep 2h for slow in-sandbox boots.
            "budget_seconds": 1800 if resolved.target == "deployed" else 7200,
        },
        "repo": state["repo"],
        "branch_name": state.get("branch_name") or "main",
        # run_agent_node reads the per-job model override off the state it is
        # handed — forward it or grok-QA jobs silently run the seed model.
        "agent_models_override": state.get("agent_models_override"),
        "agent_outputs": [],
        # #264: the parent job's per-job cap and overrun mode, so the QA
        # agent's turn is held to the job's cap rather than the default.
        **{
            k: v
            for k, v in ((config or {}).get("configurable", {}).get("qa_job_budget") or {}).items()
            if v is not None
        },
    }

    agent_outputs: list[dict[str, Any]] = []
    retries = 0
    while True:
        try:
            # qa_node mutates the state it is given: each run gets a copy.
            out = await legacy_qa_node(copy.deepcopy(pseudo_state), config)
        except Exception as exc:  # noqa: BLE001  — includes a hard budget stop
            return {
                "status": SubTaskStatus.INFRA_FAILURE,
                "failure_summary": f"qa agent crashed: {exc}",
                "completed_at": time.monotonic(),
            }
        agent_outputs.extend(out.get("agent_outputs", []) or [])
        attempts = (out.get("qa") or {}).get("attempts") or [{}]
        exit_reason = str(attempts[-1].get("exit_reason") or "")
        # #643 (EMB-34): an environmental failure (network, MCP, API 5xx)
        # gets exactly one more run; a second one falls through to
        # collect_artifacts like any agent failure. Never past the job's cap.
        if (
            not exit_reason.startswith("agent_transient")
            or retries >= _MAX_AGENT_TRANSIENT_RETRIES
            or out.get("budget_overrun_usd")
        ):
            break
        retries += 1
        logger.warning(
            "qa_agent_transient_retry",
            sub_job_id=sub_job_id,
            app_name=resolved.app_name,
            exit_reason=exit_reason[:200],
        )
        writer = ((config or {}).get("configurable") or {}).get("qa_parent_writer")
        if writer is not None:
            writer(events.qa_agent_transient_retry(app=resolved.app_name, reason=exit_reason[:200]))

    update: dict[str, Any] = {"agent_outputs": agent_outputs}
    if retries:
        update["agent_transient_retries"] = retries
    return update


def _synthesize_inconclusive_result(state: SubTaskState, reason: str) -> dict[str, Any]:
    """Verdict guarantee for stuck runs (EMB-37).

    The agent ran but left no usable result.json — turn cap, wall-clock
    timeout, idle kill, or a malformed report. Build a schema-valid partial
    QAResult with every criterion inconclusive so the run reports a QA
    verdict (INCONCLUSIVE) instead of INFRA_FAILURE with nothing to show.
    The last agent error (e.g. "Agent timed out after 1800s") is folded into
    the notes for diagnosability.
    """
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
    from embry0.workflows.qa.result_schema import QAAcceptanceResult, QAResult

    resolved: ResolvedAppConfig = state["resolved"]
    note = f"agent ended without reporting: {reason}"
    last_error = next(
        (
            o.get("error_message")
            for o in reversed(state.get("agent_outputs") or [])
            if isinstance(o, dict) and o.get("error_message")
        ),
        None,
    )
    if last_error:
        note = f"{note}; last agent error: {last_error}"

    synthesized = QAResult(
        job_id=f"{state.get('parent_run_id', 'unknown')}__{resolved.app_name}",
        attempt_n=1,
        phase_reached="exploratory",
        overall="inconclusive",
        acceptance_results=[
            QAAcceptanceResult(criterion=c, status="inconclusive", notes=note) for c in resolved.acceptance_criteria
        ],
        anomalies=[],
    )
    logger.warning(
        "qa_result_synthesized_for_stuck_run",
        app_name=resolved.app_name,
        reason=reason,
        last_agent_error=last_error,
    )
    return {
        "status": SubTaskStatus.INCONCLUSIVE,
        "failure_summary": note,
        "raw_result": {**state.get("raw_result", {}), **synthesized.model_dump()},
        "completed_at": time.monotonic(),
    }


def _agent_ran(state: SubTaskState) -> bool:
    return bool(state.get("agent_outputs"))


async def collect_artifacts_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Read /workspace/.qa/result.json from the sandbox; produce SubTaskStatus.

    Synthesizes the boot section from sub-task state (the agent never writes
    boot — that's the orchestrator's responsibility).

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
        # File-absence with the agent having run = a stuck run (turn cap /
        # timeout / idle kill) → synthesize a verdict. Any other cat failure
        # (e.g. docker daemon down) is genuinely infra.
        if "No such file" in str(exc) and _agent_ran(state):
            return _synthesize_inconclusive_result(state, "result.json was never written")
        return {
            "status": SubTaskStatus.INFRA_FAILURE,
            "failure_summary": f"result.json not readable: {exc}",
            "completed_at": time.monotonic(),
        }

    try:
        agent_dump = json.loads(result_json_str)
    except json.JSONDecodeError as exc:
        if _agent_ran(state):
            return _synthesize_inconclusive_result(state, f"result.json is not valid JSON ({exc})")
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
            command=resolved.boot_command or "<deployed-target: no boot command>",
            duration_ms=int(state.get("boot_duration_ms") or 0),
            ready_checks=ready_check_results,
        ).model_dump()

    try:
        # RAV-1400: scrub agent-volunteered unknown keys (observed: e2e.note)
        # before the strict validate — an extra field must not downgrade an
        # otherwise-valid report to inconclusive. Type errors still fail.
        from embry0.workflows.qa.result_schema import scrub_unknown_keys

        dropped_keys: list[str] = []
        agent_dump = scrub_unknown_keys(QAResult, agent_dump, dropped_keys)
        if dropped_keys:
            logger.warning(
                "qa_result_unknown_keys_dropped",
                app=state.get("app_name"),
                dropped=dropped_keys[:20],
            )
        validated = QAResult.model_validate(agent_dump)
    except Exception as exc:  # noqa: BLE001  — pydantic.ValidationError or similar
        if _agent_ran(state):
            return _synthesize_inconclusive_result(state, f"result.json failed schema validation ({exc})")
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
        # Merge over the accumulated raw_result rather than replacing it:
        # earlier nodes stash orchestrator-side markers there (seed_stdout,
        # e2e_stdout, auth_setup) and a plain replace silently discards
        # them from the persisted result (noticed diagnosing
        # job-ca502fac2ed4, where auth_setup evidence was lost).
        "raw_result": {**state.get("raw_result", {}), **validated.model_dump()},
        "completed_at": time.monotonic(),
    }


async def release_sandbox_node(state: SubTaskState, config: RunnableConfig) -> dict[str, Any]:
    """Always-runs cleanup. Failures logged but never override sub-task status."""
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    runtime = configurable.get("runtime")
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

    if runtime is not None:
        try:
            await cleanup_qa_resources(runtime, job_id=sub_job_id)
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
        failure_summary=failure_summary,
        raw_result=state.get("raw_result", {}) or {},
        # Phase 5A: thread boot_phase through to the orchestrator so it gets
        # persisted via QAAppResultsRepository.upsert_with_boot_phase. None
        # when boot passed (legacy behavior preserved) or never ran.
        boot_phase=state.get("boot_phase"),
    )

    # spec 3.7: the final per-app status rides the job stream too, so it is
    # persisted and replayed; the QA bus below keeps feeding the SSE route.
    boot_phase = state.get("boot_phase")
    _parent_writer(config)(
        events.qa_subtask_status(
            app=state["app_name"],
            status=str(status),
            duration_ms=duration_ms,
            failure_summary=failure_summary,
            boot_phase=boot_phase if isinstance(boot_phase, dict) else None,
        )
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
