"""Structured error codes for job failures.

Enables dashboard failure-bucketing, audit querying, and consistent error
logging across the pipeline. Always store the enum *value* (a stable string)
in the DB — never the name — so codes are stable across refactors.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from embry0.safety.xai_denials import XaiDenial, classify_xai_denial


class ErrorCode(StrEnum):
    """Canonical error codes for job.error_code column.

    Add new codes here as they are classified. Existing values must remain
    stable (they appear in DB rows and dashboards).
    """

    AGENT_TIMEOUT = "ERR_AGENT_TIMEOUT"  # Agent SDK call exceeded budget_seconds
    NO_RESULT = "ERR_NO_RESULT"  # Sandbox infrastructure returned no ResultMessage (pre-executor-refactor path). On the new AgentExecutor path, use AGENT_NO_RESULT instead.
    BUDGET_OVERRUN = "ERR_BUDGET_OVERRUN"  # Hard budget cap hit
    BUDGET_REFUSED = "ERR_BUDGET_REFUSED"  # the dispatch gate refused the job before any agent ran (daily/monthly cap or per-actor rate limit, #331) — distinct from BUDGET_OVERRUN, where the job ran, spent money and was hard-stopped in-run; only a refusal is exempt from rate-limit and attempt counts (embry0/storage/repositories/gate_refusal.py)
    MAX_RETRIES = "ERR_MAX_RETRIES"  # Reviewer rejected past max_feedback_loops
    TRIAGE_MALFORMED = "ERR_TRIAGE_MALFORMED"  # Triage output unusable after one repair retry (#455)
    ORPHANED = "ERR_ORPHANED"  # Orchestrator restart with in-flight job
    INTERRUPTED_BY_SHUTDOWN = "ERR_INTERRUPTED_BY_SHUTDOWN"  # an orchestrator shutdown (deploy, restart) stopped the job and boot could not resume it — a QA or onboard job, or one with no checkpoint or launch record (#307)
    RESUME_LIMIT = "ERR_RESUME_LIMIT"  # the job was already recovered MAX_RESUME_ATTEMPTS times after unclean orchestrator stops; it may be what takes the orchestrator down, so boot fails it instead of resuming it again (#307)
    WORKFLOW_UNKNOWN = "ERR_WORKFLOW_UNKNOWN"  # Referenced workflow not in registry
    SANDBOX_INIT = "ERR_SANDBOX_INIT"  # Sandbox container setup failed
    SANDBOX_GONE = (
        "ERR_SANDBOX_GONE"  # a paused job's sandbox container no longer exists or is stopped; dispatch the job again
    )
    DOCKER_TIMEOUT = "ERR_DOCKER_TIMEOUT"  # Underlying docker command timed out
    MAX_AGENT_QUESTIONS = "ERR_MAX_AGENT_QUESTIONS"  # Agent exceeded the ask_user round cap
    MAX_TRIAGE_QUESTIONS = "ERR_MAX_TRIAGE_QUESTIONS"  # Triage interrupt/resume loop exceeded the 5-round cap
    UNSUPPORTED_CONTEXT = "ERR_UNSUPPORTED_CONTEXT"  # Unknown context type — no workspace-init strategy registered
    LOCAL_CONTEXT_DENIED = "ERR_LOCAL_CONTEXT_DENIED"  # local context path outside LOCAL_CONTEXT_ALLOWLIST or over caps
    HTTP_FETCH_FAILED = "ERR_HTTP_FETCH_FAILED"  # http context source unreachable, private-address, or over size cap
    TEMPLATE_INVALID = "ERR_TEMPLATE_INVALID"  # Explicitly requested pipeline template invalid, no fallback (RAV-601)
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

    TRIAGE_QA_ACTION_INVALID = "ERR_TRIAGE_QA_ACTION_INVALID"  # after a QA failure triage's qa_failure_action failed schema validation twice (the original answer + the one repair retry that fed the validation error back) — distinct from QA_FAILURES_UNRESOLVED, where the loop ran out of rounds

    # #299 — orchestrator-run base-vs-head test gate
    VERIFY_NEW_TEST_FAILURES = "ERR_VERIFY_NEW_TEST_FAILURES"  # the repo's configured test command fails on the job's head in a way it does not fail on the base commit (a test, or a whole test target, that is green on base and red on head — after the head re-run). Failures that are red on base too, or pass when re-run, never produce this code.

    # #304 — sandbox runner config transport
    SANDBOX_IMAGE_STALE = "ERR_SANDBOX_IMAGE_STALE"  # the sandbox image's runner predates --config-stdin AND the agent config is too large for the argv fallback — rebuild the named image (prebaked QA / profile images are not covered by the standard deploy recipe)
    SANDBOX_EXEC_STALLED = "ERR_SANDBOX_EXEC_STALLED"  # docker exec never read the agent config from stdin within the hand-off window (wedged exec / daemon); the runner was killed before any agent work
    # #305 — the duplicate-PR check fails closed
    PR_LISTING_FAILED = "ERR_PR_LISTING_FAILED"  # the forge could not list the repo's open pull requests before the job started, so the duplicate-PR check could not run; the job stops instead of risking a second PR for the same ticket

    # #354 — the runner died before its first event
    SANDBOX_AGENT_START_FAILED = "ERR_SANDBOX_AGENT_START_FAILED"  # the sandbox could not start the agent: the runner exited non-zero without writing one stdout line, with a Python traceback / import failure on stderr (a missing or inconsistent baked module, a bad interpreter invocation) — distinct from AGENT_NO_RESULT, where the agent ran and returned nothing. The redacted stderr tail is on the run's trace row

    # spec 2026-09-29 — credential registry
    CREDENTIAL_MISSING = "ERR_CREDENTIAL_MISSING"  # no binding, no pool member and no env value for the role/provider; raised before a sandbox exists
    CREDENTIAL_UNAVAILABLE = "ERR_CREDENTIAL_UNAVAILABLE"  # a pinned binding's credential is disabled or quarantined; the job must not bill another account
    PROVIDER_RATE_LIMITED = (
        "ERR_PROVIDER_RATE_LIMITED"  # the account that served the job was rate-limited; CredentialHealth quarantines it
    )
    PROVIDER_QUOTA_EXHAUSTED = "ERR_PROVIDER_QUOTA_EXHAUSTED"  # the account's usage window or quota is spent; CredentialHealth quarantines it longer
    PROVIDER_TOKEN_STALE = "ERR_PROVIDER_TOKEN_STALE"  # the provider could not validate the access token; the xAI token loop force-refreshes it, no quarantine

    # #284 — dispatch asked for over the API
    DISPATCH_FAILED = "ERR_DISPATCH_FAILED"  # API asked for a job and execute() raised before one existed (#284) — response-only, never a jobs.error_code

    # Developer pipeline guard
    DEV_BRANCH_NOT_PUSHED = "ERR_DEV_BRANCH_NOT_PUSHED"  # developer_node finished but its branch was never pushed to origin (LLM forgot the push step) — caught before init_qa hits a generic clone failure

    # RAV-1271 — interrupt hygiene
    EMPTY_INTERRUPT = "ERR_EMPTY_INTERRUPT"  # an ask_user interrupt was reached with zero questions (upstream routing bug) — failed loudly instead of pausing on nothing

    # Spec 1A §3.8 — fail loud on an unregistered repo
    REPO_UNREGISTERED = "ERR_REPO_UNREGISTERED"  # a dispatch named a repo with no `repos` row under any REPO_RENAMES spelling — refused before any job (#360); with DEFAULT_FORGE empty also a job reaching a sandbox for such a repo, rather than cloning from the wrong forge

    # #281 — failure taxonomy. Each of these was a cluster hiding inside
    # ERR_UNKNOWN (or a NULL code) in the 2026-09-20 audit.
    AGENT_MAX_TURNS = "ERR_AGENT_MAX_TURNS"  # agent hit its max_turns cap without finishing (no decide-now recovery, or the recovery failed too)
    AGENT_PROCESS_FAILED = "ERR_AGENT_PROCESS_FAILED"  # the agent CLI subprocess exited non-zero ("Command failed with exit code N") — the cause is in jobs.agent_stderr_tail
    AGENT_IDLE = "ERR_AGENT_IDLE"  # the runner's idle watchdog killed an agent that produced no events
    AGENT_OUTPUT_OVERFLOW = "ERR_AGENT_OUTPUT_OVERFLOW"  # one agent stdout line exceeded the stream reader's limit (asyncio LimitOverrunError; #266 raised the limit)
    SANDBOX_ARGV_TOO_LONG = "ERR_SANDBOX_ARGV_TOO_LONG"  # exec failed with E2BIG — a payload was passed on argv (#304 moved the agent config to stdin)
    STATE_CONCURRENT_UPDATE = "ERR_STATE_CONCURRENT_UPDATE"  # LangGraph InvalidUpdateError — two nodes wrote one un-annotated state key in a superstep
    GRAPH_RECURSION = "ERR_GRAPH_RECURSION"  # LangGraph recursion limit — the graph looped without reaching END
    STORAGE = "ERR_STORAGE"  # a Postgres error escaped the workflow (constraint violation, lost connection)
    UPSTREAM_HTTP = "ERR_UPSTREAM_HTTP"  # an HTTP call to a forge / tracker / provider failed and nothing handled it
    JOB_STATE_CONFLICT = "ERR_JOB_STATE_CONFLICT"  # a job status transition lost a race (StatusTransitionConflict)
    ABANDONED = "ERR_ABANDONED"  # a person chose "abandon" at the max-retries pause (or gave no usable choice)
    AGENT_RUNNER_UNAVAILABLE = (
        "ERR_AGENT_RUNNER_UNAVAILABLE"  # a node ran without an agent runner in its config — orchestrator wiring bug
    )
    MODEL_DISABLED = "ERR_MODEL_DISABLED"  # the job asked for a model the operator has disabled
    SANDBOX_IMAGE_BUILD = "ERR_SANDBOX_IMAGE_BUILD"  # building / resolving a cached sandbox image failed
    WORKSPACE_PROVIDER = "ERR_WORKSPACE_PROVIDER"  # the QA workspace provider failed to produce a workspace
    PROXY_ENROLLMENT = "ERR_PROXY_ENROLLMENT"  # a credential proxy refused to enrol the sandbox
    MCP_SERVER_FAILED = "ERR_MCP_SERVER_FAILED"  # an MCP server the agent depends on failed to start or answer
    QA_FAILED = "ERR_QA_FAILED"  # standalone QA ran and at least one app failed its acceptance criteria
    QA_INFRA = "ERR_QA_INFRA"  # standalone QA could not judge an app: boot / seed / sandbox failure
    QA_INCONCLUSIVE = "ERR_QA_INCONCLUSIVE"  # standalone QA ended with no pass and no hard failure
    QA_CONFIG_INVALID = "ERR_QA_CONFIG_INVALID"  # the stored qa.yaml / job overrides were rejected before fan-out
    QA_NO_VERDICT = "ERR_QA_NO_VERDICT"  # the QA graph ended without a per-app verdict or a reason
    QA_BUDGET_EXHAUSTED = "ERR_QA_BUDGET_EXHAUSTED"  # QA retry budget spent on infra / transient failures
    QA_IDLE = "ERR_QA_IDLE"  # the QA agent idled out
    QA_RESULT_INVALID = "ERR_QA_RESULT_INVALID"  # the QA agent never wrote a valid result.json
    ONBOARD_FAILED = "ERR_ONBOARD_FAILED"  # the onboard pipeline ended without a valid qa.yaml in the config store
    UNEXPECTED_INTERRUPT = (
        "ERR_UNEXPECTED_INTERRUPT"  # a graph that never asks questions (QA, onboard) paused on an interrupt
    )

    # #358 — a turn-cap death must not discard the run
    DEV_RUN_UNFINISHED = "ERR_DEV_RUN_UNFINISHED"  # the developer hit its turn cap before committing; embry0 committed and pushed its work so it survives, but the change is incomplete and was never self-reviewed. Distinct from DEV_BRANCH_NOT_PUSHED, where nothing reached origin at all.

    # #356 — the review verdict fails closed
    REVIEW_VERDICT_UNPARSEABLE = "ERR_REVIEW_VERDICT_UNPARSEABLE"  # the review agent ran WITHOUT erroring but emitted no verdict the router acts on (empty output, prose, a fenced block with no `decision` key, an unknown decision string, `null`, truncated JSON). Distinct from MAX_RETRIES (a real `changes_requested` out of retry budget) and from an errored review, which has no output at all.
    REVIEW_TOOLCHAIN_MISSING = "ERR_REVIEW_TOOLCHAIN_MISSING"  # the reviewer could not run tests because the sandbox has no compiler or test runner. Distinct from MAX_RETRIES: another developer round in the same sandbox cannot install it. The repository's sandbox profile has to change.


class FailureCategory(StrEnum):
    """The small, stable set ``/stats.failure_categories`` groups codes into.

    A category answers "whose problem is this?" so a person (or the healing
    policy) can react to a class of failure without knowing every code.
    """

    INFRA = "infra"  # embry0's own machinery: sandbox, docker, storage, graph, upstream services
    AGENT = "agent"  # the model run itself: turn cap, timeout, crash, unusable output
    VERIFICATION = "verification"  # the work was done and did not pass review / QA
    BUDGET = "budget"  # a spend or dispatch limit stopped the job
    CONFIG = "config"  # the request or stored configuration is invalid — an operator has to change something
    CANCELLED = "cancelled"  # a person stopped it, or never answered
    UNKNOWN = "unknown"  # ERR_UNKNOWN, a NULL code, or a code this build does not know


_C = FailureCategory

# Every ErrorCode has exactly one category (a test walks the enum).
ERROR_CATEGORIES: dict[ErrorCode, FailureCategory] = {
    ErrorCode.AGENT_TIMEOUT: _C.AGENT,
    ErrorCode.NO_RESULT: _C.AGENT,
    ErrorCode.BUDGET_OVERRUN: _C.BUDGET,
    # #331: refused at the gate, before any agent ran. Same category —
    # a person reading /stats wants "a limit stopped it"; which limit, and
    # whether the job had already spent money, is the code's own job to say.
    ErrorCode.BUDGET_REFUSED: _C.BUDGET,
    ErrorCode.MAX_RETRIES: _C.VERIFICATION,
    ErrorCode.TRIAGE_MALFORMED: _C.AGENT,
    ErrorCode.ORPHANED: _C.INFRA,
    ErrorCode.INTERRUPTED_BY_SHUTDOWN: _C.INFRA,
    ErrorCode.RESUME_LIMIT: _C.INFRA,
    ErrorCode.WORKFLOW_UNKNOWN: _C.CONFIG,
    ErrorCode.SANDBOX_INIT: _C.INFRA,
    ErrorCode.SANDBOX_GONE: _C.INFRA,
    ErrorCode.DOCKER_TIMEOUT: _C.INFRA,
    ErrorCode.MAX_AGENT_QUESTIONS: _C.AGENT,
    ErrorCode.MAX_TRIAGE_QUESTIONS: _C.AGENT,
    ErrorCode.UNSUPPORTED_CONTEXT: _C.CONFIG,
    ErrorCode.LOCAL_CONTEXT_DENIED: _C.CONFIG,
    ErrorCode.HTTP_FETCH_FAILED: _C.CONFIG,
    ErrorCode.TEMPLATE_INVALID: _C.CONFIG,
    ErrorCode.UNKNOWN: _C.UNKNOWN,
    ErrorCode.INCOHERENT_CONFIG: _C.CONFIG,
    ErrorCode.MISSING_OAUTH_TOKEN: _C.CONFIG,
    ErrorCode.MISSING_API_KEY: _C.CONFIG,
    ErrorCode.INVALID_CONFIG: _C.CONFIG,
    ErrorCode.AUTH_REJECTED: _C.CONFIG,
    ErrorCode.CREDENTIAL_MISSING: _C.CONFIG,
    ErrorCode.CREDENTIAL_UNAVAILABLE: _C.CONFIG,
    # Same category as AUTH_REJECTED (CONFIG): a rate limit or a spent quota
    # is an account the operator has to wait out or replace, not an infra fault.
    ErrorCode.PROVIDER_RATE_LIMITED: _C.CONFIG,
    ErrorCode.PROVIDER_QUOTA_EXHAUSTED: _C.CONFIG,
    ErrorCode.PROVIDER_TOKEN_STALE: _C.CONFIG,
    ErrorCode.AGENT_KILLED: _C.INFRA,
    ErrorCode.AGENT_NO_RESULT: _C.AGENT,
    ErrorCode.SAFETY_HOOK_FAILED: _C.INFRA,
    ErrorCode.SANDBOX_REQUIRED: _C.INFRA,
    ErrorCode.TOOL_DENIED: _C.AGENT,
    ErrorCode.QA_FAILURES_UNRESOLVED: _C.VERIFICATION,
    ErrorCode.VERIFY_NEW_TEST_FAILURES: _C.VERIFICATION,
    ErrorCode.TRIAGE_QA_ACTION_INVALID: _C.AGENT,
    ErrorCode.SANDBOX_IMAGE_STALE: _C.INFRA,
    ErrorCode.SANDBOX_EXEC_STALLED: _C.INFRA,
    ErrorCode.SANDBOX_AGENT_START_FAILED: _C.INFRA,
    ErrorCode.PR_LISTING_FAILED: _C.INFRA,
    ErrorCode.DISPATCH_FAILED: _C.INFRA,
    ErrorCode.DEV_BRANCH_NOT_PUSHED: _C.AGENT,
    ErrorCode.EMPTY_INTERRUPT: _C.INFRA,
    ErrorCode.REPO_UNREGISTERED: _C.CONFIG,
    ErrorCode.AGENT_MAX_TURNS: _C.AGENT,
    ErrorCode.AGENT_PROCESS_FAILED: _C.AGENT,
    ErrorCode.AGENT_IDLE: _C.AGENT,
    ErrorCode.AGENT_OUTPUT_OVERFLOW: _C.INFRA,
    ErrorCode.SANDBOX_ARGV_TOO_LONG: _C.INFRA,
    ErrorCode.STATE_CONCURRENT_UPDATE: _C.INFRA,
    ErrorCode.GRAPH_RECURSION: _C.INFRA,
    ErrorCode.STORAGE: _C.INFRA,
    ErrorCode.UPSTREAM_HTTP: _C.INFRA,
    ErrorCode.JOB_STATE_CONFLICT: _C.INFRA,
    ErrorCode.ABANDONED: _C.CANCELLED,
    ErrorCode.AGENT_RUNNER_UNAVAILABLE: _C.INFRA,
    ErrorCode.MODEL_DISABLED: _C.CONFIG,
    ErrorCode.SANDBOX_IMAGE_BUILD: _C.INFRA,
    ErrorCode.WORKSPACE_PROVIDER: _C.INFRA,
    ErrorCode.PROXY_ENROLLMENT: _C.INFRA,
    ErrorCode.MCP_SERVER_FAILED: _C.INFRA,
    ErrorCode.QA_FAILED: _C.VERIFICATION,
    ErrorCode.QA_INFRA: _C.INFRA,
    ErrorCode.QA_INCONCLUSIVE: _C.VERIFICATION,
    ErrorCode.QA_CONFIG_INVALID: _C.CONFIG,
    ErrorCode.QA_NO_VERDICT: _C.INFRA,
    ErrorCode.QA_BUDGET_EXHAUSTED: _C.INFRA,
    ErrorCode.QA_IDLE: _C.AGENT,
    ErrorCode.QA_RESULT_INVALID: _C.AGENT,
    ErrorCode.ONBOARD_FAILED: _C.AGENT,
    ErrorCode.UNEXPECTED_INTERRUPT: _C.INFRA,
    # #356: unusable model output, the same call TRIAGE_MALFORMED makes. Not
    # VERIFICATION — nothing was verified; the reviewer's answer was unreadable.
    ErrorCode.REVIEW_VERDICT_UNPARSEABLE: _C.AGENT,
    # The sandbox image is the operator's choice. The model did not fail.
    ErrorCode.REVIEW_TOOLCHAIN_MISSING: _C.CONFIG,
    # #358: the model ran out of turns — the run itself fell short.
    ErrorCode.DEV_RUN_UNFINISHED: _C.AGENT,
}


def category_for_code(code: str | None) -> FailureCategory:
    """Category of a STORED code. Tolerates NULL and values this build does
    not know (a row written by a newer deploy, a hand edit) — one odd row
    must not break ``/stats``.

    KeyError is caught as well as ValueError: a code that IS an ErrorCode
    member but has no ``ERROR_CATEGORIES`` entry passes the enum lookup and
    would otherwise raise out of the dict, 500-ing the endpoint. The
    structural test keeps the map complete in this build; a parallel branch
    adding a member is exactly the case this promise exists for.
    """
    try:
        return ERROR_CATEGORIES[ErrorCode(code or "")]
    except (ValueError, KeyError):
        return FailureCategory.UNKNOWN


def failure_categories(code_counts: Iterable[tuple[str | None, int]]) -> dict[str, int]:
    """``/stats.failure_categories``: failed-job counts per category, from
    ``(stored error_code, count)`` pairs. Every category is present (zero
    included) so a dashboard has a stable shape; NULL and unrecognised codes
    count as ``unknown`` — that bucket is the taxonomy's own health metric."""
    totals = {category.value: 0 for category in FailureCategory}
    for code, count in code_counts:
        totals[category_for_code(code).value] += int(count)
    return totals


