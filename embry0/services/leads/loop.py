"""The leads loop (spec 2026-10-25): every finder, on every repository someone answers for.

A repository is read when it is registered and active and either names
owners in its preferences or is a watched stack: work is only found where
someone will be asked about it. Each pass runs the enabled finders on each
such repository with its organization's credential, stores what they see,
and closes what a whole read no longer sees. The backlog intake runs on its
own, longer interval; it reads Linear once per pass for every repository.

Each finder adds at most ``max_new_per_repo_per_day`` new leads per
repository a day, the worst first; a lead already stored is always kept up
to date. Nothing here starts work: an owner's yes does (owner proposals).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from embry0.services.areas import AREAS, area_of_labels, area_of_logs, area_of_paths, read_contents
from embry0.services.leads import LEAD_KINDS, FinderResult, Lead, by_severity
from embry0.services.leads.alerts import find_alerts
from embry0.services.leads.backlog import Tracked, find_github_tickets, linear_leads
from embry0.services.leads.ci import find_failing_checks
from embry0.services.leads.flaky import find_flaky_checks
from embry0.services.leads.reviews import find_unanswered_reviews

logger = structlog.get_logger(__name__)

#: Seconds after start before the first pass: a deploy must not compete with it.
STARTUP_DELAY_SECONDS = 300.0


@dataclass(frozen=True)
class LeadsSettings:
    """The deployment's leads settings (``LEADS_*`` in docs/configuration.md)."""

    finders: frozenset[str] = frozenset(LEAD_KINDS)
    interval_seconds: float = 6 * 3600.0
    backlog_interval_seconds: float = 24 * 3600.0
    max_new_per_repo_per_day: int = 5
    ticket_min_score: int = 40
    review_stale_days: int = 3
    flaky_commits: int = 10


def leads_settings(config: Any) -> LeadsSettings:
    """The settings ``config`` holds; an unknown finder name is ignored with a warning."""
    names = {n.strip().lower() for n in str(getattr(config, "leads_finders", "") or "").split(",") if n.strip()}
    unknown = names - set(LEAD_KINDS)
    if unknown:
        logger.warning("leads_unknown_finders", unknown=sorted(unknown), known=list(LEAD_KINDS))
    return LeadsSettings(
        finders=frozenset(names & set(LEAD_KINDS)),
        interval_seconds=max(0.25, float(getattr(config, "leads_interval_hours", 6.0))) * 3600.0,
        backlog_interval_seconds=max(1.0, float(getattr(config, "leads_backlog_interval_hours", 24.0))) * 3600.0,
        max_new_per_repo_per_day=max(0, int(getattr(config, "leads_max_new_per_repo_per_day", 5))),
        ticket_min_score=int(getattr(config, "leads_ticket_min_score", 40)),
        review_stale_days=max(0, int(getattr(config, "leads_review_stale_days", 3))),
        flaky_commits=max(1, int(getattr(config, "leads_flaky_commits", 10))),
    )


async def lead_area(repo: str, client: Any, lead: Lead) -> str | None:
    """The area of a monorepo a lead is about: its files, else its labels, else what its title names."""
    if client is None:
        return None

    async def read(name: str, path: str) -> str | None:
        return await read_contents(lambda url: client.get(url, repo=name), name, path)

    areas = await AREAS.areas(repo, read)
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

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v}


