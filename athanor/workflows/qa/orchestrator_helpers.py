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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from athanor.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
from athanor.workflows.qa.qa_yaml_v2 import QAYamlConfigV2
from athanor.workflows.qa.subtask_graph import run_subtask
from athanor.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)
from athanor.workspace_providers.provider import WorkspaceProvider


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