# --- The classifier -------------------------------------------------------
#
# ONE place decides a failed job's code, from three kinds of evidence, in
# order of reliability: the exception's type, the failing agent's error text,
# the stage the run died at. Call sites hand over what they have; they do not
# pick codes themselves. ERR_UNKNOWN is the explicit last resort, and it logs
# the exception class and stage so the next audit can close the gap.

# Purpose-built exception classes, by import path. Resolved lazily: this
# module is baked into the sandbox images and must import without the
# orchestration layer (and without import cycles). ORDER MATTERS — subclasses
# before their bases.
_EXCEPTION_CODES: tuple[tuple[str, ErrorCode], ...] = (
    # #307: neither is a failure path's to see (ShutdownInterrupt is a
    # BaseException, ExecutorDraining refuses a resume), but if one ever
    # reaches a job row it says what happened.
    ("embry0.orchestration.restart:ShutdownInterrupt", ErrorCode.INTERRUPTED_BY_SHUTDOWN),
    ("embry0.orchestration.restart:ExecutorDraining", ErrorCode.INTERRUPTED_BY_SHUTDOWN),
    ("embry0.orchestration.budget:BudgetOverrunError", ErrorCode.BUDGET_OVERRUN),
    ("embry0.storage.repositories.repos:UnregisteredRepoError", ErrorCode.REPO_UNREGISTERED),
    ("embry0.storage.repositories.jobs:StatusTransitionConflict", ErrorCode.JOB_STATE_CONFLICT),
    ("embry0.orchestration.state:UnsupportedContextError", ErrorCode.UNSUPPORTED_CONTEXT),
    ("embry0.orchestration.state:TriageParseError", ErrorCode.TRIAGE_MALFORMED),
    ("embry0.workflows.issue_to_pr.plan_route:TemplateInvalidError", ErrorCode.TEMPLATE_INVALID),
    (
        "embry0.workflows.issue_to_pr.nodes._qa_failure_action:QAFailureActionInvalid",
        ErrorCode.TRIAGE_QA_ACTION_INVALID,
    ),
    ("embry0.workflows.qa._glob_match:GlobPatternError", ErrorCode.QA_CONFIG_INVALID),
    ("embry0.workspace_init.base:LocalContextDeniedError", ErrorCode.LOCAL_CONTEXT_DENIED),
    ("embry0.workspace_init.base:HttpFetchError", ErrorCode.HTTP_FETCH_FAILED),
    ("embry0.workspace_init.base:WorkspaceInitError", ErrorCode.SANDBOX_INIT),
    ("embry0.workspace_providers.provider:WorkspaceProviderError", ErrorCode.WORKSPACE_PROVIDER),
    ("embry0.orchestration.nodes.agent:SandboxRequiredError", ErrorCode.SANDBOX_REQUIRED),
    ("embry0.execution.sandbox_manager:SandboxInitError", ErrorCode.SANDBOX_INIT),
    ("embry0.execution.docker_client:ExecStdinTimeoutError", ErrorCode.SANDBOX_EXEC_STALLED),
    ("embry0.execution.tls:CABundleError", ErrorCode.SANDBOX_INIT),
    ("embry0.execution.proxy.enrollment:RegistryFullError", ErrorCode.PROXY_ENROLLMENT),
    ("embry0.execution.auth_provider:AuthConfigError", ErrorCode.INVALID_CONFIG),
    ("embry0.execution.xai_credential:XaiCredentialError", ErrorCode.AUTH_REJECTED),
    ("embry0.execution.qa.presign:PresignAuthError", ErrorCode.QA_INFRA),
    ("embry0.agents.mcp_client:McpClientError", ErrorCode.MCP_SERVER_FAILED),
    # The sandbox image's agent SDK cannot carry the Ring-3 hook (#497): the
    # sandbox cannot run an agent until the image is rebuilt.
    ("embry0.agents.sdk_support:SdkSupportError", ErrorCode.SANDBOX_INIT),
    ("embry0.cache.image_builder:ImageBuildError", ErrorCode.SANDBOX_IMAGE_BUILD),
    ("embry0.cache.lockfile_hash:LockfileNotFoundError", ErrorCode.SANDBOX_IMAGE_BUILD),
    ("embry0.services.model_enablement:DisabledModelError", ErrorCode.MODEL_DISABLED),
    ("embry0.services.github_prs:OpenPRListError", ErrorCode.PR_LISTING_FAILED),
    # #219: a provider model with no price entry would run unmetered.
    ("embry0.services.model_enablement:UnpricedModelError", ErrorCode.INVALID_CONFIG),
    # Third-party / stdlib failures that reached a job row in production.
    ("langgraph.errors:InvalidUpdateError", ErrorCode.STATE_CONCURRENT_UPDATE),
    ("langgraph.errors:GraphRecursionError", ErrorCode.GRAPH_RECURSION),
    ("asyncpg:PostgresError", ErrorCode.STORAGE),
    ("asyncpg:InterfaceError", ErrorCode.STORAGE),
    ("httpx:HTTPError", ErrorCode.UPSTREAM_HTTP),
    ("asyncio:LimitOverrunError", ErrorCode.AGENT_OUTPUT_OVERFLOW),
)

