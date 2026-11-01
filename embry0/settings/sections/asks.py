"""The ``asks`` section: when and how often embry0 asks a person about a decision.

A key an operator writes the way they would say it::

    asks:
      enabled: true
      hours: "08-17"          # HH-HH, start before end, 00 to 24
      weekdays: mon-fri       # a day, a range (sun-thu wraps), or a list: mon,wed,fri
      timezone: Europe/Berlin # an IANA zone this system knows

A value that does not read is refused when it is written, naming its key,
never replaced by a guess: so a tick never meets one. :func:`parse_hours`
and :func:`parse_weekdays` are the one reading of the two text forms, for
the validators here and for :func:`embry0.services.asks.ask_settings`.
"""

from __future__ import annotations

import re
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from embry0.settings.registry import Section, SectionModel

#: Day names, Monday first, so a day's index is its ``date.weekday()``.
DAYS: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_HOURS: Final = re.compile(r"(\d{1,2})-(\d{1,2})", re.ASCII)
_HOURS_FORM: Final = "write working hours as HH-HH, such as 09-18"
_HOURS_RANGE: Final = "working hours run from 00 to 24, and start before they end"
_DAYS_FORM: Final = (
    "write days as mon to sun: one day, a range such as mon-fri or sun-thu, or a list such as mon,wed,fri"
)
_ZONE: Final = "not a time zone this system knows; use an IANA name such as Europe/Berlin or UTC"


def _compact(text: str) -> str:
    return "".join(text.split())


def parse_hours(text: str) -> tuple[int, int]:
    """``"09-18"`` as ``(9, 18)``: whole hours, start before end, 0 to 24.

    Raises :class:`ValueError`, whose message never repeats the text.
    """
    match = _HOURS.fullmatch(_compact(text))
    if match is None:
        raise ValueError(_HOURS_FORM)
    start, end = int(match[1]), int(match[2])
    if not 0 <= start < end <= 24:
        raise ValueError(_HOURS_RANGE)
    return start, end


def parse_weekdays(text: str) -> frozenset[int]:
    """``"mon-fri"``, ``"sun-thu"`` or ``"mon,wed,fri"`` as weekday numbers, Monday 0.

    A range that runs past Sunday wraps to Monday. An unknown name, an empty
    part or an empty text raises :class:`ValueError`, whose message never
    repeats the text.
    """
    out: set[int] = set()
    for part in _compact(text).lower().split(","):
        first, dash, last = part.partition("-")
        if first not in DAYS or (dash and last not in DAYS):
            raise ValueError(_DAYS_FORM)
        i = DAYS.index(first)
        j = DAYS.index(last) if dash else i
        out.update(range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)])
    return frozenset(out)


def check_timezone(name: str) -> str:
    """``name`` when this system can load it as a time zone; else :class:`ValueError`.

    Loading it is exactly what a tick does with it, so a zone that passes
    here never fails there.
    """
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError(_ZONE) from None
    return name


class AsksSettings(SectionModel):
    enabled: bool = Field(
        default=False,
        description=(
            "Keep one ask per person a decision waits on, for a chat assistant to send from GET /asks/due. "
            "While false the ask loop does nothing and the feed is empty; asks already stored wait."
        ),
    )
    hours: str = Field(
        default="09-18",
        max_length=16,
        description=(
            "Working hours in `timezone`, HH-HH from 00 to 24, start first. "
            "An ask is due, and a reminder goes, only within them."
        ),
    )
    weekdays: str = Field(
        default="mon-fri",
        max_length=64,
        description=(
            "The days asks go out: a day name (mon to sun), a range such as mon-fri or sun-thu, "
            "or a list such as mon,wed,fri."
        ),
    )
    timezone: str = Field(
        default="UTC",
        max_length=64,
        description="The IANA time zone `hours` and `weekdays` are read in, such as Europe/Berlin.",
    )
    remind_hours: float = Field(
        default=24.0, gt=0, le=8760, description="Hours an ask waits unanswered before its one reminder."
    )
    expire_days: float = Field(
        default=7.0,
        gt=0,
        le=365,
        description=(
            "Days after it was sent that an unanswered ask expires. That answers nothing, "
            "and the person is not asked about that decision again. One handed out but never "
            "reported sent expires this many days after it was handed out, and its person may "
            "be asked again this many days later. An ask still queued does not expire."
        ),
    )
    max_open_per_person: int = Field(
        default=3,
        ge=1,
        description=(
            "Unanswered asks one person holds at once about one organization's decisions; the next, "
            "worst first, is released as one is answered or expires."
        ),
    )
    lease_minutes: float = Field(
        default=15.0,
        gt=0,
        le=1440,
        description=(
            "Minutes an ask handed out by `GET /asks/due` stays with the poll that took it: it counts toward "
            "its person's limit and goes to no other poll until it is reported sent or this lapses. Then it is "
            "handed out again, before any other ask of its person. A second report of a send within this many "
            "minutes of the first, with no reminder handed out between, is a second poll's copy, not the reminder."
        ),
    )
    tick_seconds: float = Field(
        default=120.0,
        ge=30,
        le=86400,
        description=(
            "How often the ask loop runs: it expires asks past `expire_days`, withdraws those whose decision "
            "was answered or moved on, and queues one for each person a decision now waits on."
        ),
    )

    @field_validator("hours")
    @classmethod
    def _hours(cls, value: str) -> str:
        start, end = parse_hours(value)
        return f"{start:02d}-{end:02d}"

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, value: str) -> str:
        parse_weekdays(value)
        return _compact(value).lower()

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        return check_timezone(value)


ASKS = Section(
    name="asks",
    model=AsksSettings,
    summary="When and how often embry0 asks a person about a decision that waits on them.",
    # What an organization may set for asks about its own decisions: when and
    # how often its people are asked, and how many asks about its decisions
    # one person holds. The ask loop and GET /asks/due read each ask by its
    # decision's organization. Whether asks run at all, and how often the one
    # loop ticks, stay the deployment's.
    org_keys=frozenset({"hours", "weekdays", "timezone", "remind_hours", "expire_days", "max_open_per_person"}),
    repo_keys=frozenset(),
    env={
        "enabled": "asks_enabled",
        "hours": "ask_hours",
        "weekdays": "ask_weekdays",
        "timezone": "ask_timezone",
        "remind_hours": "ask_remind_hours",
        "expire_days": "ask_expire_days",
        "max_open_per_person": "ask_max_open_per_person",
        "tick_seconds": "ask_tick_seconds",
    },
)
