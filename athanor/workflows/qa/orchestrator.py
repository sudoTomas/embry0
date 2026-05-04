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

from dataclasses import dataclass, field
from pathlib import Path

from athanor.workflows.qa.qa_yaml_v2 import QAYamlConfigV2
from athanor.workspace_providers.provider import WorkspaceProvider


@dataclass(frozen=True, slots=True)
class OrchestratorContext:
    """Per-run context the orchestrator threads through its nodes."""

    parent_run_id: str
    repo_root: Path
    base_branch: str
    changed_files: list[Path]


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
