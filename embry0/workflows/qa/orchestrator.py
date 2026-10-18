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
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from embry0.execution import events
from embry0.orchestration.budget import BudgetOverrunError, resolve_job_cap
from embry0.orchestration.nodes.agent import evaluate_job_cap
from embry0.safety.error_codes import ErrorCode
from embry0.workflows.pipeline_graphs import qa_pipeline_graph
from embry0.workflows.qa._orchestrator_workspace import (
    compute_changed_files_via_diff,
    resolve_base_branch,
    stage_workspace_for_provider,
)
from embry0.workflows.qa._subtask_env import build_qa_sandbox_env
from embry0.workflows.qa.orchestrator_helpers import (
    OrchestratorOutcome,
    _outcome_to_dict,
    affected_app_names,
    fan_out_subtasks,
    gate_deployed_apps,
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


async def _configured_base_branch(*, state: dict[str, Any], repo: str, prefs_repo: Any) -> str | None:
    """The explicitly-configured diff base for this QA run, or None.

    Precedence: the job override (``QAJobOverrides.base_branch``, already on
    state) → ``repo_preferences.base_branch`` (Spec 1A §3.7). None means "no
    preference expressed", which lets ``resolve_base_branch`` fall through to
    the clone's ``origin/HEAD`` — free, network-local, and correct for the
    overwhelming majority of repos. Answering "main" here instead would undo
    RAV-1405.

    Imported inside the body on purpose: the module-level
    ``resolve_base_branch`` binding is the *sandbox symref* helper
    (``embry0.execution.workspace_diff``), and the same-named resolver in
    ``embry0.services.base_branch`` must not shadow it.
    """
    from embry0.services.base_branch import preferred_base_branch

    override = state.get("base_branch")
    if override:
        return str(override)
    return await preferred_base_branch(prefs_repo, repo)


def _stream_writer() -> Any:
    """The node's LangGraph stream writer, or a no-op outside a runnable
    context (unit tests)."""
    try:
        return get_stream_writer()
    except RuntimeError:
        return lambda _event: None


async def init_orchestrator_node(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """LangGraph node: brackets ``_init_orchestrator_node_impl`` with
    ``node_started``/``node_completed`` on the job stream (spec 3.7), so
    every non-raising return path, early ones included, is closed."""
    writer = _stream_writer()
    writer(events.node_started(node="init_orchestrator"))
    writer(events.pipeline_graph(graph=qa_pipeline_graph(str(state.get("job_id") or "")), node="init_orchestrator"))
    out = await _init_orchestrator_node_impl(state, config)
    writer(events.node_completed(node="init_orchestrator"))
    return out


async def _init_orchestrator_node_impl(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """Bootstrap-sandbox QA-run setup (clone, diff, staging) + DB config load.

    Allocates a short-lived sandbox using the `slim` profile, clones the
    target repo (for the diff + workspace staging), loads the repo's QA
    config from the embry0 DB (RAV-1253: the only source — no repo files),
    destroys the sandbox, and writes the raw text and parsed dict into
    state["qa"] so qa_orchestrator_node can run without doing its own clone.
    """
    from embry0.safety.error_codes import ErrorCode
    from embry0.services.forge_resolver import resolve_forge
    from embry0.storage.repositories.repos import UnregisteredRepoError
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

    # Spec 3.6: the forge decides the clone origin for the bootstrap sandbox
    # and the git-proxy credential host enrolled with it. Resolved before the
    # try: block so an unregistered repo reports its own error code instead of
    # being folded into "bootstrap qa.yaml load failed".
    gitlab_url = getattr(configurable.get("embry0_config"), "gitlab_url", "") or ""
    try:
        forge = await resolve_forge(configurable.get("repos_repo"), repo, job_id=job_id)
    except UnregisteredRepoError as exc:
        outcome = OrchestratorOutcome(
            overall_status="infra_error",
            apps_to_qa=[],
            failure_summary=f"{ErrorCode.REPO_UNREGISTERED}: {exc}",
        )
        qa = dict(state.get("qa") or {})
        qa["outcome"] = _outcome_to_dict(outcome)
        return {"qa": qa}

    container_id: str | None = None
    lockfile_text: str = ""
    changed_files: list[str] = []
    staging_path_str: str | None = None
    try:
        container_id, sandbox_token = await sandbox_mgr.create(
            bootstrap_job_id,
            profile=profile,
            env=env,
            repo=repo,
            forge=forge,
            llm_providers=tuple(configurable.get("llm_providers") or ()),
            parent_job_id=job_id,
        )
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
            prefs_repo=configurable.get("repo_preferences_repo"),
            forge=forge,
            gitlab_url=gitlab_url,
            forge_credential=(
                sandbox_mgr.last_forge_credential(container_id)
                if hasattr(sandbox_mgr, "last_forge_credential")
                else None
            ),
        )
        # RAV-1251/RAV-1253: the embry0 DB is the ONLY source of QA config —
        # target repos carry no config file, and the legacy import channels
        # (file store, in-repo .embry0/qa.yaml, tree app-local files) are
        # removed. A missing row is a configuration error with a clear fix,
        # surfaced as infra_error via the surrounding try.
        qa_configs_repo = configurable.get("qa_configs_repo")
        yaml_text: str | None = None
        if qa_configs_repo is not None:
            db_row = await qa_configs_repo.get(repo)
            if db_row is not None:
                yaml_text = db_row["yaml_text"]
                logger.info("qa_config_db_hit", repo=repo)
        if not yaml_text:
            raise RuntimeError(
                f"repo {repo!r} has no QA config stored in embry0 — store one via "
                "PUT /repos/{owner}/{repo}/qa-config, the dashboard config editor, "
                "the qa_config_set MCP tool, or onboarding"
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

        # RAV-1405: resolve the repo's real default branch (origin/HEAD) when
        # nothing configured one — a hardcoded "main" mis-scopes app-selection
        # on master-default repos. Spec 1A §3.7: a repo_preferences row is a
        # configured value too, and outranks the symref.
        configured_base = await _configured_base_branch(
            state=state,
            repo=repo,
            prefs_repo=configurable.get("repo_preferences_repo"),
        )
        default_base_branch = await resolve_base_branch(
            docker=docker,
            container_id=container_id,
            configured=configured_base,
        )
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

    # Phase-2 C3: shared-volume warming. Scope can only be per-job or
    # per-repo — per-org is rejected at qa.yaml parse time (RAV-1061).
    sv_cfg = cfg.cache.shared_volume
    if sv_cfg.enabled:
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
                    # RAV-1400: warm on the repo's prebaked image when one
                    # exists — its baked ~/.npm cache turns the install into
                    # mostly link-time. Lookup is fail-open (None → slim).
                    warm_image_tag: str | None = None
                    if cfg.cache.prebaked_image.enabled:
                        from embry0.cache.prebaked_image import lookup_prebaked_tag

                        # #334: None also when the image's base was rebuilt.
                        warm_image_tag = await lookup_prebaked_tag(configurable, repo or "")

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
                        install_command=cfg.cache.install_command,
                        prebaked_image_tag=warm_image_tag,
                        forge=forge,
                        gitlab_url=gitlab_url,
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
    # #65: the report node dispatches on the forge this node already resolved
    # for the clone, instead of assuming GitHub.
    qa["forge"] = forge
    if staging_path_str:
        out["repo_root"] = staging_path_str
    # Spec 1A §3.7: publish the resolved base so downstream nodes (and the
    # qa_run_metadata row, which used to persist "") read one value.
    out["base_branch"] = default_base_branch
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

    Spec 3.7: it also brackets itself with ``node_started`` /
    ``node_completed`` on the job stream, the latter carrying
    ``action=<overall_status>`` as the run-level outcome.
    """
    writer = _stream_writer()
    writer(events.node_started(node="orchestrate_qa"))
    out = await _qa_orchestrator_node_impl(state, config)

    # Pull the final status from whichever path the impl returned through:
    # success path writes outcome.overall_status, every infra-error path
    # also fills it in (see the early returns in _qa_orchestrator_node_impl).
    out_qa = out.get("qa") if isinstance(out, dict) else None
    outcome = out_qa.get("outcome") if isinstance(out_qa, dict) else None
    overall_status = outcome.get("overall_status") if isinstance(outcome, dict) else None

    # Phase 5C: publish run-level "done" so SSE clients can close cleanly.
    # No-op when no bus is wired (tests / legacy callers).
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    bus = configurable.get("qa_event_bus")
    if bus is not None:
        run_id = state.get("job_id")
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

    # spec 3.7: the run-level outcome on the job stream (the QA bus `done`
    # keeps closing the SSE route).
    writer(events.node_completed(node="orchestrate_qa", action=str(overall_status) if overall_status else None))
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

    from embry0.workflows.qa.browser_backends import DEFAULT_BROWSER_BACKEND, backend_for
    from embry0.workflows.qa.conditional_criteria import evaluate_conditional_criteria
    from embry0.workflows.qa.qa_yaml_resolve import resolve_app_config
    from embry0.workflows.qa.qa_yaml_v2 import QAYamlConfigV2, parse_qa_yaml_v2
    from embry0.workflows.qa.subtask_result_schema import overall_status as compute_overall

    qa_state = dict(state.get("qa") or {})

    # Live-progress plumbing: this node is a real node of the streamed parent
    # graph, so get_stream_writer() works here — sub-graphs invoked via
    # ainvoke() inside it get a dead writer (custom events only flow under
    # the parent's astream), which is why standalone QA runs historically
    # produced ZERO job_logs events until the very end. Capture the live
    # writer once and thread it down explicitly as
    # configurable["qa_parent_writer"] for fan_out_subtasks / subtask nodes /
    # qa_node to emit through.
    try:
        writer = get_stream_writer()
    except RuntimeError:  # outside a runnable context (unit tests)
        writer = lambda _event: None  # noqa: E731

    # Surface progress on the jobs row too: "updates"-mode stage persistence
    # only fires at node boundaries, and this node IS the whole run. Direct
    # best-effort write so the Jobs board shows qa_running immediately.
    early_configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    jobs_repo = early_configurable.get("jobs_repo")
    if jobs_repo is not None:
        try:
            await jobs_repo.update(state["job_id"], current_stage="qa_running")
        except Exception:  # noqa: BLE001 — a stage write must never fail the run
            logger.warning("qa_stage_persist_failed", job_id=state.get("job_id"))

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
        writer(events.qa_apps_resolved(apps=[]))
        writer(events.progress(message="QA skipped: qa_required is never", node="orchestrate_qa"))
        return {"qa": qa_state}

    # Phase-C: force-all-apps short-circuit. Either the per-repo qa_required
    # set to "always" in qa.yaml, OR the per-job state['qa']['force_all_apps']
    # set via QAJobOverrides.force_all_apps on the API request, makes us run
    # every declared app regardless of the diff. Skip the affected-set work
    # entirely so a wrong base_branch (or a stale main) doesn't poison the run.
    force_all_apps = bool(qa_state.get("force_all_apps")) or cfg.qa_required == "always"

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    qa_app_results_repo = configurable.get("qa_app_results_repo")

    # 1. Load workspace provider. None is permitted when every declared app
    #    is target: deployed (schema-enforced, EMB-27) — nothing boots
    #    in-sandbox, so there is no workspace topology to map.
    deployed_apps = cfg.deployed_app_names()
    repo_root = Path(state.get("repo_root") or "/workspace")
    provider = None
    provider_subdir = ""
    if cfg.workspace_provider is not None:
        try:
            # EMB-32: `config.root` runs any provider on a repo subdirectory
            # (e.g. a turbo workspace under frontend/). The key is stripped
            # before the inner provider sees it (provider configs are
            # extra="forbid") and changed-file paths are rebased by the
            # wrapper so prefix matching stays consistent.
            provider_config = dict(cfg.workspace_provider.config or {})
            provider_subdir = str(provider_config.pop("root", "") or "").strip("/")
            provider = load_provider(
                cfg.workspace_provider.type,
                repo_root / provider_subdir if provider_subdir else repo_root,
                provider_config,
            )
            if provider_subdir:
                from embry0.workspace_providers.root_scoped import RootScopedProvider

                provider = RootScopedProvider(provider, root=provider_subdir)
        except Exception as exc:  # noqa: BLE001
            outcome = OrchestratorOutcome(
                overall_status="infra_error",
                apps_to_qa=[],
                failure_summary=f"workspace_provider load failed: {exc}",
            )
            qa_state["outcome"] = _outcome_to_dict(outcome)
            qa_state["error_message"] = outcome.failure_summary
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
        qa_state["error_message"] = f"{outcome.failure_summary}: {'; '.join(errors)}"
        qa_state["final_status"] = "failed"
        return {"qa": qa_state}

    # 3. Resolve apps to QA.
    changed_files_str = qa_state.get("changed_files") or []
    changed_files = [Path(p) for p in changed_files_str]
    affected_managed: list[str] = []
    provider_affected: list[str] = []
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
        provider_affected = affected_app_names(provider, cfg, changed_files=changed_files) if provider else []
        affected_managed = sorted(set(provider_affected) & set(cfg.apps))
        apps = sorted(set(affected_managed) | set(gate_deployed_apps(cfg, deployed_apps, changed_files)))
    if qa_state.get("exclude_deployed_apps"):
        # #298: a branch job never QAs a deployed target — the deployed site
        # does not contain the branch, so its verdict says nothing about the
        # change. Applied after every selection rule, force_all_apps included.
        dropped = sorted(set(apps) & set(deployed_apps))
        if dropped:
            apps = [a for a in apps if a not in dropped]
            warnings = [
                *warnings,
                "deployed-target apps are not used to check a branch (they run the released code, "
                f"not this change): skipped {', '.join(dropped)}",
            ]
            logger.info("qa_deployed_apps_excluded_for_branch_job", parent_run_id=state.get("job_id"), dropped=dropped)
    qa_state["apps_to_qa"] = list(apps)
    qa_state["validation_warnings"] = warnings

    # 3b. EMB-39: evaluate conditional acceptance criteria against the run's
    # change context. Deployed/standalone runs arrive with an empty diff, so
    # changed_paths/affected_apps predicates never match there — only issue
    # labels or the job-level force knob fire groups on such runs (default-OFF
    # rule for costly criteria).
    forced_group_names = list(qa_state.get("force_conditional_groups") or [])
    issue_labels = list(state.get("issue_labels") or [])
    if force_all_apps:
        # The affected-set computation was skipped above; recompute it only
        # if some group actually predicates on affected_apps.
        needs_affected = any(g.when.affected_apps for g in cfg.conditional_acceptance_criteria)
        affected_for_predicates: set[str] = (
            set(resolve_apps_to_qa(provider, cfg, changed_files=changed_files))
            if (needs_affected and provider is not None and changed_files)
            else set()
        )
    else:
        # Diff-derived MANAGED affected set — deliberately NOT apps_to_qa:
        # deployed apps always join apps_to_qa and would trivially satisfy
        # an affected_apps predicate on every run. Gated on a non-empty diff
        # so the default-OFF rule is structural, not provider-dependent (a
        # provider that returns a non-empty affected set for an empty diff
        # must not fire cost-gated criteria).
        affected_for_predicates = set(affected_managed) if changed_files else set()
    cond = evaluate_conditional_criteria(
        cfg,
        changed_files=list(changed_files_str),
        apps_to_qa=apps,
        affected_apps_from_diff=affected_for_predicates,
        labels=issue_labels,
        forced_group_names=forced_group_names,
    )

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
                # EMB-39: which conditional-criteria groups fired and why.
                conditional_groups=cond.groups_persisted(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qa_run_metadata_persist_failed",
                job_id=state.get("job_id"),
                error=str(exc),
            )

    # EMB-39: an unknown forced group name fails the run before fan-out. The
    # operator explicitly requested a (typically costly) check — silently
    # ignoring a typo is the worst failure mode. Checked after the metadata
    # upsert so the run's row still records the decision context.
    if cond.unknown_forced_groups:
        known = sorted(g.name for g in cfg.conditional_acceptance_criteria)
        msg = (
            "unknown conditional-criteria group(s) in force_conditional_groups: "
            f"{cond.unknown_forced_groups} — known groups: {known}"
        )
        outcome = OrchestratorOutcome(
            overall_status="infra_error",
            apps_to_qa=list(apps),
            failure_summary=msg,
            validation_errors=[msg],
            validation_warnings=warnings,
        )
        qa_state["outcome"] = _outcome_to_dict(outcome)
        qa_state["validation_errors"] = [msg]
        qa_state["error_message"] = msg
        qa_state["final_status"] = "failed"
        return {"qa": qa_state}

    if not apps:
        declared = sorted(cfg.apps.keys())
        reason = "no_change_context" if not changed_files_str else "diff_touched_no_declared_app"
        logger.info(
            "qa_no_apps_to_qa",
            parent_run_id=state.get("job_id"),
            reason=reason,
            declared_apps=declared,
            provider_affected=provider_affected,
            n_changed_files=len(changed_files_str),
        )
        writer(events.qa_apps_resolved(apps=[]))
        if reason == "no_change_context":
            msg = (
                "QA selected no app: the diff against the base branch is empty and no declared app is "
                f"always on (declared: {declared}; provider-affected: {provider_affected}). "
                "Set qa_required: always, pass force_all_apps on the job, or declare an app without prefixes."
            )
            writer(events.progress(message=msg, node="orchestrate_qa"))
            outcome = OrchestratorOutcome(
                overall_status="failed",
                apps_to_qa=[],
                failure_summary=msg,
                validation_warnings=warnings,
            )
            qa_state["outcome"] = _outcome_to_dict(outcome)
            qa_state["per_app_results"] = []
            qa_state["error_code"] = ErrorCode.QA_NO_APPS.value
            qa_state["error_message"] = msg
            qa_state["final_status"] = "failed"
            return {"qa": qa_state}
        msg = (
            f"QA selected no app: the change touched no declared app (declared: {declared}; "
            f"provider-affected: {provider_affected}; changed files: {len(changed_files_str)})."
        )
        writer(events.progress(message=msg, node="orchestrate_qa"))
        outcome = OrchestratorOutcome(
            overall_status="passed",
            apps_to_qa=[],
            validation_warnings=[*warnings, msg],
        )
        qa_state["outcome"] = _outcome_to_dict(outcome)
        # qa_report_node renders qa["validation_warnings"], not the outcome's.
        qa_state["validation_warnings"] = [*warnings, msg]
        qa_state["per_app_results"] = []
        qa_state["final_status"] = "passed"
        return {"qa": qa_state}

    # 4. Resolve per-app configs, applying the stored document's app_local
    # overrides (RAV-1253: tree-collected apps/<name>/.embry0/app.yaml files
    # are gone — the config document is the only override channel).
    # resolve_app_config raises ValueError for semantically invalid merges
    # (e.g. a deployed app whose merged ready_checks are empty) — surfaced
    # as a validation failure, not a node crash.
    try:
        app_locals = dict(cfg.app_local)
        for matched_name in sorted(app_locals):
            logger.info("qa_app_local_loaded", app=matched_name, parent_run_id=state.get("job_id"))
        # #229: deployment-default browser backend (weakest precedence layer)
        # comes from app config; the per-job override (strongest layer) from
        # QAJobOverrides via qa_state. Both are registry-validated inside
        # this try so a bad name surfaces as a validation failure, not a
        # node crash.
        early_cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
        embry0_cfg = early_cfg.get("embry0_config")
        deployment_backend = getattr(embry0_cfg, "qa_browser_backend_default", None) or DEFAULT_BROWSER_BACKEND
        resolved_configs = [
            resolve_app_config(
                name,
                cfg,
                app_local=app_locals.get(name),
                default_browser_backend=deployment_backend,
            )
            for name in apps
        ]
        override_backend = qa_state.get("browser_backend")
        if override_backend:
            backend_for(override_backend)
            resolved_configs = [replace(rc, browser_backend=override_backend) for rc in resolved_configs]
            logger.info(
                "qa_browser_backend_overridden",
                job_id=state.get("job_id"),
                browser_backend=override_backend,
            )
        # #537: the per-job sandbox profile (QAJobOverrides.sandbox_profile)
        # outranks every qa.yaml layer, the same way the browser backend does.
        override_profile = qa_state.get("sandbox_profile")
        if override_profile:
            resolved_configs = [replace(rc, sandbox_profile=override_profile) for rc in resolved_configs]
            logger.info(
                "qa_sandbox_profile_overridden",
                job_id=state.get("job_id"),
                sandbox_profile=override_profile,
            )
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
        qa_state["error_message"] = outcome.failure_summary
        qa_state["final_status"] = "failed"
        return {"qa": qa_state}

    # 4a. EMB-34 fix #3: config-time capability check — the QA agent always
    # drives a browser (Playwright MCP is in its static seed), so a resolved
    # sandbox profile whose image has no browser guarantees a mid-run
    # failure after boot spend. Browser capability is inferred from the
    # base_image (the qa image family bundles Playwright+Chromium); custom
    # profiles derived from it pass automatically. Missing profiles are NOT
    # failed here — acquire_sandbox already reports those with a clearer
    # per-app INFRA_FAILURE.
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    profiles_repo = configurable.get("profiles_repo")
    if profiles_repo is not None:
        browserless: list[str] = []
        for rc in resolved_configs:
            try:
                profile = await profiles_repo.get(rc.sandbox_profile)
            except Exception:  # noqa: BLE001 — capability check is advisory-fatal, not crash-worthy
                profile = None
            if profile is None:
                continue
            base_image = str(profile.get("base_image") or "")
            if "embry0-sandbox-qa" not in base_image:
                browserless.append(
                    f"app {rc.app_name!r}: profile {rc.sandbox_profile!r} "
                    f"(base_image {base_image!r}) has no browser, but the QA agent "
                    "requires Playwright — use a profile built on embry0-sandbox-qa"
                )
        if browserless:
            outcome = OrchestratorOutcome(
                overall_status="infra_error",
                apps_to_qa=list(apps),
                failure_summary=f"sandbox profile capability check failed: {'; '.join(browserless)}",
                validation_errors=browserless,
                validation_warnings=warnings,
            )
            qa_state["outcome"] = _outcome_to_dict(outcome)
            qa_state["validation_errors"] = browserless
            qa_state["error_message"] = outcome.failure_summary
            qa_state["final_status"] = "failed"
            return {"qa": qa_state}

    # #537: the profile the sub-task sandboxes run on, for the jobs row. One
    # value when every app shares it; None when they differ — a single
    # column cannot say which app ran where.
    used_profiles = {rc.sandbox_profile for rc in resolved_configs}
    qa_state["sandbox_profile_used"] = used_profiles.pop() if len(used_profiles) == 1 else None

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

    # 4c. EMB-39: append fired conditional criteria AFTER the 4b replace.
    # The relevance gate is the orchestrator's decision, not the caller's —
    # and on issue→PR runs triage's criteria arrive through the very same
    # replace path, so appending before it would mean conditional groups
    # never fire on the feature's primary use case.
    if cond.criteria_by_app:
        resolved_configs = [
            replace(
                rc,
                acceptance_criteria=rc.acceptance_criteria
                + [c for c in cond.criteria_by_app.get(rc.app_name, []) if c not in rc.acceptance_criteria],
            )
            for rc in resolved_configs
        ]
        logger.info(
            "qa_conditional_criteria_applied",
            job_id=state.get("job_id"),
            matched=cond.matched_groups,
            forced=cond.forced_groups,
            n_apps=len(cond.criteria_by_app),
        )

    # 4a. Look up prebaked image tag if cache layer is enabled.
    # #334: an image whose base sandbox was rebuilt since is skipped (None) —
    # sub-tasks then run on the profile's base image + the warm volume.
    image_tag: str | None = None
    if cfg.cache.prebaked_image.enabled:
        from embry0.cache.prebaked_image import lookup_prebaked_tag

        image_tag = await lookup_prebaked_tag(configurable, state.get("repo") or "")

    # 5. Fan out.
    # Pass shared_volume_name from init_orchestrator_node (C3 wiring).
    shared_volume_name: str | None = qa_state.get("shared_volume_name")
    app_names = [rc.app_name for rc in resolved_configs]
    writer(
        events.progress(
            message=f"QA fan-out: {len(app_names)} app(s): {', '.join(app_names)}",
            node="orchestrate_qa",
        )
    )
    writer(events.qa_apps_resolved(apps=app_names))
    # Thread the live stream writer into the sub-task config — sub-graphs
    # run under ainvoke() where get_stream_writer() is dead (see above).
    child_config: dict[str, Any] = dict(config or {})
    child_config["configurable"] = {
        **early_configurable,
        "qa_parent_writer": writer,
        # #264: the job's per-job cap and overrun mode, for the QA agent turns.
        # Sub-task states are built fresh, so the parent's cap rides here.
        "qa_job_budget": {
            "max_budget_usd": resolve_job_cap(state),
            "budget_overrun_mode_override": state.get("budget_overrun_mode_override"),
        },
    }

    async def _job_budget_spent() -> bool:
        """True once a hard cap is crossed: start no more sub-tasks (#264)."""
        check = await evaluate_job_cap(state, config, float(state.get("total_cost_usd") or 0.0))
        return not check.decision.allowed

    per_app_results = await fan_out_subtasks(
        resolved_configs,
        parent_run_id=state["job_id"],
        repo=state.get("repo") or "",
        branch_name=state.get("branch_name"),
        user_env_vars=state.get("user_env_vars"),
        agent_models_override=state.get("agent_models_override"),
        image_tag=image_tag,
        shared_volume_name=shared_volume_name,
        turbo_remote_enabled=cfg.cache.turbo_remote.enabled,
        max_concurrent=cfg.parallelism.max_concurrent_apps,
        config=child_config,
        budget_spent=_job_budget_spent,
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
    writer(events.progress(message=f"QA fan-out complete: {overall}", node="orchestrate_qa"))
    # RAV-1249: aggregate the failing apps' summaries onto the run-level
    # outcome. Triage's qa_failure_block reads outcome.failure_summary at
    # re-entry — without this it rendered "no failure summary recorded"
    # (and decided blind) while the qa_subtask_finished events carried the
    # full per-criterion verdicts.
    from embry0.workflows.qa.subtask_result_schema import FAILED_STATUSES, SubTaskStatus

    failure_summary: str | None = None
    if overall != "passed":
        failure_summary = (
            "\n".join(
                f"[{r.app_name}] {r.status}: {r.failure_summary or 'no per-app failure summary recorded'}"
                for r in per_app_results
                if r.status in FAILED_STATUSES or r.status == SubTaskStatus.INFRA_FAILURE
            )
            or None
        )
    outcome = OrchestratorOutcome(
        overall_status=overall,
        apps_to_qa=list(apps),
        failure_summary=failure_summary,
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

    # #264: layer 1 over the whole fan-out. The QA agents' cost never reaches
    # this state, but their traces do: a soft crossing is recorded for the
    # job's routing, a hard one fails the job like any agent turn would.
    # Results are persisted above first, so the per-app verdicts survive.
    update: dict[str, Any] = {"qa": qa_state}
    cap_check = await evaluate_job_cap(state, config, float(state.get("total_cost_usd") or 0.0))
    if cap_check.decision.overrun > 0:
        update["budget_overrun_usd"] = cap_check.decision.overrun
        logger.warning(
            "job_budget_exceeded",
            agent_type="qa",
            total_cost_usd=cap_check.total,
            max_budget_usd=cap_check.cap,
            overrun_usd=cap_check.decision.overrun,
            overrun_mode=cap_check.mode,
            job_id=state.get("job_id"),
        )
    if not cap_check.decision.allowed:
        raise BudgetOverrunError(cap_check.total, cap_check.cap)
    return update
