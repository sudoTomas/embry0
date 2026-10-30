"""What GET /progression answers for a seeded history, pinned field for field (#176).

The golden file was recorded from the code before the content moved into the
shipped pack. Under that pack, every field it holds must read the same; fields
added since are checked by their own tests. Record it again only on purpose,
after a change that moves a recorded value, then run the file and read the diff:

    EMBRY0_RECORD_GOLDEN=1 pytest tests/unit/services/test_progression_golden.py
    pytest tests/unit/services/test_progression_golden.py
    git diff tests/fixtures/progression_golden.json

A re-record writes only the fields the file already holds, with the values
the code answers now (#247). It adds none, so the file never starts pinning
``rollup``, which each read answers differently, or the pack's own names and
art. The tests that record the same name agree on those fields, so they
write the same text and it does not matter which runs last. Re-recording an
unchanged tree changes no byte. A list takes the entries the code answers,
each cut to the fields of the stored entry at its place; an entry with no
stored entry of its shape there, such as a ``recent`` entry of another kind
or one past the stored end, is kept whole. A name the file does not hold yet
is recorded whole.
"""

from __future__ import annotations

import difflib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from embry0.execution.skill_actions import LEGACY_ACTION_KEYS, current_actions
from embry0.services.progression import ProgressionService
from embry0.services.progression_pack import ShippedPack, default_rules
from embry0.services.world import quest_xp
from embry0.storage.repositories.progression import ProgressionRepository
from tests.fixtures.progression_history import MIXED, OLD_TOOLS, seed_history

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "progression_golden.json"
RECORD = os.environ.get("EMBRY0_RECORD_GOLDEN") == "1"
NOW = datetime(2026, 1, 2, tzinfo=UTC)
RULES = default_rules()


