"""The Slop-O-Meter's background loop (spec 2026-10-26, slop-o-meter).

Every ``slop_meter.interval_hours`` of the settings document (a day by
default; ``slop_meter.enabled: false`` stops the reading), for every
repository in the meter's scope: registered, active and enrolled in
``background.slop_meter``, through its organization's layer and its own
(:mod:`embry0.services.background_scope`, #165). First, once that enrollment
is settled, what the meter keeps of any repository that left its scope is
removed, ``background.prune_after_days`` after it left
(:mod:`embry0.services.slop_meter.prune`); an inactive repository is not read
and keeps what was read. Then, for each repository in scope: fetch its bare
mirror, read the next batch of its history, read its forge events from their
cursors and map a few logins (forge calls dealt round-robin from a per-tick
budget for each credential, stopping at its rate-limit reserve), judge a few
outlier commits, and, once it is caught up, recompute its weekly series and
its scores (repository and areas) off the event loop. Every step is bounded
and resumes where the last tick stopped; while a repository has a backlog the
loop comes back sooner (``SLOP_METER_CATCHUP_SECONDS``).

A tick reads its enrollment, its judge's model and the ``slop_meter``
section from one snapshot of the settings documents (:func:`meter_reader`),
and scores each repository with the section as that repository reads it
(#169). Each score records the section's :func:`config_version` for it. The
schedule looks at the settings every :data:`LOOK_SECONDS`, so turning the
meter on or off, or a new interval, applies within them. Between runs, each
look compares every repository's stored version with the one it reads now and
rescores only the repositories whose versions differ, from what was read,
with no fetch and no forge call (:meth:`SlopMeterLoop.rescore_changed`): a
change rescores what it touches at once, and the next run keeps its time.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
import structlog

from embry0.services.areas import AREAS_PATH, area_of_path, parse_areas
from embry0.services.background_models import BackgroundCallRefused, BackgroundCalls, ModelLookup, model_lookup
from embry0.services.background_scope import Enrollment, Registered, enrollment
from embry0.services.slop_meter.events import ApiBudget, BudgetSpent, EventReader, ShareBudget
from embry0.services.slop_meter.history import (
    HISTORY_VERSION,
    Commit,
    agent_prefixes,
    is_bot,
    login_of_email,
    own_identities,
    parse_log,
    with_bots,
)
from embry0.services.slop_meter.judge import FEATURE
from embry0.services.slop_meter.prune import Audit, PruneOutcome, Pruner
from embry0.services.slop_meter.score import DEFAULTS, Event, Meter, Outlier, meters, outliers
from embry0.services.slop_meter.weights import CONFIG_FILES, FileRules, Weigher, ignore_patterns, outlier_files
from embry0.settings.layers import PerRepository
from embry0.settings.sections.slop_meter import SLOP_METER, SlopMeterSettings, config_version

logger = structlog.get_logger(__name__)

#: The longest the schedule waits between two looks at the settings: how soon turning the meter on or off, a
#: new interval, or a change to what a score is computed from applies.
LOOK_SECONDS: Final = 300.0


@dataclass(frozen=True)
class SlopMeterLimits:
    """What one tick may do per repository, and how soon it comes back while a history is behind.

    The process's own limits, from the environment (``SLOP_METER_*``): they
    bound the work and the memory of one tick. What the meter reads and how
    it scores are the ``slop_meter`` section of the settings document.
    """

    catchup_seconds: float = 300.0
    commits_per_tick: int = 5_000
    event_pages: int = 10
    reviews_per_tick: int = 50
    logins_per_tick: int = 20
    judgments_per_tick: int = 5


def slop_meter_limits(config: Any) -> SlopMeterLimits:
    return SlopMeterLimits(
        catchup_seconds=float(getattr(config, "slop_meter_catchup_seconds", 300)),
        commits_per_tick=int(getattr(config, "slop_meter_commits_per_tick", 5_000)),
        event_pages=int(getattr(config, "slop_meter_event_pages", 10)),
        reviews_per_tick=int(getattr(config, "slop_meter_reviews_per_tick", 50)),
        logins_per_tick=int(getattr(config, "slop_meter_logins_per_tick", 20)),
        judgments_per_tick=int(getattr(config, "slop_meter_judgments_per_tick", 5)),
    )


#: The meter's key of the settings document's ``background`` section.
SCOPE: Final = "slop_meter"


class _Defaults:
    """The ``slop_meter`` section with nothing written, for every repository: a read that names no snapshot."""

    def deployment(self) -> SlopMeterSettings:
        return DEFAULTS

    def repo(self, repo: str | None) -> SlopMeterSettings:
        return DEFAULTS


@dataclass(frozen=True)
class MeterRead:
    """What one tick of the meter reads, all from one snapshot of the settings documents."""

    #: ``repo -> model`` its outliers are judged on (``background_models.slop_judge``); '' for none.
    models: ModelLookup
    #: Which repositories it reads (``background.slop_meter``), and whether that is settled.
    enrollment: Enrollment
    #: The ``slop_meter`` section: the deployment's values (whether and how often it reads), and each
    #: repository's (its organization's layer and its own over the deployment's).
    meter: PerRepository[SlopMeterSettings] = field(default_factory=_Defaults)


@dataclass(frozen=True)
class _NotImported:
    """The ``slop_meter`` section with the keys the import has not imported read from their variables, as the
    release before read them (``legacy``), where no layer sets them."""

    scoped: Any
    legacy: Mapping[str, Any]

    def deployment(self) -> SlopMeterSettings:
        model: SlopMeterSettings = self.scoped.deployment()
        return model.model_copy(update=dict(self.legacy))

    def repo(self, repo: str | None) -> SlopMeterSettings:
        resolved = self.scoped.snapshot.resolve(SLOP_METER, repo=repo)
        own = {key for values in resolved.applied.values() for key in values}
        updates = {key: value for key, value in self.legacy.items() if key not in own}
        model: SlopMeterSettings = resolved.model
        return model.model_copy(update=updates) if updates else model


def not_imported(snapshot: Any, config: Any) -> dict[str, Any]:
    """The keys of the ``slop_meter`` section the environment import has not imported into the deployment's
    document (a first start whose import failed) and that it does not set, each with its variable's value as the
    import would read it; a variable not set is left out, so its key reads its default, as it did."""
    from embry0.settings.bootstrap import env_value
    from embry0.settings.document import mapping

    stored = mapping(snapshot.data.get(SLOP_METER.name))
    out: dict[str, Any] = {}
    for key in SLOP_METER.imports():
        if snapshot.imported(f"{SLOP_METER.name}.{key}") or key in stored:
            continue
        value = env_value(SLOP_METER, key, config)
        if value is not None:
            out[key] = value
    return out


def meter_reader(store: Any, config: Any = None) -> Callable[[], Awaitable[MeterRead]]:
    """The reader the meter is built with: each call takes one snapshot of the settings documents
    (``store.snapshot()``) and reads the judge's model, the enrollment and the ``slop_meter`` section from it.

    With ``config``, a key the environment import has not imported yet reads
    its variable, as the budget's readers do (#171): a deployment that had the
    meter off keeps it off until the import, which the budget policy tries
    again while it runs, succeeds (``slop_meter_settings_not_imported``).
    """
    logged: set[str] = set()

    async def read() -> MeterRead:
        snapshot = await store.snapshot()
        meter: PerRepository[SlopMeterSettings] = snapshot.scoped(SLOP_METER)
        legacy = not_imported(snapshot, config) if config is not None else {}
        if legacy:
            meter = _NotImported(meter, legacy)
            if set(legacy) - logged:
                logged.update(legacy)
                logger.error(
                    "slop_meter_settings_not_imported",
                    keys=sorted(f"{SLOP_METER.name}.{key}" for key in legacy),
                    hint="read from their variables, as the release before read them, until the import succeeds",
                )
        return MeterRead(models=model_lookup(snapshot, "slop_judge"), enrollment=enrollment(snapshot), meter=meter)

    return read


@dataclass(frozen=True)
class Inputs:
    """What a repository's score is computed from: its history, its head's config files, and its settings."""

    commits: list[Commit]
    events: list[Event]
    logins: dict[str, str]
    judgments: dict[str, bool]
    files: dict[str, str | None]
    #: The ``slop_meter`` section as the repository reads it.
    settings: SlopMeterSettings = DEFAULTS
    #: Its file rules, built once for one score (they keep each path's weight); None builds them.
    rules: FileRules | None = None


def weigher_for(
    commits: Iterable[Commit],
    files: Mapping[str, str | None],
    settings: SlopMeterSettings = DEFAULTS,
    rules: FileRules | None = None,
) -> Weigher:
    """The repository's line weights: its ``slop_meter`` file rules, its linters' ignore lists, and its
    outlier files."""
    rules = rules if rules is not None else FileRules.of(settings)
    base = Weigher(ignore_patterns(files), rules=rules)
    sizes: dict[str, int] = {}
    for c in commits:
        for path, a, d in c.files:
            if base(path):
                sizes[path] = sizes.get(path, 0) + a - d
    return Weigher(ignore_patterns(files), outlier_files({p: s for p, s in sizes.items() if s > 0}), rules=rules)


def logins_from(commits: Iterable[Commit], looked_up: Mapping[str, str]) -> dict[str, str]:
    """Login → email: from ``{id}+{login}@users.noreply.github.com`` addresses, then from the forge's answers."""
    out: dict[str, str] = {}
    for c in commits:
        for email in (c.author_email, *(e for _n, e in c.coauthors)):
            login = login_of_email(email)
            if login:
                out.setdefault(login, email.lower())
    for login, email in looked_up.items():
        if email:
            out.setdefault(login.lower(), email.lower())
    return out


