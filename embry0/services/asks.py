"""Asks (spec 2026-10-27): one message to one person about one decision, tracked until answered.

embry0 decides who is asked about what, and when; a chat assistant only
renders and sends (``GET /asks/due``), reports it sent, and passes a button
press back (``POST /asks/answer``). No model turns a reply into an answer.

- **Who.** The person an open hand-off went to, and only them while it is
  open; else each owner of the decision. A quest on offer a chat does not ask
  (``held``) is asked of nobody.
- **When.** Within working hours; one reminder after ``remind_hours``
  unanswered; expired ``expire_days`` after it was sent, which answers
  nothing. An ask that waits in the queue, behind its person's limit or for
  working hours, was never sent and does not expire (#295); one the feed
  handed out that was never reported sent expires ``expire_days`` after it
  was handed out, and its person may be asked again ``expire_days`` later.
- **Codes.** One per decision, from its id, lengthened on a collision and
  never given to another decision, so a late answer cannot land on the wrong
  quest.

The policy is the ``asks`` section of the settings document
(:mod:`embry0.settings.sections.asks`), read by the loop every tick and by
the feed on every request. An organization's layer may set when and how often
its people are asked about its decisions, so each ask is held to the values of
its decision's organization (:class:`AskPolicy`); :func:`ask_settings` turns
them into the forms used here, so this module stays pure.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from embry0.settings.layers import PerRepository
from embry0.settings.scope import organization_of
from embry0.settings.sections.asks import AsksSettings, parse_hours, parse_weekdays

#: The states of an ask still waiting on its person.
LIVE = ("queued", "sent", "reminded")


@dataclass(frozen=True)
class AskSettings:
    """The ``asks`` section of the settings document, read into the forms the functions below use.

    No defaults here: the section's model is their only home (:func:`ask_settings`).
    """

    enabled: bool
    #: Working hours, ``(start, end)`` in ``timezone``, on ``weekdays``.
    hours: tuple[int, int]
    #: Weekday numbers, Monday 0.
    weekdays: frozenset[int]
    timezone: str
    remind_hours: float
    expire_days: float
    tick_seconds: float
    #: Unanswered asks one person holds at once about one organization's decisions; the next is
    #: sent as one is answered or expires.
    max_open_per_person: int


def ask_settings(section: AsksSettings) -> AskSettings:
    """The section as the functions here read it.

    The section refused every value that does not read when it was written,
    and a stored value the code no longer accepts reads as its default, so
    nothing here falls back: a value that still does not read raises.
    """
    return AskSettings(
        enabled=section.enabled,
        hours=parse_hours(section.hours),
        weekdays=parse_weekdays(section.weekdays),
        timezone=section.timezone,
        remind_hours=section.remind_hours,
        expire_days=section.expire_days,
        tick_seconds=section.tick_seconds,
        max_open_per_person=section.max_open_per_person,
    )


class AskPolicy:
    """The ``asks`` section of one settings snapshot, as each decision's asks are held to it.

    An ask follows its decision's organization: the values that
    organization's layer sets, and the deployment's for the rest. An ask
    about a decision with no repository, or one not among ``decisions``,
    follows the deployment's. Each repository is resolved once, in memory, so
    a tick or a feed over many decisions reads nothing more.
    """

    def __init__(self, section: PerRepository[AsksSettings], decisions: Iterable[Mapping[str, Any]]) -> None:
        self._section = section
        #: The deployment's values: what an ask about a decision with no repository is held to.
        self.deployment = ask_settings(section.deployment())
        self._repos = {str(d["id"]): str(d.get("repo") or "") for d in decisions}
        self._by_repo: dict[str, tuple[str, AskSettings]] = {}

    def _of(self, decision_id: str) -> tuple[str, AskSettings]:
        repo = self._repos.get(decision_id, "")
        if not repo:
            return "", self.deployment
        found = self._by_repo.get(repo)
        if found is None:
            found = self._by_repo[repo] = (organization_of(repo), ask_settings(self._section.repo(repo)))
        return found

    def settings(self, decision_id: str) -> AskSettings:
        """The values an ask about ``decision_id`` is held to."""
        return self._of(decision_id)[1]

    def organization(self, decision_id: str) -> str:
        """The organization whose ``max_open_per_person`` an ask about ``decision_id`` counts against;
        empty for a decision with no repository, or one not among the decisions."""
        return self._of(decision_id)[0]


def next_working_time(now: datetime, s: AskSettings) -> datetime:
    """``now`` when it is within working hours, else the next start of them."""
    tz = ZoneInfo(s.timezone)
    local = now.astimezone(tz)
    start, end = s.hours
    for offset in range(8):
        day = (local + timedelta(days=offset)).date()
        if day.weekday() not in s.weekdays:
            continue
        opens = datetime.combine(day, time(start), tz)
        closes = datetime.combine(day, time(0), tz) + timedelta(hours=end)
        if offset == 0 and opens <= local < closes:
            return now
        if local < opens:
            return opens.astimezone(now.tzinfo)
    return now


def working(now: datetime, s: AskSettings) -> bool:
    """Whether ``now`` is within the working hours of ``s``."""
    return next_working_time(now, s) == now


def code_for(decision_id: str, taken: Collection[str]) -> str:
    """Four letters from the decision's id, lengthened while another decision holds them."""
    digest = base64.b32encode(hashlib.sha256(decision_id.encode()).digest()).decode().rstrip("=")
    letters = "".join(c for c in digest if c.isalpha())
    for size in range(4, len(letters) + 1):
        if letters[:size] not in taken:
            return letters[:size]
    raise ValueError("no free code")


