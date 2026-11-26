"""A spent account never fails a job: it moves to a ready model, or it waits (spec 2026-10-25, spent account waits).

docs/superpowers/specs/2026-10-25-spent-account-waits-design.md. A pause with
a known end (a quarantine until a time, a run of the job that ended on a spent
or rate-limited account, the local model's slots all taken) is a wait, not a
failure. Where nothing ready can take the run, the job is parked:
``status = paused`` with ``jobs.account_wait`` saying what it waits for, and
:class:`AccountWaitLoop` goes on with it when the wait ends.

- The account pre-check parks a job before it starts (:class:`AccountWaitRequired`).
- An agent run parks a running job from inside its node
  (:class:`AccountWaitInterrupt`, a BaseException like the drain's
  ``ShutdownInterrupt``, so no node's failure path turns it into a failure).
- The executor writes the park and raises :class:`JobParked` out of the
  runners, which their ``except Exception`` failure paths do not see either.

Whether a job waits, and how long, is its ``account_wait`` settings
(:mod:`embry0.settings.sections.account_wait`), read for its repository when
it started, and the time it has waited so far (``jobs.account_waited_seconds``,
summed when each wait ends): :class:`WaitPolicy`. A job that may not wait
(``enabled`` false, or ``max_total_wait_seconds`` spent) still moves to a
ready model; with none, it fails at once with the account's own code
(:func:`account_error_code`): the pre-check raises :class:`AccountSpent`, and
an agent run ends as a run the account refused.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import structlog

from embry0.agents.fallback import Pause
from embry0.safety.error_codes import ErrorCode
from embry0.settings.sections.account_wait import ACCOUNT_WAIT, AccountWaitSettings

logger = structlog.get_logger(__name__)

#: A forced run on the local model that found every slot taken.
LOCAL_BUSY = "local_busy"
#: How long a woken job whose sandbox the proxies would not take back waits before it tries again.
REENROLL_RETRY = timedelta(minutes=5)
#: How often the loop looks at the parked jobs.
POLL_SECONDS = 60.0
#: Where a run's graph config carries its job's :class:`WaitPolicy` (:func:`job_wait_policy`).
CONFIG_KEY = "account_wait"

# The quarantine window CredentialHealth gives each account code, for a pause that names no end.
_WINDOW_ATTRS: dict[str, str] = {
    "rate_limited": "credential_rate_limit_quarantine_seconds",
    "quota_exhausted": "credential_quota_quarantine_seconds",
}

Mode = Literal["resume", "relaunch"]


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=UTC)


@dataclass(frozen=True)
class AccountWait:
    """What a parked job waits for, and until when.

    ``chosen``: the model or key is one a person chose for the job, so it
    never moves to another provider. ``watch``: the pause was read from the
    credential registry, so the loop may go on before ``until`` once the
    registry says the account (or another one) is usable. ``mode`` is how the
    job goes on: ``resume`` from its last checkpoint, ``relaunch`` from its
    launch record.
    """

    provider: str
    reason: str
    until: datetime
    model: str | None = None
    agent: str | None = None
    chosen: bool = False
    watch: bool = False
    mode: Mode = "resume"
    # The answer a person gave the resume that was parked (a Command's value),
    # handed to the resume that goes on; None for none.
    resume_with: Any = None
    # When the job was parked: the account-wait loop adds the time from then to
    # jobs.account_waited_seconds when it goes on. None for a wait not parked yet.
    since: datetime | None = None

    def record(self) -> dict[str, Any]:
        """The JSON ``jobs.account_wait`` holds; ``until`` in UTC ISO."""
        record = {
            "provider": self.provider,
            "reason": self.reason,
            "until": _aware(self.until).astimezone(UTC).isoformat(),
            "model": self.model,
            "agent": self.agent,
            "chosen": self.chosen,
            "watch": self.watch,
            "mode": self.mode,
        }
        if self.resume_with is not None:
            record["resume_with"] = self.resume_with
        if self.since is not None:
            record["since"] = _aware(self.since).astimezone(UTC).isoformat()
        return record

    @classmethod
    def from_record(cls, raw: Any) -> AccountWait | None:
        if not isinstance(raw, Mapping) or not raw.get("provider") or not raw.get("until"):
            return None
        try:
            until = _aware(datetime.fromisoformat(str(raw["until"])))
        except ValueError:
            return None
        try:
            since = _aware(datetime.fromisoformat(str(raw["since"]))) if raw.get("since") else None
        except ValueError:
            since = None
        mode: Mode = "relaunch" if raw.get("mode") == "relaunch" else "resume"
        return cls(
            provider=str(raw["provider"]),
            reason=str(raw.get("reason") or "paused"),
            until=until,
            model=str(raw["model"]) if raw.get("model") else None,
            agent=str(raw["agent"]) if raw.get("agent") else None,
            chosen=bool(raw.get("chosen")),
            watch=bool(raw.get("watch")),
            mode=mode,
            resume_with=raw.get("resume_with"),
            since=since,
        )

    def with_mode(self, mode: Mode) -> AccountWait:
        return replace(self, mode=mode)

    def with_resume(self, value: Any) -> AccountWait:
        """Keep a person's answer to the parked resume, when it is JSON: the job goes on with it."""
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            return self
        return replace(self, resume_with=value)

    def describe(self) -> str:
        """The wait in words, for the job's events. No time: the page shows ``until`` in the viewer's own."""
        who = self.agent or "the job"
        on = f" on {self.model}" if self.model else ""
        if self.reason == LOCAL_BUSY:
            return f"{who}{on} waits for a free slot on the local model; it tries again in a few minutes."
        why = self.reason.replace("_", " ")
        if self.chosen:
            return (
                f"{who}{on} waits for the {self.provider} account ({why}): a person chose this model or key for "
                "the job, so it does not move to another one. The job goes on when the account is ready again."
            )
        return (
            f"{who}{on} waits for the {self.provider} account ({why}): no other model of the fallback order is "
            "ready. The job goes on when the account is ready again."
        )

    def not_taken(self, why: str) -> str:
        """The wait in words, for the failure of a job that may not take it (``why``, :meth:`WaitPolicy.why_not`)."""
        on = f" for {self.model}" if self.model else ""
        if self.reason == LOCAL_BUSY:
            state = "busy: every slot of the local model is taken"
        else:
            state = f"{self.reason.replace('_', ' ')} until {_aware(self.until).astimezone(UTC).isoformat()}"
        return f"the {self.provider} account{on} is {state}, no other model of the fallback order is ready, and {why}"


