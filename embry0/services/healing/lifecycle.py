"""Stage machine for a healing case (#278). Pure: no I/O, no clock.

A case moves through::

    detected → tracked → accepted → pr_open → merged → deployed → verified → closed

The events that drive it arrive from independent sources (watcher tick,
tracker webhook, forge webhook, deploy marker, confirm-healed), so they can
be redelivered, reordered, or skip stages entirely — an out-of-band human
merge takes a ``tracked`` case straight to ``merged``. The rules:

* An event whose stage is **ahead** of the case advances it (``APPLIED``);
  stages in between are skipped.
* An event whose stage **is** the case's stage is a redelivery (``REPEAT``).
* An event whose stage is **behind** the case arrived late (``LATE``). The
  stage holds; the caller may still fill in data the case is missing.
* A deploy is never refused, but it only moves a case when it can
  **anchor**: the case has a merge and the deploy is not older than it —
  the second fact the machine is told. A deploy that cannot (the marker
  beat the merge webhook; the merge is not known at all) is ``NOTED`` and
  kept in the history, from where the merge later picks its anchor.
  Likewise a merge that belongs to a fix attempt a ``reopened`` already
  discarded is ``NOTED``: redelivered, it must not become the retry's merge.
* A verdict needs nothing. A finding can be confirmed healed with no fix
  ever merged (a vendor topped up credits; the error stopped by itself), so
  **``verified`` does not mean "a fix was deployed"**. That question is
  answered by the merge and deploy columns — see :func:`fix_deployed` —
  never by comparing stage ranks.
* ``pr_closed`` (a PR closed unmerged) sends a ``pr_open`` case back to
  ``accepted`` — unless another PR of the case is still open, the one fact
  about the case's data the machine has to be told. ``pr_updated`` (a push
  to, or late news about, a PR the case already has) and ``recurrence``
  (the signature fired again) never move a case. All three are ``NOTED``
  wherever they do not change the stage.
* ``abandoned`` is the honest end of a fix attempt nobody will finish: the
  human chose "abandon" at the retry pause and no PR came of it. It closes
  an ``accepted`` case and is ``NOTED`` anywhere else — a case that already
  has a PR, a merge or a verdict is not abandoned by one job giving up, and
  a case nobody accepted has no attempt to abandon. Only the person's own
  choice raises it: a job that merely *failed* leaves the case ``accepted``,
  because the acceptance still stands and a stalled pipeline is exactly what
  the accepted-case dead-man alert is for (#410).
* ``reopened`` is the other way back, and only from ``verified`` — a
  ``not_healed`` verdict earns the case another fix attempt. It lands on
  ``tracked``, not ``accepted`` (#658): the reopen dispatches nothing, so
  the retry waits for a person's yes exactly as the first attempt did, and
  ``accepted`` keeps meaning "a job should be starting" — the stage the
  accepted-case dead-man alert watches. Accepting the case again is an
  ordinary ``accepted``.
* ``closed`` is terminal but not deaf: whatever still arrives (a cancelled
  ticket fixed out of band weeks later) lands as ``POST_CLOSE`` — history
  only, the case itself frozen. Only ``reopened`` is refused: a regression
  after close is a NEW case linked to this one, never a resurrection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Stage(StrEnum):
    """Where a case is. Declaration order is lifecycle order."""

    DETECTED = "detected"
    TRACKED = "tracked"
    ACCEPTED = "accepted"
    PR_OPEN = "pr_open"
    MERGED = "merged"
    DEPLOYED = "deployed"
    VERIFIED = "verified"
    CLOSED = "closed"


class CaseEvent(StrEnum):
    TRACKER_ATTACHED = "tracker_attached"
    ACCEPTED = "accepted"
    PR_OPENED = "pr_opened"
    PR_UPDATED = "pr_updated"
    PR_CLOSED = "pr_closed"
    MERGED = "merged"
    DEPLOYED = "deployed"
    VERDICT_RECORDED = "verdict_recorded"
    RECURRENCE = "recurrence"
    ABANDONED = "abandoned"
    REOPENED = "reopened"
    CLOSED = "closed"


class Origin(StrEnum):
    WATCHER = "watcher"
    HUMAN = "human"
    WEBHOOK = "webhook"
    ALERT = "alert"
    CI = "ci"
    # ADR 0011: a need of an organization, filed so the awaiting-decision
    # list shows it; answered by dismissing or acting on the need.
    ORG = "org"


class ResolutionPath(StrEnum):
    EMBRY0 = "embry0"
    OUT_OF_BAND = "out_of_band"
    CANCELLED = "cancelled"
    NON_CODE = "non_code"
    SELF_RESOLVED = "self_resolved"
    # #410: accepted, attempted, given up — no fix, and not because the
    # finding went away. Distinct from CANCELLED (the draft was cancelled
    # before anyone accepted it) and from SELF_RESOLVED (it stopped by
    # itself): somebody looked and decided there was nothing to ship.
    ABANDONED = "abandoned"


class Verdict(StrEnum):
    PENDING = "pending"
    HEALED = "healed"
    NOT_HEALED = "not_healed"
    INCONCLUSIVE = "inconclusive"
    REGRESSED = "regressed"


class SignatureSource(StrEnum):
    """How the watcher chose a case's ``signature_id`` (#657).

    NULL on a case the watcher did not anchor: other origins, cases with no
    signature, and every case opened before this was recorded.
    """

    # A window cluster the proposal's own evidence quotes.
    EVIDENCE = "evidence"
    # Nothing the proposal quotes is a line of the window: the window's
    # loudest line (#290's rule), which says nothing about this finding, so
    # confirm-healed answers ``inconclusive`` for it.
    FALLBACK = "fallback"


class Effect(StrEnum):
    """How an event landed on a case."""

    APPLIED = "applied"  # the stage changed
    REPEAT = "repeat"  # redelivery of the event that produced this stage
    LATE = "late"  # the case is already past this event's stage
    NOTED = "noted"  # an event that stands for no stage, recorded as it is
    POST_CLOSE = "post_close"  # arrived after close: history only


@dataclass(frozen=True)
class Transition:
    stage: Stage
    effect: Effect
    # The stage the event stands for, whether or not the case moved there:
    # a LATE event still dates ``<target>_at`` when nothing else has.
    target: Stage | None = None
    # The case moved to an EARLIER stage (pr_closed, reopened): the first
    # <stage>_at is kept then, so funnel timings survive a closed PR. A
    # reopen writes its own `tracked_at` (the service's call, not this one's).
    backward: bool = False


class IllegalTransitionError(ValueError):
    """The event cannot apply to a case at this stage."""

    def __init__(self, stage: Stage, event: CaseEvent) -> None:
        super().__init__(f"event {event.value!r} is not legal for a case at stage {stage.value!r}")
        self.stage = stage
        self.event = event


# P1.6: a case holds a proposal-cap slot exactly while it is in one of these.
OPEN_UNACCEPTED_STAGES: tuple[Stage, ...] = (Stage.DETECTED, Stage.TRACKED)

_ORDER: dict[Stage, int] = {stage: rank for rank, stage in enumerate(Stage)}

# The stage each forward event lands a case on.
_TARGET: dict[CaseEvent, Stage] = {
    CaseEvent.TRACKER_ATTACHED: Stage.TRACKED,
    CaseEvent.ACCEPTED: Stage.ACCEPTED,
    CaseEvent.PR_OPENED: Stage.PR_OPEN,
    CaseEvent.MERGED: Stage.MERGED,
    CaseEvent.DEPLOYED: Stage.DEPLOYED,
    CaseEvent.VERDICT_RECORDED: Stage.VERIFIED,
    CaseEvent.ABANDONED: Stage.CLOSED,
    CaseEvent.CLOSED: Stage.CLOSED,
}


def fix_deployed(*, merge_sha: str | None, merged_at: datetime | None, deployed_at: datetime | None) -> bool:
    """Whether a merged fix for this case is known to be running.

    THE definition — consumers must use it (the API exposes it as
    ``fix_deployed`` and filters on it) instead of reading anything into the
    stage: a case reaches ``verified`` or ``closed`` without any fix when it
    resolved itself or was resolved outside the code. A deploy older than
    the merge cannot contain it, so it never counts.
    """
    return merge_sha is not None and merged_at is not None and deployed_at is not None and deployed_at >= merged_at


def transition(
    stage: Stage | str,
    event: CaseEvent | str,
    *,
    other_open_prs: bool = False,
    deploy_anchors: bool = True,
    merge_current: bool = True,
) -> Transition:
    """Resolve ``event`` against a case at ``stage``.

    The guards are the only facts about a case's data the machine needs.
    ``other_open_prs`` matters to ``pr_closed`` alone: the case has an open
    PR besides the one that closed. ``deploy_anchors`` matters to
    ``deployed`` alone: the case has a merge and this deploy is not older
    than it. ``merge_current`` matters to ``merged`` alone: the merge belongs
    to the current fix attempt, not to one a ``reopened`` already discarded
    (a redelivery of the failed attempt's merge must not become the retry's).

    Raises :class:`IllegalTransitionError` when the event cannot apply, and
    plain ``ValueError`` for a stage or event outside the vocabulary.
    """
    stage = Stage(stage)
    event = CaseEvent(event)
    target = _TARGET.get(event)

    if stage is Stage.CLOSED:
        if event is CaseEvent.CLOSED:
            return Transition(Stage.CLOSED, Effect.REPEAT, target)
        if event is CaseEvent.REOPENED:
            raise IllegalTransitionError(stage, event)
        return Transition(Stage.CLOSED, Effect.POST_CLOSE, target)

    if event is CaseEvent.REOPENED:
        if stage is Stage.VERIFIED:
            return Transition(Stage.TRACKED, Effect.APPLIED, backward=True)
        if stage is Stage.TRACKED:
            return Transition(Stage.TRACKED, Effect.REPEAT)
        raise IllegalTransitionError(stage, event)

    if event is CaseEvent.PR_CLOSED:
        if stage is Stage.PR_OPEN and not other_open_prs:
            return Transition(Stage.ACCEPTED, Effect.APPLIED, backward=True)
        return Transition(stage, Effect.NOTED)

    if event is CaseEvent.ABANDONED:
        # Only an accepted case has a fix attempt to give up. Past pr_open the
        # change request is out there and outlives the job that opened it;
        # before accepted nothing was ever dispatched. Both are history, not
        # a close — the guard is the stage itself, so no caller can pass a
        # flag that closes a case with a live PR.
        if stage is Stage.ACCEPTED:
            return Transition(Stage.CLOSED, Effect.APPLIED, Stage.CLOSED)
        return Transition(stage, Effect.NOTED)

    if event in (CaseEvent.RECURRENCE, CaseEvent.PR_UPDATED):
        return Transition(stage, Effect.NOTED)

    if event is CaseEvent.DEPLOYED and not deploy_anchors:
        return Transition(stage, Effect.NOTED)
    if event is CaseEvent.MERGED and not merge_current:
        return Transition(stage, Effect.NOTED)

    if target is None:  # pragma: no cover — every remaining event stands for a stage
        raise IllegalTransitionError(stage, event)
    if _ORDER[target] > _ORDER[stage]:
        return Transition(target, Effect.APPLIED, target)
    if target is stage:
        return Transition(stage, Effect.REPEAT, target)
    return Transition(stage, Effect.LATE, target)