def compute(inputs: Inputs) -> dict[str, Meter]:
    """The repository's meter (``""``) and its areas', from the history as read, scored with ``inputs.settings``.
    Pure: runs in a thread."""
    weigh = weigher_for(inputs.commits, inputs.files, inputs.settings, inputs.rules)
    areas = parse_areas(inputs.files.get(AREAS_PATH))

    def area_of(path: str) -> str | None:
        found = area_of_path(areas, path)
        return found.name if found is not None else None

    logins = logins_from(inputs.commits, inputs.logins)
    return meters(
        inputs.commits, inputs.events, logins, weigh, inputs.judgments, area_of=area_of, settings=inputs.settings
    )


def history_of(
    rows: Iterable[Mapping[str, Any]],
    event_rows: Iterable[Mapping[str, Any]],
    bots: frozenset[str],
    agents: Iterable[str],
) -> tuple[list[Commit], list[Event]]:
    """A repository's stored commits and events as a score reads them: each commit with ``bots`` and the AI
    agents ``agents`` known (:func:`embry0.services.slop_meter.history.with_bots`), and the events of people.
    Pure: runs in a thread."""
    commits = with_bots([Commit.from_row(dict(row)) for row in rows], bots, agents)
    events = [
        Event(str(e["login"]), e["at"], str(e["kind"]))
        for e in event_rows
        if not is_bot(str(e["login"]), "", bots, agents)
    ]
    return commits, events


