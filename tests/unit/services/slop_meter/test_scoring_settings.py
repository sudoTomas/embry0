"""The Slop-O-Meter scores with the ``slop_meter`` section (#169): nothing written scores exactly as before.

Two proofs that the defaults moved without changing a score. A fixture
history is scored to the bit-for-bit numbers the pre-#169 code produced
(pinned below, computed on 65cf0eaf with the same builder, which uses only
APIs that tree has), read with no section, with an empty stored document and
with a document that writes every key at its default. And the weight of every
path of a large corpus equals what the pre-#169 rules (copied verbatim below)
gave it. Then each key is shown to move the score the way its description
says. No git, no network, no database.
"""

from __future__ import annotations

import random
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import product
from typing import Any

import pytest

from embry0.services.slop_meter.history import parse_log
from embry0.services.slop_meter.loop import Inputs, compute
from embry0.services.slop_meter.score import Event, meters
from embry0.services.slop_meter.weights import Weigher, type_weight
from embry0.settings.document import check
from embry0.settings.sections import CATALOG
from embry0.settings.store import build_snapshot

START = datetime(2024, 3, 4, 9, 0, tzinfo=UTC)  # a Monday
REPO = "acme/widgets"

# ---- the fixture history (the builder the golden numbers were computed with) ----

PATHS = (
    "api/server.py", "api/models.py", "api/db/queries.sql", "api/tests/test_server.py", "api/server_test.go",
    "web/src/App.tsx", "web/src/app.ts", "web/src/app.test.ts", "web/src/style.css", "web/src/theme.scss",
    "web/dist/bundle.js", "web/vendor/lib.js", "web/node_modules/x/index.js", "web/types/api.d.ts",
    "web/src/Button.stories.tsx", "docs/README.md", "package.json", "lib/core.rs", "lib/util.go",
    "examples/demo.py", "scripts/run.sh", "assets/logo.svg", "web/app.min.js", "web/app.js.map",
    "gen/schema.pb.go", "legacy/old.py",
)  # fmt: skip
AUTHORS = (
    ("Ana", "ana@example.com"),
    ("Bo", "bo@example.com"),
    ("Cy", "cy@example.com"),
    ("Drive By", "drive@example.com"),
    ("12345+octo", "12345+octo@users.noreply.github.com"),
)


def _record(sha: str, at: datetime, name: str, email: str, body: str, files: list[tuple[str, int, int]]) -> str:
    numstat = "\n".join(f"{a}\t{d}\t{p}" for p, a, d in files)
    return f"\x1e{sha}\x1f{at.isoformat()}\x1f{name}\x1f{email}\x1f{body}\n\x1f\n{numstat}\n"


def fixture_inputs() -> Inputs:
    """240 commits over a year: a core author, drive-bys, a bot, squashes, human and AI co-authors, judged
    outliers, ignore lists, areas, and forge events."""
    rng = random.Random(169)
    text = []
    at = START
    for i in range(240):
        at += timedelta(hours=rng.choice((2, 3, 5, 8, 13, 26, 49)))
        name, email = AUTHORS[0 if i % 3 == 0 else rng.randrange(len(AUTHORS))]
        if i % 17 == 5:
            name, email = "dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"
        files = [(rng.choice(PATHS), rng.randint(1, 120), rng.randint(0, 60)) for _ in range(rng.randint(1, 5))]
        body = "Work"
        if i % 11 == 0:
            body = "Squash\n\n* one\n* two\n* three\n"
        if i % 7 == 0:
            body += "\n\nCo-authored-by: Claude <claude@anthropic.com>"
        if i % 13 == 0:
            body += "\n\nCo-authored-by: Bo <bo@example.com>\nCo-authored-by: Cy <cy@example.com>"
        if i in (0, 1):
            files = [("api/server.py", 3_000, 0), ("web/src/App.tsx", 2_000, 0)]
        if i == 120:
            files = [("legacy/old.py", 8_000, 0), ("web/src/app.ts", 1_500, 10)]
        if i == 200:
            files = [("lib/core.rs", 6_000, 100)]
        text.append(_record(f"c{i:04d}", at, name, email, body, files))
    commits = parse_log("".join(text))
    events = []
    for _ in range(90):
        when = START + timedelta(days=rng.randint(0, 300), hours=rng.randint(0, 23))
        login = rng.choice(("ana", "bo", "octo", "renovate[bot]", "stranger"))
        events.append(Event(login, when, rng.choice(("comment", "review", "review_comment"))))
    files = {
        ".prettierignore": "web/dist/\n",
        ".gitattributes": "gen/** linguist-generated=true\n",
        ".embry0/areas.yml": "areas:\n  - name: Api\n    paths: [api/**]\n  - name: Web\n    paths: [web/**]\n",
    }
    return Inputs(commits, events, {"bo": "bo@example.com"}, {"c0120": True, "c0200": False}, files)


