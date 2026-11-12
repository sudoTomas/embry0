"""A stack's stored signature masks are read entry by entry, and what its ticks leave out is said.

A write checks a stack's whole list of masks, so a stored list fails only a
check it was written before: masks stored before they were matched in linear
time, by an engine that read look-around and back-references. Before, one
such entry left out the stack's every mask, with a warning on every tick.
Now it leaves out itself alone; the stack's view lists it with the reason, an
error line says it once per stack, and an audit row once across restarts. A
mask whose ``\\b`` reads otherwise than Python's ``re`` is kept, and said once
as a warning.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import pytest
import structlog

from embry0.services import watcher_stacks
from embry0.services.watcher_stacks import WatcherStack
from tests.unit.services.test_watcher_signature_masks import IDS, MASK, _tick_clusters, _watcher

LOOK_BEHIND = {"pattern": r"(?<=record )rec_\w+", "replacement": "<record>"}
PLAIN = {"pattern": r"tkt-[0-9]{4}", "replacement": "<ticket>"}
ASCII_EDGE = {"pattern": r"(?a)\bord_[0-9]{4}\b", "replacement": "<order>"}


@pytest.fixture(autouse=True)
def _nothing_reported_yet() -> Iterator[None]:
    """Each stack's reading is logged once per process: each test starts with none logged."""
    reported = getattr(watcher_stacks, "_REPORTED", {})
    reported.clear()
    yield
    reported.clear()


def _stack(repo: str, masks: Any) -> WatcherStack:
    return WatcherStack(repo=repo, logql='{compose_project="acme"}', settings={"signature_masks": masks})


def _events(logs: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == event]


# ---- one refused entry leaves out itself alone ----------------------------------


@pytest.mark.asyncio
async def test_a_stored_mask_that_no_longer_checks_leaves_the_stacks_other_masks_applied() -> None:
    clusters = await _tick_clusters(_watcher({"signature_masks": [LOOK_BEHIND, MASK]}, repo="acme/mixed"))
    assert [c.template for c in clusters] == ["WARN invoice sync failed for record <record>: upstream timeout"], (
        f"one stored mask the engine refuses dropped the stack's other masks: {[c.template for c in clusters]}"
    )
    assert clusters[0].count == len(IDS), [c.count for c in clusters]


def test_each_entry_is_read_on_its_own_and_each_refused_one_named_with_why() -> None:
    reading = watcher_stacks.read_signature_masks([MASK, LOOK_BEHIND, {"pattern": "rec_["}, "rec_\\d+", PLAIN])
    assert reading.usable == (MASK, PLAIN), reading.usable
    assert [(r["index"], r["pattern"]) for r in reading.refused] == [
        (1, LOOK_BEHIND["pattern"]),
        (2, "rec_["),
        (3, None),
    ], reading.refused
    reasons = [r["reason"] for r in reading.refused]
    assert reasons[0].startswith("settings.signature_masks.1.pattern: ") and "look-ahead and look-behind" in reasons[0]
    assert reasons[1].startswith("settings.signature_masks.2.pattern: ") and "not a Python regular" in reasons[1]
    assert reasons[2].startswith("settings.signature_masks.3: "), reasons[2]


def test_masks_past_the_number_a_stack_may_have_are_left_out_and_named() -> None:
    reading = watcher_stacks.read_signature_masks([PLAIN] * (watcher_stacks.MAX_SIGNATURE_MASKS + 2))
    assert len(reading.usable) == watcher_stacks.MAX_SIGNATURE_MASKS
    assert [r["index"] for r in reading.refused] == [50, 51], reading.refused
    assert "past the 50 masks a stack may have" in reading.refused[0]["reason"], reading.refused[0]


def test_a_value_that_is_no_list_is_refused_whole() -> None:
    reading = watcher_stacks.read_signature_masks({"pattern": "rec_\\d+"})
    assert reading.usable == () and [r["index"] for r in reading.refused] == [None], reading


def test_no_masks_reads_as_none_refused() -> None:
    assert watcher_stacks.read_signature_masks(None) == watcher_stacks.MaskReading()
    assert watcher_stacks.read_signature_masks([]) == watcher_stacks.MaskReading()


# ---- said once ------------------------------------------------------------------


def test_refused_masks_are_logged_at_error_level_once_per_stack_and_reading() -> None:
    mixed = _stack("acme/mixed", [LOOK_BEHIND, PLAIN])
    with structlog.testing.capture_logs() as logs:
        for _tick in range(3):
            assert watcher_stacks.signature_masks_of(mixed) == [PLAIN]
        assert watcher_stacks.signature_masks_of(_stack("acme/other", [LOOK_BEHIND])) == []
    said = _events(logs, "watcher_signature_masks_refused")
    assert [(e["stack"], e["log_level"], e["kept"]) for e in said] == [
        ("acme/mixed", "error", 1),
        ("acme/other", "error", 0),
    ], f"every tick reads the masks: a reading must be said once per stack, at error level: {said}"
    assert said[0]["refused"] == [
        {"index": 0, "reason": watcher_stacks.read_signature_masks([LOOK_BEHIND]).refused[0]["reason"]}
    ]

    with structlog.testing.capture_logs() as logs:
        watcher_stacks.signature_masks_of(_stack("acme/mixed", [PLAIN, LOOK_BEHIND]))
        watcher_stacks.signature_masks_of(_stack("acme/mixed", [PLAIN]))
        watcher_stacks.signature_masks_of(_stack("acme/mixed", [PLAIN]))
    assert [e["refused"][0]["index"] for e in _events(logs, "watcher_signature_masks_refused")] == [1], (
        "a stack whose stored masks change is said again, and one with none refused is not"
    )