def unjudged(inputs: Inputs) -> list[Outlier]:
    """The repository's outlier commits no model has judged yet. Pure: runs in a thread."""
    weigh = weigher_for(inputs.commits, inputs.files, inputs.settings, inputs.rules)
    return [o for o in outliers(inputs.commits, weigh, inputs.judgments, inputs.settings) if o.slop is None]


TokenFor = Callable[[str], Awaitable[tuple[str, str, str] | None]]
ClientFor = Callable[[str], Awaitable[Any]]


@dataclass
class _Work:
    """A repository's forge, and whether it still has history or events to read."""

    forge: str
    more: bool
    #: Which credential reads it: a digest of its token, never the token. Each
    #: credential has its own rate limit, so each gets its own budget (#185).
    credential: str = ""
    #: Its events were cut short by its organization's narrower share alone: not scored this run, and no
    #: reason to come back sooner.
    held: bool = False


#: Reads what an organization's own settings still say of a moved key, until its layer has imported it
#: (``BudgetView.moved_values``): ``(section, owner, settings) -> {key: value}``.
MovedReader = Callable[[str, str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


async def repo_bots(
    repo: str, *, prefs: Any, orgs: Any, credentials: Any, moved: MovedReader | None = None
) -> list[str]:
    """The identities a repository's readings count as bots beyond embry0's default one and the
    ``slop_meter.bots`` its layers list (#184).

    The addresses embry0 commits under there (the repository's preferred
    author, the owner's credential's author), and, until the organization's
    layer has imported it, the ``slop_meter_bots`` its own settings still
    list (``moved``; :data:`embry0.services.organizations.MOVED_SETTINGS`).
    """
    out: list[str] = []
    pref = await prefs.get(repo)
    if pref and pref.get("git_author_email"):
        out.append(str(pref["git_author_email"]))
    org = await orgs.get(repo.split("/", 1)[0])
    if org is not None:
        if org.credential_id:
            cred = await credentials.get(org.credential_id)
            if cred and cred.get("git_author_email"):
                out.append(str(cred["git_author_email"]))
        if moved is not None:
            listed = (await moved(SLOP_METER.name, org.owner, org.settings)).get("bots")
            if isinstance(listed, list | tuple):
                out.extend(str(v) for v in listed)
    return out


class SlopMeterLoop:
    """Reads each repository in scope a bounded batch a tick, and scores it when caught up.

    ``repos()`` lists the registered repositories, active or not
    (:func:`embry0.services.background_scope.registry_reader`): the tick reads
    the active ones enrolled in the meter, and keeps what it read of every
    enrolled one. ``read()``, once a tick, the enrollment, the model each
    repository's outliers are judged on ('' for none:
    ``background_models.slop_judge``, else its default) and the
    ``slop_meter`` section, from one snapshot (:func:`meter_reader`);
    ``token_for(repo)`` the forge, credential and
    GitLab URL to fetch with (None when there is none), ``client_for(repo)``
    the GitHub client to read events with (None for a GitLab repository), and
    ``calls`` makes each judgment's call with the repository's key, the
    budget caps and the spend recorded (#164). ``mirror``, ``store``,
    ``exits`` and ``audit`` remove what the meter keeps of a repository that
    left its scope, after ``background.prune_after_days``
    (:class:`embry0.services.slop_meter.prune.Pruner`). ``limits`` are the
    process's (:class:`SlopMeterLimits`), and ``sleep`` how the schedule
    waits (:meth:`start`).
    """

    def __init__(
        self,
        *,
        repos: Callable[[], Awaitable[Registered]],
        read: Callable[[], Awaitable[MeterRead]],
        store: Any,
        mirror: Any,
        exits: Any,
        audit: Audit,
        token_for: TokenFor,
        client_for: ClientFor,
        bots: Callable[[str], Awaitable[Iterable[str]]],
        calls: BackgroundCalls,
        limits: SlopMeterLimits,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._repos = repos
        self._read = read
        self._store = store
        self._mirror = mirror
        self._pruner = Pruner(store=store, mirror=mirror, exits=exits, audit=audit, clock=clock)
        self._token_for = token_for
        self._client_for = client_for
        #: The tick's ``repo -> model`` and ``slop_meter`` section, read at its start.
        self._lookup: ModelLookup | None = None
        self._meter: PerRepository[SlopMeterSettings] = _Defaults()
        #: A repository's own bots and embry0's commit identities for it (#184).
        self._bots = bots
        self._calls = calls
        self._limits = limits
        self._clock = clock
        self._http = http
        self._sleep = sleep
        self._turn = 0
        #: When the last run of the schedule ended; None before the first.
        self._last_pass: datetime | None = None
        #: When the next run falls due, as the last look read the section.
        self._due: datetime | None = None
        #: Whether the last run left a repository with a backlog: the next comes ``catchup_seconds`` later.
        self._backlog = False
        #: Whether the last look found the meter on; None before the first.
        self._enabled: bool | None = None
        #: The repositories the last full run did not score because their organization's share cut their events
        #: short: a rescore from what was read would score that short read, so they wait for a run that reads all.
        self._held: set[str] = set()
        #: Each repository's configuration version as this process last scored it, or tried to: a look rescores a
        #: repository whose stored score has another version only once per version, so one whose rescoring fails
        #: is not tried again every look.
        self._tried: dict[str, str] = {}

    @property
    def last_pass(self) -> datetime | None:
        """When the schedule's last run ended, a reading or a removal only; None before its first."""
        return self._last_pass

    async def tick(self, read: MeterRead | None = None) -> bool:
        """One pass over the repositories in scope, whether the meter is on or off; True when one still has a
        backlog. ``read`` is what the schedule's look read (else it is read now).

        The settings and the registry are read first: which repositories are
        enrolled, the judge's model and the ``slop_meter`` section, from one
        snapshot. What the meter keeps under a spelling the registry spells
        otherwise moves onto the registry's. Once the enrollment is settled,
        what it keeps of a repository out of scope is removed after the grace
        period. Then the git work (no API calls). Then the forge calls, dealt
        one per repository in turn from its credential's budget, so a large
        backlog in one cannot starve the rest; the starting repository
        rotates every tick. Last, every repository that is caught up is
        scored with the section as it reads it, recording its version.
        """
        if read is None:
            read = await self._read()
        registered = await self._repos()
        await self._respell(registered.every)
        await self._prune(read, registered)
        names = read.enrollment.scope(SCOPE, registered.active)
        if not names:
            return False
        self._lookup = read.models
        self._meter = read.meter
        turn = self._turn % len(names)
        self._turn += 1
        order = names[turn:] + names[:turn]
        work: dict[str, _Work] = {}
        for repo in order:
            try:
                work[repo] = await self._read_history(repo)
            except Exception as exc:  # noqa: BLE001 - one repository's failure never stops the rest
                await self._failed(repo, exc)
        await self._read_forge(order, work)
        backlog = False
        for repo in order:
            if repo not in work:
                continue
            if work[repo].more:
                backlog = True
                continue
            if work[repo].held:
                self._held.add(repo)
                logger.info(
                    "slop_meter_score_held",
                    repo=repo,
                    reason="its organization's forge share ran out before its events were read",
                    hint="its previous score stays; a larger api_calls_per_tick for the organization lets a run "
                    "read them all",
                )
                continue
            self._held.discard(repo)
            try:
                await self._score(repo)
            except Exception as exc:  # noqa: BLE001
                await self._failed(repo, exc)
        return backlog

    async def _respell(self, registered: Iterable[str]) -> None:
        """Move what the meter keeps under a spelling that differs from the registry's only by case onto the
        registry's, so it is neither read again nor kept twice."""
        spelled = {name.lower(): name for name in registered}
        try:
            for held in await self._store.held():
                canonical = spelled.get(held.lower())
                if canonical is None or canonical == held:
                    continue
                done = await self._store.respell(held, canonical)
                if done:
                    logger.info("slop_meter_respelled", repo=canonical, was=held, rows=done)
        except Exception:  # noqa: BLE001 - the next tick tries again
            logger.warning("slop_meter_respell_failed", exc_info=True)

    async def _prune(self, read: MeterRead, registered: Registered) -> PruneOutcome | None:
        """Remove, after the grace period, what the meter keeps of every repository out of its scope: not
        enrolled, or not registered. An inactive repository that is enrolled keeps what was read. Nothing
        until the enrollment is settled."""
        if not read.enrollment.settled(SCOPE):
            return None
        keep = read.enrollment.scope(SCOPE, registered.every)
        try:
            return await self._pruner.run(keep, grace=read.enrollment.prune_after, registered=len(registered.every))
        except Exception:  # noqa: BLE001 - reading what is in scope goes on; the next tick tries again
            logger.warning("slop_meter_prune_failed", exc_info=True)
            return None

    async def prune_only(self, read: MeterRead | None = None) -> PruneOutcome | None:
        """What a tick removes, and nothing else: the run of a schedule with the meter off."""
        if read is None:
            read = await self._read()
        registered = await self._repos()
        await self._respell(registered.every)
        return await self._prune(read, registered)

    async def _failed(self, repo: str, exc: BaseException) -> None:
        logger.warning("slop_meter_repo_failed", repo=repo, error=str(exc)[:300])
        await self._store.set_state(repo, error=str(exc)[:500], error_at=self._clock())

    async def _read_history(self, repo: str) -> _Work:
        """Fetch the mirror and read the next bounded batch of the history (git only, no API calls)."""
        resolved = await self._token_for(repo)
        if resolved is None:
            raise RuntimeError("no forge credential for this repository")
        forge, token, gitlab_url = resolved
        await self._mirror.sync(repo, forge=forge, token=token, gitlab_url=gitlab_url)
        await self._store.set_state(repo, mirrored_at=self._clock())

        history = await self._mirror.history(repo)
        state = await self._store.state(repo)
        done = int(state.get("commits_read") or 0)
        if done and (done > len(history) or history[done - 1] != state.get("head_sha")):
            logger.info("slop_meter_history_rewritten", repo=repo, read=done)
            await self._store.forget_history(repo)
            done = 0
        if int(state.get("history_version") or 1) != HISTORY_VERSION:
            # Read with an older way of reading a history: read it again.
            if done:
                logger.info("slop_meter_history_reread", repo=repo, read=done, version=HISTORY_VERSION)
                await self._store.forget_history(repo)
                done = 0
            await self._store.set_state(repo, history_version=HISTORY_VERSION)
        batch = history[done : done + self._limits.commits_per_tick]
        if batch:
            commits = parse_log(await self._mirror.log(repo, batch))
            await self._store.add_commits(repo, done, [c.to_row() for c in commits])
            done += len(batch)
            await self._store.set_state(repo, commits_read=done, commits_total=len(history), head_sha=batch[-1])
        credential = hashlib.sha256(token.encode()).hexdigest()[:16]
        return _Work(forge=forge, more=done < len(history), credential=credential)

    async def _reader(self, repo: str) -> EventReader | None:
        client = await self._client_for(repo)
        if client is None:
            return None
        s = self._limits
        state = await self._store.state(repo)
        known = set(await self._store.logins())
        known |= {login for email in await self._store.authors(repo) if (login := login_of_email(email))}
        return EventReader(
            client,
            repo,
            comments_since=state.get("comments_since"),
            review_comments_since=state.get("review_comments_since"),
            reviews_since=state.get("reviews_since"),
            max_pages=s.event_pages,
            max_pulls=s.reviews_per_tick,
            known_logins=known,
            stored_logins=[str(e["login"]) for e in await self._store.events(repo)],
            max_logins=s.logins_per_tick,
        )

    async def _read_forge(self, order: list[str], work: dict[str, _Work]) -> None:
        """Every GitHub repository's events, one API call per repository in turn.

        Repositories read with one credential share its budget for the tick:
        the deployment's ``slop_meter.api_calls_per_tick`` calls, stopping at
        its rate limit's ``rate_reserve``. An organization's own values only
        narrow its own share of that budget (:class:`ShareBudget`): fewer calls
        for its repositories, or a larger reserve they stop at, never more,
        and never less for another organization's repositories on the same
        credential. One credential running low never stops a repository read
        with another (#185).

        A repository stopped by its credential's own budget or reserve waits:
        it is scored once its events are read, and the loop comes back after
        ``catchup_seconds``, as before. One stopped only by its organization's
        narrower share keeps its previous score, or stays unscored, since a
        read cut short would look complete: its events are stored and its
        listings' cursors move, but a run starts its reading over at the first
        listing, so a share too small for one whole read never reaches the
        reviews (#396 would keep the reader's place). It waits for the next
        run, at the interval: an organization that reads less never holds the
        meter, or every other organization's repositories, on its catch-up
        cadence.
        """
        deployment = self._meter.deployment()
        readers: dict[str, EventReader] = {}
        budgets: dict[str, ApiBudget] = {}
        shares: dict[tuple[str, str], ShareBudget] = {}
        share_of: dict[str, ShareBudget] = {}
        for repo in order:
            if repo in work and work[repo].forge == "github":
                try:
                    reader = await self._reader(repo)
                except Exception as exc:  # noqa: BLE001 - events are a bonus; the history still scores
                    logger.warning("slop_meter_events_failed", repo=repo, error=str(exc)[:300])
                    continue
                if reader is not None:
                    readers[repo] = reader
                    credential = work[repo].credential
                    shared = budgets.setdefault(
                        credential, ApiBudget(deployment.api_calls_per_tick, deployment.rate_reserve)
                    )
                    owner = repo.split("/", 1)[0].lower()
                    if (credential, owner) not in shares:
                        own = self._meter.repo(repo)
                        shares[(credential, owner)] = ShareBudget(
                            shared,
                            calls=min(own.api_calls_per_tick, deployment.api_calls_per_tick),
                            reserve=max(own.rate_reserve, deployment.rate_reserve),
                        )
                    share_of[repo] = shares[(credential, owner)]
        active = list(readers)
        waiting: list[str] = []
        narrowed: list[str] = []
        while active:
            for repo in list(active):
                try:
                    going = await readers[repo].step(share_of[repo])
                except BudgetSpent:
                    # Its share is gone; the others' repositories go on. Whose limit stopped it, the
                    # credential's or only its organization's, is what it is at this moment.
                    active.remove(repo)
                    (waiting if share_of[repo].shared.spent else narrowed).append(repo)
                    continue
                except Exception as exc:  # noqa: BLE001 - this repository's events stop for the tick
                    logger.warning("slop_meter_events_failed", repo=repo, error=str(exc)[:300])
                    active.remove(repo)
                    continue
                if not going:
                    active.remove(repo)
        if waiting or narrowed:
            spent = [b for b in shares.values() if b.spent]
            logger.info(
                "slop_meter_api_budget_spent",
                calls=[b.made for b in spent],
                remaining=[b.remaining for b in spent],
                reserve=[b.reserve for b in spent],
                waiting=sorted(waiting),
                narrowed=sorted(narrowed),
            )
        for repo, reader in readers.items():
            batch = reader.batch
            await self._store.add_events(repo, batch.events)
            await self._store.set_state(
                repo,
                comments_since=batch.comments_since,
                review_comments_since=batch.review_comments_since,
                reviews_since=batch.reviews_since,
            )
            for login, email in batch.logins.items():
                await self._store.set_login(login, email)
            if repo in waiting:
                work[repo].more = True
            elif repo in narrowed:
                work[repo].held = True

    async def _inputs(self, repo: str, settings: SlopMeterSettings) -> Inputs:
        bots = own_identities([*await self._bots(repo), *settings.bots])
        agents = agent_prefixes(settings.ai_agents)
        rows, event_rows = await self._store.commits(repo), await self._store.events(repo)
        # Building every commit of a long history takes a while: off the event loop, as the scoring is.
        commits, events = await asyncio.to_thread(history_of, rows, event_rows, bots, agents)
        files = {name: await self._mirror.read(repo, name) for name in (*CONFIG_FILES, AREAS_PATH)}
        logins, judgments = await self._store.logins(), await self._store.judgments(repo)
        return Inputs(commits, events, logins, judgments, files, settings, FileRules.of(settings))

    async def _score(self, repo: str, *, judge: bool = True) -> None:
        """Score ``repo`` with the ``slop_meter`` section as it reads it, recording that section's version.

        From the rows and the mirror's files as kept: nothing is fetched and
        nothing is read from the forge. ``judge`` asks the model about a few
        outlier commits not judged yet first; a rescore for a change of the
        settings does not, so it spends nothing: a new outlier stays unjudged,
        and the score provisional, until the next run.
        """
        settings = self._meter.repo(repo)
        version = config_version(settings)
        self._tried[repo] = version
        inputs = await self._inputs(repo, settings)
        if judge:
            await self._judge(repo, inputs)
        results = await asyncio.to_thread(compute, inputs)
        now = self._clock()
        await self._store.store(repo, results, now, config_version=version)
        await self._store.set_state(repo, computed_at=now, error=None, error_at=None)
        top = results[""]
        logger.info(
            "slop_meter_scored",
            repo=repo,
            score=round(top.score, 4),
            level=top.name,
            provisional=top.provisional,
            config_version=version,
        )

    async def _judge(self, repo: str, inputs: Inputs) -> None:
        """Ask the model about a few outlier commits not judged yet; what it answers is kept on ``inputs``."""
        settings = inputs.settings
        pending = await asyncio.to_thread(unjudged, inputs)
        if not pending:
            return
        lookup = self._lookup if self._lookup is not None else (await self._read()).models
        model = lookup(repo).strip()
        endpoint = await self._calls.endpoint(FEATURE, model, repo) if model else None
        if endpoint is None:
            return
        from embry0.services.slop_meter.judge import judge

        client = self._http or httpx.AsyncClient()
        try:
            for outlier in pending[: self._limits.judgments_per_tick]:
                try:
                    slop, reason = await judge(
                        repo, outlier, endpoint, client, self._calls, guidance=settings.judge_prompt
                    )
                except BackgroundCallRefused:
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    logger.info("slop_meter_judgment_failed", repo=repo, sha=outlier.commit.sha, error=str(exc)[:200])
                    continue
                await self._store.judge(repo, outlier.commit.sha, slop=slop, reason=reason, model=model)
                inputs.judgments[outlier.commit.sha] = slop
        finally:
            if self._http is None:
                await client.aclose()

    # ---- the schedule ----

    def wait_seconds(self) -> float:
        """How long the schedule waits after a look: :data:`LOOK_SECONDS`, or less when a run falls due sooner."""
        if self._due is None:
            return 0.0
        return min(LOOK_SECONDS, max(0.0, (self._due - self._clock()).total_seconds()))

    async def rescore_changed(self, read: MeterRead | None = None) -> list[str]:
        """Rescore each repository in scope whose stored score was computed with another configuration version than
        the one it reads now: an operator changed what its score is computed from. Answers the ones rescored.

        Only those, from their rows and mirrors as kept, with no fetch, no
        forge call and no judgment (:meth:`_score`), so a change made every few
        minutes costs a rescore of what it touches and nothing more. One still
        reading its history is left to its catch-up, and each version is tried
        once per process.
        """
        if read is None:
            read = await self._read()
        registered = await self._repos()
        names = read.enrollment.scope(SCOPE, registered.active)
        if not names:
            return []
        stored = await self._store.versions()
        changed: list[str] = []
        for repo in names:
            version = config_version(read.meter.repo(repo))
            held = stored.get(repo)
            if held is not None and held != version and self._tried.get(repo) != version:
                changed.append(repo)
        if not changed:
            return []
        logger.info("slop_meter_settings_changed", repos=changed, hint="rescoring them from what was read")
        self._meter = read.meter
        rescored: list[str] = []
        for repo in changed:
            self._tried[repo] = config_version(read.meter.repo(repo))
            state = await self._store.state(repo)
            if int(state.get("commits_read") or 0) < int(state.get("commits_total") or 0):
                continue  # still reading its history: its catch-up scores it
            if repo in self._held:
                continue  # its events were cut short: a full run that reads them all scores it
            try:
                await self._score(repo, judge=False)
                rescored.append(repo)
            except Exception as exc:  # noqa: BLE001 - one repository's failure never stops the rest
                await self._failed(repo, exc)
        return rescored

    async def scheduled(self) -> str | None:
        """What the schedule does each time it looks: ``"run"``, ``"rescore"`` or nothing (None).

        Reads the settings first, so turning the meter on or off and a new
        interval apply at this look. It runs (:meth:`tick`, or with the meter
        off :meth:`prune_only`, which only removes what left the scope) when
        the interval, or while a history is behind ``catchup_seconds``, has
        passed since the last run ended, and at once when the meter was just
        turned on. Between runs, a repository whose stored score has another
        configuration version than it reads now is rescored alone
        (:meth:`rescore_changed`); the next run keeps its time.
        """
        read = await self._read()
        policy = read.meter.deployment()
        turned_on = policy.enabled and self._enabled is False
        if policy.enabled != self._enabled:
            logger.info(
                "slop_meter_on" if policy.enabled else "slop_meter_off",
                reason="slop_meter.enabled in the settings document",
            )
            self._enabled = policy.enabled
        last = self._last_pass
        if last is not None and not turned_on:
            next_run = last + timedelta(seconds=self._period(policy))
            if self._clock() < next_run:
                self._due = next_run
                if policy.enabled and await self.rescore_changed(read):
                    return "rescore"
                return None
        try:
            if policy.enabled:
                self._backlog = await self.tick(read)
            else:
                self._backlog = False
                await self.prune_only(read)
        finally:
            self._last_pass = self._clock()
            self._due = self._last_pass + timedelta(seconds=self._period(policy))
        return "run"

    def _period(self, policy: SlopMeterSettings) -> float:
        """Seconds from the end of one run to the next: ``catchup_seconds`` while a history is behind, else the
        interval."""
        if self._backlog and policy.enabled:
            return self._limits.catchup_seconds
        return policy.interval_hours * 3600

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        """Look for ever, whether the meter is on or off, so turning it on, or any change to the section, needs
        no restart. The first look runs at once."""
        while True:
            try:
                await self.scheduled()
            except Exception:  # noqa: BLE001 - the loop goes on
                logger.warning("slop_meter_tick_failed", exc_info=True)
                if self._due is None or self._due <= self._clock():
                    self._due = self._clock() + timedelta(seconds=LOOK_SECONDS)
            await self._sleep(self.wait_seconds())
