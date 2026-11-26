"""The ``account_wait`` section: whether a job waits for a spent account, and for how long.

A job whose LLM account is spent or rate limited moves to a ready model of
the fallback order (``provider_config.fallback_models``); with none, it is
parked until the account's reset and goes on then (spec 2026-10-25, spent
account waits). So is a run that must use the local model while every one of
its slots is taken. An operator writes::

    account_wait:
      enabled: true               # false: a job with nowhere to move fails at once
      max_wait_seconds: 691200    # the furthest a wait's end may be: eight days
      local_busy_retry_seconds: 300

``enabled: false`` is for a team that would rather see the failure than wait
for it. A job still moves to a ready model of the fallback order; with none,
it fails at once with the account's own error code
(``ERR_PROVIDER_QUOTA_EXHAUSTED``, ``ERR_PROVIDER_RATE_LIMITED``, or
``ERR_LOCAL_MODEL_BUSY`` for the local model's slots), as a job did before
waits.

``max_wait_seconds`` cuts a wait whose reset is further out than it: the
job then asks again, and waits again while the account is still spent. A
reset further out than the default, eight days (the cap of the quarantine a
spent usage window gets, ``QUOTA_RESET_QUARANTINE_MAX``), is more likely
misread than real.

How long a team accepts to wait differs, so an organization's layer and a
repository's may set every key. A job reads the section for its repository
once, when it starts (:mod:`embry0.services.job_settings`), and keeps what it
read through every pause and resume: a change reaches the next job that
starts. A job that starts again from its beginning (a relaunch) reads it
again. Before this section the two times were the constants ``WAIT_MAX`` and
``LOCAL_BUSY_RETRY`` of :mod:`embry0.services.account_waits`, and no job
could fail instead of waiting.
"""

from __future__ import annotations

from typing import Final

from pydantic import Field

from embry0.settings.registry import Section, SectionModel

#: The least either time may be: the account-wait loop looks at the parked jobs once a minute.
MIN_SECONDS: Final = 60
#: The furthest ``max_wait_seconds`` may put a wait's end: thirty days.
MAX_WAIT_SECONDS: Final = 30 * 24 * 3600
#: The longest ``local_busy_retry_seconds`` may be: a day.
MAX_LOCAL_BUSY_RETRY_SECONDS: Final = 24 * 3600


class AccountWaitSettings(SectionModel):
    enabled: bool = Field(
        default=True,
        description=(
            "Whether a job whose LLM account is spent or rate limited, or whose run must use the local model "
            "while every slot is taken, waits for it when no model of the fallback order is ready. false: it "
            "still moves to a ready model of the fallback order; with none, it fails at once with the account's "
            "own error code (ERR_PROVIDER_QUOTA_EXHAUSTED, ERR_PROVIDER_RATE_LIMITED, ERR_LOCAL_MODEL_BUSY)."
        ),
    )
    max_wait_seconds: int = Field(
        default=8 * 24 * 3600,
        ge=MIN_SECONDS,
        le=MAX_WAIT_SECONDS,
        description=(
            "The furthest a wait's end may be, in seconds from when the job is parked. A reset further out is "
            "cut to it; the job then asks again, and waits again while the account is still spent."
        ),
    )
    local_busy_retry_seconds: int = Field(
        default=300,
        ge=MIN_SECONDS,
        le=MAX_LOCAL_BUSY_RETRY_SECONDS,
        description=(
            "How long a job whose run found every slot of the local model taken waits before it tries again, "
            "in seconds."
        ),
    )


ACCOUNT_WAIT = Section(
    name="account_wait",
    model=AccountWaitSettings,
    summary="Whether a job waits for a spent LLM account or a busy local model, or fails at once, and how long a "
    "wait may last.",
    org_keys=frozenset({"enabled", "max_wait_seconds", "local_busy_retry_seconds"}),
    repo_keys=frozenset({"enabled", "max_wait_seconds", "local_busy_retry_seconds"}),
)
