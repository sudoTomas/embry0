"""The ``asks`` section of the settings document: the YAML an operator writes, and what it refuses.

A value that does not read is refused naming its key, never replaced by a
guess (#202): working hours that do not parse, an unknown day name, a time
zone this system does not know. The section is read through the catalog, so
each test fails with its own message on a tree that has no asks section.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from embry0.services.asks import ask_settings, next_working_time
from embry0.settings.document import DocumentInvalid, check, load_yaml, read_sections
from embry0.settings.env import env_name
from embry0.settings.registry import Section
from embry0.settings.sections import CATALOG


def _asks() -> Section[Any]:
    section = CATALOG.get("asks")
    assert section is not None, "asks is a section of the settings document"
    return section


def _model(data: dict[str, Any]) -> Any:
    """The asks section as a loop reads it from stored ``data``."""
    return read_sections(CATALOG, data).models[_asks().name]


def _stored(document: dict[str, Any]) -> Any:
    """What a write of ``document`` stores, or each of its problems as ``(loc, msg)``."""
    try:
        return check(CATALOG, document, stored={}).data
    except DocumentInvalid as exc:
        return [(problem.loc, problem.msg) for problem in exc.problems]


def _problems(values: dict[str, Any]) -> list[tuple[tuple[str | int, ...], str]]:
    """Each problem a document holding ``asks: values`` has, as ``(loc, msg)``."""
    found = _stored({"asks": values})
    return found if isinstance(found, list) else []


def test_the_section_reads_as_an_operator_writes_it() -> None:
    text = (
        "# asks go out in the office's hours\n"
        "asks:\n"
        "  enabled: true\n"
        "  hours: 08-17\n"
        "  weekdays: sun-thu\n"
        "  timezone: Asia/Dubai\n"
        "  remind_hours: 12\n"
        "  expire_days: 3\n"
    )
    data = _stored(load_yaml(text))
    assert data == {
        "asks": {
            "enabled": True,
            "hours": "08-17",
            "weekdays": "sun-thu",
            "timezone": "Asia/Dubai",
            "remind_hours": 12.0,
            "expire_days": 3.0,
        }
    }, "unquoted 08-17 and sun-thu are text in YAML, and are stored as written"
    s = ask_settings(_model(data))
    assert (s.enabled, s.hours, s.weekdays, s.timezone) == (True, (8, 17), frozenset({6, 0, 1, 2, 3}), "Asia/Dubai")
    assert (s.remind_hours, s.expire_days, s.max_open_per_person, s.tick_seconds) == (12.0, 3.0, 3, 120.0)


def test_nothing_written_is_every_default() -> None:
    s = ask_settings(_model({}))
    assert (s.enabled, s.hours, s.weekdays, s.timezone) == (False, (9, 18), frozenset(range(5)), "UTC")
    assert (s.remind_hours, s.expire_days, s.max_open_per_person, s.tick_seconds) == (24.0, 7.0, 3, 120.0)


@pytest.mark.parametrize("hours", ["nine to five", "18-09", "09-09", "09-25", "9", "", "09:00-18:00", "-1-5", "٠٩-١٨"])
def test_working_hours_that_do_not_read_are_refused_naming_the_key(hours: str) -> None:
    found = _problems({"hours": hours})
    assert [loc for loc, _ in found] == [("asks", "hours")], f"hours {hours!r} must be refused as asks.hours: {found}"


@pytest.mark.parametrize("weekdays", ["never", "mon-fry", "", "mon,,fri", "mon-", "mon-wed-fri", "monday", "1-5"])
def test_days_that_do_not_read_are_refused_naming_the_key(weekdays: str) -> None:
    found = _problems({"weekdays": weekdays})
    assert [loc for loc, _ in found] == [("asks", "weekdays")], (
        f"weekdays {weekdays!r} must be refused as asks.weekdays, not dropped or read as mon-fri: {found}"
    )


@pytest.mark.parametrize("zone", ["Mars/Base", "Europe/Berlinn", "Europe", "../etc/passwd", "/etc/localtime", ""])
def test_a_time_zone_this_system_does_not_know_is_refused_naming_the_key(zone: str) -> None:
    found = _problems({"timezone": zone})
    assert [loc for loc, _ in found] == [("asks", "timezone")], (
        f"timezone {zone!r} must be refused when it is written, not raise in every tick: {found}"
    )


def test_every_bad_value_is_named_at_once_and_none_is_repeated_back() -> None:
    found = _problems({"hours": "half past nine", "weekdays": "someday", "timezone": "Atlantis/Lost"})
    assert [loc for loc, _ in found] == [("asks", "hours"), ("asks", "weekdays"), ("asks", "timezone")], found
    for _, msg in found:
        for value in ("half past nine", "someday", "Atlantis/Lost"):
            assert value not in msg, f"a refusal never repeats the value it refused: {msg!r}"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("enabled", "true"),
        ("remind_hours", 0),
        ("remind_hours", 1e308),
        ("expire_days", -1),
        ("expire_days", 1000),
        ("max_open_per_person", 0),
        ("max_open_per_person", 2.5),
        ("tick_seconds", 10),
        ("tick_seconds", 1e12),
    ],
)
def test_a_number_or_switch_out_of_bounds_is_refused_naming_the_key(key: str, value: Any) -> None:
    """An upper bound too: a remind or expiry past a timedelta, or a sleep past the clock, would raise in a tick."""
    found = _problems({key: value})
    assert [loc for loc, _ in found] == [("asks", key)], f"{key} {value!r} must be refused as asks.{key}: {found}"


def test_a_spelling_that_reads_the_same_is_stored_one_way() -> None:
    stored = _stored({"asks": {"hours": " 9 - 17 ", "weekdays": "Mon, Wed,FRI"}})
    assert stored == {"asks": {"hours": "09-17", "weekdays": "mon,wed,fri"}}, (
        "an equivalent spelling is stored one way, so an equivalent variable is not imported as a change"
    )


def test_a_stored_value_the_code_refuses_reads_as_its_default_and_never_reaches_a_tick() -> None:
    """A value stored under an older, looser schema, or written to the row by hand."""
    sections = read_sections(CATALOG, {"asks": {"timezone": "Mars/Base", "hours": "late", "remind_hours": 6}})
    model: Any = sections.models.get("asks")
    read = None if model is None else (model.timezone, model.hours, model.remind_hours)
    assert read == ("UTC", "09-18", 6.0), "each refused key reads as its default; its sibling keeps its value"
    assert sorted(r.key for r in sections.refused) == ["hours", "timezone"]
    now = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
    assert next_working_time(now, ask_settings(model)) == now


def test_what_an_organization_may_override_and_where_each_key_came_from() -> None:
    section = CATALOG.get("asks")
    assert section is not None and section.org_keys == {
        "hours",
        "weekdays",
        "timezone",
        "remind_hours",
        "expire_days",
        "max_open_per_person",
    }, "an organization may set when and how often its people are asked; whether asks run, and the tick, stay global"
    assert section.repo_keys == frozenset()
    assert {key: env_name(imported.attr) for key, imported in section.imports().items()} == {
        "enabled": "ASKS_ENABLED",
        "hours": "ASK_HOURS",
        "weekdays": "ASK_WEEKDAYS",
        "timezone": "ASK_TIMEZONE",
        "remind_hours": "ASK_REMIND_HOURS",
        "expire_days": "ASK_EXPIRE_DAYS",
        "max_open_per_person": "ASK_MAX_OPEN_PER_PERSON",
        "tick_seconds": "ASK_TICK_SECONDS",
    }, "every ASK* variable is imported once into its key"
