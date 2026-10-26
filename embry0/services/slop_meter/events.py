"""The forge events that show human attention, read a call at a time under a shared budget (spec 2026-10-26, slop-o-meter).

Issue and pull request comments, review comments and reviews, from GitHub
through the organization's forge client, each from a stored cursor. The
credential is the one jobs clone, push and open pull requests with, so every
API call is counted against a per-tick budget shared by all repositories, and
the tick stops calling the forge when the credential's remaining rate limit
drops below a reserve. An :class:`EventReader` makes one call per ``step``, so
the loop deals calls round-robin and one repository's backlog cannot starve the
others. Logins become commit emails from the login's own commits.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

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
    """The forge API calls one tick may make, shared by every repository, and the rate-limit reserve it keeps.

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
    #: More is waiting than this tick read.
    more: bool = False


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


class EventReader:
    """One repository's forge events since its cursors, one API call per :meth:`step`.

    Stages: the two comment listings (oldest update first, each from its own
    cursor), the pull requests updated since ``reviews_since`` (newest first)
    and their reviews, then the logins its events name that have no email yet.
    ``batch`` holds what was read, with cursors that are safe to store whenever
    the reader stops: a listing read oldest update first has read everything
    up to the newest update it saw, so its cursor moves there.
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
        self._next: str | None = None
        self._pages = 0
        self._pulls: list[tuple[datetime, int]] = []
        self._logins: list[str] | None = None

    @property
    def done(self) -> bool:
        return self._stage >= 5

    async def step(self, budget: ApiBudget) -> bool:
        """Make the next call; False when there is nothing left to read. Raises BudgetSpent with the budget gone."""
        if self._stage <= 1:
            await self._listing_page(budget)
        elif self._stage == 2:
            await self._pulls_page(budget)
        elif self._stage == 3:
            await self._reviews(budget)
        elif self._stage == 4:
            await self._login(budget)
        if self._stage == 4 and self._logins == []:
            self._stage = 5
        return not self.done

    def _advance(self) -> None:
        self._stage += 1
        self._next, self._pages = None, 0

    async def _page(self, budget: ApiBudget, path: str, params: dict[str, Any]) -> tuple[list[Any], bool, bool]:
        """One page of a listing: its items, whether to read the next one, and whether the listing ended.

        A listing cut at the page cap, or answered with an error, has not ended
        (and more is waiting); only one with no next page has.
        """
        if self._next is None:
            response = await budget.get(self._client, path, repo=self._repo, params={**params, "per_page": 100})
        else:
            response = await budget.get(self._client, self._next, repo=self._repo)
        self._pages += 1
        if response.status_code != 200:
            self._next = None
            self.batch.more = True
            return [], False, False
        body = response.json()
        self._next = (getattr(response, "links", {}) or {}).get("next", {}).get("url")
        if self._next and self._pages >= self._max_pages:
            self.batch.more = True
            return (body if isinstance(body, list) else []), False, False
        return (body if isinstance(body, list) else []), bool(self._next), not self._next

    async def _listing_page(self, budget: ApiBudget) -> None:
        name, kind, prefix, cursor = _LISTINGS[self._stage]
        since: datetime | None = getattr(self.batch, cursor)
        params: dict[str, Any] = {"sort": "updated", "direction": "asc"}
        if since is not None:
            params["since"] = _iso(since)
        items, go_on, _ended = await self._page(budget, f"/repos/{self._repo}/{name}", params)
        self.batch.events += _rows(items, kind, prefix, "created_at")
        stamps = [t for t in (_at(i.get("updated_at")) for i in items if isinstance(i, dict)) if t]
        if stamps:
            setattr(self.batch, cursor, max([*stamps, *([since] if since else [])]))
        if not go_on:
            self._advance()

    async def _pulls_page(self, budget: ApiBudget) -> None:
        items, has_next, _ended = await self._page(
            budget, f"/repos/{self._repo}/pulls", {"state": "all", "sort": "updated", "direction": "desc"}
        )
        since = self.batch.reviews_since
        reached = False
        for p in items:
            updated = _at(p.get("updated_at")) if isinstance(p, dict) else None
            if updated is None:
                continue
            if since is not None and updated <= since:
                reached = True
                break
            self._pulls.append((updated, int(p["number"])))
        if reached or not has_next:
            # Pull requests past the page cap are not read: their reviews are
            # skipped, and the cursor moves past them once the ones read are done.
            self._pulls.sort()
            self._advance()

    async def _reviews(self, budget: ApiBudget) -> None:
        if not self._pulls or self._max_pulls <= 0:
            if self._pulls:
                self.batch.more = True
            self._advance()
            return
        updated, number = self._pulls[0]
        response = await budget.get(
            self._client, f"/repos/{self._repo}/pulls/{number}/reviews", repo=self._repo, params={"per_page": 100}
        )
        self._pulls.pop(0)
        self._max_pulls -= 1
        if response.status_code == 200 and isinstance(response.json(), list):
            self.batch.events += _rows(response.json(), "review", "rv", "submitted_at")
        self.batch.reviews_since = updated
        if not self._pulls:
            self._advance()

    async def _login(self, budget: ApiBudget) -> None:
        if self._logins is None:
            named = self._stored | {login.lower() for _id, _kind, login, _at in self.batch.events}
            missing = sorted(n for n in named if n not in self._known and not is_bot(n, ""))
            self._logins = missing[: self._max_logins]
            if len(missing) > self._max_logins:
                self.batch.more = True
            if not self._logins:
                return
        login = self._logins.pop(0)
        response = await budget.get(
            self._client, f"/repos/{self._repo}/commits", repo=self._repo, params={"author": login, "per_page": 1}
        )
        email = ""
        if response.status_code == 200:
            items = response.json()
            if isinstance(items, list) and items:
                email = str((((items[0].get("commit") or {}).get("author")) or {}).get("email") or "")
        self.batch.logins[login] = email
