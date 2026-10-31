"""The Slop-O-Meter's background loop (spec 2026-10-26, slop-o-meter).

Once a day (``SLOP_METER_INTERVAL_SECONDS``; 0 is off), for every repository
in the meter's scope: registered, active and enrolled in ``background.slop_meter``
of the settings document, through its organization's layer and its own
(:mod:`embry0.services.background_scope`, #165). First, once that enrollment
is settled, what the meter keeps of any repository that left its scope is
removed, ``background.prune_after_days`` after it left
(:mod:`embry0.services.slop_meter.prune`); an inactive repository is not read
and keeps what was read. Then, for each repository
in scope: fetch its bare mirror, read the next batch of its history, read its
forge events from their cursors and map a few logins (forge calls dealt
round-robin from a shared per-tick budget, stopping at the credential's
rate-limit reserve), judge a few outlier commits, and, once it is caught up,
recompute its weekly series and its scores (repository and areas) off the
event loop. Every step is bounded and resumes where the last tick stopped;
while a repository has a backlog the loop comes back sooner
(``SLOP_METER_CATCHUP_SECONDS``). A tick reads its enrollment and its judge's
model from one snapshot of the settings documents (:func:`meter_reader`), so a
change to either applies on the next tick with no restart.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import httpx
import structlog

from embry0.services.areas import AREAS_PATH, area_of_path, parse_areas
from embry0.services.background_models import BackgroundCallRefused, BackgroundCalls, ModelLookup, model_lookup
from embry0.services.background_scope import Enrollment, Registered, enrollment
from embry0.services.slop_meter.events import ApiBudget, BudgetSpent, EventReader
from embry0.services.slop_meter.history import (
    HISTORY_VERSION,
    Commit,
    is_bot,
    login_of_email,
    own_identities,
    parse_log,
    with_bots,
)
from embry0.services.slop_meter.judge import FEATURE
from embry0.services.slop_meter.prune import Audit, PruneOutcome, Pruner
from embry0.services.slop_meter.score import Event, Meter, meters, outliers
from embry0.services.slop_meter.weights import CONFIG_FILES, Weigher, ignore_patterns, outlier_files

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SlopMeterSettings:
    interval_seconds: float = 86_400.0
    catchup_seconds: float = 300.0
    commits_per_tick: int = 5_000
    event_pages: int = 10
    reviews_per_tick: int = 50
    logins_per_tick: int = 20
    judgments_per_tick: int = 5
    #: Forge API calls one tick may make across every repository, and the rate limit it leaves for jobs.
    api_calls_per_tick: int = 200
    rate_reserve: int = 2_000


def slop_meter_settings(config: Any) -> SlopMeterSettings:
    return SlopMeterSettings(
        interval_seconds=float(getattr(config, "slop_meter_interval_seconds", 86_400)),
        catchup_seconds=float(getattr(config, "slop_meter_catchup_seconds", 300)),
        commits_per_tick=int(getattr(config, "slop_meter_commits_per_tick", 5_000)),
        event_pages=int(getattr(config, "slop_meter_event_pages", 10)),
        reviews_per_tick=int(getattr(config, "slop_meter_reviews_per_tick", 50)),
        logins_per_tick=int(getattr(config, "slop_meter_logins_per_tick", 20)),
        judgments_per_tick=int(getattr(config, "slop_meter_judgments_per_tick", 5)),
        api_calls_per_tick=int(getattr(config, "slop_meter_api_calls_per_tick", 200)),
        rate_reserve=int(getattr(config, "slop_meter_rate_reserve", 2_000)),
    )


#: The meter's key of the settings document's ``background`` section.
SCOPE: Final = "slop_meter"


@dataclass(frozen=True)
class MeterRead:
    """What one tick of the meter reads, all from one snapshot of the settings documents."""

    #: ``repo -> model`` its outliers are judged on (``background_models.slop_judge``); '' for none.
    models: ModelLookup
    #: Which repositories it reads (``background.slop_meter``), and whether that is settled.
    enrollment: Enrollment


def meter_reader(store: Any) -> Callable[[], Awaitable[MeterRead]]:
    """The reader the meter is built with: each call takes one snapshot of the settings documents
    (``store.snapshot()``) and reads both the judge's model and the enrollment from it."""

    async def read() -> MeterRead:
        snapshot = await store.snapshot()
        return MeterRead(models=model_lookup(snapshot, "slop_judge"), enrollment=enrollment(snapshot))

    return read


