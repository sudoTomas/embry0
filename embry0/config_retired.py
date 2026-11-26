"""The variables a one-time import read, and where what they set lives now (#168).

Before a feature's policy moved into a document stored in the database (the
settings document, the provider catalogue), its environment variables were
read at the first start of the release that moved it, once, and copied in.
That import is gone (0.1.0): none of these variables is read any more. For
one release, startup (``settings_env_retired``) and ``embry0 config`` name
each one still set, so an operator can remove it; the config lints count
them as known, not as a typo of a setting. Imports nothing, so the lints can
read it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

_SETTINGS = "the settings document's"
_ENROLLED = "background.{feature} in the settings document (each repository, organization or the deployment)"

#: Each variable no longer read, with where what it set lives now.
MOVED_TO_DOCUMENTS: Final[Mapping[str, str]] = {
    "ASK_USER_ROUNDS_CAP": f"{_SETTINGS} agent_questions.rounds_cap",
    "USER_RETRY_ROUNDS_CAP": f"{_SETTINGS} agent_questions.retry_rounds_cap",
    "ASKS_ENABLED": f"{_SETTINGS} asks.enabled",
    "ASK_HOURS": f"{_SETTINGS} asks.hours",
    "ASK_WEEKDAYS": f"{_SETTINGS} asks.weekdays",
    "ASK_TIMEZONE": f"{_SETTINGS} asks.timezone",
    "ASK_REMIND_HOURS": f"{_SETTINGS} asks.remind_hours",
    "ASK_EXPIRE_DAYS": f"{_SETTINGS} asks.expire_days",
    "ASK_MAX_OPEN_PER_PERSON": f"{_SETTINGS} asks.max_open_per_person",
    "ASK_TICK_SECONDS": f"{_SETTINGS} asks.tick_seconds",
    "CONTEXT_AUDIT_LOCAL_MODEL": f"{_SETTINGS} background_models.context_audit",
    "SCOUT_MODEL": f"{_SETTINGS} background_models.scout",
    "PROPOSAL_BRIEF_MODEL": f"{_SETTINGS} background_models.proposal_brief",
    "JOB_BRIEF_MODEL": f"{_SETTINGS} background_models.job_brief",
    "MAX_BUDGET_USD": f"{_SETTINGS} budget.max_budget_per_job_usd",
    "DAILY_BUDGET_CAP_USD": f"{_SETTINGS} budget.daily_cap_usd",
    "MONTHLY_BUDGET_CAP_USD": f"{_SETTINGS} budget.monthly_cap_usd",
    "RATE_LIMIT_PER_AUTHOR_PER_HOUR": f"{_SETTINGS} budget.rate_limit_per_author_per_hour",
    "BUDGET_OVERRUN_MODE": f"{_SETTINGS} budget.overrun_mode",
    "CONTEXT_AUDIT_JOB_BUDGET_USD": f"{_SETTINGS} budget.context_audit_job_usd, area_map_job_usd and fix_job_usd",
    "CONTEXT_AUDIT_MAX_JOBS_PER_TICK": f"{_SETTINGS} budget.context_audit_max_jobs_per_tick",
    "LEADS_ENABLED": f"{_SETTINGS} leads.enabled",
    "LEADS_FINDERS": f"{_SETTINGS} leads.finders",
    "LEADS_INTERVAL_HOURS": f"{_SETTINGS} leads.interval_hours",
    "LEADS_BACKLOG_INTERVAL_HOURS": f"{_SETTINGS} leads.backlog_interval_hours",
    "LEADS_MAX_NEW_PER_REPO_PER_DAY": f"{_SETTINGS} leads.max_new_per_repo_per_day",
    "LEADS_REVIEW_STALE_DAYS": f"{_SETTINGS} leads.review_stale_days",
    "LEADS_FLAKY_COMMITS": f"{_SETTINGS} leads.flaky_commits",
    "LEADS_TICKET_MIN_SCORE": f"{_SETTINGS} leads.offer_min",
    "PROPOSALS_TICKET_MIN_READINESS": f"{_SETTINGS} leads.ask_min",
    "SCOUT_TEMPLATES": f"{_SETTINGS} scout.templates",
    "SCOUT_MAX_IN_FLIGHT": f"{_SETTINGS} scout.max_in_flight",
    "SCOUT_REVISIT_HOURS": f"{_SETTINGS} scout.revisit_hours",
    "SCOUT_MAX_LEADS_PER_JOB": f"{_SETTINGS} scout.max_leads_per_job",
    "SCOUT_REPOS": _ENROLLED.format(feature="scout"),
    "SCOUT_EXCLUDE": _ENROLLED.format(feature="scout"),
    "SLOP_METER_INTERVAL_SECONDS": f"{_SETTINGS} slop_meter.enabled and interval_hours",
    "SLOP_METER_API_CALLS_PER_TICK": f"{_SETTINGS} slop_meter.api_calls_per_tick",
    "SLOP_METER_RATE_RESERVE": f"{_SETTINGS} slop_meter.rate_reserve",
    "SLOP_METER_REPOS": _ENROLLED.format(feature="slop_meter"),
    "SLOP_METER_EXCLUDE": _ENROLLED.format(feature="slop_meter"),
    "OPENAI_COMPATIBLE_PROVIDERS": "the provider catalogue's providers.<name>.enabled",
    "OPENROUTER_CATALOG_REFRESH_HOURS": "the provider catalogue's openrouter.refresh_hours",
}

#: Variables whose setting became a constant (#168, #665), removed before 0.1.0: nothing reads them and nothing
#: imports them. While one is still set, startup and ``embry0 config`` name it as they name the variables above, so
#: an operator hears the same of every variable embry0 no longer reads; neither config lint takes one for a typo.
REMOVED_BEFORE_0_1_0: Final[tuple[str, ...]] = (
    "SCOUT_TICK_SECONDS",
    "PROPOSAL_BRIEFS_PER_TICK",
    "PROPOSAL_BRIEF_INTERVAL_SECONDS",
    "JOB_BRIEFS_PER_TICK",
    "JOB_BRIEF_INTERVAL_SECONDS",
)