def test_a_mask_whose_word_edge_reads_otherwise_than_re_is_kept_and_said_once() -> None:
    stack = _stack("acme/edges", [MASK, ASCII_EDGE, PLAIN, LOOK_BEHIND])
    with structlog.testing.capture_logs() as logs:
        kept = watcher_stacks.signature_masks_of(stack)
        watcher_stacks.signature_masks_of(stack)
    assert kept == [MASK, ASCII_EDGE, PLAIN], kept
    warned = _events(logs, "watcher_signature_masks_ascii_word_boundary")
    assert [(e["masks"], e["log_level"]) for e in warned] == [([0], "warning")], (
        f"\\b outside (?a) reads only ASCII letters and digits as word characters: say so once, for that mask: {warned}"
    )
    assert len(_events(logs, "watcher_signature_masks_refused")) == 1


def test_masks_that_check_are_said_nowhere() -> None:
    with structlog.testing.capture_logs() as logs:
        assert watcher_stacks.signature_masks_of(_stack("acme/clean", [PLAIN, ASCII_EDGE])) == [PLAIN, ASCII_EDGE]
    assert [e["event"] for e in logs if e["event"].startswith("watcher_signature_masks")] == []


# ---- the audit row at startup ---------------------------------------------------


class _Stacks:
    def __init__(self, *stacks: WatcherStack) -> None:
        self.stacks = list(stacks)

    async def list_all(self, *, enabled_only: bool = False, repos: Sequence[str] | None = None) -> list[WatcherStack]:
        return list(self.stacks)


class _Audit:
    """The audit log: the rows written, the newest per stack read back as ``latest_entry`` reads it."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def write(self, details: dict[str, Any]) -> None:
        self.rows.append(details)

    async def latest_entry(self, action: str, *, detail_key: str, detail_value: str) -> dict[str, Any] | None:
        assert action == "watcher_signature_masks_refused", action
        found = [row for row in self.rows if row.get(detail_key) == detail_value]
        return {"details": found[-1]} if found else None


@pytest.mark.asyncio
async def test_startup_writes_one_audit_row_per_stack_whose_masks_its_ticks_leave_out() -> None:
    audit = _Audit()
    stacks = _Stacks(_stack("acme/mixed", [LOOK_BEHIND, PLAIN]), _stack("acme/clean", [PLAIN]))
    assert await watcher_stacks.audit_refused_signature_masks(stacks, audit, audit.write) == ["acme/mixed"]
    assert audit.rows == [
        {
            "repo": "acme/mixed",
            "kept": 1,
            "refused": [
                {"index": 0, "pattern": LOOK_BEHIND["pattern"], "reason": audit.rows[0]["refused"][0]["reason"]}
            ],
        }
    ], audit.rows
    assert "look-ahead and look-behind" in audit.rows[0]["refused"][0]["reason"]

    assert await watcher_stacks.audit_refused_signature_masks(stacks, audit, audit.write) == [], (
        "a restart with the same stored masks wrote the same audit row again"
    )
    stacks.stacks[0] = _stack("acme/mixed", [LOOK_BEHIND, {"pattern": "rec_["}])
    assert await watcher_stacks.audit_refused_signature_masks(stacks, audit, audit.write) == ["acme/mixed"]
    assert [r["index"] for r in audit.rows[-1]["refused"]] == [0, 1], audit.rows[-1]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_audit_row_is_read_back_from_the_audit_log_as_it_was_written(db_with_migrations: Any) -> None:
    """The row is compared with the stored one after a restart: what Postgres gives back must equal what was written."""
    from embry0.audit.db_logger import emit_audit_event_db
    from embry0.storage.repositories.audit import AuditRepository

    db = db_with_migrations
    audit = AuditRepository(db)
    written: list[dict[str, Any]] = []

    async def write(details: dict[str, Any]) -> None:
        written.append(details)
        await emit_audit_event_db(db, "watcher_signature_masks_refused", actor="embry0:watcher", details=details)

    stacks = _Stacks(_stack("acme/mixed", [LOOK_BEHIND, PLAIN, "rec_\\d+"]))
    assert await watcher_stacks.audit_refused_signature_masks(stacks, audit, write) == ["acme/mixed"]
    assert await watcher_stacks.audit_refused_signature_masks(stacks, audit, write) == [], (
        f"the row read back from the audit log differs from the one written: {written}"
    )
    rows = await audit.list_entries(action="watcher_signature_masks_refused")
    assert len(rows) == 1 and rows[0]["actor"] == "embry0:watcher", rows