# The Claude CLI's own wording for an account whose usage window is spent
# (#466). It ends the run with a synthetic assistant message, flagged
# ``error == "rate_limit"`` like a transient 429, whose TEXT is the only thing
# that says the account is out until a reset. Verbatim from production:
#   "You're out of extra usage · resets Oct 5, 12am (UTC)"   (2026-10-02)
#   "You're out of extra usage · resets 12:50am (UTC)"       (2026-09-28)
# The other entries are the CLI's sibling messages for the same condition:
# "You're out of usage credits", "Your org is out of usage · …", "Claude AI
# usage limit reached", "You've hit your session limit · resets 3pm (UTC)",
# "You've reached your <model> limit." Matched case-insensitively, after
# folding a typographic apostrophe. Keep this list small and explicit: a
# phrase here turns a short rate-limit quarantine into one that lasts until
# the provider's reset.
USAGE_EXHAUSTED_PHRASES: tuple[str, ...] = (
    "out of extra usage",
    "out of usage",
    "usage limit",
    "you've hit your",
    "you've reached your",
)

# Codes that say the ACCOUNT behind a run failed, not the run's work: the
# credential is quarantined (CredentialHealth), and a job whose last agent
# run died on one is reported under it rather than under the symptom a node
# saw afterwards ("triage output malformed", "branch not pushed").
ACCOUNT_FAILURE_CODES: frozenset[str] = frozenset(
    {
        ErrorCode.PROVIDER_RATE_LIMITED.value,
        ErrorCode.PROVIDER_QUOTA_EXHAUSTED.value,
        ErrorCode.AUTH_REJECTED.value,
        # Not quarantined (CredentialHealth has no reason for it): the xAI token
        # loop force-refreshes the account. Still the account's failure, not the run's.
        ErrorCode.PROVIDER_TOKEN_STALE.value,
    }
)