def _contained(golden: Any, actual: Any, path: str = "$") -> list[str]:
    """Every difference between ``golden`` and the same fields of ``actual``; new keys in ``actual`` are ignored."""
    if isinstance(golden, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected an object, got {type(actual).__name__}"]
        problems: list[str] = []
        for key, value in golden.items():
            if key not in actual:
                problems.append(f"{path}.{key}: missing")
            else:
                problems += _contained(value, actual[key], f"{path}.{key}")
        return problems
    if isinstance(golden, list):
        if not isinstance(actual, list) or len(actual) != len(golden):
            return [f"{path}: expected a list of {len(golden)}, got {actual!r:.200}"]
        return [
            p for i, (g, a) in enumerate(zip(golden, actual, strict=True)) for p in _contained(g, a, f"{path}[{i}]")
        ]
    return [] if golden == actual else [f"{path}: expected {golden!r}, got {actual!r}"]


def _recordable(stored: Any, actual: Any) -> Any:
    """``actual`` cut to the fields ``stored`` holds, in its shape: what a re-record writes in its place."""
    if isinstance(stored, dict) and isinstance(actual, dict):
        return {key: _recordable(value, actual[key]) for key, value in stored.items() if key in actual}
    if isinstance(stored, list) and isinstance(actual, list):
        return [_recordable_entry(stored[i] if i < len(stored) else None, entry) for i, entry in enumerate(actual)]
    return actual


def _recordable_entry(stored: Any, actual: Any) -> Any:
    """A list entry cut to the stored entry at its place, or kept whole when it lacks a field that entry holds."""
    if isinstance(stored, dict) and isinstance(actual, dict) and not stored.keys() <= actual.keys():
        return actual
    return _recordable(stored, actual)


def _golden_text(recorded: dict[str, Any]) -> str:
    return json.dumps(recorded, indent=1, sort_keys=True) + "\n"


def _quest_xp_cases() -> dict[str, Any]:
    runs = [
        {
            "agent_type": "developer",
            "result": "success",
            "error_code": None,
            "tools_called": OLD_TOOLS,
            "skill_actions": None,
            "created_at": NOW,
        },
        {
            "agent_type": "review",
            "result": "success",
            "error_code": None,
            "tools_called": {},
            "skill_actions": MIXED,
            "created_at": NOW,
        },
        {
            "agent_type": "qa",
            "result": "error",
            "error_code": "ERR_X",
            "tools_called": {"Read": 9},
            "skill_actions": None,
            "created_at": NOW,
        },
    ]
    return {
        "plain": quest_xp({"status": "completed", "finished_at": NOW}, runs, rules=RULES),
        "merged": quest_xp(
            {"status": "pr_merged", "pr_url": "https://forge/pull/1", "finished_at": NOW}, runs, rules=RULES
        ),
        "cleanup": quest_xp(
            {
                "status": "pr_merged",
                "pipeline_template": "slop-cleanup",
                "pr_url": "https://forge/pull/2",
                "work_nature": {"cleanup": {"added": 3, "removed": 90}},
                "finished_at": NOW,
            },
            [],
            rules=RULES,
        ),
    }


def _check(name: str, actual: Any) -> None:
    recorded = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if RECORD:
        recorded[name] = _recordable(recorded[name], actual) if name in recorded else actual
        GOLDEN.write_text(_golden_text(recorded))
        return
    assert name in recorded, f"{GOLDEN} has no {name!r}; record it with EMBRY0_RECORD_GOLDEN=1"
    problems = _contained(recorded[name], actual)
    assert not problems, f"{len(problems)} fields differ from the recorded output:\n" + "\n".join(problems[:40])


def _body(progression: Any) -> dict[str, Any]:
    """GET /progression's body, less the one field that is the time of the read."""
    body = json.loads(json.dumps(progression.to_dict()))
    body.pop("computed_at")
    return body


async def _read_raw(db: Any) -> dict[str, Any]:
    """The body read from every raw row."""
    return _body(await ProgressionService(ProgressionRepository(db), packs=ShippedPack(), rollups=None).read())


async def _read_through_rollup(db: Any, clock: datetime) -> dict[str, Any]:
    """The body read from the rollup at ``clock`` and the raw rows after it."""
    from embry0.storage.repositories.progression_rollup import ProgressionRollupRepository

    service = ProgressionService(
        ProgressionRepository(db), packs=ShippedPack(), rollups=ProgressionRollupRepository(db), now=lambda: clock
    )
    await service.read()
    await service.settle()
    progression = await service.read()
    assert progression.rollup is not None, "the second read is the rollup's"
    return _body(progression)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_progression_reads_the_seeded_history_as_recorded(db_with_migrations) -> None:
    await seed_history(db_with_migrations)
    body = await _read_raw(db_with_migrations)
    assert body["achievements"] and body["characters"] and body["diaries"], "the history must reach every part"
    _check("progression", body)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "clock",
    [
        datetime(2025, 10, 20, tzinfo=UTC),  # a few days folded, the rest raw
        datetime(2025, 12, 28, 6, tzinfo=UTC),  # the Christmas merge folded
        datetime(2026, 1, 3, 1, tzinfo=UTC),  # the year's last run folded, the new year's first run after it
        datetime(2026, 3, 1, tzinfo=UTC),  # everything folded
    ],
    ids=["october", "christmas", "new-year", "all"],
)
async def test_the_rollup_reads_the_seeded_history_as_recorded(db_with_migrations, clock: datetime) -> None:
    """#176: the same history read from the rollup and the raw events after it answers the recorded output."""
    await seed_history(db_with_migrations)
    _check("progression", await _read_through_rollup(db_with_migrations, clock))


def test_a_quest_scroll_prices_its_runs_as_recorded() -> None:
    _check("quest_xp", json.loads(json.dumps(_quest_xp_cases())))


@pytest.mark.requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("through_rollup", [False, True], ids=["raw", "rollup"])
async def test_the_same_history_on_todays_category_keys_reads_the_same(
    db_with_migrations, through_rollup: bool
) -> None:
    """Runs written since #176 store categories (shell:test-runner), not skills: priced the same, field for field.

    Through the rollup too, split across the new year: its stored days were priced from the same keys.
    """
    db = db_with_migrations
    await seed_history(db)
    rows = await db.fetch("SELECT trace_id, skill_actions FROM traces WHERE skill_actions <> '{}'::jsonb")
    assert rows and all(set(r["skill_actions"]) <= set(LEGACY_ACTION_KEYS) for r in rows), "the seed holds older keys"
    for row in rows:
        await db.execute(
            "UPDATE traces SET skill_actions = $2 WHERE trace_id = $1",
            row["trace_id"],
            current_actions(row["skill_actions"]),
        )
    assert not {k for r in await db.fetch("SELECT skill_actions FROM traces") for k in r["skill_actions"]} & set(
        LEGACY_ACTION_KEYS
    ), "every run now holds categories"
    if through_rollup:
        body = await _read_through_rollup(db, datetime(2026, 1, 3, 1, tzinfo=UTC))
    else:
        body = await _read_raw(db)
    _check("progression", body)


