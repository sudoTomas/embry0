"""Structured error codes for job failures.

Enables dashboard failure-bucketing, audit querying, and consistent error
logging across the pipeline. Always store the enum *value* (a stable string)
in the DB — never the name — so codes are stable across refactors.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Canonical error codes for job.error_code column.

    Add new codes here as they are classified. Existing values must remain
    stable (they appear in DB rows and dashboards).
    """

    AGENT_TIMEOUT = "ERR_AGENT_TIMEOUT"  # Agent SDK call exceeded budget_seconds
    NO_RESULT = "ERR_NO_RESULT"  # Sandbox returned no ResultMessage
    BUDGET_OVERRUN = "ERR_BUDGET_OVERRUN"  # Hard budget cap hit
    MAX_RETRIES = "ERR_MAX_RETRIES"  # Reviewer rejected past max_feedback_loops
    TRIAGE_MALFORMED = "ERR_TRIAGE_MALFORMED"  # Triage LLM output failed schema validation
    ORPHANED = "ERR_ORPHANED"  # Orchestrator restart with in-flight job
    WORKFLOW_UNKNOWN = "ERR_WORKFLOW_UNKNOWN"  # Referenced workflow not in registry
    SANDBOX_INIT = "ERR_SANDBOX_INIT"  # Sandbox container setup failed
    DOCKER_TIMEOUT = "ERR_DOCKER_TIMEOUT"  # Underlying docker command timed out
    MAX_AGENT_QUESTIONS = "ERR_MAX_AGENT_QUESTIONS"  # Agent exceeded the ask_user round cap
    UNKNOWN = "ERR_UNKNOWN"  # Uncategorised — should diminish over time