# What an xAI 403 means for the job and the account (embry0/safety/xai_denials.py).
XAI_DENIAL_CODES: dict[XaiDenial, ErrorCode] = {
    XaiDenial.SPENDING_LIMIT: ErrorCode.PROVIDER_QUOTA_EXHAUSTED,
    XaiDenial.NO_SUBSCRIPTION: ErrorCode.AUTH_REJECTED,
    XaiDenial.STALE_TOKEN: ErrorCode.PROVIDER_TOKEN_STALE,
}


def _fold_case(message: str) -> str:
    """Lower-case ``message`` and fold the typographic apostrophe, so one
    phrase table matches both "you've" and "you\u2019ve" (U+2019)."""
    return message.lower().replace("\u2019", "'")


# Failure text → code. Substrings are matched case-insensitively against text
# embry0 or the agent SDK produced itself, and every entry is a cluster
# observed in production (#281 PR body). A message that embeds the agent's
# own stderr never reaches here: the site that builds one sets its code
# explicitly (AgentRunner's no-final-result return), because a traceback
# that merely mentions a turn cap would otherwise be read as one.
_TEXT_CODES: tuple[tuple[str, ErrorCode], ...] = (
    # Most specific first: a spent usage window before "quota"; both before
    # "rate limit" and "429", which a spent window's message can also carry.
    *((phrase, ErrorCode.PROVIDER_QUOTA_EXHAUSTED) for phrase in USAGE_EXHAUSTED_PHRASES),
    ("quota", ErrorCode.PROVIDER_QUOTA_EXHAUSTED),
    ("rate limit", ErrorCode.PROVIDER_RATE_LIMITED),
    ("429", ErrorCode.PROVIDER_RATE_LIMITED),
    ("error code: 401", ErrorCode.AUTH_REJECTED),
    ("error code: 403", ErrorCode.AUTH_REJECTED),
    # Triage (or any later agent) named a provider the sandbox was not given.
    # The executor's missing-key line is that miss; the container cannot gain
    # the key afterwards.
    ("injected into the sandbox at create", ErrorCode.CREDENTIAL_MISSING),
    ("reached maximum number of turns", ErrorCode.AGENT_MAX_TURNS),
    ("reached max_turns", ErrorCode.AGENT_MAX_TURNS),
    ("idle watchdog", ErrorCode.AGENT_IDLE),
    ("agent timed out after", ErrorCode.AGENT_TIMEOUT),
    ("command failed with exit code", ErrorCode.AGENT_PROCESS_FAILED),
    ("no final result received from sandbox", ErrorCode.AGENT_NO_RESULT),
    ("separator is found, but chunk is longer than limit", ErrorCode.AGENT_OUTPUT_OVERFLOW),
    ("separator is not found, and chunk exceed the limit", ErrorCode.AGENT_OUTPUT_OVERFLOW),
    ("argument list too long", ErrorCode.SANDBOX_ARGV_TOO_LONG),
    ("can receive only one value per step", ErrorCode.STATE_CONCURRENT_UPDATE),
    ("triage_qa_failure_action_invalid", ErrorCode.TRIAGE_QA_ACTION_INVALID),
    ("triage_malformed:", ErrorCode.TRIAGE_MALFORMED),
    ("developer agent did not push branch", ErrorCode.DEV_BRANCH_NOT_PUSHED),
    ("abandoned after", ErrorCode.ABANDONED),
    ("user-initiated retries", ErrorCode.MAX_RETRIES),
    ("no agent runner available", ErrorCode.AGENT_RUNNER_UNAVAILABLE),
    ("sandbox initialization failed", ErrorCode.SANDBOX_INIT),
)

