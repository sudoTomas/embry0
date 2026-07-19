"""QA orchestrator — multi-app fan-out node.

Replaces the existing single-stage QA node in the pipeline (Task 27 wires it
into graph.py). Responsibilities (spec §6.3):
  1. Resolve affected apps via workspace_provider.
  2. Validate apps_to_qa against qa.yaml apps:.
  3. Fan out via LangGraph Send (Task 21).
  4. Throttle via parallelism semaphore (Task 21).
  5. Reduce results into a single QA record (Task 22).
  6. Persist + report.

This task implements (1) + (2). Subsequent tasks layer on (3)–(6).

Helper functions and data types live in orchestrator_helpers.py.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import structlog
from langchain_core.runnables import RunnableConfig

from embry0.workflows.qa._orchestrator_workspace import (
    compute_changed_files_via_diff,
    stage_workspace_for_provider,
)
from embry0.workflows.qa._subtask_env import build_qa_sandbox_env
from embry0.workflows.qa.orchestrator_helpers import (
    OrchestratorOutcome,
    _outcome_to_dict,
    fan_out_subtasks,
    resolve_apps_to_qa,
    validate_against_qa_config,
)
from embry0.workspace_providers.npm_workspaces_turbo._filter_parse import (
    parse_affected_filter,
)
from embry0.workspace_providers.registry import load_provider

logger = structlog.get_logger(__name__)


def _staging_dir_for(job_id: str) -> Path:
    """Local /tmp dir into which the orchestrator stages workspace package.json
    files for the workspace_provider to read. Replaceable in tests via
    monkeypatch."""
    return Path(f"/tmp/embry0-workspace-{job_id}")


async def init_orchestrator_node(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """Bootstrap-sandbox load of `.embry0/qa.yaml` (v2).

    Allocates a short-lived sandbox using the `slim` profile, clones the
    target repo, reads `.embry0/qa.yaml`, destroys the sandbox. Writes
    the raw text and parsed dict into state["qa"] so qa_orchestrator_node
    can run without doing its own clone.

    Phase 2's prebaked image will eliminate this overhead by having qa.yaml
    available in the image. Phase 1 pays ~10s per QA run.
    """
    from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone
    from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_mgr = configurable.get("sandbox_manager")
    profiles_repo = configurable.get("profiles_repo")
    proxy_mgr = configurable.get("proxy_manager")

    job_id = state.get("job_id")
    repo = state.get("repo")
    branch = state.get("branch_name") or "main"

    if not all([docker, sandbox_mgr, profiles_repo, job_id, repo]):
        outcome = OrchestratorOutcome(
            overall_status="infra_error",
            apps_to_qa=[],
            failure_summary="init_orchestrator missing required deps or state",
        )
        qa = dict(state.get("qa") or {})
        qa["outcome"] = _outcome_to_dict(outcome)
        return {"qa": qa}

    # The `all([...])` guard above returned for any falsy dep/state value,
    # so these are present and well-typed here (always true at runtime).
    assert docker is not None
    assert sandbox_mgr is not None
    assert profiles_repo is not None
    assert isinstance(job_id, str)
    assert isinstance(repo, str)

    profile = await profiles_repo.get("slim")
    if profile is None:
        outcome = OrchestratorOutcome(
            overall_status="infra_error",
            apps_to_qa=[],
            failure_summary="bootstrap profile 'slim' not found",
        )
        qa = dict(state.get("qa") or {})
        qa["outcome"] = _outcome_to_dict(outcome)
        return {"qa": qa}

    bootstrap_job_id = f"{job_id}__bootstrap"
    base: list[str] = docker._build_base_cmd() if hasattr(docker, "_build_base_cmd") else []

    git_proxy_url = ""
    if proxy_mgr is not None:
        git_proxy_url = getattr(proxy_mgr, "git_proxy_url", "") or ""
    env = build_qa_sandbox_env(
        user_env_vars=None,
        git_proxy_url=git_proxy_url,
        qa_job_id=bootstrap_job_id,
        attempt_n=1,
        qa_network_name="",
    )

    container_id: str | None = None
    lockfile_text: str = ""
    changed_files: list[str] = []
    staging_path_str: str | None = None
    try:
        container_id, sandbox_token = await sandbox_mgr.create(bootstrap_job_id, profile=profile, env=env, repo=repo)
        cloned = await prep_qa_sandbox_clone(
            docker=docker,
            proxy_mgr=proxy_mgr,
            container_id=container_id,
            sandbox_token=sandbox_token,
            job_id=bootstrap_job_id,
            repo=repo,
            branch=branch,
            is_dind=False,
            qa_net="",
            base=base,
        )
        yaml_text = await docker.run_cmd(
            docker.build_exec_cmd(container_id, ["cat", "/workspace/.embry0/qa.yaml"]),
            timeout=10,
        )
        # Phase-2 C3: read lockfile while bootstrap container is still alive.
        # We compute the sha here so the warmer can use it without a second
        # clone.  Failures are non-fatal — lockfile_text stays "" and the
        # warmer will just re-warm (it won't skip on matching sha).
        try:
            lockfile_text = await docker.run_cmd(
                docker.build_exec_cmd(
                    container_id,
                    [
                        "bash",
                        "-c",
                        "cat /workspace/package-lock.json 2>/dev/null"
                        " || cat /workspace/pnpm-lock.yaml 2>/dev/null"
                        " || cat /workspace/yarn.lock 2>/dev/null"
                        " || echo ''",
                    ],
                ),
                timeout=10,
            )
        except Exception as lock_exc:  # noqa: BLE001
            logger.warning("bootstrap_lockfile_read_failed", error=str(lock_exc))

        # Phase 3: compute changed_files inside the bootstrap sandbox.
        # The bootstrap sandbox already has /workspace cloned; using the
        # local git tree is faster + free vs the GitHub PR API.
        try:
            cfg_for_filter = parse_qa_yaml_v2(yaml_text)
            # workspace_provider may be None for all-deployed configs (EMB-27).
            affected_filter_text = (
                cfg_for_filter.workspace_provider.config.get("affected_filter")
                if cfg_for_filter.workspace_provider is not None
                and isinstance(cfg_for_filter.workspace_provider.config, dict)
                else None
            )
        except Exception:
            affected_filter_text = None

        default_base_branch = state.get("base_branch") or "main"
        filter_parsed = parse_affected_filter(
            affected_filter_text,
            default_base_branch=default_base_branch,
        )
        if filter_parsed.warning:
            logger.warning(
                "qa_orchestrator_affected_filter_warning",
                job_id=job_id,
                warning=filter_parsed.warning,
            )

        try:
            changed_files = await compute_changed_files_via_diff(
                docker=docker,
                container_id=container_id,
                base_branch=filter_parsed.base_branch,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qa_orchestrator_changed_files_compute_failed",
                job_id=job_id,
                error=str(exc),
            )
            changed_files = []

        staging_dir = _staging_dir_for(job_id)
        try:
            await stage_workspace_for_provider(
                docker=docker,
                container_id=container_id,
                job_id=job_id,
                target_root=staging_dir,
            )
            staging_path_str = str(staging_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qa_orchestrator_workspace_stage_failed",
                job_id=job_id,
                error=str(exc),
            )
            staging_path_str = None

    except Exception as exc:  # noqa: BLE001
        outcome = OrchestratorOutcome(
            overall_status="infra_error",
            apps_to_qa=[],
            failure_summary=f"bootstrap qa.yaml load failed: {exc}",
        )
        qa = dict(state.get("qa") or {})
        qa["outcome"] = _outcome_to_dict(outcome)
        if container_id is not None:
            try:
                await sandbox_mgr.destroy(container_id)
            except Exception:
                pass
        return {"qa": qa}
    finally:
        if container_id is not None:
            try:
                await sandbox_mgr.destroy(container_id)
            except Exception:
                pass

    try:
        cfg = parse_qa_yaml_v2(yaml_text)
    except Exception as exc:  # noqa: BLE001
        outcome = OrchestratorOutcome(
            overall_status="infra_error",
            apps_to_qa=[],
            failure_summary=f"qa.yaml v2 parse failed: {exc}",
        )
        qa = dict(state.get("qa") or {})
        qa["outcome"] = _outcome_to_dict(outcome)
        return {"qa": qa}

    qa = dict(state.get("qa") or {})
    qa["qa_yaml_v2_raw"] = yaml_text
    qa["qa_yaml_v2_parsed"] = cfg.model_dump()
    qa["head_sha"] = cloned.head_sha

    # Phase-2 C3: shared-volume warming.
    # Only attempted when the cache layer is enabled AND the scope is per-job
    # or per-repo (per-org is not implemented in Phase 2).
    sv_cfg = cfg.cache.shared_volume
    if sv_cfg.enabled and sv_cfg.scope in ("per-job", "per-repo"):
        volume_mgr = configurable.get("qa_shared_volume_manager")
        volume_state_repo = configurable.get("qa_volume_state_repo")
        if volume_mgr is not None and volume_state_repo is not None:
            try:
                from embry0.cache.volume_manager import VolumeScope
                from embry0.cache.volume_warmer import warm_shared_volume

                scope = sv_cfg.scope
                scope_key = job_id if scope == "per-job" else (repo or "")
                vol_scope = VolumeScope(scope)
                volume_name = await volume_mgr.ensure(scope=vol_scope, scope_key=scope_key)

                lockfile_sha = hashlib.sha256(lockfile_text.encode()).hexdigest() if lockfile_text else ""

                if lockfile_sha:
                    warm_result = await warm_shared_volume(
                        repo=repo,
                        branch=branch,
                        volume_name=volume_name,
                        current_lockfile_sha=lockfile_sha,
                        scope=scope,
                        scope_key=scope_key,
                        docker=docker,
                        sandbox_mgr=sandbox_mgr,
                        profiles_repo=profiles_repo,
                        proxy_mgr=proxy_mgr,
                        state_repo=volume_state_repo,
                    )
                    logger.info(
                        "qa_shared_volume_warm_result",
                        result=warm_result,
                        volume_name=volume_name,
                        scope=scope,
                        scope_key=scope_key,
                    )
                    qa["shared_volume_name"] = volume_name
                else:
                    logger.warning(
                        "qa_shared_volume_skipped_no_lockfile",
                        job_id=job_id,
                        msg="No lockfile found in bootstrap sandbox — skipping volume warm",
                    )
            except Exception as vol_exc:  # noqa: BLE001
                # Volume warming is non-fatal: sub-tasks fall back to
                # individual npm ci without the shared volume.
                logger.warning(
                    "qa_shared_volume_warm_failed",
                    job_id=job_id,
                    error=str(vol_exc),
                )

    out: dict[str, Any] = {"qa": qa}
    # Phase 3 additions
    qa["changed_files"] = list(changed_files)
    if staging_path_str:
        out["repo_root"] = staging_path_str
    return out


async def qa_orchestrator_node(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """LangGraph node: fan-out QA across all apps_to_qa.

    Reads from state['qa']: qa_yaml_v2_raw (set by init_orchestrator_node),
    changed_files. Reads from state: job_id, repo, branch_name, user_env_vars.
    Reads from config['configurable']: workspace provider deps + docker/sandbox/etc.

    Writes to state['qa']: outcome (dict), per_app_results (list[dict]),
    apps_to_qa, validation_errors, validation_warnings, final_status.

    Phase 5C: after the inner implementation returns, publishes a single
    ``done`` event to the QA event bus (when wired). The dashboard's SSE
    route uses that event to close the stream and let polling take over.
    """
    out = await _qa_orchestrator_node_impl(state, config)

    # Phase 5C: publish run-level "done" so SSE clients can close cleanly.
    # No-op when no bus is wired (tests / legacy callers).
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    bus = configurable.get("qa_event_bus")
    if bus is not None:
        run_id = state.get("job_id")
        # Pull the final status from whichever path the impl returned through:
        # success path writes outcome.overall_status, every infra-error path
        # also fills it in (see early-return blocks above).
        out_qa = out.get("qa") if isinstance(out, dict) else None
        outcome = out_qa.get("outcome") if isinstance(out_qa, dict) else None
        overall_status = outcome.get("overall_status") if isinstance(outcome, dict) else None
        if run_id and overall_status:
            try:
                await bus.publish(
                    run_id,
                    {
                        "type": "done",
                        "run_id": run_id,
                        "overall_status": overall_status,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "qa_event_bus_publish_done_failed",
                    parent_run_id=run_id,
                    error=str(exc),
                )

    return out


async def _qa_orchestrator_node_impl(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """Pure run-orchestration logic. ``qa_orchestrator_node`` wraps this to
    layer the SSE ``done`` publish on top — separating the two keeps every
    existing early-return short-circuit working without per-branch publish
    plumbing.
    """
    from dataclasses import replace
    from pathlib import Path

    from embry0.workflows.qa.qa_yaml_resolve import resolve_app_config
    from embry0.workflows.qa.qa_yaml_v2 import QAYamlConfigV2, parse_qa_yaml_v2
    from embry0.workflows.qa.subtask_result_schema import overall_status as compute_overall

    qa_state = dict(state.get("qa") or {})

    # If init_orchestrator_node already failed (no raw yaml stashed OR
    # outcome already set to infra_error), short-circuit through.
    existing_outcome = qa_state.get("outcome")
    if existing_outcome and existing_outcome.get("overall_status") == "infra_error":
        return {"qa": qa_state}

    yaml_text = qa_state.get("qa_yaml_v2_raw")
    parsed_dict = qa_state.get("qa_yaml_v2_parsed")

    if not yaml_text and not parsed_dict:
        outcome = OrchestratorOutcome(
            overall_status="infra_error",
            apps_to_qa=[],
            failure_summary="qa.yaml not loaded — init_orchestrator skipped or failed?",
        )
        qa_state["outcome"] = _outcome_to_dict(outcome)
        qa_state["final_status"] = "failed"
        return {"qa": qa_state}

    if parsed_dict is not None:
        cfg = QAYamlConfigV2.model_validate(parsed_dict)
    else:
        # parsed_dict is None here, so the guard above guarantees yaml_text
        # is a non-empty str (always true at runtime).
        assert isinstance(yaml_text, str)
        cfg = parse_qa_yaml_v2(yaml_text)

    if cfg.qa_required == "never":
        outcome = OrchestratorOutcome(
            overall_status="passed",
            apps_to_qa=[],
            failure_summary=None,
        )
        qa_state["outcome"] = _outcome_to_dict(outcome)
        qa_state["per_app_results"] = []
        qa_state["apps_to_qa"] = []
        qa_state["final_status"] = "passed"
        logger.info(
            "qa_skipped_qa_required_never",
            parent_run_id=state.get("job_id"),
        )
        return {"qa": qa_state}

    # Phase-C: force-all-apps short-circuit. Either the per-repo qa_required
    # set to "always" in qa.yaml, OR the per-job state['qa']['force_all_apps']
    # set via QAJobOverrides.force_all_apps on the API request, makes us run
    # every declared app regardless of the diff. Skip the affected-set work
    # entirely so a wrong base_branch (or a stale main) doesn't poison the run.
    force_all_apps = bool(qa_state.get("force_all_apps")) or cfg.qa_required == "always"

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    qa_app_results_repo = configurable.get("qa_app_results_repo")

    # Phase 5G: dashboard-set workspace_provider override.
    # When the admin UI has stored a row for this repo, prefer it over the
    # qa.yaml file-based config so operators can tweak affected_filter /
    # apps_glob without committing. Best-effort: if the lookup fails, log
    # and fall back to the qa.yaml — never block the run on an admin-side
    # storage hiccup.
    override_repo = configurable.get("qa_workspace_provider_overrides_repo")
    repo_name = state.get("repo")
    if override_repo is not None and repo_name:
        try:
            override = await override_repo.get(repo_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qa_workspace_provider_override_lookup_failed",
                repo=repo_name,
                error=str(exc),
            )
        else:
            if override is not None:
                from embry0.workflows.qa.qa_yaml_v2 import WorkspaceProviderRef

                # Snapshot the qa.yaml provider before the model_copy so the
                # log line carries the full pre/post diff — operators can
                # reconstruct exactly what changed without cross-referencing
                # the committed qa.yaml at run time.
                # workspace_provider may be None for all-deployed configs.
                qa_yaml_provider_type = cfg.workspace_provider.type if cfg.workspace_provider else None
                qa_yaml_provider_config = dict(cfg.workspace_provider.config) if cfg.workspace_provider else {}

                cfg = cfg.model_copy(
                    update={
                        "workspace_provider": WorkspaceProviderRef(
                            type=override.provider_type,
                            config=dict(override.config),
                        )
                    }
                )
                logger.info(
                    "qa_workspace_provider_override_applied",
                    repo=repo_name,
                    qa_yaml_provider_type=qa_yaml_provider_type,
                    qa_yaml_provider_config=qa_yaml_provider_config,
                    override_provider_type=override.provider_type,
                    override_provider_config=dict(override.config),
                )

    # 1. Load workspace provider. None is permitted when every declared app
    #    is target: deployed (schema-enforced, EMB-27) — nothing boots
    #    in-sandbox, so there is no workspace topology to map.
    deployed_apps = cfg.deployed_app_names()
    repo_root = Path(state.get("repo_root") or "/workspace")
    provider = None
    if cfg.workspace_provider is not None:
        try:
            provider = load_provider(
                cfg.workspace_provider.type,
                repo_root,
                cfg.workspace_provider.config,
            )
        except Exception as exc:  # noqa: BLE001
            outcome = OrchestratorOutcome(
                overall_status="infra_error",
                apps_to_qa=[],
                failure_summary=f"workspace_provider load failed: {exc}",
            )
            qa_state["outcome"] = _outcome_to_dict(outcome)
            qa_state["final_status"] = "failed"
            return {"qa": qa_state}

    # 2. Validate qa.yaml apps: vs workspace — managed apps only. Deployed
    #    apps are not workspace packages by definition (they run outside
    #    the sandbox), so the provider must not reject them.
    if provider is not None:
        errors, warnings = validate_against_qa_config(provider, cfg, app_names=cfg.managed_app_names())
    else:
        errors, warnings = [], []
    if errors:
        outcome = OrchestratorOutcome(
            overall_status="infra_error",
            apps_to_qa=[],
            failure_summary="qa.yaml validation errors blocked QA fan-out",
            validation_errors=errors,
            validation_warnings=warnings,
        )
        qa_state["outcome"] = _outcome_to_dict(outcome)
        qa_state["validation_errors"] = errors
        qa_state["validation_warnings"] = warnings
        qa_state["final_status"] = "failed"
        return {"qa": qa_state}

    # 3. Resolve apps to QA.
    changed_files_str = qa_state.get("changed_files") or []
    changed_files = [Path(p) for p in changed_files_str]
    if force_all_apps:
        # Bypass affected-set; QA every declared app. validate_against_qa_config
        # already errored out on apps that don't exist in the workspace, so
        # every cfg.apps key is known to be a real workspace app.
        apps = sorted(cfg.apps.keys())
        logger.info(
            "qa_force_all_apps",
            parent_run_id=state.get("job_id"),
            n_apps=len(apps),
            reason="qa_required=always" if cfg.qa_required == "always" else "force_all_apps=true",
        )
    else:
        # Managed apps follow the provider's affected-set; deployed apps
        # always run when QA runs — a diff cannot be mapped onto an
        # externally running instance, and the run's value there is
        # liveness + regression against the live deployment (EMB-27).
        affected_managed = resolve_apps_to_qa(provider, cfg, changed_files=changed_files) if provider else []
        apps = sorted(set(affected_managed) | set(deployed_apps))
    qa_state["apps_to_qa"] = list(apps)
    qa_state["validation_warnings"] = warnings

    # Phase 5D: persist the affected-set decision so the dashboard can show
    # which apps ran vs were skipped, what diff drove selection, and the
    # dep graph. Best-effort — failure is logged but never blocks the run.
    # Upsert *after* `apps` is decided but *before* fan-out so the row exists
    # even if fan-out throws. Repo is read off configurable; absent in tests.
    qa_run_metadata_repo = configurable.get("qa_run_metadata_repo")
    if qa_run_metadata_repo is not None:
        try:
            all_apps = sorted(cfg.apps.keys())
            apps_set = set(apps)
            skipped = [a for a in all_apps if a not in apps_set]
            await qa_run_metadata_repo.upsert(
                job_id=state.get("job_id") or "",
                apps_to_qa=list(apps),
                apps_skipped=skipped,
                force_all_apps=bool(force_all_apps),
                changed_files=list(changed_files_str or []),
                base_branch=str(state.get("base_branch") or ""),
                # dep_graph: empty for Phase 5D — extracting npm-workspaces-turbo
                # edges out of the provider is a follow-up. The list view works
                # without it; the schema reserves the field for the future.
                dep_graph=[],
                # Phase 5F: head_sha was stashed by init_orchestrator_node at
                # qa["head_sha"]; persist it so flake-heatmap can detect status
                # changes between consecutive runs on the same workspace head.
                # Empty string when init_orchestrator didn't run (older test
                # paths inject qa_yaml_v2_raw directly without cloning).
                head_sha=str(qa_state.get("head_sha") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qa_run_metadata_persist_failed",
                job_id=state.get("job_id"),
                error=str(exc),
            )

    if not apps:
        outcome = OrchestratorOutcome(
            overall_status="passed",
            apps_to_qa=[],
            validation_warnings=warnings,
        )
        qa_state["outcome"] = _outcome_to_dict(outcome)
        qa_state["per_app_results"] = []
        qa_state["final_status"] = "passed"
        return {"qa": qa_state}

    # 4. Resolve per-app configs. Phase 1: app-local files default to None.
    # resolve_app_config raises ValueError for semantically invalid merges
    # (e.g. a deployed app whose merged ready_checks are empty) — surface
    # that as a validation failure, not a node crash.
    try:
        resolved_configs = [resolve_app_config(name, cfg, app_local=None) for name in apps]
    except ValueError as exc:
        outcome = OrchestratorOutcome(
            overall_status="infra_error",
            apps_to_qa=list(apps),
            failure_summary=f"app config resolution failed: {exc}",
            validation_errors=[str(exc)],
            validation_warnings=warnings,
        )
        qa_state["outcome"] = _outcome_to_dict(outcome)
        qa_state["validation_errors"] = [str(exc)]
        qa_state["final_status"] = "failed"
        return {"qa": qa_state}

    # 4b. Job-level acceptance-criteria override (QAJobOverrides). A caller
    # that supplies a non-empty list on POST /jobs replaces every app's
    # resolved criteria for this one run — the qa.yaml stays the durable
    # contract. issue_executor stashes the list at qa["acceptance_criteria"].
    override_criteria = list(qa_state.get("acceptance_criteria") or [])
    if override_criteria:
        resolved_configs = [replace(rc, acceptance_criteria=override_criteria) for rc in resolved_configs]
        logger.info(
            "qa_acceptance_criteria_overridden",
            job_id=state.get("job_id"),
            n_criteria=len(override_criteria),
        )

    # 4a. Look up prebaked image tag if cache layer is enabled.
    image_repo = configurable.get("qa_image_tags_repo")
    image_tag: str | None = None
    if image_repo is not None and cfg.cache.prebaked_image.enabled:
        from embry0.cache.prebaked_image import PrebakedImageLookup

        image_tag = await PrebakedImageLookup(image_repo=image_repo).get_tag_for_repo(state.get("repo") or "")

    # 5. Fan out.
    # Pass shared_volume_name from init_orchestrator_node (C3 wiring).
    shared_volume_name: str | None = qa_state.get("shared_volume_name")
    per_app_results = await fan_out_subtasks(
        resolved_configs,
        parent_run_id=state["job_id"],
        repo=state.get("repo") or "",
        branch_name=state.get("branch_name"),
        user_env_vars=state.get("user_env_vars"),
        image_tag=image_tag,
        shared_volume_name=shared_volume_name,
        turbo_remote_enabled=cfg.cache.turbo_remote.enabled,
        max_concurrent=cfg.parallelism.max_concurrent_apps,
        # RunnableConfig is a TypedDict (a plain dict at runtime); the helper
        # treats config as a generic mapping. Cast is type-only.
        config=cast("dict[str, Any]", config or {}),
    )

    # 6. Persist (idempotent upsert).  Phase 5A: route through
    # upsert_with_boot_phase so the new boot_phase JSONB column is populated.
    if qa_app_results_repo is not None:
        for r in per_app_results:
            try:
                await qa_app_results_repo.upsert_with_boot_phase(
                    job_id=state["job_id"],
                    app_name=r.app_name,
                    status=str(r.status),
                    duration_ms=int(r.duration_ms),
                    cache_hits=r.cache_hits,
                    trace_url=r.trace_url,
                    failure_summary=r.failure_summary,
                    raw_result=r.raw_result,
                    boot_phase=r.boot_phase,
                )
            except Exception as exc:  # noqa: BLE001
                # Log but don't fail the run on persistence error.
                logger.warning(
                    "qa_app_results_upsert_failed",
                    job_id=state["job_id"],
                    app_name=r.app_name,
                    error=str(exc),
                )

    # 7. Compute overall_status.
    overall = compute_overall(per_app_results)
    outcome = OrchestratorOutcome(
        overall_status=overall,
        apps_to_qa=list(apps),
        validation_warnings=warnings,
    )

    qa_state["outcome"] = _outcome_to_dict(outcome)
    qa_state["per_app_results"] = [r.to_dict() for r in per_app_results]
    qa_state["final_status"] = (
        "passed" if overall == "passed" else "failed" if overall in ("failed", "infra_error") else "pending"
    )

    logger.info(
        "qa_run_complete",
        parent_run_id=state["job_id"],
        overall_status=overall,
        apps=apps,
    )

    return {"qa": qa_state}