def _hours(seconds: int) -> str:
    return f"{round(seconds / 3600, 1):g} h"


@dataclass(frozen=True)
class WaitPolicy:
    """Whether a job may wait for an account now, and how long: its ``account_wait`` values and what it has waited.

    ``waited_seconds`` is ``jobs.account_waited_seconds``: the time the job
    has spent parked for accounts, over every wait, resume and relaunch.
    """

    settings: AccountWaitSettings
    waited_seconds: int = 0

    @property
    def left_seconds(self) -> int:
        """What is left of ``max_total_wait_seconds``."""
        return max(0, self.settings.max_total_wait_seconds - self.waited_seconds)

    @property
    def may_wait(self) -> bool:
        """``enabled``, and the job has not waited ``max_total_wait_seconds`` yet."""
        return self.settings.enabled and self.left_seconds > 0

    def longest(self, seconds: int) -> timedelta:
        """A wait of ``seconds`` cut to what the job has left of its total, when it has any left."""
        left = self.left_seconds
        return timedelta(seconds=min(seconds, left) if left > 0 else seconds)

    def why_not(self) -> str:
        """Why the job may not wait, for its failure."""
        if not self.settings.enabled:
            return "this job does not wait for an account (account_wait.enabled is false)"
        return (
            f"this job has waited {_hours(self.waited_seconds)} for accounts, its "
            f"account_wait.max_total_wait_seconds ({_hours(self.settings.max_total_wait_seconds)})"
        )


def account_error_code(reason: str) -> ErrorCode:
    """The code a run that may not wait fails with: the one its account's state is.

    The local model's slots all taken is ``ERR_LOCAL_MODEL_BUSY``, a rate
    limit ``ERR_PROVIDER_RATE_LIMITED``, and any other spent account (a usage
    window, an xAI spending limit) ``ERR_PROVIDER_QUOTA_EXHAUSTED``.
    """
    if reason == LOCAL_BUSY:
        return ErrorCode.LOCAL_MODEL_BUSY
    if reason == "rate_limited":
        return ErrorCode.PROVIDER_RATE_LIMITED
    return ErrorCode.PROVIDER_QUOTA_EXHAUSTED


