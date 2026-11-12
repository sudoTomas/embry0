"""The forge events that show human attention, read a call at a time under a budget (spec 2026-10-26, slop-o-meter).

Issue and pull request comments, review comments and reviews, from GitHub
through the organization's forge client, each from a stored cursor. The
credential is the one jobs clone, push and open pull requests with, so every
API call is counted against a per-tick budget of that credential, shared by the
repositories it reads, and the tick stops calling the forge with it when its
remaining rate limit drops below a reserve. An :class:`EventReader` makes one call per ``step``, so
the loop deals calls round-robin and one repository's backlog cannot starve the
others. Logins become commit emails from the login's own commits.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import structlog

from embry0.execution.forge_client import RETRY_STATUSES
from embry0.services.slop_meter.history import is_bot

logger = structlog.get_logger(__name__)


def _at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class BudgetSpent(Exception):
    """The tick's forge calls are spent, or the credential's rate limit is down to its reserve."""


class ApiBudget:
    """The forge API calls one tick may make with one credential, shared by the repositories it reads, and the
    rate-limit reserve it keeps.

    Each response's ``X-RateLimit-Remaining`` is read; once it is below
    ``reserve`` no further call is made this tick (jobs need the rest).
    """

    def __init__(self, calls: int, reserve: int) -> None:
        self.left = max(0, calls)
        self.reserve = reserve
        self.remaining: int | None = None
        self.made = 0
        self._logged = False

    @property
    def spent(self) -> bool:
        return self.left <= 0 or (self.remaining is not None and self.remaining < self.reserve)

    async def get(self, client: Any, path: str, **kwargs: Any) -> Any:
        if self.spent:
            raise BudgetSpent
        self.left -= 1
        self.made += 1
        response = await client.get(path, **kwargs)
        raw = getattr(response, "headers", {}).get("x-ratelimit-remaining")
        if raw is not None and str(raw).isdigit():
            self.remaining = int(raw)
            if self.remaining < self.reserve and not self._logged:
                self._logged = True
                logger.warning(
                    "slop_meter_rate_reserve_reached", remaining=self.remaining, reserve=self.reserve, calls=self.made
                )
        return response


class ShareBudget:
    """One organization's share of a credential's :class:`ApiBudget` for the tick (#169).

    At most ``calls`` of the credential's calls, stopping once the credential's
    remaining rate limit is below ``reserve``; and never past the credential's
    own budget or reserve, which every share of it draws on. A share narrower
    than the credential's ends only its own organization's reads.
    """

    def __init__(self, shared: ApiBudget, *, calls: int, reserve: int) -> None:
        self.shared = shared
        self.left = max(0, calls)
        self.reserve = reserve
        self.made = 0

    @property
    def remaining(self) -> int | None:
        return self.shared.remaining

    @property
    def spent(self) -> bool:
        remaining = self.shared.remaining
        return self.left <= 0 or self.shared.spent or (remaining is not None and remaining < self.reserve)

    async def get(self, client: Any, path: str, **kwargs: Any) -> Any:
        if self.spent:
            raise BudgetSpent
        self.left -= 1
        self.made += 1
        return await self.shared.get(client, path, **kwargs)


class ForgeStopped(Exception):
    """The forge answered a call with a status a retry can change (429, 5xx), after its client's own retries (#465).

    Raised before anything of the call is kept or passed: no cursor moves, no
    pull request or login leaves its list. The loop treats it as a call that
    failed: the read is held at that call, its place stored, and
    :meth:`EventReader.failed` counts it, so a call that keeps failing is passed
    after :data:`MAX_FAILURES` runs in a row.
    """


@dataclass
class EventBatch:
    #: (id, kind, login, at)
    events: list[tuple[str, str, str, datetime]] = field(default_factory=list)
    #: Each listing's cursor: the newest update it has read.
    comments_since: datetime | None = None
    review_comments_since: datetime | None = None
    reviews_since: datetime | None = None
    #: login (lower-case) → its commit email ('' when none was found)
    logins: dict[str, str] = field(default_factory=dict)
    #: A cap cut the read short: a listing's pages (or a next page it cannot ask for by number), or the
    #: reviews or the logins one pass may read. The cursors moved past what was read, so the next pass reads
    #: the rest, and the repository is scored once its events are all read, as its history is (#465).
    more: bool = False
    #: The last call the forge answered with a status a retry can change, and that status (#465): the read
    #: stopped there (:class:`ForgeStopped`).
    stopped: str = ""


def _rows(items: Iterable[Any], kind: str, prefix: str, stamp: str) -> list[tuple[str, str, str, datetime]]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        login = str(((item.get("user") or {}).get("login")) or "")
        at = _at(item.get(stamp))
        if login and at is not None and item.get("id") is not None:
            out.append((f"{prefix}:{item['id']}", kind, login, at))
    return out


#: The comment listings: path, event kind, id prefix, and the cursor each moves.
_LISTINGS = (
    ("issues/comments", "comment", "ic", "comments_since"),
    ("pulls/comments", "review_comment", "rc", "review_comments_since"),
)


#: The items one page of a listing asks for.
PER_PAGE = 100

#: How many times in a row the call a read stands at may fail before the read moves past it.
MAX_FAILURES = 3


@dataclass(frozen=True)
class ReaderPlace:
    """Where a reader stopped in its pass, so the next run goes on from there (#396).

    A run whose budget runs out stops a reader mid-pass. Its cursors are
    stored, and a comment listing goes on from its cursor; the rest of where
    it was is here: its stage, the pages of the stage's listing read so far,
    and in the pull request listing the next page, how many pull requests it
    found and the ones it will still review (the oldest it may review, no
    more), with how many reviews it may still read, the logins it has still to
    look up, and how many times in a row its next call failed. A pass resumed
    from its place reads what one run with budget enough would have read.
    Stored with the repository (``slop_meter_repos.reader_place``), so a
    restart goes on from it too. The next page is kept as its number, never as
    a URL: the reader asks for it on its own listing's path, through the
    forge client's base.
    """

    stage: int
    pages: int = 0
    next_page: int | None = None
    pulls: tuple[tuple[datetime, int], ...] = ()
    found: int = 0
    seen: tuple[int, ...] = ()
    pulls_left: int = 0
    logins: tuple[str, ...] | None = None
    failures: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "pages": self.pages,
            "next_page": self.next_page,
            "pulls": [[_iso(updated), number] for updated, number in self.pulls],
            "found": self.found,
            "seen": list(self.seen),
            "pulls_left": self.pulls_left,
            "logins": None if self.logins is None else list(self.logins),
            "failures": self.failures,
        }

    @classmethod
    def from_json(cls, data: Any) -> ReaderPlace | None:
        """The place a stored value names; None for nothing, or anything else, so the read starts over."""
        if not isinstance(data, dict):
            return None
        try:
            pulls = []
            for updated, number in data.get("pulls") or []:
                at = _at(updated)
                if at is None:
                    return None
                pulls.append((at, int(number)))
            logins = data.get("logins")
            stage = int(data["stage"])
            if not 0 <= stage <= 4:
                return None
            next_page = data.get("next_page")
            if next_page is not None and (
                isinstance(next_page, bool) or not isinstance(next_page, int) or next_page < 2
            ):
                return None
            return cls(
                stage=stage,
                pages=int(data.get("pages") or 0),
                next_page=next_page,
                pulls=tuple(pulls),
                found=int(data.get("found") or 0),
                seen=tuple(_number(n) for n in data.get("seen") or []),
                pulls_left=int(data.get("pulls_left") or 0),
                logins=None if logins is None else tuple(str(login) for login in logins),
                failures=int(data.get("failures") or 0),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _number(value: Any) -> int:
    """A pull request's number from a stored place; anything but a whole number is no place (ValueError)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"not a pull request number: {value!r}")
    return value


def _next_page(response: Any) -> tuple[bool, int | None]:
    """Whether the response links a next page, and that page's number (None when its link names none)."""
    url = ((getattr(response, "links", {}) or {}).get("next") or {}).get("url")
    if not url:
        return False, None
    try:
        page = int(parse_qs(urlsplit(str(url)).query)["page"][0])
    except (KeyError, IndexError, ValueError):
        return True, None
    return True, page if page >= 2 else None


class EventReader:
    """One repository's forge events since its cursors, one API call per :meth:`step`.

    Stages: the two comment listings (oldest update first, each from its own
    cursor), the pull requests updated since ``reviews_since`` (newest first)
    and their reviews, then the logins its events name that have no email yet.
    ``batch`` holds what was read, with cursors that are safe to store whenever
    the reader stops: a listing read oldest update first has read everything
    up to the newest update it saw, so its cursor moves there. ``place``,
    when given, is where a reader of an earlier tick stopped (:attr:`place`):
    this one goes on from there instead of starting the pass over.
    """

    def __init__(
        self,
        client: Any,
        repo: str,
        *,
        comments_since: datetime | None,
        review_comments_since: datetime | None,
        reviews_since: datetime | None,
        max_pages: int,
        max_pulls: int,
        known_logins: Iterable[str] = (),
        stored_logins: Iterable[str] = (),
        max_logins: int = 0,
        place: ReaderPlace | None = None,
    ) -> None:
        self._client = client
        self._repo = repo
        self._max_pages = max_pages
        self._max_pulls = max_pulls
        self._known = {k.lower() for k in known_logins}
        self._stored = {k.lower() for k in stored_logins}
        self._max_logins = max_logins
        self.batch = EventBatch(
            comments_since=comments_since, review_comments_since=review_comments_since, reviews_since=reviews_since
        )
        self._stage = 0  # 0, 1: the listings; 2: pulls; 3: reviews; 4: logins; 5: done
        self._next: int | None = None  # the listing's next page, by number
        self._since: datetime | None = None  # the cursor a comment listing's first page was asked for with
        self._pages = 0
        self._pulls: list[tuple[datetime, int]] = []
        self._found = 0
        self._seen: set[int] = set()  # the numbers of the pull requests the listing has found
        self._logins: list[str] | None = None
        self._failures = 0
        if place is not None:
            self._stage, self._pages = place.stage, place.pages
            # A comment listing goes on from its cursor, which holds everything it read; the pull request
            # listing has none, so it goes on from its next page.
            self._next = place.next_page if place.stage == 2 else None
            self._pulls = list(place.pulls)
            self._found = place.found
            self._seen = set(place.seen) | {number for _updated, number in place.pulls}
            self._max_pulls = place.pulls_left
            self._logins = None if place.logins is None else list(place.logins)
            self._failures = place.failures

    @property
    def done(self) -> bool:
        return self._stage >= 5

    @property
    def place(self) -> ReaderPlace | None:
        """Where the pass stands, for the next run to go on from; None once it is done.

        Of the pull requests found it keeps only the ones the pass will still
        review: the oldest, as many as it may review. A later page of the
        listing only finds older ones, so a newer one past that number would
        never be reviewed this pass anyway.
        """
        if self.done:
            return None
        return ReaderPlace(
            stage=self._stage,
            pages=self._pages,
            next_page=self._next,
            pulls=tuple(sorted(self._pulls)[: max(0, self._max_pulls)]),
            found=self._found,
            # While the listing is read, every pull request it found, so a page that brings one again (a newer one
            # pushed the rest down) does not count it twice; once it is read, nothing does.
            seen=tuple(sorted(self._seen)) if self._stage == 2 else (),
            pulls_left=self._max_pulls,
            logins=None if self._logins is None else tuple(self._logins),
            failures=self._failures,
        )

    @property
    def stage(self) -> str:
        """What the read stands at, in words, for a log line."""
        return ("issue comments", "review comments", "pull requests", "reviews", "logins", "done")[self._stage]

    async def step(self, budget: ApiBudget | ShareBudget) -> bool:
        """Make the next call; False when there is nothing left to read. Raises BudgetSpent with the budget gone,
        and whatever the call raised when it failed (:meth:`failed` counts it)."""
        if self._stage <= 1:
            await self._listing_page(budget)
        elif self._stage == 2:
            await self._pulls_page(budget)
        elif self._stage == 3:
            await self._reviews(budget)
        elif self._stage == 4:
            await self._login(budget)
        self._failures = 0
        if self._stage == 4 and self._logins == []:
            self._stage = 5
        return not self.done

    def failed(self) -> bool:
        """Count a call that raised. The :data:`MAX_FAILURES`-th failure in a row at one place moves the read past
        that call, so one call that always fails cannot hold the read there for good: a listing ends for this
        pass, a pull request's reviews are not read (the reviews cursor moves past it), a login is kept with no
        email. True when it moved past."""
        self._failures += 1
        if self._failures < MAX_FAILURES:
            return False
        self._failures = 0
        if self._stage == 3 and self._pulls and self._max_pulls > 0:
            updated, _number = self._pulls.pop(0)
            self._max_pulls -= 1
            self.batch.reviews_since = updated
            if not self._pulls:
                self._advance()
        elif self._stage == 4 and self._logins:
            self.batch.logins[self._logins.pop(0)] = ""
            if not self._logins:
                self._stage = 5
        else:
            self._advance()
        return True

    def _advance(self) -> None:
        self._stage += 1
        self._next, self._pages = None, 0

    def _answered(self, path: str, response: Any) -> bool:
        """Whether ``path`` was answered 200.

        A status a retry can change (429, 5xx; the forge client has already
        retried it) raises :class:`ForgeStopped`, before the caller keeps or
        passes anything. Any other is the forge refusing that call, which a
        retry does not change: it is logged, and the read goes on past it.
        """
        status = response.status_code
        if status == 200:
            return True
        if status in RETRY_STATUSES:
            self.batch.stopped = f"{path} answered {status}"
            raise ForgeStopped(self.batch.stopped)
        logger.warning("slop_meter_events_refused", repo=self._repo, path=path, status=status)
        return False

    async def _page(
        self, budget: ApiBudget | ShareBudget, path: str, params: dict[str, Any], *, capped: bool = True
    ) -> tuple[list[Any], bool, bool]:
        """One page of a listing: its items, whether to read the next one, and whether the listing ended.

        A listing cut at the page cap (``capped``) has not ended, and more is
        waiting; nor has one answered with anything but 200 (:meth:`_answered`).
        Only one with no next page has.
        Every page is asked for on ``path``, by its number: the reader never
        fetches a URL a response's ``Link`` header, or a stored place, names.
        """
        page = {} if self._next is None else {"page": self._next}
        response = await budget.get(
            self._client, path, repo=self._repo, params={**params, "per_page": PER_PAGE, **page}
        )
        answered = self._answered(path, response)
        self._pages += 1
        if not answered:
            self._next = None
            return [], False, False
        body = response.json()
        linked, self._next = _next_page(response)
        if linked and self._next is None:
            # A next page this reader cannot ask for by number: the listing is cut here, as at the page cap.
            self.batch.more = True
            return (body if isinstance(body, list) else []), False, False
        if capped and self._next and self._pages >= self._max_pages:
            self.batch.more = True
            return (body if isinstance(body, list) else []), False, False
        return (body if isinstance(body, list) else []), bool(self._next), not self._next

    async def _listing_page(self, budget: ApiBudget | ShareBudget) -> None:
        name, kind, prefix, cursor = _LISTINGS[self._stage]
        if self._next is None:
            # Every page of a listing is asked for with the cursor its first page had: the cursor moves as pages
            # are read, and a later page asked for from the moved one would leave out the comments in between.
            self._since = getattr(self.batch, cursor)
        params: dict[str, Any] = {"sort": "updated", "direction": "asc"}
        if self._since is not None:
            params["since"] = _iso(self._since)
        items, go_on, _ended = await self._page(budget, f"/repos/{self._repo}/{name}", params)
        self.batch.events += _rows(items, kind, prefix, "created_at")
        stamps = [t for t in (_at(i.get("updated_at")) for i in items if isinstance(i, dict)) if t]
        current: datetime | None = getattr(self.batch, cursor)
        if stamps:
            setattr(self.batch, cursor, max([*stamps, *([current] if current else [])]))
        if not go_on:
            self._advance()

    async def _pulls_page(self, budget: ApiBudget | ShareBudget) -> None:
        """One page of the pull requests updated since the reviews cursor, newest first.

        The cap is on the pull requests found, ``max_pages`` pages' worth, not
        on the pages read: one opened or updated between two pages moves to
        the first and pushes the rest down a place, so a page can bring again
        one already found. Counted by pages, each such shift would push an
        older pull request past the cap, and the cursor past it for good.
        """
        items, has_next, _ended = await self._page(
            budget,
            f"/repos/{self._repo}/pulls",
            {"state": "all", "sort": "updated", "direction": "desc"},
            capped=False,
        )
        since = self.batch.reviews_since
        cap = self._max_pages * PER_PAGE
        reached = False
        found = self._seen
        for p in items:
            updated = _at(p.get("updated_at")) if isinstance(p, dict) else None
            if updated is None:
                continue
            if since is not None and updated <= since:
                reached = True
                break
            number = int(p["number"])
            if number in found:
                continue
            if self._found >= cap:
                break
            found.add(number)
            self._found += 1
            self._pulls.append((updated, number))
        full = self._found >= cap
        # However many pages the shifts cost, a pass reads at most twice its page cap of them.
        if reached or full or not has_next or self._pages >= 2 * self._max_pages:
            if has_next and not reached:
                # Pull requests past the cap are not read: their reviews are
                # skipped, and the cursor moves past them once the ones read are done.
                self.batch.more = True
            self._pulls.sort()
            self._advance()

    async def _reviews(self, budget: ApiBudget | ShareBudget) -> None:
        if not self._pulls or self._max_pulls <= 0:
            if self._pulls:
                self.batch.more = True
            self._advance()
            return
        updated, number = self._pulls[0]
        path = f"/repos/{self._repo}/pulls/{number}/reviews"
        response = await budget.get(self._client, path, repo=self._repo, params={"per_page": 100})
        # A stop raises here: this pull request stays first in line, and the cursor short of it.
        answered = self._answered(path, response)
        self._pulls.pop(0)
        self._max_pulls -= 1
        if answered and isinstance(response.json(), list):
            self.batch.events += _rows(response.json(), "review", "rv", "submitted_at")
        self.batch.reviews_since = updated
        if not self._pulls:
            self._advance()

    async def _login(self, budget: ApiBudget | ShareBudget) -> None:
        if self._logins is None:
            named = self._stored | {login.lower() for _id, _kind, login, _at in self.batch.events}
            missing = sorted(n for n in named if n not in self._known and not is_bot(n, ""))
            self._logins = missing[: self._max_logins]
            if len(missing) > self._max_logins:
                self.batch.more = True
            if not self._logins:
                return
        login = self._logins[0]
        path = f"/repos/{self._repo}/commits"
        response = await budget.get(self._client, path, repo=self._repo, params={"author": login, "per_page": 1})
        # A stop raises here, and the login stays first on the list.
        answered = self._answered(path, response)
        # Taken off the list once looked up: a budget spent before the call leaves it for the next tick.
        self._logins.pop(0)
        email = ""
        if answered:
            items = response.json()
            if isinstance(items, list) and items:
                email = str((((items[0].get("commit") or {}).get("author")) or {}).get("email") or "")
        self.batch.logins[login] = email