#: Per scope: score, level, name, provisional, weeks, and the sums of the weeks' cost, capacity and slop, as the
#: pre-#169 code (65cf0eaf) computed them from :func:`fixture_inputs`.
GOLDEN = {
    "": (0.5719725615148507, 3, "Lasagna", False, 24, 32441.512143782704, 8949.335858490183, 18555.654800294527),
    "Api": (0.673697461755875, 4, "Sloppy Joe", False, 24, 10120.407362766382, 3753.1871163668848, 6818.092752231182),
    "Web": (0.7043431761241699, 4, "Sloppy Joe", False, 24, 5503.127229395564, 1958.1180324877657, 3876.090111367875),
}


def _scored(inputs: Inputs) -> dict[str, tuple[Any, ...]]:
    out = {}
    for scope, m in sorted(compute(inputs).items()):
        weeks = m.weeks
        out[scope] = (
            m.score,
            m.level,
            m.name,
            m.provisional,
            len(weeks),
            sum(w.cost for w in weeks),
            sum(w.capacity for w in weeks),
            sum(w.slop for w in weeks),
        )
    return out


def _read_back(document: dict[str, Any]) -> Any:
    """The ``slop_meter`` section a repository reads from a deployment document storing ``document``."""
    section = CATALOG.get("slop_meter")
    if section is None:
        pytest.fail("the settings document has no slop_meter section: the meter scores with constants in code")
    stored = check(CATALOG, document, stored={}).data
    rows = [{"scope": "deployment", "data": stored, "generation": 1, "updated_at": None}]
    return build_snapshot(CATALOG, rows, token=(), log=False).scoped(section).repo(REPO)


def test_the_fixture_scores_exactly_as_the_code_before_the_section_did() -> None:
    assert _scored(fixture_inputs()) == GOLDEN, "a repository read with no settings scores as it did before #169"


def test_the_default_document_scores_the_fixture_identically_to_the_old_constants() -> None:
    nothing = _read_back({})
    assert _scored(replace(fixture_inputs(), settings=nothing)) == GOLDEN, "an empty stored document"
    every_key = _read_back({"slop_meter": CATALOG.get("slop_meter").defaults().model_dump(mode="json")})  # type: ignore[union-attr]
    assert _scored(replace(fixture_inputs(), settings=every_key)) == GOLDEN, (
        "a document that writes every key at its default, through the same read the meter's tick makes"
    )


def test_the_check_for_an_unchanged_score_can_fail() -> None:
    """The golden comparison is not vacuous: one default moved by a hair changes the fixture's numbers."""
    for moved in ({"lines_per_hour": 40.5}, {"weights": {"*.sql": 0.81}}, {"test_weight": 0.51}):
        assert _scored(replace(fixture_inputs(), settings=_read_back({"slop_meter": moved}))) != GOLDEN, moved


# ---- the weight of every path, against the rules the code held before (verbatim) ----