@dataclass(frozen=True)
class Inputs:
    """What a repository's score is computed from: its history and its head's config files."""

    commits: list[Commit]
    events: list[Event]
    logins: dict[str, str]
    judgments: dict[str, bool]
    files: dict[str, str | None]


def weigher_for(commits: Iterable[Commit], files: Mapping[str, str | None]) -> Weigher:
    """The repository's line weights: its type weights, its linters' ignore lists, and its outlier files."""
    base = Weigher(ignore_patterns(files))
    sizes: dict[str, int] = {}
    for c in commits:
        for path, a, d in c.files:
            if base(path):
                sizes[path] = sizes.get(path, 0) + a - d
    return Weigher(ignore_patterns(files), outlier_files({p: s for p, s in sizes.items() if s > 0}))


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
    """The repository's meter (``""``) and its areas', from the history as read. Pure: runs in a thread."""
    weigh = weigher_for(inputs.commits, inputs.files)
    areas = parse_areas(inputs.files.get(AREAS_PATH))

    def area_of(path: str) -> str | None:
        found = area_of_path(areas, path)
        return found.name if found is not None else None

    logins = logins_from(inputs.commits, inputs.logins)
    return meters(inputs.commits, inputs.events, logins, weigh, inputs.judgments, area_of=area_of)


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


