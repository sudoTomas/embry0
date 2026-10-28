"""The progression content pack (#176): the shipped default, the checks a pack must pass, and the contract."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from embry0.services import progression_vocabulary as vocab
from embry0.services.progression_pack import (
    PackInvalid,
    contract,
    default_pack,
    default_rules,
    default_text,
    fact_key,
    load_text,
    pack_hash,
    parse,
    parse_text,
    rules_for,
    warnings,
)
from embry0.storage.repositories.progression import FRAGMENTS, facts_sql

CONTRACT = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "progression.json"


def _default() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(default_text()))


def _problems(data: dict[str, Any]) -> list[str]:
    with pytest.raises(PackInvalid) as caught:
        parse(data)
    return [str(p) for p in caught.value.problems]


# ---- the shipped default ----


def test_the_shipped_default_is_a_valid_pack_that_maps_every_category() -> None:
    pack = default_pack()
    assert len(pack.skills) == 23 and len(pack.achievements) == 50 and len(pack.quests) == 4
    assert warnings(pack) == [], "every action category trains a skill in the shipped pack"
    assert parse_text(default_text()) == pack


def test_comments_and_layout_do_not_change_a_packs_identity() -> None:
    as_json = json.dumps(default_pack().model_dump(mode="json"))
    assert pack_hash(parse_text(as_json)) == pack_hash(default_pack()), "JSON in, the same pack"
    changed = _default()
    changed["rates"]["actions"]["edit"] = 1401
    assert pack_hash(parse(changed)) != pack_hash(default_pack()), "a value is part of the identity"


def test_the_rules_give_the_classic_curve_and_the_formulas_top() -> None:
    rules = default_rules()
    assert rules.xp_for_level(2) == 83 and rules.xp_for_level(99) == 13_034_431
    assert rules.character_max_level == 126
    assert [rules.tier_for_level(n).id for n in (1, 15, 89, 90)] == ["bronze", "silver", "obsidian", "divine"]
    assert rules_for(default_pack()) is rules, "built once per pack"


# ---- what a pack must pass: every problem names its key ----


def _edit(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    data = _default()
    change(data)
    return data


BAD: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("an unknown key", lambda d: d.update(colour="red"), "colour: Extra inputs are not permitted"),
    ("a wrong type", lambda d: d["curve"].update(max_level="99"), "curve.max_level: Input should be a valid integer"),
    (
        "a value out of range",
        lambda d: d["curve"].update(max_level=500),
        "curve.max_level: Input should be less than or equal to 200",
    ),
    ("a newer schema", lambda d: d.update(version=2), "version: this release reads version 1"),
    ("a bad timezone", lambda d: d.update(timezone="Mars/Olympus"), "timezone: 'Mars/Olympus' is not a timezone name"),
    ("a skill in no group", lambda d: d["skills"][0].update(group="nowhere"), "skills[0].group: no group 'nowhere'"),
    (
        "an unknown category",
        lambda d: d["skills"][0].update(trained_by=["edit:poems"]),
        "skills[0].trained_by[0]: 'edit:poems' is not an action category",
    ),
    (
        "a category training two skills",
        lambda d: d["skills"][1]["trained_by"].append("edit:source"),
        "skills[1].trained_by[1]: edit:source already trains 'coding'",
    ),
    (
        "an unknown action kind",
        lambda d: d["rates"]["actions"].update(dance=5),
        "rates.actions.dance: 'dance' is not an action kind",
    ),
    (
        "an outcome in no skill",
        lambda d: d["rates"]["outcomes"]["merged"].update(skill="juggling"),
        "rates.outcomes.merged.skill: no skill 'juggling'",
    ),
    (
        "a first tier above 1",
        lambda d: d["tiers"][0].update(from_level=2),
        "tiers[0].from_level: the first tier starts at level 1",
    ),
    (
        "tiers out of order",
        lambda d: d["tiers"][2].update(from_level=10),
        "tiers[2].from_level: each tier starts at a higher level",
    ),
    (
        "gear the dashboard cannot draw",
        lambda d: d["tiers"][0].update(gear="tin"),
        "tiers[0].gear: 'tin' is not a gear step the dashboard draws",
    ),
    ("a bad colour", lambda d: d["tiers"][0].update(color="brown"), "tiers[0].color: String should match pattern"),
    (
        "a rare drawn with no art",
        lambda d: d["rares"][0].update(art="cape"),
        "rares[0].art: 'cape' is not a rare the dashboard draws",
    ),
    (
        "a rare with two dates",
        lambda d: d["rares"][1].update(when={"merge_on": "10-31"}),
        "rares[2].when.merge_on: another rare drops on 10-31",
    ),
    (
        "duplicate ids",
        lambda d: d["achievements"][1].update(id="forged_in_fire"),
        "achievements[1].id: 'forged_in_fire' is used twice",
    ),
    (
        "an unknown metric",
        lambda d: d["achievements"][0].update(metric="lines_of_code"),
        "achievements[0].metric: 'lines_of_code' is not a metric",
    ),
    (
        "a parameter the metric does not take",
        lambda d: d["achievements"][3]["params"].update(colour=1),
        "achievements[3].params.colour: Extra inputs are not permitted",
    ),
    (
        "a parameter missing",
        lambda d: d["achievements"][3].update(params={}),
        "achievements[3].params.max_usd: Field required",
    ),
    (
        "a parameter out of range",
        lambda d: d["achievements"][8]["params"].update(count=0),
        "achievements[8].params.count: Input should be greater than or equal to 1",
    ),
    (
        "too few thresholds",
        lambda d: d["achievements"][0].update(thresholds=[1, 2, 3]),
        "achievements[0].thresholds: 3 values; an objective has one, a tiered achievement one per tier (7)",
    ),
    (
        "thresholds that fall",
        lambda d: d["achievements"][0].update(thresholds=[1, 5, 4, 30, 60, 120, 250]),
        "achievements[0].thresholds: thresholds start at 1 or more and rise",
    ),
    (
        "an unknown icon",
        lambda d: d["achievements"][0].update(icon="rocket"),
        "achievements[0].icon: 'rocket' is not an icon the dashboard draws",
    ),
    (
        "an unknown placeholder",
        lambda d: d["achievements"][0].update(what_counts="Merges {size}"),
        "achievements[0].what_counts: unknown placeholder {size}",
    ),
    (
        "a level past the curve",
        lambda d: d["achievements"][40]["params"].update(level=120),
        "achievements[40].params.level: above the maximum level 99",
    ),
    (
        "a quest with the wrong steps",
        lambda d: d["quests"][0].update(steps=["One"]),
        "quests[0].steps: a onboard_to_merge quest has 3 steps, not 1",
    ),
    (
        "XP on a capstone",
        lambda d: d["quests"][3].update(xp=[{"agent": "developer", "skill": "coding", "xp": 1}]),
        "quests[3].xp: a capstone gives no XP",
    ),
    (
        "a trim on another quest",
        lambda d: d["quests"][0].update(cloak_trim=True),
        "quests[0].cloak_trim: only a capstone trims the cloaks",
    ),
    (
        "a capstone on a missing diary tier",
        lambda d: d["quests"][3]["params"].update(diary_tier="legend"),
        "quests[3].params.diary_tier: no diary tier 'legend'",
    ),
    (
        "all_above in the first tier",
        lambda d: d["diaries"]["tiers"][0]["tasks"].append({"kind": "all_above", "text": "All"}),
        "diaries.tiers[0].tasks[3].kind: the first tier has no tier above it",
    ),
    (
        "an unknown task kind",
        lambda d: d["diaries"]["tiers"][0]["tasks"][0].update(kind="dance"),
        "diaries.tiers[0].tasks[0].kind: 'dance' is not a diary task kind",
    ),
    (
        "a message with another kind's placeholder",
        lambda d: d["messages"].update(quest="{character} did it."),
        "messages.quest: unknown placeholder {character}; this text may use {name}",
    ),
    ("a label missing", lambda d: d["labels"].pop("complete"), "labels.complete: Field required"),
]


@pytest.mark.parametrize(("change", "expected"), [(c, e) for _, c, e in BAD], ids=[name for name, _, _ in BAD])
def test_a_bad_pack_is_refused_naming_its_key(change: Callable[[dict[str, Any]], None], expected: str) -> None:
    problems = _problems(_edit(change))
    assert any(expected in p for p in problems), f"expected {expected!r} among {problems}"


def test_every_problem_is_reported_at_once() -> None:
    def three(d: dict[str, Any]) -> None:
        d["skills"][0]["group"] = "nowhere"
        d["achievements"][0]["icon"] = "rocket"
        d["messages"]["quest"] = "{character}"

    assert len(_problems(_edit(three))) == 3


def test_a_problem_has_the_settings_documents_shape() -> None:
    with pytest.raises(PackInvalid) as caught:
        parse(_edit(lambda d: d["curve"].update(max_level=500)))
    assert caught.value.problems[0].to_dict() == {
        "loc": ["curve", "max_level"],
        "type": "less_than_equal",
        "msg": "Input should be less than or equal to 200",
    }


# ---- reading text ----


def test_an_alias_a_syntax_error_and_a_huge_text_are_refused() -> None:
    with pytest.raises(PackInvalid, match="aliases are not allowed"):
        load_text("a: &x [1, 2]\nb: *x\n")
    with pytest.raises(PackInvalid, match="line 2"):
        load_text("a: 1\n  b: [\n")
    with pytest.raises(PackInvalid, match="larger than 256 KiB"):
        load_text("#" * (256 * 1024 + 1))
    with pytest.raises(PackInvalid, match="a pack is a mapping"):
        parse_text("- just a list\n")


# ---- metrics and their facts ----


def test_every_sql_metric_has_its_fragment_and_every_fragment_a_metric() -> None:
    used = {m.fragment for m in vocab.METRICS.values() if m.fragment is not None}
    assert used == set(FRAGMENTS), "the vocabulary and the repository name the same fragments"


def test_a_metrics_key_carries_its_parameters() -> None:
    assert fact_key("merged", {}) == "merged"
    assert fact_key("healed", {}) == "healed", "a metric given only its defaults keeps its own name"
    assert fact_key("healed", {"severity": "high"}) == 'healed{"severity":"high"}'
    assert fact_key("healed", {"min_seen": 10}) != fact_key("healed", {"severity": "high"})


def test_no_value_of_the_pack_enters_the_sql() -> None:
    rules = rules_for(
        parse(
            _edit(
                lambda d: (
                    d.update(timezone="Pacific/Chatham"),
                    d["achievements"][3]["params"].update(max_usd=4.75),
                )
            )
        )
    )
    sql, args = facts_sql(rules.fact_queries, rules.pack.timezone)
    assert "Pacific/Chatham" not in sql and "4.75" not in sql and "pr-reviewer" not in sql
    assert "Pacific/Chatham" in args and 4.75 in args and ["review", "pr-reviewer"] in args


# ---- the contract the frontend pins ----


def test_the_contract_file_is_what_the_code_says() -> None:
    assert CONTRACT.exists(), (
        "run: python -m embry0.services.progression_pack --write-contract docs/contracts/progression.json"
    )
    assert json.loads(CONTRACT.read_text()) == json.loads(json.dumps(contract())), (
        "docs/contracts/progression.json differs from the code; regenerate it with "
        "python -m embry0.services.progression_pack --write-contract docs/contracts/progression.json"
    )
