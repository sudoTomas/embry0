"""What GET /progression answers for a seeded history, pinned field for field (#176).

The golden file was recorded from the code before the content moved into the
shipped pack. Under that pack, every field it holds must read the same; fields
added since are checked by their own tests. Record it again only on purpose:

    EMBRY0_RECORD_GOLDEN=1 pytest tests/unit/services/test_progression_golden.py
"""

from __future__ import annotations

import json
import os
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
        recorded[name] = actual
        GOLDEN.write_text(json.dumps(recorded, indent=1, sort_keys=True) + "\n")
        return
    assert name in recorded, f"{GOLDEN} has no {name!r}; record it with EMBRY0_RECORD_GOLDEN=1"
    problems = _contained(recorded[name], actual)
    assert not problems, f"{len(problems)} fields differ from the recorded output:\n" + "\n".join(problems[:40])


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_progression_reads_the_seeded_history_as_recorded(db_with_migrations) -> None:
    await seed_history(db_with_migrations)
    progression = await ProgressionService(
        ProgressionRepository(db_with_migrations), packs=ShippedPack(), rollups=None
    ).read()
    body = json.loads(json.dumps(progression.to_dict()))
    body.pop("computed_at")
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
    from embry0.storage.repositories.progression_rollup import ProgressionRollupRepository

    await seed_history(db_with_migrations)
    service = ProgressionService(
        ProgressionRepository(db_with_migrations),
        packs=ShippedPack(),
        rollups=ProgressionRollupRepository(db_with_migrations),
        now=lambda: clock,
    )
    await service.read()
    await service.settle()
    progression = await service.read()
    assert progression.rollup is not None, "the second read is the rollup's"
    body = json.loads(json.dumps(progression.to_dict()))
    body.pop("computed_at")
    _check("progression", body)


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
        from embry0.storage.repositories.progression_rollup import ProgressionRollupRepository

        clock = datetime(2026, 1, 3, 1, tzinfo=UTC)
        service = ProgressionService(
            ProgressionRepository(db), packs=ShippedPack(), rollups=ProgressionRollupRepository(db), now=lambda: clock
        )
        await service.read()
        await service.settle()
        progression = await service.read()
        assert progression.rollup is not None, "the second read is the rollup's"
    else:
        progression = await ProgressionService(ProgressionRepository(db), packs=ShippedPack(), rollups=None).read()
    body = json.loads(json.dumps(progression.to_dict()))
    body.pop("computed_at")
    _check("progression", body)