_STAGE_CODES: dict[str, ErrorCode] = {
    "abandoned": ErrorCode.ABANDONED,
    "dev_branch_missing": ErrorCode.DEV_BRANCH_NOT_PUSHED,
    "review_retries_exhausted": ErrorCode.MAX_RETRIES,
    "review_unparseable": ErrorCode.REVIEW_VERDICT_UNPARSEABLE,
    "review_toolchain_missing": ErrorCode.REVIEW_TOOLCHAIN_MISSING,
    "dev_run_unfinished": ErrorCode.DEV_RUN_UNFINISHED,
}


def _resolve(path: str) -> type[BaseException] | None:
    """Import ``module:Class``; None when the module is absent (sandbox
    images carry only part of the package) or the name moved."""
    import importlib

    module_name, _, attr = path.partition(":")
    try:
        obj = getattr(importlib.import_module(module_name), attr, None)
    except Exception:
        return None
    return obj if isinstance(obj, type) and issubclass(obj, BaseException) else None


def _code_for_exception(exc: BaseException) -> ErrorCode | None:
    import errno

    # An exception that knows its own code (SandboxInitError) is authoritative.
    own = getattr(exc, "error_code", None)
    if isinstance(own, ErrorCode):
        return own
    for path, code in _EXCEPTION_CODES:
        cls = _resolve(path)
        if cls is not None and isinstance(exc, cls):
            return code
    if isinstance(exc, OSError) and exc.errno == errno.E2BIG:
        return ErrorCode.SANDBOX_ARGV_TOO_LONG
    if isinstance(exc, RuntimeError) and "not registered" in str(exc):
        return ErrorCode.WORKFLOW_UNKNOWN
    return classify_agent_error(str(exc))


