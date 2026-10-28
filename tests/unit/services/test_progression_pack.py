"""The progression content pack (#176): the shipped default, the checks a pack must pass, and the contract."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from embry0.services import progression_templates as templates
from embry0.services import progression_vocabulary as vocab
from embry0.services.progression_pack import (
    PackInvalid,
    _main,
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
    """Every problem ``data`` has, none for a valid pack; an exception that escapes the checks is reported as one."""
    try:
        parse(data)
    except PackInvalid as exc:
        return [str(p) for p in exc.problems]
    except Exception as exc:  # noqa: BLE001 - a crash is what some of these tests look for
        return [f"{type(exc).__name__} escaped the checks: {exc}"[:300]]
    return []


def _load_problems(text: str) -> list[str]:
    try:
        load_text(text)
    except PackInvalid as exc:
        return [str(p) for p in exc.problems]
    except RecursionError:
        return ["RecursionError escaped the loader"]
    return []


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
    ("a newer schema", lambda d: d.update(version=2), "version: this release reads version 1;"),
    (
        "a bad timezone",
        lambda d: d.update(timezone="Mars/Olympus"),
        "timezone: not a timezone name (IANA, such as UTC)",
    ),
    ("a skill in no group", lambda d: d["skills"][0].update(group="nowhere"), "skills[0].group: no group has this id"),
    (
        "an unknown category",
        lambda d: d["skills"][0].update(trained_by=["edit:poems"]),
        "skills[0].trained_by[0]: not an action category; one of:",
    ),
    (
        "a category training two skills",
        lambda d: d["skills"][1]["trained_by"].append("edit:source"),
        "skills[1].trained_by[1]: this category already trains skills[0]",
    ),
    (
        "an unknown action kind",
        lambda d: d["rates"]["actions"].update(dance=5),
        "rates.actions.dance: not an action kind; one of:",
    ),
    (
        "an outcome in no skill",
        lambda d: d["rates"]["outcomes"]["merged"].update(skill="juggling"),
        "rates.outcomes.merged.skill: no skill has this id",
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
        lambda d: d["tiers"][0].update(gear="copper"),
        "tiers[0].gear: not a gear step the dashboard draws; one of:",
    ),
    ("a bad colour", lambda d: d["tiers"][0].update(color="brown"), "tiers[0].color: String should match pattern"),
    (
        "a rare drawn with no art",
        lambda d: d["rares"][0].update(art="cape"),
        "rares[0].art: not a rare the dashboard draws; one of:",
    ),
    (
        "a rare with two dates",
        lambda d: d["rares"][1].update(when={"merge_on": "10-31"}),
        "rares[2].when.merge_on: another rare already drops on this date",
    ),
    (
        "duplicate ids",
        lambda d: d["achievements"][1].update(id="forged_in_fire"),
        "achievements[1].id: this id is already used in achievements",
    ),
    (
        "an unknown metric",
        lambda d: d["achievements"][0].update(metric="lines_of_code"),
        "achievements[0].metric: not a metric; one of:",
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
        "achievements[0].icon: not an icon the dashboard draws; one of:",
    ),
    (
        "an unknown placeholder",
        lambda d: d["achievements"][0].update(what_counts="Merges {size}"),
        "achievements[0].what_counts: names a value this text does not have; it may use none",
    ),
    (
        "a level past the curve",
        lambda d: d["achievements"][40]["params"].update(level=120),
        "achievements[40].params.level: above the maximum level 99",
    ),
    (
        "a quest with the wrong steps",
        lambda d: d["quests"][0].update(steps=["One"]),
        "quests[0].steps: this kind of quest has 3 steps, not 1",
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
        "quests[3].params.diary_tier: no diary tier has this id",
    ),
    (
        "all_above in the first tier",
        lambda d: d["diaries"]["tiers"][0]["tasks"].append({"kind": "all_above", "text": "All"}),
        "diaries.tiers[0].tasks[3].kind: the first tier has no tier above it",
    ),
    (
        "an unknown task kind",
        lambda d: d["diaries"]["tiers"][0]["tasks"][0].update(kind="dance"),
        "diaries.tiers[0].tasks[0].kind: not a diary task kind; one of:",
    ),
    (
        "a message with another kind's placeholder",
        lambda d: d["messages"].update(quest="{character} did it."),
        "messages.quest: names a value this text does not have; it may use {name}",
    ),
    ("a label missing", lambda d: d["labels"].pop("complete"), "labels.complete: Field required"),
]


@pytest.mark.parametrize(("change", "expected"), [(c, e) for _, c, e in BAD], ids=[name for name, _, _ in BAD])
def test_a_bad_pack_is_refused_naming_its_key(change: Callable[[dict[str, Any]], None], expected: str) -> None:
    problems = _problems(_edit(change))
    assert any(expected in p for p in problems), f"expected {expected!r} among {problems}"


ECHOED = (
    "Mars/Olympus",
    "nowhere",
    "edit:poems",
    "dance",
    "juggling",
    "copper",
    "lines_of_code",
    "rocket",
    "legend",
    "size",
)


@pytest.mark.parametrize(("change", "expected"), [(c, e) for _, c, e in BAD], ids=[name for name, _, _ in BAD])
def test_a_problem_never_echoes_what_was_written(change: Callable[[dict[str, Any]], None], expected: str) -> None:
    """#160's rule: a message says what is wrong, never repeats the value (it may be large, or not text)."""
    for problem in _problems(_edit(change)):
        message = problem.split(": ", 1)[1]
        assert not any(value in message for value in ECHOED), f"the message repeats the value: {problem}"


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


