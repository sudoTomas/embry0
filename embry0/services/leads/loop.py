"""The leads loop (spec 2026-10-25): every finder, on every repository someone answers for.

A repository is read when it is registered and active and either names
owners in its preferences or is a watched stack: work is only found where
someone will be asked about it. Each pass runs the enabled finders on each
such repository with its organization's credential, stores what they see,
and closes what a whole read no longer sees. The backlog intake runs on its
own, longer interval; it asks every tracker of the registry for the backlog
of the repositories it serves (Linear: every repository, once per pass;
GitHub and GitLab: the repositories on that forge).

Each finder reads the forges it declares (:data:`FINDER_FORGES`): a finder
enabled for a repository on another forge is skipped, and the reason is
recorded on the pass and on the leads status (:meth:`LeadsLoop.status`),
never silently.

Each finder adds at most ``max_new_per_repo_per_day`` new leads per
repository a day, the worst first; a lead already stored is always kept up
to date. Nothing here starts work: an owner's yes does (owner proposals).

The policy is the ``leads`` section of the settings document, read at the
top of every pass and every look of the schedule, so a change applies with
no restart. Whether the loop runs and how often are the deployment's; what
one repository's pass does (its finders, its daily cap, its thresholds, the
words its tickets' readiness is read in) is read through its organization's
layer. The loop always runs: it looks at least every ``TICK_SECONDS``, does
nothing while ``leads.enabled`` is false, and runs a pass exactly
``interval_hours`` after the last one ended, as the loop did before the
section (it slept the interval after each pass): when a pass falls due
before the next look, the loop sleeps only until then.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from embry0.services.areas import Area, area_of_labels, area_of_logs, area_of_paths
from embry0.services.leads import FinderResult, Lead, by_severity, skipped
from embry0.services.leads.alerts import find_alerts
from embry0.services.leads.backlog import Tracked, backlog_leads
from embry0.services.leads.ci import find_failing_checks
from embry0.services.leads.flaky import find_flaky_checks
from embry0.services.leads.gitlab_ci import find_failing_jobs, find_flaky_jobs
from embry0.services.leads.readiness import Readiness
from embry0.services.leads.reviews import find_unanswered_reviews
from embry0.settings.layers import SnapshotSection
from embry0.settings.sections.leads import LeadsSettings
from embry0.trackers.models import BacklogItem, BacklogRead, read_failed

logger = structlog.get_logger(__name__)

#: Seconds after start before the first pass: a deploy must not compete with it.
STARTUP_DELAY_SECONDS = 300.0
#: The longest the schedule sleeps between looks: how soon turning leads on, or a new interval, applies.
TICK_SECONDS = 300.0

#: The forges each finder reads a repository on (#181). ``ticket`` is read on every repository: Linear's backlog
#: serves any, and GitHub's or GitLab's backlog the repositories on that forge.
FINDER_FORGES: dict[str, frozenset[str]] = {
    "ticket": frozenset({"github", "gitlab"}),
    "ci": frozenset({"github", "gitlab"}),
    "flaky": frozenset({"github", "gitlab"}),
    "alert": frozenset({"github"}),
    "review": frozenset({"github"}),
}

#: The trackers that read a repository's backlog by the forge it is on; any other (Linear) serves every repository.
FORGE_TRACKERS = frozenset({"github", "gitlab"})


def not_supported(kind: str, forge: str | None) -> str:
    """Why ``kind`` reads nothing on a repository on ``forge``."""
    reads = " and ".join(sorted(FINDER_FORGES[kind]))
    return f"{kind} reads {reads} repositories; this one is on {forge or 'a forge embry0 does not know'}"


def lead_area(areas: Sequence[Area], lead: Lead) -> str | None:
    """The area of a monorepo a lead is about: its files, else its labels, else what its title names."""
    area = area_of_paths(areas, lead.paths) or area_of_labels(areas, lead.labels) or area_of_logs(areas, lead.title)
    return area.name if area else None


@dataclass
class _Pass:
    """What one pass did on one repository and kind."""

    found: int = 0
    new: int = 0
    capped: int = 0
    gone: int = 0
    skipped: str | None = None
    #: The backlog intake only: each tracker that did not read the repository whole, and why
    #: (``github: the read failed (ReadTimeout)``); none of its leads there closed.
    unread: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v}


@dataclass
class LeadsLoop:
    #: The ``leads`` section of the current snapshot, read per repository at the top of every pass and
    #: every look of the schedule: ``scoped_reader(store, LEADS)`` in production.
    settings: Callable[[], Awaitable[SnapshotSection[LeadsSettings]]]
    leads: Any
    #: The repositories to read, registered and active, with owners or a watched stack, and enrolled in
    #: ``background.leads``: given the section the pass read, so they come from its snapshot (#165).
    targets: Callable[[SnapshotSection[LeadsSettings]], Awaitable[list[str]]]
    #: The client of a repository's forge (GitHub's or GitLab's) with its organization's credential; None when
    #: there is none.
    client_for: Callable[[str], Awaitable[Any]]
    #: The forge a repository is on (``github``, ``gitlab``), or None when nothing says.
    forge_of: Callable[[str], Awaitable[str | None]]
    #: The trackers whose backlogs the intake reads, by name (the tracker registry's, Linear's resolved fresh).
    trackers: Callable[[], Awaitable[Mapping[str, Any]]]
    tracked: Tracked
    trigger_labels: Callable[[], Awaitable[Iterable[str]]]
    #: A repository's areas as embry0 keeps them (``AreasStore.areas_for`` in production), read once per store.
    areas_for: Callable[[str], Awaitable[list[Area]]]
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    startup_delay: float = STARTUP_DELAY_SECONDS
    _last_backlog: datetime | None = None
    #: When the last scheduled pass ended; None before the first.
    _last_pass: datetime | None = None
    #: When the next scheduled pass falls due, as the last look read the section; None while leads are off.
    _due: datetime | None = None
    #: Whether the last look found leads on; None before the first.
    _enabled: bool | None = None
    _noted: dict[tuple[str, str, str], str] = field(default_factory=dict)
    #: What the last pass did on each repository, by kind, and when (the leads status).
    _status: dict[str, dict[str, Any]] = field(default_factory=dict)
    _status_at: datetime | None = None

    def _note(self, repo: str, kind: str, reason: str | None, *, event: str = "leads_finder_skipped") -> None:
        """One log line per standing reason a finder read nothing (or a backlog was not read whole), not one per
        pass."""
        key = (repo, kind, event)
        if reason is None:
            self._noted.pop(key, None)
        elif self._noted.get(key) != reason:
            self._noted[key] = reason
            logger.info(event, repo=repo, kind=kind, reason=reason)

    def backlog_due(self, now: datetime, policy: LeadsSettings) -> bool:
        return self._last_backlog is None or now - self._last_backlog >= timedelta(hours=policy.backlog_interval_hours)

    def pass_due(self, now: datetime, policy: LeadsSettings) -> bool:
        return self._last_pass is None or now - self._last_pass >= timedelta(hours=policy.interval_hours)

    def wait_seconds(self) -> float:
        """How long the schedule sleeps after a look: ``TICK_SECONDS``, or less when a pass falls due sooner."""
        if self._due is None:
            return TICK_SECONDS
        return min(TICK_SECONDS, max(0.0, (self._due - self.clock()).total_seconds()))

    async def scheduled(self) -> dict[str, Any] | None:
        """What the schedule does each time it looks: one pass, when leads are on and one is due.

        Reads the section first, so turning leads on or off, or changing the
        interval, applies at the next look. Returns the pass's summary, or
        None when it ran none.
        """
        section = await self.settings()
        policy = section.deployment()
        if policy.enabled != self._enabled:
            logger.info("leads_on" if policy.enabled else "leads_off", reason="leads.enabled in the settings document")
            self._enabled = policy.enabled
        interval = timedelta(hours=policy.interval_hours)
        if not policy.enabled:
            self._due = None
            return None
        if not self.pass_due(self.clock(), policy):
            self._due = self._last_pass + interval if self._last_pass is not None else None
            return None
        try:
            return await self.tick(section=section)
        finally:
            self._last_pass = self.clock()
            self._due = self._last_pass + interval

    async def tick(
        self,
        repos: Sequence[str] | None = None,
        *,
        backlog: bool | None = None,
        section: SnapshotSection[LeadsSettings] | None = None,
    ) -> dict[str, Any]:
        """One pass, whether leads are on or off; returns what it did.

        ``repos`` limits it, ``backlog`` forces the backlog intake on or off,
        and ``section`` is the policy the caller read this look (else it is
        read now). Each repository's pass follows its organization's values.
        """
        if section is None:
            section = await self.settings()
        now = self.clock()
        targets = list(repos) if repos is not None else await self.targets(section)
        policies = {repo: section.repo(repo) for repo in targets}
        forges = {repo: await self._forge(repo) for repo in targets}
        wanted = [repo for repo in targets if "ticket" in policies[repo].finders]
        run_backlog = bool(wanted) and (self.backlog_due(now, section.deployment()) if backlog is None else backlog)
        read: dict[str, BacklogRead] = {}
        if run_backlog:
            self._last_backlog = now
            read = await self._backlogs(wanted, forges)
        triggers = list(await self.trigger_labels()) if run_backlog else []
        summary: dict[str, Any] = {}
        for repo in targets:
            policy = policies[repo]
            forge = forges[repo]
            try:
                client = await self.client_for(repo)
            except Exception as exc:  # noqa: BLE001 - one repository's credential is its own problem
                logger.info("leads_client_unavailable", repo=repo, error=type(exc).__name__)
                client = None
            done: dict[str, Any] = {}
            if run_backlog and "ticket" in policy.finders:
                result, keep, unread = await self._tickets(repo, read, triggers, policy)
                done["ticket"] = await self._store(repo, "ticket", result, now, policy, keep_open=keep)
                self._note(repo, "ticket", unread, event="leads_backlog_not_whole")
                if unread:
                    done["ticket"]["unread"] = unread
            for kind, by_forge in self._finders(repo, now, policy).items():
                if kind not in policy.finders:
                    continue
                find = by_forge.get(forge or "")
                if find is None:
                    reason = not_supported(kind, forge)
                    self._note(repo, kind, reason)
                    done[kind] = _Pass(skipped=reason).to_dict()
                    continue
                if client is None:
                    self._note(repo, kind, "no forge client with the organization's credential")
                    done[kind] = _Pass(skipped="no forge client").to_dict()
                    continue
                try:
                    result = await find(client)
                except Exception as exc:  # noqa: BLE001 - one finder's failure is its own log line
                    logger.warning("leads_finder_failed", repo=repo, kind=kind, error=type(exc).__name__)
                    # Skipped with why, so the pass and the status say it; nothing of it closes.
                    result = skipped(read_failed(exc))
                done[kind] = await self._store(repo, kind, result, now, policy)
            summary[repo] = done
            self._status[repo] = {"forge": forge, "at": now.isoformat(), "kinds": done}
        if repos is None:
            # A whole pass: a repository that left the leads leaves the status too.
            self._status = {repo: entry for repo, entry in self._status.items() if repo in policies}
        self._status_at = now
        logger.info("leads_pass", repos=len(targets), backlog=run_backlog)
        return summary

    def status(self) -> dict[str, Any]:
        """The leads status: when a pass last ran, and what it did on each repository it read, by kind, a skipped
        finder with the reason (``skipped``). Each repository keeps the last pass that read it."""
        return {
            "last_pass_at": self._status_at.isoformat() if self._status_at else None,
            "repos": {repo: dict(entry) for repo, entry in self._status.items()},
        }

    async def _forge(self, repo: str) -> str | None:
        try:
            return await self.forge_of(repo)
        except Exception as exc:  # noqa: BLE001 - an unknown forge is skipped by name, not fatal
            logger.info("leads_forge_unread", repo=repo, error=type(exc).__name__)
            return None

    def _finders(
        self, repo: str, now: datetime, policy: LeadsSettings
    ) -> dict[str, dict[str, Callable[[Any], Awaitable[FinderResult]]]]:
        """Each finder but ``ticket``, by the forge it reads (:data:`FINDER_FORGES`), given the forge's client."""
        finders: dict[str, dict[str, Callable[[Any], Awaitable[FinderResult]]]] = {
            "ci": {
                "github": lambda c: find_failing_checks(c, repo),
                "gitlab": lambda c: find_failing_jobs(c, repo),
            },
            "alert": {"github": lambda c: find_alerts(c, repo)},
            "flaky": {
                "github": lambda c: find_flaky_checks(c, repo, commits=policy.flaky_commits),
                "gitlab": lambda c: find_flaky_jobs(c, repo, commits=policy.flaky_commits),
            },
            "review": {
                "github": lambda c: find_unanswered_reviews(c, repo, now=now, stale_days=policy.review_stale_days)
            },
        }
        return finders

    async def _backlogs(self, repos: Sequence[str], forges: Mapping[str, str | None]) -> dict[str, BacklogRead]:
        """Each tracker's backlog read, for the repositories it serves: Linear (any tracker not a forge) every one,
        GitHub and GitLab those on their forge. A tracker whose read fails reads nothing whole, and says why.

        Linear is read first, then the forges by name: the order their tickets of one severity are stored in when a
        day's cap cuts them, as before the registry (Linear's, then GitHub's)."""
        trackers = await self.trackers()
        read: dict[str, BacklogRead] = {}
        for name, tracker in sorted(trackers.items(), key=lambda pair: (pair[0] in FORGE_TRACKERS, pair[0])):
            serves = [repo for repo in repos if name not in FORGE_TRACKERS or forges.get(repo) == name]
            if not serves or tracker is None:
                continue
            try:
                # A forge's issues are read with the organization's own credential, as its finders read.
                read[name] = await tracker.list_backlog(serves, client_for=self.client_for)
            except Exception as exc:  # noqa: BLE001 - the other trackers' backlogs still go
                logger.warning("leads_backlog_unread", tracker=name, error=type(exc).__name__, detail=str(exc)[:200])
                read[name] = BacklogRead(unread=dict.fromkeys(serves, read_failed(exc)))
        return read

    async def _tickets(
        self,
        repo: str,
        read: Mapping[str, BacklogRead],
        triggers: Sequence[str],
        policy: LeadsSettings,
    ) -> tuple[FinderResult, set[str], str | None]:
        """The repository's backlog leads, read in its organization's words (``policy``'s vocabulary) and
        scoring its ``offer_min`` or more; the open ones a tracker that did not read it whole keeps open; and each
        such tracker with why (``github: the read failed (ReadTimeout)``), or None when every tracker that serves
        the repository read it whole."""
        items: list[BacklogItem] = [
            item for backlog in read.values() for item in backlog.items if item.repo.lower() == repo.lower()
        ]
        leads = await backlog_leads(
            items,
            trigger_labels=triggers,
            tracked=self.tracked,
            min_score=policy.offer_min,
            readiness=Readiness.of(policy),
        )
        read_whole = {name for name, backlog in read.items() if repo in backlog.whole}
        keep = {
            lead_id
            for lead_id, source in (await self.leads.open_sources(repo, "ticket")).items()
            if source.split(":", 1)[0] not in read_whole
        }
        unread = [
            f"{name}: {backlog.unread[repo]}"
            for name, backlog in read.items()
            if repo not in backlog.whole and repo in backlog.unread
        ]
        return FinderResult(leads, complete=True), keep, "; ".join(unread) or None

    async def _store(
        self,
        repo: str,
        kind: str,
        result: FinderResult,
        now: datetime,
        policy: LeadsSettings,
        *,
        keep_open: set[str] | None = None,
    ) -> dict[str, Any]:
        record = _Pass(found=len(result.leads), skipped=result.skipped)
        self._note(repo, kind, result.skipped)
        if result.skipped:
            return record.to_dict()
        known = await self.leads.known_ids(repo, kind)
        fresh = by_severity([lead for lead in result.leads if lead.id not in known])
        allowance = max(
            0, policy.max_new_per_repo_per_day - await self.leads.new_since(repo, kind, now - timedelta(days=1))
        )
        kept = fresh[:allowance]
        record.new, record.capped = len(kept), len(fresh) - len(kept)
        stored = [lead for lead in result.leads if lead.id in known] + kept
        try:
            repo_areas = await self.areas_for(repo)
        except Exception:  # noqa: BLE001 - a lead without an area is still a lead
            logger.warning("leads_areas_unread", repo=repo, exc_info=True)
            repo_areas = []
        areas = {lead.id: lead_area(repo_areas, lead) for lead in stored}
        await self.leads.upsert(stored, now=now, areas=areas)
        if result.complete:
            seen = {lead.id for lead in stored} | (keep_open or set())
            record.gone = await self.leads.mark_gone(repo, kind, seen, now=now)
        return record.to_dict()

    async def start(self) -> None:
        """Look for ever, whether leads are on or off, so turning them on needs no restart."""
        await self.sleep(self.startup_delay)
        while True:
            try:
                await self.scheduled()
            except Exception:  # noqa: BLE001 - the loop goes on
                logger.warning("leads_tick_failed", exc_info=True)
            await self.sleep(self.wait_seconds())