_OLD_CORE = (
    "py", "pyi", "ts", "mts", "cts", "js", "mjs", "cjs", "go", "rs", "java", "kt", "kts", "scala", "c", "h",
    "cc", "cpp", "cxx", "hpp", "hh", "cs", "rb", "php", "swift", "m", "mm", "ex", "exs", "erl", "clj", "cljs",
    "hs", "ml", "fs", "lua", "pl", "pm", "r", "jl", "dart", "zig", "nim", "groovy", "sh", "bash", "zsh",
    "ps1", "tf", "sol", "proto", "graphql", "gql",
)  # fmt: skip
_OLD_UI = ("tsx", "jsx", "vue", "svelte", "astro", "html", "htm", "erb", "hbs", "ejs", "jinja", "j2", "twig", "liquid")  # fmt: skip
_OLD_STYLE = ("css", "scss", "sass", "less", "styl", "pcss")
_OLD_TYPE_WEIGHTS = {
    **dict.fromkeys(_OLD_CORE, 1.0),
    "sql": 0.8,
    **dict.fromkeys(_OLD_UI, 0.5),
    **dict.fromkeys(_OLD_STYLE, 0.3),
}
_OLD_VENDORED_DIRS = ("vendor", "node_modules", "third_party", "third-party", "external", "deps", "bower_components")
_OLD_GENERATED = re.compile(r"(\.min\.(js|css)|\.bundle\.js|\.map|\.d\.ts)\Z")
_OLD_NON_PRODUCTION_DIRS = (
    "__tests__", "tests", "test", "spec", "specs", "examples", "example", "demos", "demo", "samples", "sample",
    "fixtures", "testdata",
)  # fmt: skip
_OLD_NON_PRODUCTION_FILE = re.compile(r"(\.(test|spec|stories|story)\.[^.]+|_test\.(go|py|rb|exs?)|_spec\.rb)\Z|(\A|/)test_[^/]+\.py\Z")  # fmt: skip


def _old_type_weight(path: str) -> float:
    lowered = path.lower()
    parts = lowered.split("/")
    if any(p in _OLD_VENDORED_DIRS for p in parts[:-1]) or _OLD_GENERATED.search(lowered):
        return 0.0
    name = lowered.rsplit("/", 1)[-1]
    weight = _OLD_TYPE_WEIGHTS.get(name.rsplit(".", 1)[-1].lower() if "." in name else "", 0.0)
    if weight and (any(p in _OLD_NON_PRODUCTION_DIRS for p in parts[:-1]) or _OLD_NON_PRODUCTION_FILE.search(lowered)):
        weight *= 0.5
    return weight


def _corpus() -> list[str]:
    extensions = [*_OLD_TYPE_WEIGHTS, "json", "md", "yaml", "png", "", "min.js", "min.css", "bundle.js", "map"]
    extensions += ["d.ts", "js.map", "PY", "Ts", "TSX", "snap", "lock", "txt", "gz", "tar.gz"]
    dirs = ["", "src/", "vendor/", "a/node_modules/b/", "tests/", "Tests/", "pkg/test/", "examples/", "x/__tests__/"]
    dirs += [
        "third-party/",
        "spec/",
        "fixtures/",
        "testdata/",
        "src/vendored/",
        "lib/deps/",
        "external/x/",
        "a.test.d/",
    ]
    stems = ["a", "a.test", "a.spec", "a.stories", "a.story", "a.test.helper", "a_test", "a_spec", "test_a", "test_"]
    stems += ["TEST_x", "x.Test", ".test", "test", "a.min", "foo.bundle", ".", "a..", "b.e2e.spec", "c.spec.e2e"]
    stems += ["Button.stories.module", "x.d", "y.TEST"]
    out = [d + s + ("." + e if e else "") for d, s, e in product(dirs, stems, extensions)]
    rng = random.Random(7)
    tails = ["", ".py", ".ts", ".js", ".test.ts", "_test.go", ".min.js", ".d.ts", ".css", ".map", ".spec.rb"]
    for _ in range(50_000):
        out.append("".join(rng.choice("abcXYZ._-/ te_stpy") for _ in range(rng.randint(1, 18))) + rng.choice(tails))
    return out


def test_every_path_weighs_what_the_rules_before_the_section_weighed_it() -> None:
    corpus = _corpus()
    differ = [(p, _old_type_weight(p), type_weight(p)) for p in corpus if _old_type_weight(p) != type_weight(p)]
    assert len(corpus) > 80_000 and differ == [], f"{len(differ)} of {len(corpus)} paths weigh otherwise: {differ[:10]}"


# ---- each key moves the score the way it says ----


def _section(values: dict[str, Any]) -> Any:
    """The section a repository reads from a deployment document writing ``values``: ``weights`` merged over
    the default globs, as the meter's tick reads it."""
    return _read_back({"slop_meter": values})


def _rules(values: dict[str, Any]) -> Any:
    settings = _section(values)
    from embry0.services.slop_meter.weights import FileRules

    return FileRules.of(settings)


