"""Who asked for a job — the fixed service actors for ``jobs.dispatched_by``.

Every path that creates a job records an actor (#281): a person where one is
known (SSO email, API key name, webhook sender), otherwise one of the names
below. ``dispatched_by`` was NULL on all 573 jobs before #256/#332, which left
"who is spending this money" unanswerable.

Service actors carry the reserved ``embry0:`` prefix. ``trackers.models.
actor_of`` refuses a human actor that starts with it, so a display name cannot
impersonate one. (``linear-reconciler`` predates the prefix and stays as it is:
its caps count rows by that exact string, and it is rate-limited, not exempt,
so impersonating it gains nothing.)

Rate limit
----------
``budget_config.rate_limit_per_author_per_hour`` is a per-HUMAN throttle
(layer 3 of the budget gate). An actor in :data:`RATE_LIMIT_EXEMPT` skips that
one layer — the daily and monthly spend caps still apply to every job:

* ``embry0:watcher`` — the log watcher dispatches on its own timer (24 ticks a
  day by default) under its own hard per-job budget (#275/#322). An operator
  setting the per-author limit to 1 would otherwise refuse every tick, because
  the count includes the job being checked; and a manual ``watcher_trigger``
  burst must not starve the scheduled tick.
* ``embry0:auto-retriage`` — the orchestrator re-running triage with
  auto-answered questions folded in. It continues a job a person already paid
  a rate-limit slot for, and is bounded by the triage question-round cap.
* ``embry0:answer-resume`` — a fresh run started because the last blocking
  question was answered on a channel that names nobody (Telegram, tracker
  comment). Same reasoning; answers given through the API carry the person
  instead.
* ``embry0:context-audit`` — the context-audit schedule (ADR 0020). It has
  brakes of its own: at most a few jobs per tick, one live job per
  repository, a hard per-job budget per organization (ADR 0021). The hourly
  limit counted its own burst of starts against it and refused the jobs the
  schedule had just been told it could start, so every repository it reached
  past the limit waited a full interval for nothing.

Anything else that acts WITHOUT a person and is not a continuation (the
Linear reconciler today) is deliberately NOT exempt: the limit is one of its
brakes.
"""

from __future__ import annotations

RESERVED_PREFIX = "embry0:"

WATCHER = f"{RESERVED_PREFIX}watcher"
AUTO_RETRIAGE = f"{RESERVED_PREFIX}auto-retriage"
ANSWER_RESUME = f"{RESERVED_PREFIX}answer-resume"
# A PR-comment follow-up whose comment payload named no author. The author
# gate upstream makes this near-impossible; the row still gets an actor.
PR_COMMENT = f"{RESERVED_PREFIX}pr-comment"
# A pull request review whose trigger named nobody (#30). Rate-limited like
# any other non-continuation actor.
PR_REVIEW = f"{RESERVED_PREFIX}pr-review"
# The context audit schedule (ADR 0020): exempt from the per-author limit, see
# the module docstring; its per-tick cap and per-job budget are its brakes.
CONTEXT_AUDIT = f"{RESERVED_PREFIX}context-audit"
# A watcher draft accepted by the stack's auto-accept bar rather than a person
# (spec 2026-10-25). Rate-limited: it acts without a person and is not a
# continuation, and its own daily cap is not the only brake it should have.
AUTO_ACCEPT = f"{RESERVED_PREFIX}auto-accept"
# The API's master key (``API_KEY``) names no person, and behind the dashboard
# proxy all its requests share the proxy's address, so the key is the actor.
# Rate-limited: it is a person at a keyboard or a script acting for one.
MASTER_KEY = f"{RESERVED_PREFIX}master-key"
# A request with no identity at all: only AUTH_DEV_MODE lets one through.
ANONYMOUS = f"{RESERVED_PREFIX}anonymous"

RATE_LIMIT_EXEMPT: frozenset[str] = frozenset({WATCHER, AUTO_RETRIAGE, ANSWER_RESUME, CONTEXT_AUDIT})


def is_rate_limit_exempt(actor: str | None) -> bool:
    """True when ``actor`` skips the per-author hourly limit (never the
    spend caps). Exact match on a closed set — a prefix test would exempt any
    future service actor by accident."""
    return actor in RATE_LIMIT_EXEMPT


def is_reserved(actor: str | None) -> bool:
    """True when ``actor`` uses the service-actor namespace."""
    return bool(actor) and str(actor).startswith(RESERVED_PREFIX)
