"""The ``slop_meter`` section of the settings document (#169): the YAML an operator writes, and what it refuses.

Nothing written keeps the meter exactly as it scored before the section: the
defaults are pinned here against the constants the meter's code held, so a
default that drifts silently rescoring every repository fails with its own
message. The section is read through the catalog, so each test fails with its
own message on a tree that has no slop_meter section.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from unittest.mock import patch

import pytest

from embry0.config import Embry0Config
from embry0.settings.bootstrap import env_values
from embry0.settings.document import DocumentInvalid, check, load_yaml
from embry0.settings.registry import Section
from embry0.settings.sections import CATALOG
from embry0.settings.store import Snapshot, build_snapshot

WIDGETS = "acme/widgets"
GADGETS = "globex/gadgets"

# ---- what the meter's code held before the section (embry0/services/slop_meter/*.py at 65cf0eaf) ----

OLD_CORE = (
    "py", "pyi", "ts", "mts", "cts", "js", "mjs", "cjs", "go", "rs", "java", "kt", "kts", "scala", "c", "h",
    "cc", "cpp", "cxx", "hpp", "hh", "cs", "rb", "php", "swift", "m", "mm", "ex", "exs", "erl", "clj", "cljs",
    "hs", "ml", "fs", "lua", "pl", "pm", "r", "jl", "dart", "zig", "nim", "groovy", "sh", "bash", "zsh",
    "ps1", "tf", "sol", "proto", "graphql", "gql",
)  # fmt: skip
OLD_UI = ("tsx", "jsx", "vue", "svelte", "astro", "html", "htm", "erb", "hbs", "ejs", "jinja", "j2", "twig", "liquid")  # fmt: skip
OLD_STYLE = ("css", "scss", "sass", "less", "styl", "pcss")
OLD_VENDORED = ("vendor", "node_modules", "third_party", "third-party", "external", "deps", "bower_components")
OLD_TEST_DIRS = (
    "__tests__", "tests", "test", "spec", "specs", "examples", "example", "demos", "demo", "samples", "sample",
    "fixtures", "testdata",
)  # fmt: skip
OLD_NUMBERS = {
    "bootstrap_lines": 5_000,  # BOOTSTRAP_THRESHOLD
    "complexity_weight": 0.05,  # COMPLEXITY_WEIGHT
    "commit_hours": 1.0,  # BASE_NEIGHBORHOOD
    "event_hours": 1.0,  # BASE_NEIGHBORHOOD, for a forge event
    "subcommit_hours": 1.0,  # MARGINAL_HOURS_PER_SUBCOMMIT
    "ai_coauthor_share": 0.8,  # AI_CO_AUTHOR_ATTENTION_WEIGHT
    "coauthor_shares": [0.5, 0.25, 0.125],  # CO_AUTHOR_WEIGHTS
    "lines_per_hour": 40.0,  # LINES_PER_HOUR
    "core_min_commits": 10,  # CORE_RAMP_START
    "core_full_commits": 60,  # CORE_RAMP_END
    "weekly_hours_cap": 80.0,  # WEEKLY_HOURS_CAP
    "outlier_min_lines": 2_000,  # OUTLIER_MIN_ADDITIONS
    "outliers_per_year": 4,  # OUTLIERS_PER_YEAR
    "test_weight": 0.5,  # NON_PRODUCTION_WEIGHT
    "level_bounds": [0.2, 0.4, 0.6, 0.8],  # LEVELS
    "level_names": ["Buttered Noodles", "Spaghetti Bolognese", "Lasagna", "Sloppy Joe", "Just The Slop"],
    "small_repository_in_full": True,  # the small-repository rule, always on
    "interval_hours": 24.0,  # SLOP_METER_INTERVAL_SECONDS=86400
    "api_calls_per_tick": 200,  # SLOP_METER_API_CALLS_PER_TICK
    "rate_reserve": 2_000,  # SLOP_METER_RATE_RESERVE
    "enabled": True,
    "bots": [],
    "ai_agents": [],
}
OLD_SYSTEM_PROMPT = """\
You judge one large commit of a software repository: did it add bulk code no
person wrote or reviewed line by line (slop, such as a large AI-generated
change), or is its size explained by something else (not slop: the project's
initial import, a code drop moved in from elsewhere, a restructuring that moved
files, a vendored or generated library, a data or fixture dump)?
Judge from what the message and the files say. When it is unclear, answer
slop: false only if the files themselves look moved, vendored or generated.
Answer with JSON: {"slop": true|false, "reason": "<one plain sentence>"}."""


def _section() -> Section[Any]:
    found = CATALOG.get("slop_meter")
    if found is None:
        pytest.fail("the settings document has no slop_meter section: the meter's weights and pace are code")
    return found


def _defaults() -> dict[str, Any]:
    return _section().defaults().model_dump(mode="json")


def _problems(values: Mapping[str, Any]) -> dict[str, str]:
    """Each key a document holding ``slop_meter: values`` is refused at, with its message."""
    _section()
    try:
        check(CATALOG, {"slop_meter": dict(values)}, stored={})
    except DocumentInvalid as exc:
        return {".".join(str(part) for part in problem.loc[1:]): problem.msg for problem in exc.problems}
    return {}


def _snapshot(documents: Mapping[str, Mapping[str, Any]]) -> Snapshot:
    """A snapshot of ``documents``, each stored scope to its ``slop_meter`` values, as the store builds one."""
    rows = [
        {"scope": scope, "data": {"slop_meter": dict(values)}, "generation": 1, "updated_at": None}
        for scope, values in documents.items()
    ]
    if "deployment" not in documents:
        rows.append({"scope": "deployment", "data": {}, "generation": 1, "updated_at": None})
    return build_snapshot(CATALOG, rows, token=(), log=False)


# ---- nothing written scores as before ----


def test_nothing_written_holds_the_constants_the_meter_scored_with_before() -> None:
    defaults = _defaults()
    weights = {
        **{f"*.{e}": 1.0 for e in OLD_CORE},
        "*.sql": 0.8,
        **{f"*.{e}": 0.5 for e in OLD_UI},
        **{f"*.{e}": 0.3 for e in OLD_STYLE},
        **dict.fromkeys(("*.min.js", "*.min.css", "*.bundle.js", "*.map", "*.d.ts"), 0.0),
    }
    assert defaults["weights"] == weights, "every extension the meter weighed, at its weight, and the generated 0s"
    assert defaults["excluded_dirs"] == list(OLD_VENDORED), "the vendored directories it excluded"
    assert defaults["test_dirs"] == list(OLD_TEST_DIRS), "the directories of tests and examples"
    assert defaults["test_files"] == ["*_test.go", "*_test.py", "*_test.rb", "*_test.ex", "*_test.exs", "*_spec.rb", "test_?*.py"]  # fmt: skip
    assert defaults["test_marks"] == ["test", "spec", "stories", "story"]
    assert {key: defaults[key] for key in OLD_NUMBERS} == OLD_NUMBERS, "each number the meter's code held"
    from embry0.services.slop_meter.judge import system_prompt

    assert system_prompt(defaults["judge_prompt"]) == OLD_SYSTEM_PROMPT, "the judge is told what it was told"


def test_every_default_glob_and_name_is_one_the_section_accepts() -> None:
    defaults = _defaults()
    assert _problems(defaults) == {}, "a document that writes every default must be accepted"


# ---- what it refuses, naming every key ----


def test_every_refused_key_is_named_in_one_answer() -> None:
    problems = _problems(
        {
            "weights": {"*.PY": 1.0},
            "excluded_dirs": ["vendor", "vendor"],
            "test_files": ["tests/**/**/x.py"],
            "test_marks": ["test.unit"],
            "test_weight": 1.5,
            "level_bounds": [0.2, 0.2, 0.6, 0.8],
            "level_names": ["One", "Two", "Three", "Four"],
            "bots": ["release-bot", "line\nbreak"],
            "ai_agents": ["aider @"],
            "judge_prompt": "  \n ",
            "interval_hours": 0.1,
            "lines_per_hour": 0,
        }
    )
    assert set(problems) == {
        "weights",
        "excluded_dirs",
        "test_files",
        "test_marks",
        "test_weight",
        "level_bounds",
        "level_names",
        "bots",
        "ai_agents",
        "judge_prompt",
        "interval_hours",
        "lines_per_hour",
    }, f"one answer names every refused key: {problems}"
    assert "lowercase" in problems["weights"] and "once" in problems["excluded_dirs"]
    assert "above the one before" in problems["level_bounds"] and "one line" in problems["bots"]


@pytest.mark.parametrize(
    ("weights", "says"),
    [
        ({"": 1.0}, "write a glob"),
        ({"/src/*.py": 1.0}, "starts at the repository's root"),
        ({"src//a.py": 1.0}, "doubled"),
        ({"src/a**.py": 1.0}, "stands alone"),
        ({"**": 1.0}, "every file"),
        ({"../x.py": 1.0}, "'.' or '..'"),
        ({"*.[ch]": 1.0}, "only lowercase letters"),
        ({"*.py": 11}, "from 0 to 10"),
        ({"*.py": -1}, "from 0 to 10"),
    ],
)
def test_a_glob_or_weight_the_meter_cannot_read_is_refused_without_repeating_it(weights: Any, says: str) -> None:
    problems = _problems({"weights": weights})
    assert says in problems.get("weights", ""), f"{weights}: {problems}"
    glob = next(iter(weights))
    if len(glob) > 3:
        assert glob not in problems["weights"], "the message never repeats the text"


def test_the_core_ramp_must_rise_and_a_level_name_is_one_line() -> None:
    assert "ramp" in " ".join(_problems({"core_min_commits": 60, "core_full_commits": 60}).values())
    assert "one line" in _problems({"level_names": ["A", "B", "C", "D", "E\nF"]})["level_names"]
    assert "plain text" in _problems({"judge_prompt": "Judge\u202ethis"})["judge_prompt"]
    assert _problems({"judge_prompt": "Judge this.\n\tSlop is bulk code no person read."}) == {}


# ---- layers ----


def test_an_organization_says_what_counts_and_a_repository_says_its_layout() -> None:
    section = _section()
    assert section.settable("organization") == frozenset(section.fields) - {"enabled", "interval_hours"}, (
        "an organization may set every key but whether the meter runs and how often"
    )
    assert section.settable("repository") == {"weights", "excluded_dirs", "test_dirs", "test_files", "test_marks"}
    assert section.merged_keys == {"weights"}, "weights merge entry by entry"


def test_an_ai_agent_is_an_address_or_the_start_of_one_up_to_its_at() -> None:
    for refused in (["dev"], ["a"], ["@tools.example"], ["a@b@c"], ["aider @"]):
        assert "ai_agents" in _problems({"ai_agents": refused}), f"{refused} would name people's addresses too"
    assert _problems({"ai_agents": ["aider@", "agent@tools.example"]}) == {}


def test_bots_and_ai_agents_add_to_what_each_layer_inherits() -> None:
    section = _section()
    assert section.added_keys == {"bots", "ai_agents"}, "no layer takes out a bot the layer below lists"
    snapshot = _snapshot(
        {
            "deployment": {"bots": ["ci-runner"], "ai_agents": ["helper@"]},
            "organization:acme": {"bots": ["release-bot", "ci-runner"]},
        }
    )
    from embry0.settings.sections.slop_meter import SLOP_METER

    widgets = snapshot.scoped(SLOP_METER).repo(WIDGETS)
    assert widgets.bots == ("ci-runner", "release-bot"), "the deployment's bot counts in acme too, each once"
    assert widgets.ai_agents == ("helper@",)
    assert snapshot.scoped(SLOP_METER).repo(GADGETS).bots == ("ci-runner",), "and in every other organization"


def test_a_text_of_several_lines_is_a_literal_block_in_the_settings_document_only() -> None:
    """The settings document writes the judge's guidance as it reads; a progression pack, which keeps its own
    writer and compares stored texts, writes exactly what it wrote before."""
    import yaml

    from embry0.services import progression_pack_store
    from embry0.settings.document import dump_yaml

    _section()
    text = {"judge_prompt": "One line.\nAnother line."}
    assert dump_yaml(text) == "judge_prompt: |-\n  One line.\n  Another line.\n"
    assert yaml.safe_load(dump_yaml(text)) == text
    assert progression_pack_store.dump_yaml(text) == yaml.safe_dump(
        text, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100
    ), "a pack's YAML is unchanged, so no pack gets a new version"


def test_weights_merge_entry_by_entry_through_every_layer() -> None:
    snapshot = _snapshot(
        {
            "deployment": {"weights": {"*.sql": 1.0}, "lines_per_hour": 30},
            "organization:acme": {"weights": {"*.css": 0.0}, "lines_per_hour": 20, "api_calls_per_tick": 50},
            f"repository:{WIDGETS}": {"weights": {"db/migrations/**": 0.0}, "lines_per_hour": 10},
        }
    )
    _section()
    from embry0.settings.sections.slop_meter import SLOP_METER

    widgets = snapshot.scoped(SLOP_METER).repo(WIDGETS)
    assert widgets.weights["*.sql"] == 1.0 and widgets.weights["*.css"] == 0.0, "the deployment's and acme's entries"
    assert widgets.weights["db/migrations/**"] == 0.0 and widgets.weights["*.py"] == 1.0, "its own, and the defaults"
    assert widgets.lines_per_hour == 20, "a repository cannot set its team's pace: acme's applies"
    assert widgets.api_calls_per_tick == 50, "acme's forge budget"
    gadgets = snapshot.scoped(SLOP_METER).repo(GADGETS)
    assert gadgets.weights["*.css"] == 0.3 and gadgets.lines_per_hour == 30, (
        "another organization reads the deployment's"
    )


# ---- the configuration version ----


def test_the_configuration_version_follows_what_a_score_is_computed_from_and_nothing_else() -> None:
    section = _section()
    from embry0.settings.sections.slop_meter import NOT_SCORED, config_version

    base = config_version(section.defaults())
    assert base == config_version(section.validate({})) and len(base) == 16, "stable, for the same values"
    assert config_version(section.validate({"weights": {"*.sql": 1.0}})) != base, "a weight changes it"
    assert config_version(section.validate({"level_names": ["A", "B", "C", "D", "E"]})) != base
    assert config_version(section.validate({"bots": ["release-bot"]})) != base
    for key, value in {
        "enabled": False,
        "interval_hours": 2.0,
        "api_calls_per_tick": 5,
        "rate_reserve": 10,
        "judge_prompt": "Judge it.",
    }.items():
        assert key in NOT_SCORED
        assert config_version(section.validate({key: value})) == base, f"{key} is not what a score is computed from"
    reordered = dict(reversed(list(section.defaults().weights.items())))
    assert config_version(section.validate({"weights": reordered})) == base, "the order globs are written in is not"


# ---- the import from the environment ----


def _imported(**env: str) -> dict[str, Any]:
    _section()
    with patch.dict(os.environ, env, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    return env_values(CATALOG, config).values.get("slop_meter", {})


def test_the_reading_pace_is_imported_from_its_variables_once() -> None:
    assert _imported() == {}, "nothing set, nothing imported: a code default is never imported"
    assert _imported(SLOP_METER_INTERVAL_SECONDS="0") == {"enabled": False}, "0 turned the reading off"
    assert _imported(SLOP_METER_INTERVAL_SECONDS="86400") == {}, "the default interval is not imported"
    assert _imported(SLOP_METER_INTERVAL_SECONDS="3600") == {"interval_hours": 1.0}
    assert _imported(SLOP_METER_INTERVAL_SECONDS="60") == {"interval_hours": 0.25}, "at least a quarter hour"
    assert _imported(SLOP_METER_API_CALLS_PER_TICK="50", SLOP_METER_RATE_RESERVE="500") == {
        "api_calls_per_tick": 50,
        "rate_reserve": 500,
    }
    assert (
        _imported(SLOP_METER_COMMITS_PER_TICK="10", SLOP_METER_DATA_PATH="/x", SLOP_METER_CATCHUP_SECONDS="9") == {}
    ), "the process's own limits stay variables"


def test_the_section_reads_as_an_operator_writes_it() -> None:
    _section()
    text = (
        "slop_meter:\n"
        "  interval_hours: 12\n"
        "  weights:\n"
        '    "*.sql": 1\n'
        '    "db/migrations/**": 0\n'
        "  lines_per_hour: 30\n"
        "  level_names: [Clean, Fine, Mixed, Sloppy, Slop]\n"
        "  bots: [release-bot, ci@acme.example]\n"
    )
    data = check(CATALOG, load_yaml(text), stored={}).data["slop_meter"]
    assert data == {
        "interval_hours": 12.0,
        "weights": {"*.sql": 1.0, "db/migrations/**": 0.0},
        "lines_per_hour": 30.0,
        "level_names": ["Clean", "Fine", "Mixed", "Sloppy", "Slop"],
        "bots": ["release-bot", "ci@acme.example"],
    }, data
