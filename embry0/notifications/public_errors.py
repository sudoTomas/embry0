"""What a job's failure may say on the ticket it came from (#284).

``jobs.error_message`` is ``str(exc)`` on most failure paths: it carries
filesystem paths, internal hostnames, command lines and now and then a token
or a dollar figure. That is right for the dashboard and wrong for a tracker
comment, which a whole company — or the public — can read and which is posted
under a person's name. The tracker trail therefore states the stable
``error_code`` and one sentence from this table; the detail stays on the job's
dashboard page and in the logs.

Every :class:`~embry0.safety.error_codes.ErrorCode` must resolve to a sentence
(a test walks the enum), and an unknown code degrades to the generic one —
never to the raw message.
"""

from __future__ import annotations

from embry0.safety.error_codes import ErrorCode

_GENERIC = "the job stopped on an internal embry0 error"

_BY_CODE: dict[str, str] = {
    ErrorCode.BUDGET_OVERRUN: "a spend or dispatch limit was reached before the job could finish",
    ErrorCode.BUDGET_REFUSED: "a spend or dispatch limit had already been reached, so the job was not started",
    ErrorCode.AGENT_TIMEOUT: "an agent ran out of its time allowance",
    ErrorCode.DOCKER_TIMEOUT: "the sandbox did not respond in time",
    ErrorCode.MAX_RETRIES: "the change did not pass review within the allowed number of attempts",
    ErrorCode.QA_FAILURES_UNRESOLVED: "the change did not pass QA within the allowed number of attempts",
    ErrorCode.VERIFY_NEW_TEST_FAILURES: "the change breaks automated tests that passed before it",
    ErrorCode.MAX_AGENT_QUESTIONS: "the agents needed more clarification rounds than are allowed",
    ErrorCode.MAX_TRIAGE_QUESTIONS: "triage needed more clarification rounds than are allowed",
    ErrorCode.ORPHANED: "embry0 was restarted while the job was running",
    ErrorCode.INTERRUPTED_BY_SHUTDOWN: "embry0 was shut down while the job was running and could not resume it; run the job again",
    ErrorCode.RESUME_LIMIT: "embry0 restarted several times while the job was running, so it was not resumed again",
    ErrorCode.SANDBOX_INIT: "the sandbox for the job could not be set up",
    ErrorCode.SANDBOX_GONE: "the paused job's sandbox no longer exists; run the job again",
    ErrorCode.SANDBOX_IMAGE_STALE: "the sandbox image is out of date and has to be rebuilt by an operator",
    ErrorCode.SANDBOX_EXEC_STALLED: "the sandbox did not start the agent",
    ErrorCode.PR_LISTING_FAILED: "the repository's open pull requests could not be listed, so the job stopped rather than risk opening a duplicate",
    ErrorCode.SANDBOX_AGENT_START_FAILED: "the sandbox image could not start the agent and has to be checked by an operator",
    ErrorCode.SANDBOX_REQUIRED: "the job could not run because no sandbox was available",
    ErrorCode.DEV_BRANCH_NOT_PUSHED: "the developer agent finished without pushing its branch",
    ErrorCode.REPO_UNREGISTERED: "the repository is not registered in embry0",
    ErrorCode.AUTH_REJECTED: "a model provider rejected embry0's credentials",
    ErrorCode.PROVIDER_TOKEN_STALE: "a model provider's sign-in token had gone stale; embry0 refreshes it, so a retry should work",
    ErrorCode.MISSING_OAUTH_TOKEN: "embry0 is missing a model provider credential",
    ErrorCode.MISSING_API_KEY: "embry0 is missing a model provider credential",
    ErrorCode.TEMPLATE_INVALID: "the requested pipeline template is not valid",
    ErrorCode.TOOL_DENIED: "a safety policy stopped the agent",
    ErrorCode.SAFETY_HOOK_FAILED: "a safety check failed closed",
    ErrorCode.DISPATCH_FAILED: "the job could not be started",
    # #281 — the codes that used to be ERR_UNKNOWN. Anything not listed (graph,
    # storage and transport faults) keeps the generic sentence on purpose: the
    # reader cannot act on it and the detail is on the dashboard.
    ErrorCode.AGENT_MAX_TURNS: "an agent used up its allowed number of steps before finishing",
    ErrorCode.AGENT_PROCESS_FAILED: "an agent stopped unexpectedly",
    ErrorCode.AGENT_IDLE: "an agent stopped responding and was shut down",
    ErrorCode.ABANDONED: "the job was abandoned at the retry prompt",
    ErrorCode.MODEL_DISABLED: "the requested model is not enabled in embry0",
    ErrorCode.QA_FAILED: "the change did not pass QA",
    ErrorCode.QA_INFRA: "QA could not run because the test environment did not start",
    ErrorCode.QA_INCONCLUSIVE: "QA could not reach a verdict",
    ErrorCode.QA_CONFIG_INVALID: "the QA configuration stored for this repository is not valid",
    ErrorCode.QA_NO_APPS: "QA selected no app to run: the branch has no changes against its base and no app is always on",
}


def public_error_message(error_code: str | None) -> str:
    """The publishable sentence for ``error_code`` — never the raw error text."""
    return _BY_CODE.get(error_code or "", _GENERIC)