def job_wait_policy(configurable: Mapping[str, Any]) -> WaitPolicy:
    """The :class:`WaitPolicy` of the job a run belongs to, as its graph config carries it.

    The executor puts it there for every run and every resume
    (``_execute_workflow_stream``): the job's own ``account_wait`` values,
    read off its state, and what it has waited, read off its row. The config
    reaches every node and every qa app's sub-graph, whose states are built
    fresh. A config without it (a graph run outside the executor) reads the
    defaults and nothing waited, what every job had before the section.
    """
    policy = configurable.get(CONFIG_KEY)
    return policy if isinstance(policy, WaitPolicy) else WaitPolicy(ACCOUNT_WAIT.defaults())


class AccountWaitRequired(Exception):
    """The account pre-check found the job's provider spent with nothing to move to."""

    def __init__(self, wait: AccountWait) -> None:
        super().__init__(wait.describe())
        self.wait = wait


class AccountSpent(Exception):
    """The account pre-check found the job's provider spent with nothing to move to, and the job may not wait.

    Its ``account_wait.enabled`` is false, or it has waited its
    ``max_total_wait_seconds``, so it fails before any sandbox exists, with
    the account's own code (:func:`account_error_code`), which the runners'
    failure paths read off the exception.
    """

    def __init__(self, wait: AccountWait, policy: WaitPolicy) -> None:
        self.wait = wait
        self.error_code = account_error_code(wait.reason)
        super().__init__(f"{self.error_code.value}: {wait.not_taken(policy.why_not())}")


class AccountWaitInterrupt(BaseException):
    """An agent run stopped its graph inside the node: its provider is spent with nothing to move to.

    A BaseException, like ``ShutdownInterrupt``: the job is not failing, so
    none of the nodes' ``except Exception`` paths may see it. LangGraph keeps
    the last checkpoint, and the node runs again when the job goes on.
    """

    def __init__(self, wait: AccountWait) -> None:
        super().__init__(wait.describe())
        self.wait = wait


class JobParked(BaseException):
    """The job was parked to wait for an account; its runner stops here without failing it."""

    def __init__(self, job_id: str, wait: AccountWait) -> None:
        super().__init__(f"job {job_id} waits for the {wait.provider} account until {wait.until.isoformat()}")
        self.job_id = job_id
        self.wait = wait


def wait_for(
    pause: Pause | None,
    config: Any,
    now: datetime,
    *,
    policy: WaitPolicy,
    model: str | None = None,
    agent: str | None = None,
    chosen: bool = False,
    watch: bool = False,
) -> AccountWait | None:
    """The wait a pause means, or None when it has no known end or has ended.

    A pause with no time of its own ends after the quarantine window its
    reason has (rate limit, spent usage window); any other reason without an
    end (no key, a disabled key, a refused token) is not a wait: the job
    fails as it did. An end further out than the job's
    ``account_wait.max_wait_seconds`` is cut to it, and to what is left of its
    ``max_total_wait_seconds``. Whether the job may wait at all
    (``policy.may_wait``) is its caller's to ask: a wait that may not be taken
    is still what says the run fails, and with which code.
    """
    if pause is None:
        return None
    until = pause.until
    if until is None:
        attr = _WINDOW_ATTRS.get(pause.reason)
        if attr is None:
            return None
        until = now + timedelta(seconds=int(getattr(config, attr, 0) or 0))
    until = min(_aware(until), now + policy.longest(policy.settings.max_wait_seconds))
    if until <= now:
        return None
    return AccountWait(pause.provider, pause.reason, until, model=model, agent=agent, chosen=chosen, watch=watch)


def local_busy_wait(model: str, agent: str | None, now: datetime, *, policy: WaitPolicy) -> AccountWait:
    """A forced run on the local model that found no slot: try again after the job's ``local_busy_retry_seconds``,
    or what is left of its total."""
    retry = policy.longest(policy.settings.local_busy_retry_seconds)
    return AccountWait("custom", LOCAL_BUSY, now + retry, model=model, agent=agent)


def main_provider(agent_definitions: Iterable[Mapping[str, Any]], model_policy: Mapping[str, Any] | None) -> str:
    """The provider most of a job's agents run on: the default model's when one is set,
    else the one most agent definitions name (anthropic on a tie, and with none)."""
    from embry0.agents.providers import model_provider

    default_model = (model_policy or {}).get("default_model")
    if isinstance(default_model, str) and default_model.strip():
        return model_provider(default_model.strip())
    counts: dict[str, int] = {}
    for row in agent_definitions or ():
        model = row.get("model") if isinstance(row, Mapping) else None
        if isinstance(model, str) and model.strip():
            provider = model_provider(model.strip())
            counts[provider] = counts.get(provider, 0) + 1
    if not counts:
        return "anthropic"
    top = max(counts.values())
    leaders = sorted(p for p, n in counts.items() if n == top)
    return "anthropic" if "anthropic" in leaders else leaders[0]


