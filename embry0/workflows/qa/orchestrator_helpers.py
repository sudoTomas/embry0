"""QA orchestrator helper functions and data types.

Extracted from orchestrator.py to isolate pure logic from LangGraph node
functions. This module contains no LangGraph nodes.

Exports:
  - OrchestratorOutcome  — dataclass for the final orchestrator result.
  - _outcome_to_dict(outcome) — serialise OrchestratorOutcome to a plain dict.
  - resolve_apps_to_qa(provider, config, changed_files) — compute apps to run QA on.
  - validate_against_qa_config(provider, config) — validate qa.yaml vs workspace.
  - fan_out_subtasks(...) — run sub-tasks in parallel with a concurrency cap.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from embry0.execution import events
from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
from embry0.workflows.qa.qa_yaml_v2 import QAYamlConfigV2
from embry0.workflows.qa.subtask_graph import run_subtask
from embry0.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)
from embry0.workspace_providers.provider import WorkspaceProvider

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
    no_cascade = frozenset(name for name, entry in config.packages.items() if entry.no_cascade)
    affected = provider.affected(
        changed_files=list(changed_files),
        no_cascade_packages=no_cascade,
    )

    workspace_apps, _ = provider.discover()
    pkg_to_app_name = {a.package_name: a.name for a in workspace_apps}

    affected_app_names = {pkg_to_app_name[pkg] for pkg in affected.apps_to_qa if pkg in pkg_to_app_name}

    declared = set(config.apps.keys())
    return sorted(affected_app_names & declared)


def validate_against_qa_config(
    provider: WorkspaceProvider,
    config: QAYamlConfigV2,
    app_names: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Return (errors, warnings).

    Errors block the orchestrator from fanning out (run becomes infra_error
    with a clear message). Warnings are surfaced in the PR comment but do
    not gate. A message that starts with neither ``error:`` nor
    ``warning:`` is classified as an error (fail closed), so a provider
    that forgets the prefix cannot be ignored.

    ``app_names`` restricts validation to a subset of declared apps — the
    orchestrator passes only ``target: managed`` apps, since deployed apps
    are not workspace packages (EMB-27). Default (None) validates all.
    """
    messages = provider.validate(list(config.apps.keys()) if app_names is None else list(app_names))
    errors: list[str] = []
    warnings: list[str] = []
    for message in messages:
        lowered = message.lower()
        if lowered.startswith("error:"):
            errors.append(message)
        elif lowered.startswith("warning:"):
            warnings.append(message)
        else:
            # Fail closed: a message the provider did not classify used to be
            # dropped here, which let a misconfigured repository pass QA with
            # nothing selected (#65).
            logger.warning(
                "qa_validate_message_unprefixed",
                provider=getattr(provider, "name", type(provider).__name__),
                message=message,
            )
            errors.append(message)
    return errors, warnings


