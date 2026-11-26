"""The sentences embry0 writes on a Linear ticket about dispatch (#284).

Kept apart from the dispatch logic so they can be read — and reviewed — as
copy. The reader is a colleague who applied a label, not an operator: every
text says (a) what happened, (b) whether they need to do anything, and
(c) exactly what. No figures, no identities, no internal error text; the
machine attribution is added at the transport (``LinearSyncService.post_comment``).

``auto_retry`` is whether a reconciler in ``on`` mode will look at the ticket
again by itself. It decides the advice: telling someone to re-apply a label is
wrong when embry0 retries on its own, and the only way forward when it does not.
"""

from __future__ import annotations

_RELABEL = "remove the trigger label from this ticket and add it again"
_DASHBOARD_JOB = "start a job for it from the embry0 dashboard"


def dispatched(job_id: str, repo: str, *, agents_note: str = "", dashboard_url: str = "") -> str:
    progress = (
        f"[Follow the job on the embry0 dashboard]({dashboard_url})"
        if dashboard_url
        else ("Progress is on the embry0 dashboard.")
    )
    return (
        f"**Started work on this ticket** — job `{job_id}` on `{repo}` (triage → develop → review → QA)."
        f"{agents_note} You do not need to do anything: questions and the result will be posted here.\n{progress}"
    )


def state_not_dispatchable(state_type: str) -> str:
    return (
        f"**Did not start work on this ticket** because it is in a `{state_type or 'unknown'}` workflow state; "
        "embry0 only picks up tickets that are in a backlog, to-do or in-progress state. "
        f"If you want embry0 to work on it, move the ticket to one of those states, then {_RELABEL}."
    )


def dedupe(code: str, *, job_id: str = "", job_status: str = "", pr_url: str = "", issue_status: str = "") -> str:
    if code == "live_job":
        return (
            f"**Did not start a second job** because embry0 is already working on this ticket "
            f"(job `{job_id}` is {job_status}). You do not need to do anything: the result will be posted here."
        )
    if code == "open_pr":
        return (
            f"**Did not start a new job** because an earlier one already opened a pull request: {pr_url}. "
            "Please review that pull request. If it is closed without merging and you want embry0 to try again, "
            f"{_DASHBOARD_JOB}."
        )
    if code == "existing_pr":
        return (
            f"**Did not start a new job** because job `{job_id}` found an open pull request that already covers "
            "this ticket (linked in its result comment). Please review that pull request. If it is closed without "
            f"merging and you want embry0 to try, {_DASHBOARD_JOB}."
        )
    if code == "work_delivered":
        return (
            f"**Did not start a new job** because job `{job_id}` already finished this ticket (`{job_status}`). "
            f"You do not need to do anything. If you want another attempt anyway, {_DASHBOARD_JOB}."
        )
    return (
        f"**Did not start work on this ticket** because embry0's own record of it is `{issue_status}`, not open. "
        f"If that is wrong, reopen the issue in the embry0 dashboard, then {_RELABEL}."
    )


def unmapped_repo(project: str, *, auto_retry: bool) -> str:
    after = (
        "After that you do not need to do anything: embry0 picks the ticket up by itself within a few minutes."
        if auto_retry
        else f"After that, {_RELABEL}."
    )
    return (
        f"**Did not start work on this ticket** because its Linear project `{project or '(none)'}` does not say "
        "which code repository it belongs to. Someone who manages the project needs to add a line "
        f"`repo: owner/name` to the project summary. {after}"
    )


def unregistered_repo(repo: str, project: str, *, auto_retry: bool) -> str:
    after = (
        "After that you do not need to do anything: embry0 picks the ticket up by itself within a few minutes."
        if auto_retry
        else f"After that, {_RELABEL}."
    )
    return (
        f"**Did not start work on this ticket** because its Linear project `{project or '(none)'}` points at "
        f"the repository `{repo}`, which is not registered in embry0 (`ERR_REPO_UNREGISTERED`): embry0 holds no "
        "QA config, environment variables or secrets for it, so a job there would run without them. If the "
        "repository was renamed or the project names the wrong one, correct the `repo: owner/name` line in the "
        "project summary; otherwise ask the embry0 operator to register the repository. "
        f"{after}"
    )


def organization_removed(repo: str, owner: str, *, auto_retry: bool) -> str:
    after = (
        "After that you do not need to do anything: embry0 picks the ticket up by itself within a few minutes."
        if auto_retry
        else f"After that, {_RELABEL}."
    )
    return (
        f"**Did not start work on this ticket** because the repository `{repo}` belongs to the organization "
        f"`{owner}`, which was removed from embry0 (`ERR_ORGANIZATION_REMOVED`): no job starts on its "
        "repositories. If it should run, ask the embry0 operator to make the organization active again. "
        f"{after}"
    )


def gate_refused(limit: str, *, auto_retry: bool) -> str:
    what = (
        "too many embry0 jobs were started in the last hour"
        if limit == "rate_limit"
        else "embry0's spending limit is used up for now"
    )
    when = "within the hour" if limit == "rate_limit" else "when the limit resets (daily, or at the start of the month)"
    action = (
        f"You do not need to do anything: embry0 will start this ticket by itself {when}."
        if auto_retry
        else f"It will not retry by itself: {when}, {_RELABEL}, or {_DASHBOARD_JOB}. "
        "If it is urgent, ask the embry0 operator to raise the limit."
    )
    return f"**Did not start work on this ticket** because {what}. {action}"


def dispatch_failed(*, auto_retry: bool) -> str:
    action = (
        "You do not need to do anything: embry0 will try again by itself a few times, "
        "and will say so here if it gives up."
        if auto_retry
        else f"To retry, {_RELABEL}. If it fails again, please tell the embry0 operator."
    )
    return (
        "**Could not start work on this ticket** because of a problem on embry0's own side "
        f"(nothing is wrong with the ticket). {action}"
    )


def reconciler_ticket_attempts(attempts: int) -> str:
    return (
        f"**Will not start another job on its own** — embry0 has already started {attempts} job(s) for this "
        f"ticket by itself. If you want another attempt, {_DASHBOARD_JOB} (or {_RELABEL})."
    )


def reconciler_dispatch_failures(failures: int) -> str:
    return (
        f"**Stopped trying to start work on this ticket** — it failed {failures} times in a row because of a "
        "problem on embry0's own side (nothing is wrong with the ticket). Please tell the embry0 operator. "
        f"Once it is fixed, {_DASHBOARD_JOB} (or {_RELABEL})."
    )
