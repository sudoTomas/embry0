"""The done-when check after a coding run (spec 2026-10-26, done when).

A coding run that pushed its branch ends its final JSON with a report on each
item of the job's done-when list (``embry0.services.done_when``). This reads
that report strictly and decides what comes next: every item met → the route
goes on (review, QA); an item unmet → the coding step runs again with the
unmet items named, up to the job's ``done_when.retries`` times (once unless an
operator set another; #182); still unmet after that → the job pauses
(``max_retries``, stage ``done_when_unmet``) naming them. The report is kept on
the list, on state and on the job row, so review and the job page see it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from embry0.execution import events
from embry0.services.done_when import (
    after_retries,
    check_report,
    followup_text,
    items_of,
    unmet_summary,
)
from embry0.services.job_settings import job_section
from embry0.settings.sections.done_when import DONE_WHEN

logger = structlog.get_logger(__name__)

#: The stage a job pauses at when its done-when items stay unmet after the retry.
UNMET_STAGE = "done_when_unmet"
#: The stage of a done-when retry of the coding step.
RETRY_STAGE = "developer_done_when_retry"


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
    retries = int(state.get("done_when_retries") or 0)
    allowed = job_section(state, DONE_WHEN).retries
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
    writer(events.progress(message=f"Done when: still not met{after}: {summary}", node="developer"))
    updates.update(
        {
            "current_stage": UNMET_STAGE,
            "errors": (state.get("errors") or []) + [f"Done-when items still not met{after}: {summary}"],
        }
    )
    return updates, "max_retries"