def test_a_folder_glob_weighs_every_file_under_it_whatever_its_extension() -> None:
    """A glob with a '/' is more specific than any glob without one, so it wins over the file's extension glob
    however long that is: ranking by length let *.graphql beat gen/**."""
    rules = _rules({"weights": {"gen/**": 0.0}})
    for path in ("gen/a.py", "gen/api.proto", "gen/x.swift", "gen/schema.graphql", "gen/deep/er/x.min.js"):
        assert type_weight(path, rules) == 0.0, f"{path} is under gen/"
    assert type_weight("src/schema.graphql", rules) == 1.0 and type_weight("gene/a.py", rules) == 1.0


def test_the_most_specific_glob_wins() -> None:
    from embry0.services.slop_meter.globs import specificity

    rules = _rules(
        {
            "weights": {
                "src/**": 2.0,
                "src/legacy/**": 0.2,
                "src/legacy/keep/*.py": 3.0,
                "**/vendored/*.go": 0.1,
                "db/*": 0.7,
                "db/schema.sql": 0.9,
            }
        }
    )
    assert type_weight("src/a.py", rules) == 2.0 and type_weight("src/README.md", rules) == 2.0, (
        "src/** weighs every file under it"
    )
    assert type_weight("src/legacy/a.py", rules) == 0.2, "more segments free of wildcards: src/legacy/** over src/**"
    assert type_weight("src/legacy/keep/a.py", rules) == 3.0, "three such segments over two"
    assert type_weight("lib/vendored/x.go", rules) == 0.1, "a glob with a '/' over the extension's"
    assert type_weight("db/schema.sql", rules) == 0.9 and type_weight("db/q.sql", rules) == 0.7
    assert specificity("*.min.js") > specificity("*.js"), "between two without a '/', more literal characters"
    assert specificity("a/b/c/*") > specificity("a/**/b.py") > specificity("**/b.py") > specificity("*.longername"), (
        "segments free of wildcards first, then segments, then literal characters; any '/' over none"
    )


def test_the_most_specific_glob_wins_and_a_glob_with_a_slash_matches_from_the_root() -> None:
    rules = _rules({"weights": {"src/*.py": 2.0, "generated/**": 0.0, "*.sql": 1.0, "migrations/**/*.sql": 0.2}})
    assert type_weight("src/a.py", rules) == 2.0 and type_weight("lib/src/a.py", rules) == 1.0, (
        "src/*.py is anchored at the root, and wins over *.py where both match"
    )
    assert type_weight("Generated/deep/x.py", rules) == 0.0, "generated/** reaches every file below it, any case"
    assert type_weight("migrations/v1/up.sql", rules) == 0.2 and type_weight("db/q.sql", rules) == 1.0
    assert type_weight("src/a_test.py", rules) == 1.0, "the test share applies to the glob's weight (2.0 × 0.5)"
    assert type_weight("src/tests/a.py", rules) == 0.5, "'*' stays within one name: src/*.py does not reach it"
    assert type_weight("README.md", rules) == 0.0, "a file no glob matches weighs nothing"


def test_a_path_is_checked_against_the_few_globs_that_could_match_it() -> None:
    """20,000 paths under 200 directories, against the default globs and 900 globs of other directories: each
    path is checked against a handful, not against every glob, and each path once however often it is asked."""
    rng = random.Random(3)
    extensions = ("py", "ts", "js", "go", "rs", "java", "md", "json", "tsx", "css", "proto", "swift")
    paths = [f"d{rng.randrange(200)}/s{rng.randrange(50)}/f{i}.{rng.choice(extensions)}" for i in range(20_000)]
    rules = _rules({"weights": {f"x{i}/legacy/**": 0.0 for i in range(900)}})
    weigh = Weigher(rules=rules)
    for path in paths + paths:
        weigh(path)
    for path in paths:
        type_weight(path, rules)
    assert rules.evaluations < 5 * len(paths), (
        f"{rules.evaluations} glob checks for {len(paths)} paths: a path is checked against every glob, or more "
        "than once"
    )