def test_a_key_written_twice_and_a_merge_key_are_refused_by_the_loader(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """YAML keeps only the second of two equal keys, silently; the loader every path reads with refuses it.

    So the shipped default, ``--check``, ``embry0 progression-pack check``
    and every write meet one rule (review of #211).
    """
    twice = default_text() + "\ntimezone: Europe/Berlin\n"
    merged = default_text().replace("cloaks:\n", "cloaks:\n  <<: {quest: {base: '#000000', trim: '#000000'}}\n", 1)
    for text, kind in ((twice, "duplicate_key"), (merged, "yaml_merge")):
        with pytest.raises(PackInvalid) as refused:
            load_text(text)
        assert [p.type for p in refused.value.problems] == [kind], f"{kind}: {refused.value.problems}"
    target = tmp_path / "pack.yaml"
    target.write_text(twice)
    assert _main(["--check", str(target)]) == 1, "--check must refuse a key written twice"
    assert "timezone: 'timezone' is written twice" in capsys.readouterr().err


# ---- metrics and their facts ----


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


# ---- texts: one grammar, {name} and {name:,} (review of #207, finding 1a) ----

OUTSIDE_THE_GRAMMAR = [
    "under {max_usd:q}",  # an unknown format code raised on every read
    "{max_usd:>30000000}",  # padding built a 30 MB string per read
    "{max_usd!r}",
    "{max_usd:{max_usd}}",
    "{0}",
    "{}",
    "{max_usd.real}",
    "{max_usd[0]}",
    "a lone { brace",
    "a lone } brace",
]


@pytest.mark.parametrize("text", OUTSIDE_THE_GRAMMAR)
def test_a_text_outside_the_grammar_is_refused_naming_its_key(text: str) -> None:
    problems = _problems(_edit(lambda d: d["achievements"][3].update(what_counts=text)))
    assert any(p.startswith("achievements[3].what_counts: not a template") for p in problems), (
        f"{text!r} must be refused before it can be rendered on every read: {problems}"
    )


@pytest.mark.parametrize("field", ["messages", "labels"])
def test_the_client_rendered_texts_take_the_same_grammar(field: str) -> None:
    key = "level" if field == "messages" else "gear"
    problems = _problems(_edit(lambda d: d[field].update({key: "{level!r}"})))
    assert any(p.startswith(f"{field}.{key}: not a template") for p in problems), (
        f"the dashboard's texts take the same grammar: {problems}"
    )


def test_the_grammar_allows_names_grouped_numbers_and_escaped_braces() -> None:
    def texts(d: dict[str, Any]) -> None:
        d["achievements"][3]["what_counts"] = "Merges under {max_usd} US dollars, {{one}} or {max_usd:,}"
        d["quests"][3]["requirement"] = "A character at total level {total_level:,}"

    pack = parse(_edit(texts))
    body = rules_for(pack).achievements[3]
    assert templates.render(body.entry.what_counts, body.entry.params) == "Merges under 1 US dollars, {one} or 1"


def test_a_list_cannot_be_written_into_a_text() -> None:
    index = next(i for i, a in enumerate(default_pack().achievements) if a.id == "holiday_event")
    problems = _problems(_edit(lambda d: d["achievements"][index].update(what_counts="On {dates}")))
    assert any("a list cannot be written into a text" in p for p in problems), (
        f"a list written into a text reads as Python, not words: {problems}"
    )


def test_render_fills_the_grammar_and_never_raises() -> None:
    for text, values, out in templates.CASES:
        assert templates.render(text, values) == out, text
    assert templates.render("{x!r} stays as written", {"x": 1}) == "{x!r} stays as written", "a read never fails"


# ---- bounds: every value a pack sets stays inside what reads can take (findings 1b, 1c) ----

INT32 = 2**31 - 1


def test_every_parameter_cast_to_int_in_sql_is_bounded_to_int32() -> None:
    """A parameter Postgres reads as ::int must be refused past int32, or the facts query fails on every read."""
    shipped = {a.metric: a.params for a in default_pack().achievements}
    tried, unbounded = 0, []
    for metric_id, metric in vocab.METRICS.items():
        if metric.fragment is None:
            continue
        template = FRAGMENTS[metric.fragment]
        cast = set(re.findall(r"\{(\w+)\}::int\b", template)) | set(re.findall(r"\{(\w+)\}::int\[\]", template))
        for name in sorted(cast):
            params = dict(shipped[metric_id])
            params[name] = [INT32 + 1] if "{" + name + "}::int[]" in template else INT32 + 1
            tried += 1
            try:
                metric.params.model_validate(params)
            except ValidationError:
                continue
            unbounded.append(f"{metric_id}.{name}")
    assert tried >= 9, "every ::int parameter of the fragments was tried"
    assert not unbounded, f"accepted past int32, so the facts query would fail on every read: {unbounded}"


def test_a_min_seen_past_int32_is_refused() -> None:
    problems = _problems(_edit(lambda d: d["achievements"][12]["params"].update(min_seen=10**12)))
    assert any(
        p.startswith("achievements[12].params.min_seen: Input should be less than or equal to 1000000")
        for p in problems
    ), f"past int32 the facts query fails on every read: {problems}"


@pytest.mark.parametrize(
    ("curve", "expected"),
    [
        ({"doubling": 0.01}, "curve.doubling: Input should be greater than or equal to 1"),
        ({"divisor": 0.0001}, "curve.divisor: Input should be greater than or equal to 1"),
        ({"doubling": 1, "points": 100_000, "max_level": 200}, "curve: the curve's top must stay under"),
    ],
)
def test_a_curve_past_what_reads_can_hold_is_a_problem_not_a_crash(curve: dict[str, Any], expected: str) -> None:
    problems = _problems(_edit(lambda d: d["curve"].update(curve)))
    assert any(expected in p for p in problems), f"a curve past what reads can hold must be a problem: {problems}"


@pytest.mark.parametrize("text", ["[" * 50_000, "a: " + "[" * 40 + "]" * 40], ids=["50000 brackets", "40 deep"])
def test_a_document_too_deep_is_a_problem_not_a_crash(text: str) -> None:
    problems = _load_problems(text)
    assert any("nests deeper than 32 levels" in p for p in problems), f"too deep must be a problem: {problems}"


def test_a_document_too_deep_is_refused_by_parse_too() -> None:
    problems = _problems({"timezone": "UTC", "x": json.loads("[" * 40 + "]" * 40)})
    assert any("nests deeper than 32 levels" in p for p in problems), f"too deep must be a problem: {problems}"


def test_an_impossible_date_is_refused_and_29_february_kept() -> None:
    problems = _problems(_edit(lambda d: d["rares"][1]["when"].update(merge_on="02-30")))
    assert any(p.startswith("rares[1].when.merge_on: Value error, no such day in that month") for p in problems), (
        f"30 February must be refused: {problems}"
    )
    assert _problems(_edit(lambda d: d["rares"][1]["when"].update(merge_on="02-29"))) == [], "29 February occurs"


# ---- the vocabulary is pinned to the code that implements it (finding 2) ----


def test_a_query_carries_every_parameter_and_a_missing_one_is_refused() -> None:
    healed = next(q for q in default_rules().fact_queries if q.key == "healed")
    assert healed.params == {"severity": None, "min_seen": None}, "defaults included, so the fragment binds each one"
    try:
        facts_sql([type(healed)("healed", "healed", {"severity": None})], "UTC")
    except KeyError as exc:
        assert "needs the parameter 'min_seen'" in str(exc)
    else:
        raise AssertionError("a missing parameter was bound as NULL, and its achievement would count nothing")


def test_check_prints_the_warnings_of_a_valid_pack(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = _default()
    next(s for s in data["skills"] if s["id"] == "shell")["trained_by"] = []
    target = tmp_path / "pack.yaml"
    target.write_text(yaml.safe_dump(data))
    assert _main(["--check", str(target)]) == 0
    assert "warning: shell:plain trains no skill" in capsys.readouterr().err, (
        "--check prints what a valid pack leaves out"
    )