def classify_agent_error(message: str | None) -> ErrorCode | None:
    """Code for a failure TEXT (an agent's ``error_message``, a node's
    ``errors`` entry, ``str(exc)``), or None when it matches no known
    cluster. Never guesses."""
    if not message:
        return None
    # Before the table: an xAI 403 body names its condition, and the table's
    # "error code: 403" would read every one of them as refused credentials.
    denial = classify_xai_denial(message)
    if denial is not None:
        return XAI_DENIAL_CODES[denial]
    lowered = _fold_case(message)
    for needle, code in _TEXT_CODES:
        if needle in lowered:
            return code
    return None


def classify_failure(
    *,
    exc: BaseException | None = None,
    stage: str | None = None,
    agent_error: str | None = None,
    errors: list[str] | tuple[str, ...] = (),
) -> ErrorCode:
    """The code for a failed job, from whatever evidence the caller has.

    Precedence: exception type, then the terminal agent's error text, then the
    node-collected ``errors`` (newest first), then the stage the run died at.
    ERR_UNKNOWN is returned only when all of that says nothing, and is logged
    with the exception class and stage.
    """
    if exc is not None:
        code = _code_for_exception(exc)
        if code is not None:
            return code
    for text in (agent_error, *reversed([str(e) for e in errors])):
        code = classify_agent_error(text)
        if code is not None:
            return code
    code = _STAGE_CODES.get(stage or "")
    if code is not None:
        return code

    import structlog

    structlog.get_logger().error(
        "failure_unclassified",
        exception_class=f"{type(exc).__module__}.{type(exc).__qualname__}" if exc is not None else None,
        stage=stage or None,
        had_agent_error=bool(agent_error),
        error_count=len(errors),
    )
    return ErrorCode.UNKNOWN


def classify_qa_outcome(
    *,
    qa_error_code: str | None = None,
    validation_errors: list[str] | tuple[str, ...] = (),
    infra_apps: list[str] | tuple[str, ...] = (),
    failed_apps: list[str] | tuple[str, ...] = (),
    inconclusive_apps: list[str] | tuple[str, ...] = (),
) -> ErrorCode:
    """The code for a FAILED standalone QA job. These rows had no code at all
    before #281. A code the QA graph set itself wins when it is a real one;
    then a rejected config; then the worst per-app outcome."""
    if qa_error_code:
        try:
            return ErrorCode(qa_error_code)
        except ValueError:
            pass
    if validation_errors:
        return ErrorCode.QA_CONFIG_INVALID
    if infra_apps:
        return ErrorCode.QA_INFRA
    if failed_apps:
        return ErrorCode.QA_FAILED
    if inconclusive_apps:
        return ErrorCode.QA_INCONCLUSIVE
    return ErrorCode.QA_NO_VERDICT


def error_code_for_exception(exc: BaseException) -> ErrorCode:
    """Classify a workflow exception. Kept for callers that have nothing but
    the exception; prefer :func:`classify_failure` and pass the stage."""
    return classify_failure(exc=exc)