@dataclass
class LeadsLoop:
    settings: LeadsSettings
    leads: Any
    #: The repositories to read, registered and active, with owners or a watched stack.
    targets: Callable[[], Awaitable[list[str]]]
    #: The forge client for a repository with its organization's credential; None when there is none.
    client_for: Callable[[str], Awaitable[Any]]
    #: The Linear service, or None when Linear is not configured.
    linear: Callable[[], Awaitable[Any]]
    tracked: Tracked
    trigger_labels: Callable[[], Awaitable[Iterable[str]]]
    area_of: Callable[[str, Any, Lead], Awaitable[str | None]] = lead_area
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    startup_delay: float = STARTUP_DELAY_SECONDS
    _last_backlog: datetime | None = None
    _noted: dict[tuple[str, str], str] = field(default_factory=dict)

    def _note(self, repo: str, kind: str, reason: str | None) -> None:
        """One log line per standing reason a finder read nothing, not one per pass."""
        key = (repo, kind)
        if reason is None:
            self._noted.pop(key, None)
        elif self._noted.get(key) != reason:
            self._noted[key] = reason
            logger.info("leads_finder_skipped", repo=repo, kind=kind, reason=reason)

    def backlog_due(self, now: datetime) -> bool:
        return self._last_backlog is None or now - self._last_backlog >= timedelta(
            seconds=self.settings.backlog_interval_seconds
        )

    async def tick(self, repos: Sequence[str] | None = None, *, backlog: bool | None = None) -> dict[str, Any]:
        """One pass; ``repos`` limits it, ``backlog`` forces the backlog intake on or off. Returns what it did."""
        now = self.clock()
        targets = list(repos) if repos is not None else await self.targets()
        enabled = self.settings.finders
        run_backlog = "ticket" in enabled and (self.backlog_due(now) if backlog is None else backlog)
        tickets: list[Any] = []
        linear_read = False
        if run_backlog:
            self._last_backlog = now
            linear = await self.linear()
            if linear is not None:
                try:
                    tickets, linear_read = await linear.list_backlog_tickets()
                except Exception as exc:  # noqa: BLE001 - GitHub's backlog still goes
                    logger.warning("leads_linear_unread", error=type(exc).__name__, detail=str(exc)[:200])
        triggers = list(await self.trigger_labels()) if run_backlog else []
        summary: dict[str, Any] = {}
        for repo in targets:
            try:
                client = await self.client_for(repo)
            except Exception as exc:  # noqa: BLE001 - one repository's credential is its own problem
                logger.info("leads_client_unavailable", repo=repo, error=type(exc).__name__)
                client = None
            done: dict[str, Any] = {}
            if run_backlog:
                result, keep = await self._tickets(repo, client, tickets, linear_read, triggers)
                done["ticket"] = await self._store(repo, "ticket", client, result, now, keep_open=keep)
            for kind, find in self._github_finders(repo, client, now):
                if kind not in enabled:
                    continue
                if client is None:
                    self._note(repo, kind, "no forge client with the organization's credential")
                    done[kind] = _Pass(skipped="no forge client").to_dict()
                    continue
                try:
                    result = await find()
                except Exception as exc:  # noqa: BLE001 - one finder's failure is its own log line
                    logger.warning("leads_finder_failed", repo=repo, kind=kind, error=type(exc).__name__)
                    result = FinderResult([], complete=False)
                done[kind] = await self._store(repo, kind, client, result, now)
            summary[repo] = done
        logger.info("leads_pass", repos=len(targets), backlog=run_backlog)
        return summary

    def _github_finders(
        self, repo: str, client: Any, now: datetime
    ) -> list[tuple[str, Callable[[], Awaitable[FinderResult]]]]:
        s = self.settings
        return [
            ("ci", lambda: find_failing_checks(client, repo)),
            ("alert", lambda: find_alerts(client, repo)),
            ("flaky", lambda: find_flaky_checks(client, repo, commits=s.flaky_commits)),
            ("review", lambda: find_unanswered_reviews(client, repo, now=now, stale_days=s.review_stale_days)),
        ]

    async def _tickets(
        self, repo: str, client: Any, tickets: Sequence[Any], linear_read: bool, triggers: Sequence[str]
    ) -> tuple[FinderResult, set[str]]:
        """The repository's backlog leads, and the open ones a source not read whole keeps open."""
        min_score = self.settings.ticket_min_score
        leads = await linear_leads(repo, tickets, trigger_labels=triggers, tracked=self.tracked, min_score=min_score)
        github_read = False
        if client is not None:
            try:
                github = await find_github_tickets(
                    client, repo, trigger_labels=triggers, tracked=self.tracked, min_score=min_score
                )
                leads += github.leads
                github_read = github.complete
            except Exception as exc:  # noqa: BLE001 - Linear's leads still count
                logger.warning("leads_github_issues_unread", repo=repo, error=type(exc).__name__)
        unread = {prefix for prefix, read in (("linear:", linear_read), ("github:", github_read)) if not read}
        keep = {
            lead_id
            for lead_id, source in (await self.leads.open_sources(repo, "ticket")).items()
            if any(source.startswith(prefix) for prefix in unread)
        }
        return FinderResult(leads, complete=True), keep

    async def _store(
        self,
        repo: str,
        kind: str,
        client: Any,
        result: FinderResult,
        now: datetime,
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
            0, self.settings.max_new_per_repo_per_day - await self.leads.new_since(repo, kind, now - timedelta(days=1))
        )
        kept = fresh[:allowance]
        record.new, record.capped = len(kept), len(fresh) - len(kept)
        stored = [lead for lead in result.leads if lead.id in known] + kept
        areas: dict[str, str | None] = {}
        for lead in stored:
            try:
                areas[lead.id] = await self.area_of(repo, client, lead)
            except Exception:  # noqa: BLE001 - a lead without an area is still a lead
                areas[lead.id] = None
        await self.leads.upsert(stored, now=now, areas=areas)
        if result.complete:
            seen = {lead.id for lead in stored} | (keep_open or set())
            record.gone = await self.leads.mark_gone(repo, kind, seen, now=now)
        return record.to_dict()

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        await asyncio.sleep(self.startup_delay)
        while True:
            try:
                await self.tick()
            except Exception:
                logger.warning("leads_tick_failed", exc_info=True)
            await asyncio.sleep(self.settings.interval_seconds)