def test_what_a_test_is_and_what_it_weighs() -> None:
    assert type_weight("pkg/a_test.go") == 0.5
    full = _rules({"test_weight": 1.0})
    assert type_weight("pkg/a_test.go", full) == 1.0 and type_weight("tests/a.py", full) == 1.0
    own = _rules({"test_dirs": ["qa"], "test_files": ["*.it.ts"], "test_marks": ["e2e"]})
    assert type_weight("qa/a.py", own) == 0.5 and type_weight("tests/a.py", own) == 1.0, "a list replaces the default"
    assert type_weight("src/login.it.ts", own) == 0.5 and type_weight("src/login.e2e.ts", own) == 0.5
    assert type_weight("src/a.test.ts", own) == 1.0, "test is no longer a mark there"
    counted = _rules({"excluded_dirs": []})
    assert type_weight("vendor/lib/a.go", counted) == 1.0 and type_weight("vendor/lib/a.go") == 0.0


def _steady_history(weeks: int, per_week: int, lines: int) -> list[Any]:
    rows = []
    for w in range(weeks):
        for i in range(per_week):
            at = START + timedelta(weeks=w, hours=2 * i)
            rows.append(_record(f"s{w}-{i}", at, "Dev", "dev@example.com", "Work", [("src/a.py", lines, 0)]))
    return parse_log("".join(rows))


def test_a_slower_pace_reads_more_slop_and_the_levels_are_the_section_s() -> None:
    commits = _steady_history(weeks=12, per_week=6, lines=120)
    default = meters(commits, [], {}, Weigher(), {})[""]
    slower = meters(commits, [], {}, Weigher(), {}, settings=_section({"lines_per_hour": 10}))[""]
    assert slower.score > default.score, f"fewer lines an hour covers, more slop: {default.score} → {slower.score}"
    names = ["Clean", "Fine", "Mixed", "Sloppy", "Slop"]
    banded = meters(commits, [], {}, Weigher(), {}, settings=_section({"lines_per_hour": 10, "level_names": names}))
    assert banded[""].name in names and banded[""].score == slower.score, "a name is data; the score is not moved"
    from embry0.services.slop_meter.score import level_of

    tight = _section({"level_bounds": [0.01, 0.02, 0.03, 0.04]})
    assert level_of(0.035, tight) == (4, "Sloppy Joe") and level_of(0.5, tight) == (5, "Just The Slop")


def test_a_small_repository_reads_as_the_published_algorithm_with_the_departure_off() -> None:
    drop = "".join(
        _record(
            f"d{i}", START + timedelta(hours=6 * i), "Dev", "dev@example.com", "More", [(f"src/m{i}.py", 10_000, 0)]
        )
        for i in range(5)
    )
    commits = parse_log(drop)
    assert meters(commits, [], {}, Weigher(), {})[""].score == pytest.approx(0.796, abs=1e-3), "embry0's rule"
    written = meters(commits, [], {}, Weigher(), {}, settings=_section({"small_repository_in_full": False}))
    assert written[""].score == pytest.approx(0.8), "as written, the five-commit author counts for nothing"


def test_an_ai_agent_of_the_section_co_authors_as_an_agent_and_authors_as_a_bot() -> None:
    agents = _section({"ai_agents": ["helper-agent@"]}).ai_agents
    from embry0.services.slop_meter.history import agent_prefixes, own_identities, with_bots

    body = "x\n\nCo-authored-by: Helper <helper-agent@tools.example>\n"
    commits = parse_log(
        _record("h1", START, "Ana", "ana@example.com", body, [("src/a.py", 10, 0)])
        + _record("h2", START + timedelta(hours=2), "Helper", "Helper-Agent@tools.example", "x", [("src/b.py", 5, 0)])
    )
    assert not commits[0].ai_coauthor and not commits[1].bot, "an agent no deployment knows reads as a person"
    human, agent = with_bots(commits, own_identities(), agent_prefixes(agents))
    assert human.ai_coauthor and human.coauthors == (), "its co-authorship is an agent's"
    assert agent.bot, "and a commit it authors adds no attention"


def test_the_judge_is_told_the_section_s_guidance_then_the_answer_s_form() -> None:
    guidance = _section({"judge_prompt": "Slop is code no person read.\n"}).judge_prompt
    from embry0.services.slop_meter.judge import ANSWER_FORM, system_prompt

    assert system_prompt(guidance) == f"Slop is code no person read.\n{ANSWER_FORM}"
