"""The Slop-O-Meter's background loop (spec 2026-10-26, slop-o-meter).

Once a day (``SLOP_METER_INTERVAL_SECONDS``; 0 is off), for every repository
the world map shows (plus ``SLOP_METER_REPOS``, minus ``SLOP_METER_EXCLUDE``):
fetch its bare mirror, read the next batch of its history, read its forge
events from their cursors and map a few logins (forge calls dealt round-robin
from a shared per-tick budget, stopping at the credential's rate-limit
reserve), judge a few outlier commits, and, once it is caught up, recompute its
weekly series and its scores (repository and areas) off the event loop. Every
step is bounded and resumes where the last tick stopped; while a repository
has a backlog the loop comes back sooner (``SLOP_METER_CATCHUP_SECONDS``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from embry0.services.areas import AREAS_PATH, area_of_path, parse_areas
from embry0.services.slop_meter.events import ApiBudget, BudgetSpent, EventReader
from embry0.services.slop_meter.history import HISTORY_VERSION, Commit, login_of_email, parse_log
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


def _names(text: str) -> list[str]:
    return [name.strip() for name in str(text or "").split(",") if name.strip()]


def scope(shown: Iterable[str], include: str = "", exclude: str = "") -> list[str]:
    """The repositories the meter reads: those the world map shows, plus ``include``, minus ``exclude``.

    ``shown`` is the world's regions (repositories with jobs, owners or a
    watched stack) that are registered; ``include`` and ``exclude`` are comma-separated names
    (``SLOP_METER_REPOS``, ``SLOP_METER_EXCLUDE``), compared without regard to
    case. A repository that leaves the set is no longer read; its scores stay.
    """
    out: dict[str, str] = {}
    for name in [*shown, *_names(include)]:
        out.setdefault(name.lower(), name)
    for name in _names(exclude):
        out.pop(name.lower(), None)
    return sorted(out.values(), key=str.lower)


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


class SlopMeterLoop:
    """Reads each repository in scope a bounded batch a tick, and scores it when caught up.

    ``repos()`` lists the repositories in scope (:func:`scope`),
    ``token_for(repo)`` the forge, credential and GitLab URL to fetch with
    (None when there is none), ``client_for(repo)`` the GitHub client to read
    events with (None for a GitLab repository), ``model()`` the brief model
    ('' for none).
    """

    def __init__(
        self,
        *,
        repos: Callable[[], Awaitable[list[str]]],
        store: Any,
        mirror: Any,
        token_for: TokenFor,
        client_for: ClientFor,
        model: Callable[[], Awaitable[str]],
        resolver: Any,
        settings: SlopMeterSettings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._repos = repos
        self._store = store
        self._mirror = mirror
        self._token_for = token_for
        self._client_for = client_for
        self._model = model
        self._resolver = resolver
        self._settings = settings
        self._clock = clock
        self._http = http
        self._turn = 0

    async def tick(self) -> bool:
        """One pass over the repositories in scope; True when one still has a backlog.

        The git work comes first (no API calls). Then the forge calls, dealt
        one per repository in turn from a budget shared by all of them, so a
        large backlog in one cannot starve the rest; the starting repository
        rotates every tick. Last, every repository that is caught up is scored.
        """
        names = list(await self._repos())
        if not names:
            return False
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
        return _Work(forge=forge, more=done < len(history))

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
        """Every GitHub repository's events, one API call per repository in turn, under the tick's shared budget."""
        budget = ApiBudget(self._settings.api_calls_per_tick, self._settings.rate_reserve)
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
        active = list(readers)
        while active and not budget.spent:
            for repo in list(active):
                try:
                    going = await readers[repo].step(budget)
                except BudgetSpent:
                    break
                except Exception as exc:  # noqa: BLE001 - this repository's events stop for the tick
                    logger.warning("slop_meter_events_failed", repo=repo, error=str(exc)[:300])
                    active.remove(repo)
                    continue
                if not going:
                    active.remove(repo)
        if active:
            logger.info(
                "slop_meter_api_budget_spent",
                calls=budget.made,
                remaining=budget.remaining,
                reserve=budget.reserve,
                waiting=sorted(active),
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
            if repo in active:
                work[repo].more = True

    async def _inputs(self, repo: str) -> Inputs:
        commits = [Commit.from_row(r) for r in await self._store.commits(repo)]
        events = [Event(str(e["login"]), e["at"], str(e["kind"])) for e in await self._store.events(repo)]
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
        model = await self._model()
        from embry0.services.proposal_briefs import resolve_endpoint

        endpoint = await resolve_endpoint(model, self._resolver) if model else None
        if endpoint is None:
            return
        from embry0.services.slop_meter.judge import judge

        client = self._http or httpx.AsyncClient()
        try:
            for outlier in pending[: self._settings.judgments_per_tick]:
                try:
                    slop, reason = await judge(repo, outlier, endpoint, client)
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
            logger.info("slop_meter_off")
            return
        while True:
            try:
                backlog = await self.tick()
            except Exception:  # noqa: BLE001 - the loop goes on
                logger.warning("slop_meter_tick_failed", exc_info=True)
                backlog = False
            await asyncio.sleep(self._settings.catchup_seconds if backlog else self._settings.interval_seconds)
