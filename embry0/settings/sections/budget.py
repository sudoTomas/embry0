"""The ``budget`` section: what embry0 may spend, per job, per day and per month, and who may raise it.

One place for every spending cap. An operator writes it the way they would
say it::

    budget:
      max_budget_per_job_usd: 10   # the ceiling of every job's cap; 0 sets none
      daily_cap_usd: 100           # no dispatch once today's spend reaches it; 0 sets none
      monthly_cap_usd: 500         # the same for this month; 0 sets none
      rate_limit_per_author_per_hour: 5
      overrun_mode: soft           # soft pauses a job at its cap, hard fails it
      context_audit_job_usd: 2     # the unattended jobs' hard caps
      area_map_job_usd: 2
      fix_job_usd: 2
      context_audit_max_jobs_per_tick: 3
      continue_may_exceed_ceiling: false  # a person's continue stays under the ceiling

**No cap.** For the four fleet limits, ``0`` sets no limit, as their
variables and the dispatch gate always read it; a key left out (or taken out
with ``null`` through ``PATCH``) reads its default, the cap a deployment that
never set it had. ``null`` is never a stored cap: a document that writes one
is refused, so an empty value is never read as "no cap". An unattended job's
cap is never unlimited: it takes a number above 0, and its variable's ``0``
or a bad value always read as the default.

**Every per-job cap is bounded by** ``max_budget_per_job_usd`` where a job is
run (``embry0.orchestration.budget.resolve_job_cap``), checked after every
agent turn: an organization may set its own unattended caps, and lowering
the ceiling lowers every one of them at once without refusing what the
organizations stored. The dollar caps have no bound of their own, so with no
ceiling (``0``) nothing bounds them. ``context_audit_max_jobs_per_tick``
keeps the bound of 50 an organization's setting had, as a guard against a
typo; a variable above it is not imported, so it reads the default.

Before this section the five fleet keys lived in the ``budget_config`` row,
which won over the variables once saved; the first start of this release
imports that row, else the variables, once (:mod:`embry0.settings.bootstrap`
with :data:`embry0.settings.earlier.EARLIER_SOURCES`).
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field, model_validator

from embry0.settings.registry import EnvImport, Section, SectionModel

OverrunMode = Literal["soft", "hard"]


def cap_from_env(value: Any) -> float:
    """A fleet cap's variable as the budget's readers read it before this section: a positive, finite
    number is that cap, and anything at or below 0, or not finite (``inf``, ``nan``), is 0, no cap: an
    infinite cap was no cap.

    A value that is no number at all raises, so the import refuses it and the
    key keeps its default, as the old reader fell back to its default.
    """
    number = float(value)
    return number if math.isfinite(number) and number > 0 else 0.0


def limit_from_env(value: Any) -> int:
    """``RATE_LIMIT_PER_AUTHOR_PER_HOUR`` as the dispatch gate read it: a positive number, else 0, no limit."""
    number = int(value)
    return number if number > 0 else 0


class BudgetSettings(SectionModel):
    max_budget_per_job_usd: float = Field(
        default=10.0,
        ge=0,
        description=(
            "The most one job may spend, in US dollars: the ceiling of the cap triage or a job's own "
            "max_budget_usd picks, and of every unattended job's cap below, checked after every agent turn. "
            "0 sets no ceiling. A person's continue past the cap goes on top of it unless "
            "continue_may_exceed_ceiling is false."
        ),
    )
    daily_cap_usd: float = Field(
        default=100.0,
        ge=0,
        description="No job is dispatched once today's spend reaches this, in US dollars. 0 sets no daily cap.",
    )
    monthly_cap_usd: float = Field(
        default=500.0,
        ge=0,
        description="No job is dispatched once this month's spend reaches this, in US dollars. 0 sets no monthly cap.",
    )
    rate_limit_per_author_per_hour: int = Field(
        default=5,
        ge=0,
        description=(
            "Jobs one person may dispatch in any hour. 0 sets no limit. embry0's own watcher and the "
            "continuations of a job a person already started are exempt; the spend caps still apply to them."
        ),
    )
    overrun_mode: OverrunMode = Field(
        default="soft",
        description=(
            "What a job does on crossing its cap: soft lets the step finish, then pauses for a person to "
            "continue, merge or stop it; hard fails it with ERR_BUDGET_OVERRUN. A job may pin itself to hard, "
            "and the unattended jobs always do."
        ),
    )
    context_audit_job_usd: float = Field(
        default=2.0,
        gt=0,
        description=(
            "The hard cap of one job the context audit schedule starts, or a person starts with "
            "POST /orgs/{owner}/context-audit/run, in US dollars."
        ),
    )
    context_audit_max_jobs_per_tick: int = Field(
        default=3,
        ge=0,
        le=50,
        description=(
            "Most context-audit jobs one round of the schedule (every 15 minutes) starts for an organization; "
            "the due repositories past it wait for the next round. 0 starts none."
        ),
    )
    area_map_job_usd: float = Field(
        default=2.0,
        gt=0,
        description=(
            "The hard cap of one job that maps a repository's areas (POST /repos/{repo}/-/areas/map), in US dollars."
        ),
    )
    fix_job_usd: float = Field(
        default=2.0,
        gt=0,
        description=(
            "The hard cap of the job that fixes one context-audit finding an owner accepted from the proposals, "
            "in US dollars."
        ),
    )
    continue_extra_max_usd: float = Field(
        default=1000.0,
        gt=0,
        description=(
            "The most a person may name as extra_budget_usd when they continue a job past its cap "
            "(POST /jobs/{id}/resume), in US dollars."
        ),
    )
    continue_may_exceed_ceiling: bool = Field(
        default=True,
        description=(
            "Whether a person's continue may take a job past max_budget_per_job_usd. false makes the ceiling a "
            "hard limit: a continue adds only what is left under it, and a job already at it cannot be continued."
        ),
    )
    continue_default_extra_usd: float | None = Field(
        default=None,
        gt=0,
        description=(
            "How much more a continue lets a job spend when the person names no amount, in US dollars; "
            "null is one more cap. At most continue_extra_max_usd."
        ),
    )

    @model_validator(mode="after")
    def _default_within_max(self) -> BudgetSettings:
        extra = self.continue_default_extra_usd
        if extra is not None and extra > self.continue_extra_max_usd:
            raise ValueError("continue_default_extra_usd is more than continue_extra_max_usd allows a person to name")
        return self


#: The variable each unattended cap was imported from: one variable served all three jobs.
_UNATTENDED = "context_audit_job_budget_usd"

BUDGET = Section(
    name="budget",
    model=BudgetSettings,
    summary="What embry0 may spend: each job's ceiling, the daily and monthly caps, the unattended jobs' caps, "
    "and how far a person may continue a job past its cap.",
    # An organization may set the caps of the unattended jobs started for its
    # own repositories, as organizations.settings let it set the context
    # audit's two before this section (each copied into the organization's
    # layer once). The fleet limits stay the deployment's: the dispatch gate
    # counts every job of every organization against one daily and one
    # monthly cap, and the ceiling bounds every job, an organization's
    # unattended ones included.
    org_keys=frozenset({"context_audit_job_usd", "context_audit_max_jobs_per_tick", "area_map_job_usd", "fix_job_usd"}),
    repo_keys=frozenset(),
    env={
        "max_budget_per_job_usd": EnvImport("max_budget_usd", cap_from_env),
        "daily_cap_usd": EnvImport("daily_budget_cap_usd", cap_from_env),
        "monthly_cap_usd": EnvImport("monthly_budget_cap_usd", cap_from_env),
        "rate_limit_per_author_per_hour": EnvImport("rate_limit_per_author_per_hour", limit_from_env),
        "overrun_mode": "budget_overrun_mode",
        "context_audit_job_usd": _UNATTENDED,
        "context_audit_max_jobs_per_tick": "context_audit_max_jobs_per_tick",
        "area_map_job_usd": _UNATTENDED,
        "fix_job_usd": _UNATTENDED,
    },
)
