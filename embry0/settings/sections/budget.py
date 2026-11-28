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
      continue_past_ceiling_max: 5        # continues that may take one job past the ceiling
      refuse_calls_outside_runs: true     # a metering proxy refuses a sandbox's model call outside an agent run

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
typo.

**Calls outside a run.** ``refuse_calls_outside_runs`` is read when a sandbox
is enrolled with the metering proxies (the xai-proxy, and the llm-proxy for a
sandbox with a model server there), so a change reaches the next sandbox. It
replaces the proxies' ``METER_REQUIRE_RUN``, which nothing set, so the proxies
never refused such a call; it is no longer read.

Before this section the five fleet keys lived in the ``budget_config`` row and
their variables. Neither is read any more (:mod:`embry0.settings.env`); the
row is left in place, unread.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from embry0.settings.registry import Section, SectionModel

OverrunMode = Literal["soft", "hard"]


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
            "Most context-audit jobs one round of the schedule (every context_audit.check_minutes) starts for "
            "an organization; the due repositories past it wait for the next round. 0 starts none."
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
    refuse_calls_outside_runs: bool = Field(
        default=True,
        description=(
            "Whether a metering proxy refuses a sandbox's model call made outside a declared agent run "
            "(403 run_not_declared), on the custom provider's /v1 at the llm-proxy and at the xai-proxy (a hosted "
            "provider's path refuses one always). Every model call embry0 makes in a sandbox is an agent run's, "
            "declared before it starts and ended after; a call outside one is no run's, and its charge is "
            "nobody's. Read when a sandbox is enrolled: a change reaches the next one. false forwards such a "
            "call and counts it as the sandbox's calls under no run (Embry0MeterCallsOutsideRuns)."
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
    continue_past_ceiling_max: int = Field(
        default=5,
        ge=0,
        description=(
            "How many times a person's continue may take one job past max_budget_per_job_usd, while "
            "continue_may_exceed_ceiling is true. Once that many have, the ceiling bounds the job's next continues "
            "as continue_may_exceed_ceiling false bounds every one: a continue adds at most what is left under it, "
            "and a job at the ceiling is not offered one. Continues that keep a job under the ceiling count against "
            "agent_questions.retry_rounds_cap alone. A job reads it when it starts and keeps it. 0: no continue may "
            "take a job past the ceiling."
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
)
