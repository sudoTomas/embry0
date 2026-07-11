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
    NO_RESULT = "ERR_NO_RESULT"  # Sandbox infrastructure returned no ResultMessage (pre-executor-refactor path). On the new AgentExecutor path, use AGENT_NO_RESULT instead.
    BUDGET_OVERRUN = "ERR_BUDGET_OVERRUN"  # Hard budget cap hit
    MAX_RETRIES = "ERR_MAX_RETRIES"  # Reviewer rejected past max_feedback_loops
    TRIAGE_MALFORMED = "ERR_TRIAGE_MALFORMED"  # Triage LLM output failed schema validation
    ORPHANED = "ERR_ORPHANED"  # Orchestrator restart with in-flight job
    WORKFLOW_UNKNOWN = "ERR_WORKFLOW_UNKNOWN"  # Referenced workflow not in registry
    SANDBOX_INIT = "ERR_SANDBOX_INIT"  # Sandbox container setup failed
    DOCKER_TIMEOUT = "ERR_DOCKER_TIMEOUT"  # Underlying docker command timed out
    MAX_AGENT_QUESTIONS = "ERR_MAX_AGENT_QUESTIONS"  # Agent exceeded the ask_user round cap
    MAX_TRIAGE_QUESTIONS = "ERR_MAX_TRIAGE_QUESTIONS"  # Triage interrupt/resume loop exceeded the 5-round cap
    UNSUPPORTED_CONTEXT = "ERR_UNSUPPORTED_CONTEXT"  # Non-git job context not executable yet
    UNKNOWN = "ERR_UNKNOWN"  # Uncategorised — should diminish over time

    # Phase 1 — pluggable agent execution modes
    INCOHERENT_CONFIG = "ERR_INCOHERENT_CONFIG"  # Config combination rejected at resolve time
    MISSING_OAUTH_TOKEN = "ERR_MISSING_OAUTH_TOKEN"  # auth_mode=oauth but no token available
    MISSING_API_KEY = "ERR_MISSING_API_KEY"  # auth_mode=api_key but ANTHROPIC_API_KEY empty
    INVALID_CONFIG = "ERR_INVALID_CONFIG"  # Unknown execution_mode/auth_mode or cli requested before Phase 2
    AUTH_REJECTED = "ERR_AUTH_REJECTED"  # OAuth token / API key refused by upstream
    AGENT_KILLED = "ERR_AGENT_KILLED"  # Subprocess killed externally
    AGENT_NO_RESULT = "ERR_AGENT_NO_RESULT"  # Agent exited with no ResultMessage (new AgentExecutor path; supersedes NO_RESULT for executor-managed failures)
    SAFETY_HOOK_FAILED = "ERR_SAFETY_HOOK_FAILED"  # Hook raised or malformed — fails closed
    SANDBOX_REQUIRED = "ERR_SANDBOX_REQUIRED"  # Sandbox-less in-process execution refused (no fallback by design)
    TOOL_DENIED = "ERR_TOOL_DENIED"  # Safety policy denied a tool call

    # Phase 5 — QA pipeline integration
    QA_FAILURES_UNRESOLVED = "ERR_QA_FAILURES_UNRESOLVED"  # QA failed and triage↔QA loop hit max_qa_failure_rounds (default 2) without a passing run; written when QAStateBlock.failure_rounds is exhausted

    # Developer pipeline guard
    DEV_BRANCH_NOT_PUSHED = "ERR_DEV_BRANCH_NOT_PUSHED"  # developer_node finished but its branch was never pushed to origin (LLM forgot the push step) — caught before init_qa hits a generic clone failure


def error_code_for_exception(exc: Exception) -> ErrorCode:
    """Classify a workflow exception into an ErrorCode. Lazy imports avoid
    import cycles with the orchestration layer."""
    from embry0.orchestration.nodes.agent import SandboxRequiredError
    from embry0.orchestration.state import TriageParseError, UnsupportedContextError

    if isinstance(exc, UnsupportedContextError):
        return ErrorCode.UNSUPPORTED_CONTEXT
    if isinstance(exc, SandboxRequiredError):
        return ErrorCode.SANDBOX_REQUIRED
    if isinstance(exc, TriageParseError):
        return ErrorCode.TRIAGE_MALFORMED
    if isinstance(exc, RuntimeError) and "not registered" in str(exc):
        return ErrorCode.WORKFLOW_UNKNOWN
    if isinstance(exc, RuntimeError) and "Sandbox initialization failed" in str(exc):
        return ErrorCode.SANDBOX_INIT
    return ErrorCode.UNKNOWN