class _PauseReader(Protocol):
    async def llm_pause(self, repo: str, provider: str) -> Pause | None: ...

    async def pinned_pause(self, job_id: str, provider: str) -> tuple[bool, Pause | None]: ...

    async def job_uses_registry(self, job_id: str, provider: str) -> bool: ...


async def provider_pause(
    resolver: _PauseReader,
    repo: str,
    provider: str,
    *,
    job_id: str = "",
    job_failures: Mapping[str, Pause] | None = None,
) -> tuple[Pause | None, bool, bool]:
    """``(pause, watch, pinned)`` for ``provider`` in this job, read without recording a use.

    A key a person chose for the job (Run with) answers for itself. Otherwise
    the registry: every bound account quarantined or disabled. Otherwise one
    of the job's own runs that ended on a spent or rate-limited account, but
    only while the job's account is not a registry one: a registry account is
    quarantined the moment a run finds it spent, so the registry says when it
    is usable again, and a cleared quarantine or a new account counts at once.
    """
    pinned = False
    if job_id:
        pinned, pause = await resolver.pinned_pause(job_id, provider)
        if pinned:
            return pause, True, True
    pause = await resolver.llm_pause(repo, provider)
    if pause is not None:
        return pause, True, False
    failure = (job_failures or {}).get(provider)
    if failure is not None and job_id and not await resolver.job_uses_registry(job_id, provider):
        return failure, False, False
    return None, False, False


class _Executor(Protocol):
    @property
    def draining(self) -> bool: ...

    def go_on_after_account_wait(self, job: Mapping[str, Any]) -> Any: ...


class _Jobs(Protocol):
    async def account_waiting(self) -> list[dict[str, Any]]: ...

    async def claim_account_wait(self, job_id: str) -> bool: ...


class AccountWaitLoop:
    """Goes on with the parked jobs whose account is ready again (spec 2026-10-25, spent account waits).

    A job is due when its ``until`` has passed, or, for a pause read from the
    registry, as soon as the registry says the provider (or the person's
    chosen key) is usable: a quarantine cleared by hand, an account added.
    """

    def __init__(
        self,
        *,
        jobs: _Jobs,
        executor: _Executor,
        resolver: Any = None,
        poll_seconds: float = POLL_SECONDS,
        clock: Any = None,
    ) -> None:
        self._jobs = jobs
        self._executor = executor
        self._resolver = resolver
        self._poll = poll_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _ready_now(self, job: Mapping[str, Any], wait: AccountWait) -> bool:
        if not wait.watch or self._resolver is None or wait.reason == LOCAL_BUSY:
            return False
        try:
            pause, _watch, _pinned = await provider_pause(
                self._resolver, str(job.get("repo") or ""), wait.provider, job_id=str(job["job_id"])
            )
        except Exception:  # noqa: BLE001 - an unreadable registry waits for the time it named
            logger.warning("account_wait_ready_check_failed", job_id=job.get("job_id"), exc_info=True)
            return False
        return pause is None

    async def tick(self) -> list[str]:
        """One pass: the ids of the jobs it went on with."""
        if self._executor.draining:
            return []
        now = self._clock()
        started: list[str] = []
        for job in await self._jobs.account_waiting():
            wait = AccountWait.from_record(job.get("account_wait"))
            job_id = str(job["job_id"])
            if wait is None:
                logger.warning("account_wait_unreadable", job_id=job_id)
                continue
            due = wait.until <= now or await self._ready_now(job, wait)
            if not due or not await self._jobs.claim_account_wait(job_id):
                continue
            try:
                self._executor.go_on_after_account_wait({**job, "account_wait": wait.record()})
            except Exception:
                logger.exception("account_wait_go_on_failed", job_id=job_id)
                continue
            started.append(job_id)
            logger.info(
                "account_wait_over",
                job_id=job_id,
                provider=wait.provider,
                reason=wait.reason,
                early=wait.until > now,
                mode=wait.mode,
            )
        return started

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        while True:
            try:
                await self.tick()
            except Exception:
                logger.warning("account_wait_tick_failed", exc_info=True)
            await asyncio.sleep(self._poll)
