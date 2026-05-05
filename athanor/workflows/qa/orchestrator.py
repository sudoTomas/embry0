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
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig

from athanor.workflows.qa._subtask_env import build_qa_sandbox_env
from athanor.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
from athanor.workflows.qa.qa_yaml_v2 import QAYamlConfigV2
from athanor.workflows.qa.subtask_graph import run_subtask
from athanor.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)
from athanor.workspace_providers.provider import WorkspaceProvider
from athanor.workspace_providers.registry import load_provider

logger = structlog.get_logger(__name__)


@dataclass
class OrchestratorOutcome:
    """Final outcome the orchestrator emits to the parent pipeline."""

    overall_status: str
    apps_to_qa: list[str]
    failure_summary: str | None = None
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)


def resolve_apps_to_qa(
    provider: WorkspaceProvider,
    config: QAYamlConfigV2,
    changed_files: list[Path],
) -> list[str]:
    """Compute the list of qa.yaml app-names to actually run QA against.

    The returned list is the intersection of:
      - apps the workspace_provider says are affected (translated from
        package_name to qa.yaml app-name)
      - apps declared under config.apps:

    Any provider-affected apps that aren't declared in qa.yaml are silently
    skipped (validate() will surface them as warnings via the comment).
    """
    no_cascade = frozenset(
        name for name, entry in config.packages.items() if entry.no_cascade
    )
    affected = provider.affected(
        changed_files=list(changed_files),
        no_cascade_packages=no_cascade,
    )

    workspace_apps, _ = provider.discover()
    pkg_to_app_name = {a.package_name: a.name for a in workspace_apps}

    affected_app_names = {
        pkg_to_app_name[pkg]
        for pkg in affected.apps_to_qa
        if pkg in pkg_to_app_name
    }

    declared = set(config.apps.keys())
    return sorted(affected_app_names & declared)


def validate_against_qa_config(
    provider: WorkspaceProvider,
    config: QAYamlConfigV2,
) -> tuple[list[str], list[str]]:
    """Return (errors, warnings).

    Errors block the orchestrator from fanning out (run becomes infra_error
    with a clear message). Warnings are surfaced in the PR comment but do
    not gate.
    """
    messages = provider.validate(list(config.apps.keys()))
    errors = [m for m in messages if m.lower().startswith("error:")]
    warnings = [m for m in messages if m.lower().startswith("warning:")]
    return errors, warnings



async def fan_out_subtasks(
    resolved_configs: list[ResolvedAppConfig],
    *,
    parent_run_id: str,
    repo: str,
    branch_name: str | None,
    user_env_vars: Any = None,
    max_concurrent: int,
    config: dict[str, Any],
) -> list[SubTaskResult]:
    """Run sub-tasks in parallel under a concurrency cap.

    Returns results in input order. Sub-task crashes are caught and
    surfaced as INFRA_FAILURE rather than propagating — sibling sub-tasks
    are isolated from each other.
    """
    sem = asyncio.Semaphore(max_concurrent)

    async def _one(resolved: ResolvedAppConfig) -> SubTaskResult:
        async with sem:
            try:
                return await run_subtask(
                    resolved,
                    parent_run_id=parent_run_id,
                    repo=repo,
                    branch_name=branch_name,
                    user_env_vars=user_env_vars,
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001
                return SubTaskResult(
                    app_name=resolved.app_name,
                    status=SubTaskStatus.INFRA_FAILURE,
                    duration_ms=0,
                    cache_hits=CacheHits(),
                    trace_url=None,
                    failure_summary=f"sub-task crashed: {exc}",
                )

    tasks = [asyncio.create_task(_one(r)) for r in resolved_configs]
    return await asyncio.gather(*tasks)


def _outcome_to_dict(outcome: OrchestratorOutcome) -> dict[str, Any]:
    return {
        "overall_status": outcome.overall_status,
        "apps_to_qa": list(outcome.apps_to_qa),
        "failure_summary": outcome.failure_summary,
        "validation_errors": list(outcome.validation_errors),
        "validation_warnings": list(outcome.validation_warnings),
    }


async def init_orchestrator_node(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """Bootstrap-sandbox load of `.athanor/qa.yaml` (v2).

    Allocates a short-lived sandbox using the `slim` profile, clones the
    target repo, reads `.athanor/qa.yaml`, destroys the sandbox. Writes
    the raw text and parsed dict into state["qa"] so qa_orchestrator_node
    can run without doing its own clone.

    Phase 2's prebaked image will eliminate this overhead by having qa.yaml
    available in the image. Phase 1 pays ~10s per QA run.
    """
    from athanor.workflows.qa._subtask_prep import prep_qa_sandbox_clone
    from athanor.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

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

    bootstrap_job_id = f"{job_id}::bootstrap"
    base: list[str] = (
        docker._build_base_cmd() if hasattr(docker, "_build_base_cmd") else []
    )

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
    try:
        container_id, sandbox_token = await sandbox_mgr.create(
            bootstrap_job_id, profile=profile, env=env
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
            base=base,
        )
        yaml_text = await docker.run_cmd(
            docker.build_exec_cmd(
                container_id, ["cat", "/workspace/.athanor/qa.yaml"]
            ),
            timeout=10,
        )
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
    return {"qa": qa}


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
    """
    from athanor.workflows.qa.qa_yaml_resolve import resolve_app_config
    from athanor.workflows.qa.qa_yaml_v2 import QAYamlConfigV2, parse_qa_yaml_v2
    from athanor.workflows.qa.subtask_result_schema import overall_status as compute_overall

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

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    qa_app_results_repo = configurable.get("qa_app_results_repo")

    # 1. Load workspace provider.
    repo_root = Path(state.get("repo_root") or "/workspace")
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

    # 2. Validate qa.yaml apps: vs workspace.
    errors, warnings = validate_against_qa_config(provider, cfg)
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
    apps = resolve_apps_to_qa(provider, cfg, changed_files=changed_files)
    qa_state["apps_to_qa"] = list(apps)
    qa_state["validation_warnings"] = warnings

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
    resolved_configs = [
        resolve_app_config(name, cfg, app_local=None) for name in apps
    ]

    # 5. Fan out.
    per_app_results = await fan_out_subtasks(
        resolved_configs,
        parent_run_id=state["job_id"],
        repo=state.get("repo") or "",
        branch_name=state.get("branch_name"),
        user_env_vars=state.get("user_env_vars"),
        max_concurrent=cfg.parallelism.max_concurrent_apps,
        config=config or {},
    )

    # 6. Persist (idempotent upsert).
    if qa_app_results_repo is not None:
        for r in per_app_results:
            try:
                await qa_app_results_repo.upsert(state["job_id"], r)
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
        "passed" if overall == "passed"
        else "failed" if overall in ("failed", "infra_error")
        else "pending"
    )

    logger.info(
        "qa_run_complete",
        parent_run_id=state["job_id"],
        overall_status=overall,
        apps=apps,
    )

    return {"qa": qa_state}