# The recorder itself (#247). Each test records into a copy of the golden
# file, in record mode, whatever EMBRY0_RECORD_GOLDEN says.


@pytest.fixture
def recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Record mode, writing to a copy of the golden file; returns the copy."""
    copy = tmp_path / GOLDEN.name
    copy.write_bytes(GOLDEN.read_bytes())
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "GOLDEN", copy)
    monkeypatch.setattr(module, "RECORD", True)
    return copy


def _changed_lines(before: str, after: str) -> list[str]:
    """The lines a re-record removed (``-``) and wrote (``+``)."""
    diff = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
    return [line for line in diff if line[:1] in "+-" and not line.startswith(("+++", "---"))]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("clock", [None, datetime(2026, 1, 3, 1, tzinfo=UTC)], ids=["raw", "rollup"])
async def test_recording_an_unchanged_tree_again_changes_no_byte(
    db_with_migrations, recording: Path, clock: datetime | None
) -> None:
    """The documented re-record, run on a tree that moved nothing, leaves the file as it was."""
    before = recording.read_text()
    await seed_history(db_with_migrations)
    if clock is None:
        body = await _read_raw(db_with_migrations)
    else:
        body = await _read_through_rollup(db_with_migrations, clock)
    held = json.loads(before)["progression"]
    assert "rollup" in body and "rollup" not in held, "the body answers fields the file never held"
    _check("progression", body)
    _check("quest_xp", json.loads(json.dumps(_quest_xp_cases())))
    changed = _changed_lines(before, recording.read_text())
    assert not changed, f"re-recording an unchanged tree changed {len(changed)} lines:\n" + "\n".join(changed[:40])


def test_a_rerecord_writes_the_changed_value_and_no_new_field(recording: Path) -> None:
    """A deliberate change moves one recorded value; the fields added since stay out of the file."""
    before = recording.read_text()
    answered = json.loads(before)["progression"]
    answered["rollup"] = {"through_day": "2025-12-31", "boundary": "2026-01-01T00:00:00+00:00"}
    answered["pack"] = {"version": 1, "revision": 0}
    for character in answered["characters"]:
        character["gear"] = "bronze"
    xp = answered["characters"][0]["xp"]
    answered["characters"][0]["xp"] = xp + 1
    _check("progression", answered)
    assert _changed_lines(before, recording.read_text()) == [f'-    "xp": {xp}', f'+    "xp": {xp + 1}'], (
        "a re-record wrote more than the one value that moved"
    )


def test_a_rerecord_keeps_the_shape_of_what_the_file_holds(recording: Path) -> None:
    """Fields and list entries keep the stored shape; an entry of another shape, or a new name, is kept whole."""
    rare = {"kind": "rare", "agent_type": "developer", "id": "paper-crown", "at": "2025-12-25T10:00:00+00:00"}
    level = {"kind": "level", "agent_type": "qa", "stat": "testing", "level": 2, "at": "2025-12-26T10:00:00+00:00"}
    recording.write_text(_golden_text({"sample": {"account": {"xp": 10, "dropped": 1}, "recent": [rare, rare]}}))
    achievement = {"kind": "achievement", "id": "first-merge", "name": "First merge", "tier": "bronze", "at": "t"}
    _check(
        "sample",
        {
            "account": {"xp": 11, "total_level": 30},
            "recent": [{**rare, "id": "harvest-mask", "icon": "mask"}, achievement, {**level, "icon": "star"}],
            "rollup": None,
        },
    )
    _check("added", {"rollup": None})
    assert json.loads(recording.read_text()) == {
        "added": {"rollup": None},
        "sample": {
            "account": {"xp": 11},
            "recent": [{**rare, "id": "harvest-mask"}, achievement, {**level, "icon": "star"}],
        },
    }
