"""Sub-task state definition and state-construction helpers.

Extracted from subtask_nodes.py to isolate the pure data-shape / helper
responsibilities from the LangGraph node functions.

Exports:
  - SubTaskState       — LangGraph TypedDict for one per-app sub-task run.
  - initial_state_for_app(...)  — build the initial state dict.
  - _synth_v1_qa_yaml(resolved) — map v2 ResolvedAppConfig onto v1 qa.yaml dict.
  - _build_job_json_payload(...)  — assemble the job.json content dict.
"""

from __future__ import annotations

import time
from typing import Any, TypedDict

from athanor.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
from athanor.workflows.qa.subtask_result_schema import (
    SubTaskResult,
    SubTaskStatus,
)


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