async def fan_out_subtasks(
    resolved_configs: list[ResolvedAppConfig],
    *,
    parent_run_id: str,
    repo: str,
    branch_name: str | None,
    user_env_vars: Any = None,
    agent_models_override: dict[str, str] | None = None,
    image_tag: str | None = None,
    shared_volume_name: str | None = None,
    turbo_remote_enabled: bool = False,
    max_concurrent: int,
    config: dict[str, Any],
    budget_spent: Callable[[], Awaitable[bool]] | None = None,
) -> list[SubTaskResult]:
    """Run sub-tasks in parallel under a concurrency cap.

    ``budget_spent`` — #264. Asked before each sub-task starts; True means
    the job crossed a hard per-job cap, and the sub-task is reported as an
    INFRA_FAILURE without acquiring a sandbox. Sub-tasks already running
    finish their current agent turn, as everywhere else layer 1 applies.

    Returns results in input order. Sub-task crashes are caught and
    surfaced as INFRA_FAILURE rather than propagating — sibling sub-tasks
    are isolated from each other.

    ``shared_volume_name`` — when set (Phase-2 C3), each sub-task mounts
    this pre-warmed volume at ``/workspace`` and skips git clone.

    ``turbo_remote_enabled`` — Phase-2 E1.  When True, TURBO_* env vars are
    injected into each sub-task's sandbox env (sourced from os.environ).
    """
    sem = asyncio.Semaphore(max_concurrent)

    # Live-progress plumbing (QA event streaming): the orchestrator node
    # captures its LangGraph stream writer and threads it down via
    # configurable["qa_parent_writer"] — sub-task lifecycle events emitted
    # through it land in job_logs + the job WebSocket. The QA event bus
    # (SSE, Phase 5C) gets a "running" subtask_status at start so the QA
    # surfaces show apps as soon as they begin, not only at emit_result.
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    writer = configurable.get("qa_parent_writer") or (lambda _event: None)
    bus = configurable.get("qa_event_bus")

    async def _one(resolved: ResolvedAppConfig) -> SubTaskResult:
        async with sem:
            if budget_spent is not None and await _budget_spent_safely(budget_spent, parent_run_id):
                return SubTaskResult(
                    app_name=resolved.app_name,
                    status=SubTaskStatus.INFRA_FAILURE,
                    duration_ms=0,
                    cache_hits=CacheHits(),
                    failure_summary="not started: the job's budget cap was reached (overrun_mode=hard)",
                )
            writer(
                events.qa_subtask_started(
                    app=resolved.app_name,
                    target=resolved.target,
                )
            )
            writer(events.qa_subtask_status(app=resolved.app_name, status="running"))
            if bus is not None:
                try:
                    await bus.publish(
                        parent_run_id,
                        {
                            "type": "subtask_status",
                            "run_id": parent_run_id,
                            "app": resolved.app_name,
                            "status": "running",
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "qa_event_bus_publish_running_failed",
                        parent_run_id=parent_run_id,
                        app=resolved.app_name,
                        error=str(exc),
                    )
            try:
                result = await run_subtask(
                    resolved,
                    parent_run_id=parent_run_id,
                    repo=repo,
                    branch_name=branch_name,
                    user_env_vars=user_env_vars,
                    agent_models_override=agent_models_override,
                    prebaked_image_tag=image_tag,
                    shared_volume_name=shared_volume_name,
                    turbo_remote_enabled=turbo_remote_enabled,
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001
                result = SubTaskResult(
                    app_name=resolved.app_name,
                    status=SubTaskStatus.INFRA_FAILURE,
                    duration_ms=0,
                    cache_hits=CacheHits(),
                    failure_summary=f"sub-task crashed: {exc}",
                )
            writer(
                events.qa_subtask_finished(
                    app=resolved.app_name,
                    status=str(result.status),
                    duration_ms=result.duration_ms,
                    failure_summary=result.failure_summary,
                )
            )
            return result

    tasks = [asyncio.create_task(_one(r)) for r in resolved_configs]
    return await asyncio.gather(*tasks)


async def _budget_spent_safely(budget_spent: Callable[[], Awaitable[bool]], parent_run_id: str) -> bool:
    # A failed lookup must not stop QA: the agent turn's own check still runs.
    try:
        return await budget_spent()
    except Exception as exc:  # noqa: BLE001
        logger.warning("qa_budget_check_failed", parent_run_id=parent_run_id, error=str(exc))
        return False


def _outcome_to_dict(outcome: OrchestratorOutcome) -> dict[str, Any]:
    return {
        "overall_status": outcome.overall_status,
        "apps_to_qa": list(outcome.apps_to_qa),
        "failure_summary": outcome.failure_summary,
        "validation_errors": list(outcome.validation_errors),
        "validation_warnings": list(outcome.validation_warnings),
    }


def gate_deployed_apps(
    config: QAYamlConfigV2,
    deployed_apps: list[str],
    changed_files: list[str] | list[Path],
) -> list[str]:
    """Narrow deployed apps to those the diff is relevant to.

    Deployed apps have no workspace topology, so historically they all ran on
    every QA run. That is still the default: an app with no ``when`` clause, and
    any run with an empty diff (standalone / main runs, where the value is
    liveness rather than regression), keeps the old behaviour.

    An app that DOES declare ``when.changed_paths`` and is evaluated against a
    non-empty diff runs only when a changed file matches one of its globs. This
    lets one repo declare several deployed apps without paying for all of them
    on every job.
    """
    from embry0.workflows.qa._glob_match import compile_glob, match_any

    if not changed_files:
        return list(deployed_apps)

    paths = [str(p) for p in changed_files]
    kept: list[str] = []
    for name in deployed_apps:
        entry = config.apps.get(name)
        when = getattr(entry, "when", None) if entry else None
        if not when or not when.changed_paths:
            kept.append(name)
            continue
        compiled = [compile_glob(g) for g in when.changed_paths]
        if any(match_any(path, compiled) for path in paths):
            kept.append(name)
    return kept
