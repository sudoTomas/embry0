"""The ``watcher`` section: whether the watcher starts fixes without a person, and what it need not analyse again.

Each watched stack is a row (``watcher_stacks``, #378) whose settings hold
only what differs from the deployment. For the keys of this section the
deployment's value is the section's (#738): an operator writes::

    watcher:
      auto_accept: false                # true: a stack's drafts that clear the bar below start a fix
      auto_accept_after_hours: 24       # how long a draft waits first, so a person can cancel it
      auto_accept_min_severity: high    # low, medium, high or critical
      auto_accept_min_occurrences: 10   # how often its signature must have been seen
      auto_accept_max_per_day: 2        # most drafts a stack accepts in any 24 hours
      skip_known_hours: 24              # 0: a signature found clean is analysed again on its return

A stack that sets one of these keys in its own row keeps its value; every
other stack reads the section. Each watcher reads it on every tick, so a
change applies to the next tick without a restart. A stack row checks these
keys with this section's types and bounds (``services/watcher_stacks.py``),
so the two cannot disagree on what a value may be.

The section is the deployment's alone: an auto-accepted draft starts a job
that spends from the shared budget, and how long a clean signature is held
bounds what the watcher pays for. A stack's own value is how one stack
differs.

Before this section the six keys were the variables ``WATCHER_AUTO_ACCEPT``,
``WATCHER_AUTO_ACCEPT_AFTER_HOURS``, ``WATCHER_AUTO_ACCEPT_MIN_SEVERITY``,
``WATCHER_AUTO_ACCEPT_MIN_OCCURRENCES``, ``WATCHER_AUTO_ACCEPT_MAX_PER_DAY``
and ``WATCHER_SKIP_KNOWN_HOURS``, which are no longer read
(:mod:`embry0.config_retired`).
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal, get_args

from pydantic import BeforeValidator, Field

from embry0.settings.registry import Section, SectionModel


def _lower(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


#: A finding's severity, as an analysis grades it; read without regard to case or blanks around it.
Severity = Literal["low", "medium", "high", "critical"]
#: Every severity, lowest first.
SEVERITIES: Final[tuple[Severity, ...]] = get_args(Severity)
#: The longest a draft may wait before it is accepted, and a clean signature be held: a year, in hours.
MAX_HOURS: Final = 8760


class WatcherSettings(SectionModel):
    auto_accept: bool = Field(
        default=False,
        description=(
            "Whether each tick accepts the stack's tracked drafts that clear the bar below without a person: "
            "through the path POST /cases/{id}/accept takes, as embry0:auto-accept, each with a case_auto_accepted "
            "audit row. A retry after a fix that did not heal, and a draft an owner declined or snoozed, always "
            "wait for a person. The actor is rate-limited and the spend caps apply to its jobs."
        ),
    )
    auto_accept_after_hours: float = Field(
        default=24.0,
        ge=0,
        le=MAX_HOURS,
        description="How long a draft waits after it is filed before it may be accepted, so a person can cancel it.",
    )
    auto_accept_min_severity: Annotated[Severity, BeforeValidator(_lower)] = Field(
        default="high",
        description="The least severity a draft must have to be accepted: low, medium, high or critical.",
    )
    auto_accept_min_occurrences: int = Field(
        default=10,
        ge=0,
        le=1_000_000,
        description="How many times a draft's log signature must have been seen for it to be accepted.",
    )
    auto_accept_max_per_day: int = Field(
        default=2,
        ge=0,
        le=1000,
        description="The most drafts a stack accepts in any 24 hours; 0 accepts none.",
    )
    skip_known_hours: int = Field(
        default=24,
        ge=0,
        le=MAX_HOURS,
        description=(
            "Read while a stack skips unchanged windows (its skip_unchanged_mode): how long a log signature that "
            "an analysis found no issue in is held. A window whose notable signatures are all held so, or have "
            "an open case, is not analysed (skipped_known), unless one is louder than that analysis read by the "
            "stack's spike factor. 0 holds none."
        ),
    )


WATCHER = Section(
    name="watcher",
    model=WatcherSettings,
    summary="Whether the watcher accepts its drafts without a person and the bar they must clear, and how long a "
    "log signature found clean is not analysed again. A stack's own value of a key wins over the section.",
)