async def repo_bots(repo: str, *, prefs: Any, orgs: Any, credentials: Any) -> list[str]:
    """The identities a repository's readings count as bots beyond embry0's default one (#184).

    The addresses embry0 commits under there (the repository's preferred author,
    the owner's credential's author) and the bots the owner lists in its
    ``slop_meter_bots`` setting.
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
        listed = org.settings.get("slop_meter_bots")
        if isinstance(listed, list):
            out.extend(str(v) for v in listed)
    return out


class SlopMeterLoop:
    """Reads each repository in scope a bounded batch a tick, and scores it when caught up.

    ``repos()`` lists the registered repositories, active or not
    (:func:`embry0.services.background_scope.registry_reader`): the tick reads
    the active ones enrolled in the meter, and keeps what it read of every
    enrolled one. ``read()``, once a tick, the enrollment and the model each
    repository's outliers are judged on ('' for none:
    ``background_models.slop_judge``, else its default), from one snapshot
    (:func:`meter_reader`); ``token_for(repo)`` the forge, credential and
    GitLab URL to fetch with (None when there is none), ``client_for(repo)``
    the GitHub client to read events with (None for a GitLab repository), and
    ``calls`` makes each judgment's call with the repository's key, the
    budget caps and the spend recorded (#164). ``mirror``, ``store``,
    ``exits`` and ``audit`` remove what the meter keeps of a repository that
    left its scope, after ``background.prune_after_days``
    (:class:`embry0.services.slop_meter.prune.Pruner`).
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
        settings: SlopMeterSettings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._repos = repos
        self._read = read
        self._store = store
        self._mirror = mirror
        self._pruner = Pruner(store=store, mirror=mirror, exits=exits, audit=audit, clock=clock)
        self._token_for = token_for
        self._client_for = client_for
        #: The tick's ``repo -> model``, read at its start.
        self._lookup: ModelLookup | None = None
        #: A repository's own bots and embry0's commit identities for it (#184).
        self._bots = bots
        self._calls = calls
        self._settings = settings
        self._clock = clock
        self._http = http
        self._turn = 0

    async def tick(self) -> bool:
        """One pass over the repositories in scope; True when one still has a backlog.

        The settings and the registry are read first: which repositories are
        enrolled, and the judge's model, from one snapshot. What the meter
        keeps under a spelling the registry spells otherwise moves onto the
        registry's. Once the enrollment is settled, what it keeps of a
        repository out of scope is removed after the grace period. Then the
        git work (no API calls). Then the forge calls, dealt one per
        repository in turn from a budget shared by all of them, so a large
        backlog in one cannot starve the rest; the starting repository
        rotates every tick. Last, every repository that is caught up is scored.
        """
        read = await self._read()
        registered = await self._repos()
        await self._respell(registered.every)
        await self._prune(read, registered)
        names = read.enrollment.scope(SCOPE, registered.active)
        if not names:
            return False
        self._lookup = read.models
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

    async def prune_only(self) -> PruneOutcome | None:
        """What a tick removes, and nothing else."""
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
        batch = history[done : done + self._settings.commits_per_tick]
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
        s = self._settings
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
        ``api_calls_per_tick`` calls, stopping at its rate limit's reserve. One
        credential running low never stops a repository read with another (#185).
        """
        s = self._settings
        budgets: dict[str, ApiBudget] = {}
        readers: dict[str, EventReader] = {}
        for repo in order:
            if repo in work and work[repo].forge == "github":
                try:
                    reader = await self._reader(repo)
                except Exception as exc:  # noqa: BLE001 - events are a bonus; the history still scores
                    logger.warning("slop_meter_events_failed", repo=repo, error=str(exc)[:300])
                    continue
                if reader is not None:
                    readers[repo] = reader
                    budgets.setdefault(work[repo].credential, ApiBudget(s.api_calls_per_tick, s.rate_reserve))
        active = list(readers)
        waiting: list[str] = []
        while active:
            for repo in list(active):
                try:
                    going = await readers[repo].step(budgets[work[repo].credential])
                except BudgetSpent:
                    # Its credential's budget is gone; the others' repositories go on.
                    active.remove(repo)
                    waiting.append(repo)
                    continue
                except Exception as exc:  # noqa: BLE001 - this repository's events stop for the tick
                    logger.warning("slop_meter_events_failed", repo=repo, error=str(exc)[:300])
                    active.remove(repo)
                    continue
                if not going:
                    active.remove(repo)
        if waiting:
            spent = [b for b in budgets.values() if b.spent]
            logger.info(
                "slop_meter_api_budget_spent",
                calls=[b.made for b in spent],
                remaining=[b.remaining for b in spent],
                reserve=s.rate_reserve,
                waiting=sorted(waiting),
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

    async def _inputs(self, repo: str) -> Inputs:
        bots = own_identities(await self._bots(repo))
        commits = with_bots([Commit.from_row(r) for r in await self._store.commits(repo)], bots)
        events = [
            Event(str(e["login"]), e["at"], str(e["kind"]))
            for e in await self._store.events(repo)
            if not is_bot(str(e["login"]), "", bots)
        ]
        files = {name: await self._mirror.read(repo, name) for name in (*CONFIG_FILES, AREAS_PATH)}
        return Inputs(commits, events, await self._store.logins(), await self._store.judgments(repo), files)

    async def _score(self, repo: str) -> None:
        inputs = await self._inputs(repo)
        await self._judge(repo, inputs)
        results = await asyncio.to_thread(compute, inputs)
        now = self._clock()
        await self._store.store(repo, results, now)
        await self._store.set_state(repo, computed_at=now, error=None, error_at=None)
        top = results[""]
        logger.info(
            "slop_meter_scored", repo=repo, score=round(top.score, 4), level=top.name, provisional=top.provisional
        )

    async def _judge(self, repo: str, inputs: Inputs) -> None:
        """Ask the model about a few outlier commits not judged yet; what it answers is kept on ``inputs``."""
        weigh = weigher_for(inputs.commits, inputs.files)
        pending = [o for o in outliers(inputs.commits, weigh, inputs.judgments) if o.slop is None]
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
            for outlier in pending[: self._settings.judgments_per_tick]:
                try:
                    slop, reason = await judge(repo, outlier, endpoint, client, self._calls)
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

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        if self._settings.interval_seconds <= 0:
            # Off: nothing is read, but what was kept of a repository that left the scope still goes, once.
            logger.info("slop_meter_off")
            try:
                await self.prune_only()
            except Exception:  # noqa: BLE001 - the next start tries again
                logger.warning("slop_meter_prune_failed", exc_info=True)
            return
        while True:
            try:
                backlog = await self.tick()
            except Exception:  # noqa: BLE001 - the loop goes on
                logger.warning("slop_meter_tick_failed", exc_info=True)
                backlog = False
            await asyncio.sleep(self._settings.catchup_seconds if backlog else self._settings.interval_seconds)
