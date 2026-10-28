"""Asks (spec 2026-10-27): one message to one person about one decision, tracked until answered.

embry0 decides who is asked about what, and when; a chat assistant only
renders and sends (``GET /asks/due``), reports it sent, and passes a button
press back (``POST /asks/answer``). No model turns a reply into an answer.

- **Who.** The person an open hand-off went to, and only them while it is
  open; else each owner of the decision. A quest on offer a chat does not ask
  (``held``) is asked of nobody.
- **When.** Within working hours; one reminder after ``remind_hours``
  unanswered; expired after ``expire_days``, which answers nothing.
- **Codes.** One per decision, from its id, lengthened on a collision and
  never given to another decision, so a late answer cannot land on the wrong
  quest.

The policy is the ``asks`` section of the settings document
(:mod:`embry0.settings.sections.asks`), read by the loop every tick and by
the feed on every request; :func:`ask_settings` turns it into the forms used
here, so this module stays pure.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
    #: Unanswered asks one person holds at once; the next is sent as one is answered or expires.
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
    live: Sequence[Mapping[str, Any]], order: Mapping[str, int], now: datetime, s: AskSettings, *, working: bool
) -> list[tuple[Mapping[str, Any], bool]]:
    """The asks to send now, ``(ask, is_reminder)``: due reminders, then queued ones, worst first, while
    their person holds fewer than ``max_open_per_person`` unanswered asks."""
    held: dict[str, int] = {}
    for a in live:
        if a.get("state") in ("sent", "reminded"):
            held[str(a["recipient"]).lower()] = held.get(str(a["recipient"]).lower(), 0) + 1
    out: list[tuple[Mapping[str, Any], bool]] = [(a, True) for a in live if working and reminder_due(a, now, s)]
    queued = [a for a in live if a.get("state") == "queued" and a["due_at"] <= now]
    for a in sorted(queued, key=lambda a: (order.get(str(a["decision_id"]), 1_000_000), a.get("created_at") or now)):
        who = str(a["recipient"]).lower()
        if held.get(who, 0) < s.max_open_per_person:
            out.append((a, False))
            held[who] = held.get(who, 0) + 1
    return out


def reminder_due(ask: Mapping[str, Any], now: datetime, s: AskSettings) -> bool:
    return (
        ask.get("state") == "sent"
        and int(ask.get("reminders") or 0) == 0
        and isinstance(ask.get("sent_at"), datetime)
        and now - ask["sent_at"] >= timedelta(hours=s.remind_hours)
    )


def expired(ask: Mapping[str, Any], now: datetime, s: AskSettings) -> bool:
    created = ask.get("created_at")
    return isinstance(created, datetime) and now - created >= timedelta(days=s.expire_days)