def recipients(decision: Mapping[str, Any], email_of: Mapping[str, str]) -> list[str]:
    """Who is asked about ``decision``: its hand-off's person, else its owners (as emails where known).

    An owner with no person in embry0 is ``github:<login>``: the deployment may
    know how to reach them, or skip them.
    """
    held_by = (decision.get("assigned_to") or {}).get("email")
    if held_by:
        return [str(held_by).lower()]
    if decision.get("held"):
        return []
    out: list[str] = []
    for owner in decision.get("owners") or []:
        login = str(owner).lstrip("@").lower()
        who = email_of.get(login) or f"github:{login}"
        if who not in out:
            out.append(who)
    return out


@dataclass(frozen=True)
class Plan:
    #: (decision id, recipient) pairs to ask.
    ask: list[tuple[str, str]]
    #: Ids of live asks whose decision is answered, moved on, or now someone else's.
    withdraw: list[str]


def plan(
    decisions: Iterable[Mapping[str, Any]], live: Sequence[Mapping[str, Any]], email_of: Mapping[str, str]
) -> Plan:
    """What changes: the new (decision, person) pairs to ask, and the live asks no longer wanted."""
    # In the decisions' own order (worst first), so the asks queue that way.
    wanted = list(dict.fromkeys((str(d["id"]), r) for d in decisions for r in recipients(d, email_of)))
    have = {(str(a["decision_id"]), str(a["recipient"]).lower()): str(a["id"]) for a in live}
    return Plan(
        ask=[pair for pair in wanted if pair not in have],
        withdraw=sorted(ask_id for pair, ask_id in have.items() if pair not in set(wanted)),
    )


def release(
    live: Sequence[Mapping[str, Any]],
    order: Mapping[str, int],
    now: datetime,
    policy: AskPolicy,
    shown: Callable[[Mapping[str, Any]], bool] | None = None,
) -> list[tuple[Mapping[str, Any], bool]]:
    """The asks to send now, ``(ask, is_reminder)``: due reminders, then queued ones, worst first.

    Each ask is held to its decision's organization's values (``policy``): a
    reminder goes only within that organization's working hours, and a queued
    ask only while its person holds fewer than that organization's
    ``max_open_per_person`` unanswered asks about its decisions. A person asked
    about two organizations' decisions holds each one's number.

    ``shown`` says which asks the caller may be handed, every one when None:
    only those are released. Every unanswered ask in ``live`` counts toward
    its person's limit whether it is shown or not, so a caller that sees only
    some of a person's asks never releases one past the limit (#272).
    """

    def counted(a: Mapping[str, Any]) -> tuple[str, str]:
        return str(a["recipient"]).lower(), policy.organization(str(a["decision_id"]))

    held: dict[tuple[str, str], int] = {}
    for a in live:
        if a.get("state") in ("sent", "reminded"):
            held[counted(a)] = held.get(counted(a), 0) + 1
    handed = [a for a in live if shown is None or shown(a)]
    out: list[tuple[Mapping[str, Any], bool]] = []
    for a in handed:
        s = policy.settings(str(a["decision_id"]))
        if reminder_due(a, now, s) and working(now, s):
            out.append((a, True))
    queued = [a for a in handed if a.get("state") == "queued" and a["due_at"] <= now]
    for a in sorted(
        queued,
        key=lambda a: (_handed_out_before(a), order.get(str(a["decision_id"]), 1_000_000), a.get("created_at") or now),
    ):
        key = counted(a)
        if held.get(key, 0) < policy.settings(str(a["decision_id"])).max_open_per_person:
            out.append((a, False))
            held[key] = held.get(key, 0) + 1
    return out


def _handed_out_before(ask: Mapping[str, Any]) -> bool:
    """Whether the feed handed ``ask`` out before and no one reported it sent.

    Such an ask goes after every queued ask never handed out, whatever their
    ranks (#295): the asks of people the assistant skips and never reports
    would otherwise fill every page, poll after poll, ahead of the fresh ones
    of people it does reach.
    """
    return ask.get("handed_out_at") is not None


def reminder_due(ask: Mapping[str, Any], now: datetime, s: AskSettings) -> bool:
    return (
        ask.get("state") == "sent"
        and int(ask.get("reminders") or 0) == 0
        and isinstance(ask.get("sent_at"), datetime)
        and now - ask["sent_at"] >= timedelta(hours=s.remind_hours)
    )


def expired(ask: Mapping[str, Any], now: datetime, s: AskSettings) -> bool:
    """Whether ``ask`` is still unanswered ``expire_days`` after it was sent, or, never reported sent, after the
    feed first handed it out (#295).

    Not counted from when it was queued: an ask that never left the queue,
    held back by its person's limit or by working hours, does not expire
    however long it waits. It is withdrawn when its decision closes. One the
    feed handed out that no one reported sent, a recipient the assistant
    cannot reach, would otherwise hold its person's slot for ever.
    """
    since = ask.get("sent_at") or ask.get("handed_out_at")
    return isinstance(since, datetime) and now - since >= timedelta(days=s.expire_days)


def still_kept_from(
    expired_pairs: Mapping[tuple[str, str], datetime | None], now: datetime, policy: AskPolicy
) -> set[tuple[str, str]]:
    """The (decision, person) pairs not to ask again now: those whose ask reached them and expired, and those
    whose ask was handed out, never reported sent, and expired less than ``expire_days`` ago (#295)."""
    return {
        pair
        for pair, since in expired_pairs.items()
        if since is None or now - since < timedelta(days=policy.settings(pair[0]).expire_days)
    }
