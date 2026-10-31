"""The ``leads`` section: whether the finders run, which ones, how often, and which backlog tickets are offered.

An operator writes it the way they would say it::

    leads:
      enabled: true
      finders: [ticket, ci, review]  # any of ticket, ci, alert, flaky, review
      interval_hours: 12
      backlog_interval_hours: 24
      max_new_per_repo_per_day: 3
      review_stale_days: 5
      flaky_commits: 10
      offer_min: 50   # a backlog ticket scoring below it is not offered at all
      ask_min: 70     # one offered below it stays on the page, never asked in chat

Leads are off until an operator turns them on. A deployment that existed
before this section, when leads ran by default on every deployment that
left ``LEADS_ENABLED`` unset, keeps them on: the first start of this release
writes ``enabled: true`` when ``LEADS_ENABLED`` is not set and the
deployment held a registered repository, a job or a lead a finder stored
before this release first started (``relied_on`` below,
:mod:`embry0.settings.bootstrap`). A new deployment held none of these then:
its tables did not exist yet.

The two readiness thresholds are two keys rather than one ``readiness``
object, so an organization's layer can set one without restating the other
(a layer replaces a key's value whole: spec 2026-10-29, L2). The labels,
weights and words that score a ticket's readiness stay in the code for now:
they become per-organization vocabulary later (#168).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final, Literal, Protocol, get_args

from pydantic import Field, field_validator

from embry0.settings.registry import EnvImport, ReliedOn, Section, SectionModel

#: A finder the leads loop runs: each stores leads of its own kind.
Finder = Literal["ticket", "ci", "alert", "flaky", "review"]
#: Every finder, in the order the loop runs them.
FINDERS: Final[tuple[Finder, ...]] = get_args(Finder)
#: The shortest intervals the loop ran before the section, whatever the variables said.
MIN_INTERVAL_HOURS: Final = 0.25
MIN_BACKLOG_INTERVAL_HOURS: Final = 1.0

#: What a true ``ReliedOn`` check of ``enabled`` means, as the import's comment and ``import-env`` say it.
EXISTED_BEFORE: Final = (
    "the deployment existed before leads became opt-in "
    "(it held a registered repository, a job or a lead a finder stored before this release first started)"
)


def finders_from_env(value: Any) -> list[Finder]:
    """``LEADS_FINDERS`` as the loop read it before this section: what the import stores.

    Names were read without case or spaces, a name that is no finder was
    ignored (``crack`` and ``weed`` included: the scout stores those), and a
    list naming none ran no finder. So ``CI, bogus`` imports ``[ci]``, and an
    empty variable imports ``[]``.
    """
    return [finder for finder in FINDERS if finder in _names(value)]


def finders_dropped(value: Any) -> list[str]:
    """The names in ``LEADS_FINDERS`` that are no finder, which the import names in a warning."""
    return sorted(_names(value) - set(FINDERS))


def _names(value: Any) -> set[str]:
    return {part.strip().lower() for part in str(value or "").split(",") if part.strip()}


def interval_from_env(value: Any) -> float:
    """``LEADS_INTERVAL_HOURS`` as the loop read it: never more often than every quarter hour."""
    return max(MIN_INTERVAL_HOURS, float(value))


def backlog_interval_from_env(value: Any) -> float:
    """``LEADS_BACKLOG_INTERVAL_HOURS`` as the loop read it: never more often than hourly."""
    return max(MIN_BACKLOG_INTERVAL_HOURS, float(value))


class DeploymentHistory(Protocol):
    """What ``ReliedOn.check`` is given: ``embry0.storage.repositories.deployment_history.DeploymentHistory``."""

    async def existed_before(self, *, lead_kinds: Sequence[str] = ()) -> bool: ...


async def existed_before_leads_were_opt_in(history: DeploymentHistory) -> bool:
    """Whether this deployment ran leads before this section: whether it existed before.

    Until this section, leads ran on every deployment that left
    ``LEADS_ENABLED`` unset, whether or not a finder stored anything, so any
    record of use from before this release first started is the evidence: a
    registered repository, a job, or a lead of a finder's kind (kept after
    its repository is removed). ``history`` holds only what is older than
    that moment; the query is the storage layer's.
    """
    return await history.existed_before(lead_kinds=FINDERS)


class LeadsSettings(SectionModel):
    enabled: bool = Field(
        default=False,
        description=(
            "Run the finders on their schedule, on the repositories someone answers for (owners set, or a watched "
            "stack), and offer what they find as owner proposals. While false the loop does nothing; "
            "POST /leads/run still runs a pass by hand, and the leads already stored stay until answered."
        ),
    )
    finders: tuple[Finder, ...] = Field(
        default=FINDERS,
        description=(
            "The finders that run, each named once: ticket (backlog tickets nobody labelled for embry0), "
            "ci (checks failing on the default branch), alert (dependency and code-scanning alerts), "
            "flaky (a check that failed and then passed on one commit), review (review threads nobody answered). "
            "An empty list runs none."
        ),
    )
    interval_hours: float = Field(
        default=6.0,
        ge=MIN_INTERVAL_HOURS,
        le=8760,
        description="Hours between passes of the finders; the backlog intake keeps its own interval.",
    )
    backlog_interval_hours: float = Field(
        default=24.0,
        ge=MIN_BACKLOG_INTERVAL_HOURS,
        le=8760,
        description="Hours between backlog intakes (ticket), which read Linear once for every repository.",
    )
    max_new_per_repo_per_day: int = Field(
        default=5,
        ge=0,
        le=1000,
        description=(
            "Most new leads each finder adds per repository in a day, the most severe first; "
            "a lead already stored is always kept up to date. 0 adds none."
        ),
    )
    review_stale_days: int = Field(
        default=3, ge=0, le=365, description="Days a review thread waits for an answer before it is a lead."
    )
    flaky_commits: int = Field(
        default=10, ge=1, le=50, description="Newest commits of the default branch the flaky-check finder reads."
    )
    offer_min: int = Field(
        default=40,
        ge=0,
        le=100,
        description=(
            "The lowest readiness (0 to 100) a backlog ticket must score to be stored as a lead at all; "
            "one below it is never offered."
        ),
    )
    ask_min: int = Field(
        default=60,
        ge=0,
        le=100,
        description=(
            "The lowest readiness (0 to 100) at which an offered backlog ticket is asked in chat. One below it "
            "is held: left out of the chat feed and never sent as an ask, shown on the page only, folded, its "
            "brief written after the others, and its readiness marked on its decision card."
        ),
    )

    @field_validator("finders")
    @classmethod
    def _finders(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("name each finder once")
        return value


LEADS = Section(
    name="leads",
    model=LeadsSettings,
    summary="Whether the finders run, which ones and how often, and which backlog tickets are offered and asked.",
    # What one repository's pass does is its organization's to choose: which
    # finders read it, how many new leads it takes a day, when a review
    # counts as stale, how far back flakiness is read (on the organization's
    # own credential), and which backlog tickets are offered and asked, since
    # organizations write tickets differently. Whether the one loop runs, and
    # how often, stay the deployment's. No repository keys: which
    # repositories a background feature reads is the repository scope rule's
    # (#165).
    org_keys=frozenset(
        {"finders", "max_new_per_repo_per_day", "review_stale_days", "flaky_commits", "offer_min", "ask_min"}
    ),
    repo_keys=frozenset(),
    env={
        "enabled": EnvImport(
            "leads_enabled",
            relied_on=ReliedOn(value=True, check=existed_before_leads_were_opt_in, evidence=EXISTED_BEFORE),
        ),
        "finders": EnvImport("leads_finders", finders_from_env, dropped=finders_dropped),
        "interval_hours": EnvImport("leads_interval_hours", interval_from_env),
        "backlog_interval_hours": EnvImport("leads_backlog_interval_hours", backlog_interval_from_env),
        "max_new_per_repo_per_day": "leads_max_new_per_repo_per_day",
        "review_stale_days": "leads_review_stale_days",
        "flaky_commits": "leads_flaky_commits",
        "offer_min": "leads_ticket_min_score",
        "ask_min": "proposals_ticket_min_readiness",
    },
)
