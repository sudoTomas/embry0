"""Single choke point for recording an answer to an agent question.

Every answer channel — the dashboard's job- and issue-scoped endpoints,
Telegram reply-to-answer, GitHub ``/answer N:`` comment directives, and the
embry0 MCP ``job_answer`` tool — funnels through :func:`record_answer` so the
side effects stay consistent regardless of where the answer came from:

1. Persist the answer on the ``issue_inputs`` row.
2. Mirror it cross-channel (edit the Telegram question message when the
   answer arrived from a different surface).
3. Leave the decision trail as a comment on the linked tracker issue
   (RAV-1076 — questions and their answers must be auditable on the
   ticket; #163 made this tracker-generic — Linear and GitLab alike).

Resume logic deliberately stays at the call sites: job-scoped and
issue-scoped resumes differ and each already carries its own idempotency
guard. This module owns only the record-and-echo side effects.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog

from embry0.notifications.dispatcher import notify_answer_cross_channel

logger = structlog.get_logger(__name__)


#: The statuses of a job that ended without finishing its work: it never goes on, and nothing reads its answers.
UNFINISHED_ENDS = frozenset({"cancelled", "failed", "expired", "skipped"})


def start_again_says(job_id: str, trigger_labels: Sequence[str] = ()) -> str:
    """How a person works on a job's issue again once the job cannot go on (#521).

    Reopening the issue on the tracker starts nothing by itself: a new run is
    the job's "Run again" (on the dashboard, or ``POST
    /api/v1/jobs/{job_id}/run-again``), or the issue's trigger label removed and
    added again on an open issue.
    """
    labels = " or ".join(f"`{label}`" for label in trigger_labels if label)
    relabel = f"the trigger label {labels}" if labels else "its trigger label"
    return (
        f"to work on it again, run the job again (Run again on the dashboard, or POST /api/v1/jobs/{job_id}/run-again) "
        f"or remove and re-add {relabel} on its issue, reopened first if it is closed"
    )


def ended_job_takes_no_answer(job: dict[str, Any] | None, trigger_labels: Sequence[str] = ()) -> str | None:
    """Why an answer to a question of ``job`` is refused, when the job ended unfinished (#521); None otherwise.

    A cancelled, failed, expired or skipped job never goes on, and reopening
    its issue does not bring it back, so an answer to it would be taken and
    then do nothing. A completed job's questions still take answers: a
    person's answer over the orchestrator's own (RAV-1059), or to a question
    that did not hold the job, reaches the issue's next run.
    """
    status = str((job or {}).get("status") or "")
    if status not in UNFINISHED_ENDS:
        return None
    job_id = str((job or {}).get("job_id"))
    return f"Job {job_id} has ended ({status}), so its questions take no more answers; " + start_again_says(
        job_id, trigger_labels
    )


async def answer_refused_for_its_job(inp: dict[str, Any], jobs_repo: Any) -> str | None:
    """For an answer arriving from a tracker or chat reply: why it is refused, when its job ended unfinished (#521)."""
    if jobs_repo is None or not inp.get("job_id"):
        return None
    return ended_job_takes_no_answer(await jobs_repo.get(inp["job_id"]))


def answers_context(answered: list[dict[str, Any]]) -> str:
    """The questions and their answers, as a resume hands them to the agent: a person's answer, else the
    orchestrator's own."""
    return "\n\n".join(f"Q: {a['question']}\nA: {a.get('answer') or a.get('auto_answer') or ''}" for a in answered)


async def record_answer(
    *,
    inp: dict[str, Any],
    answer: str,
    answered_by: str,
    channel: str,
    inputs_repo: Any,
    issues_repo: Any | None = None,
    tracker_channels: dict[str, Any] | None = None,
    config: Any | None = None,
) -> bool:
    """Persist an answer and fan out its side effects; False, with no side effect, when it was answered already.

    Args:
        inp: The pending ``issue_inputs`` row being answered (needs ``id``;
            ``issue_id``/``job_id``/``question``/``asking_node``/
            ``telegram_message_id`` drive the optional side effects).
        answer: The operator's answer text.
        answered_by: Audit identity — session email, ``key:<name>``,
            ``telegram:<user>``, ``github:<login>``.
        channel: Source surface (``api`` | ``telegram`` | ``github``). The
            Telegram cross-channel edit is skipped for ``telegram`` because
            that route edits its own message.
        inputs_repo: IssueInputsRepository (required — the persist step).
        issues_repo / tracker_channels / config: optional dependencies for
            the echo steps; each step degrades to a no-op when absent.
            ``tracker_channels`` maps tracker name to a
            :class:`~embry0.notifications.tracker_comment.TrackerCommentChannel`.
    """
    if await inputs_repo.answer(inp["id"], answer=answer, answered_by=answered_by) is False:
        # Another answer to the same question was taken first (#499): nothing of this one is said anywhere.
        return False

    if config is not None and channel != "telegram":
        try:
            await notify_answer_cross_channel(
                inputs_repo=inputs_repo,
                inp=inp,
                answer=answer,
                answered_by=answered_by,
                config=config,
            )
        except Exception:
            logger.warning("cross_channel_sync_failed", input_id=inp.get("id"), exc_info=True)

    await _post_tracker_answer(
        inp=inp,
        answer=answer,
        answered_by=answered_by,
        channel=channel,
        issues_repo=issues_repo,
        tracker_channels=tracker_channels,
    )
    return True


async def _post_tracker_answer(
    *,
    inp: dict[str, Any],
    answer: str,
    answered_by: str,
    channel: str,
    issues_repo: Any | None,
    tracker_channels: dict[str, Any] | None,
) -> None:
    """Comment the Q&A pair on the linked tracker issue. Best-effort."""
    if not tracker_channels or issues_repo is None or not inp.get("issue_id"):
        return
    try:
        issue = await issues_repo.get(inp["issue_id"])
        comment_channel = tracker_channels.get(str((issue or {}).get("tracker") or ""))
        if comment_channel is None:
            return
        await comment_channel.post_answer(issue, inp, answer, answered_by=answered_by, channel=channel)
    except Exception:
        logger.warning("tracker_answer_post_failed", input_id=inp.get("id"), exc_info=True)
