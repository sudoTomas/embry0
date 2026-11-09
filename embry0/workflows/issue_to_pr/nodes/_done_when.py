"""The done-when check after a coding run (spec 2026-10-26, done when).

A coding run that pushed its branch ends its final JSON with a report on each
item of the job's done-when list (``embry0.services.done_when``). This reads
that report strictly and decides what comes next: every item met → the route
goes on (review, QA); an item unmet → the coding step runs again with the
unmet items named, up to the job's ``done_when.retries`` times (once unless an
operator set another; #182); still unmet after that → the job's
``done_when.on_unmet`` (:func:`unmet_action`):

- ``pause`` (the default): the job pauses (``max_retries``, stage
  ``done_when_unmet``) naming them, as every job did before the key;
- ``review``: the route goes on as if they were met, and review judges the
  change against the list and the report, which names them unmet and says
  so (``on_unmet: review``), so the delivered pull request lists them; a
  route with no review step left pauses instead;
- ``fail``: the job ends at stage ``done_when_unmet`` with
  ``ERR_DONE_WHEN_UNMET`` (a run that ended in an error keeps that error as
  its failure); its branch is on origin, so the pull request of a job that
  did not finish is opened for it, or an existing one is marked so.

The report is kept on the list, on state and on the job row, so review and
the job page see it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import structlog
from langgraph.graph import END

from embry0.execution import events
from embry0.services.done_when import (
    after_retries,
    check_report,
    followup_text,
    items_of,
    unmet_summary,
)
from embry0.services.job_settings import job_section
from embry0.settings.sections.done_when import DONE_WHEN, OnUnmet
from embry0.workflows.issue_to_pr.route_plan import review_ahead

logger = structlog.get_logger(__name__)

#: The stage a job pauses (or, with done_when.on_unmet fail, fails) at when its done-when items stay unmet.
UNMET_STAGE = "done_when_unmet"
#: The stage of a done-when retry of the coding step.
RETRY_STAGE = "developer_done_when_retry"


def unmet_action(state: Mapping[str, Any]) -> OnUnmet:
    """What the job does once its done-when retries are spent: its ``done_when.on_unmet``, as it read it at start.

    ``review`` on a route with no review step left (``route_plan.review_ahead``)
    is ``pause``: unmet work is never delivered unjudged. The developer's
    contract says what this returns, so it never promises a review the route
    does not have.
    """
    action = job_section(state, DONE_WHEN).on_unmet
    if action == "review" and not review_ahead(state):
        return "pause"
    return action


def _run_errored(result: dict[str, Any]) -> bool:
    """Whether the coding run ended in an error (its turn cap, a crash): its failure is its own, not the list's."""
    outputs = result.get("agent_outputs")
    last = outputs[-1] if isinstance(outputs, list) and outputs else None
    return isinstance(last, dict) and bool(last.get("is_error"))


def _last_output(result: dict[str, Any]) -> str:
    outputs = result.get("agent_outputs")
    last = outputs[-1] if isinstance(outputs, list) and outputs else None
    if not isinstance(last, dict) or last.get("is_error"):
        return ""
    return str(last.get("output") or "")


async def check_done_when(
    state: dict[str, Any],
    configurable: dict[str, Any],
    result: dict[str, Any],
    done_when: dict[str, Any] | None,
    *,
    writer: Callable[[dict[str, Any]], None],
    agent: str = "developer",
) -> tuple[dict[str, Any], str | None]:
    """``(updates, goto)`` for a coding run that pushed; ``goto`` None lets the route go on.

    A job without a list returns no updates and no goto: nothing changes.
    """
    if not items_of(done_when):
        return {}, None
    report = check_report(done_when, _last_output(result))
    retries = int(state.get("done_when_retries") or 0)
    allowed = job_section(state, DONE_WHEN).retries
    action = unmet_action(state) if report["unmet"] and retries >= allowed else None
    if action == "review":
        # The report says the change went on with these items unmet (#182):
        # the pull request names them when the job delivers it.
        report = {**report, "on_unmet": "review"}
    checked = {**(done_when or {}), "report": report}
    updates: dict[str, Any] = {"done_when": checked, "done_when_followup": None}
    jobs_repo = configurable.get("jobs_repo")
    job_id = str(state.get("job_id") or "")
    if jobs_repo is not None and job_id:
        try:
            await jobs_repo.update(job_id, done_when=checked)
        except Exception:  # noqa: BLE001 - the state's copy still drives the job
            logger.warning("done_when_report_persist_failed", job_id=job_id, exc_info=True)
    total, unmet = len(report["items"]), list(report["unmet"])
    logger.info("done_when_checked", job_id=job_id, items=total, unmet=unmet, reported=report["reported"])
    if not unmet:
        writer(events.progress(message=f"Done when: all {total} items met.", node="developer"))
        return updates, None
    if retries < allowed:
        writer(
            events.progress(
                message=f"Done when: {len(unmet)} of {total} items not met; {agent} goes again on them.",
                node="developer",
            )
        )
        updates.update(
            {
                "done_when_followup": followup_text(checked, report),
                "done_when_retries": retries + 1,
                "current_stage": RETRY_STAGE,
            }
        )
        return updates, "developer"
    summary = unmet_summary(checked, report)
    after = after_retries(allowed)
    if action == "review":
        # done_when.on_unmet: review (#182). The report on the list names the
        # unmet items, and review judges the change against them, after QA
        # where the route puts QA first.
        logger.info("done_when_handed_to_review", job_id=job_id, unmet=unmet)
        writer(
            events.progress(
                message=(
                    f"Done when: still not met{after}: {summary}. The change goes on along its route as it "
                    "stands, and review judges it against the list."
                ),
                node="developer",
            )
        )
        return updates, None
    if action == "fail":
        writer(
            events.progress(
                message=f"Done when: still not met{after}: {summary}. The job fails, as its done_when.on_unmet says.",
                node="developer",
            )
        )
    else:
        writer(events.progress(message=f"Done when: still not met{after}: {summary}", node="developer"))
    entry = f"Done-when items still not met{after}: {summary}"
    updates["current_stage"] = UNMET_STAGE
    if action == "fail":
        from embry0.safety.error_codes import ErrorCode

        # done_when.on_unmet: fail (#182). The job ends here, failed; its
        # branch is on origin, so _open_fallback_pr opens the pull request of
        # a job that did not finish, naming the unmet items. JobState.errors
        # appends, so this returns the new entry alone.
        logger.info("done_when_failed_the_job", job_id=job_id, unmet=unmet)
        updates["errors"] = [entry]
        if not _run_errored(result):
            # A run that ended in an error (its turn cap, a crash) keeps that
            # error as the job's failure: the job is classified by its last
            # agent output, as every failed run is, not as a missed finish line.
            updates["error_code"] = ErrorCode.DONE_WHEN_UNMET.value
        return updates, END
    updates["errors"] = (state.get("errors") or []) + [entry]
    return updates, "max_retries"
