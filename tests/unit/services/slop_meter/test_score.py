"""The Slop-O-Meter's scorer on fixture histories (spec 2026-10-26, slop-o-meter): no git, no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.branding import GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME
from embry0.services.slop_meter.history import LOG_FORMAT, is_bot, login_of_email, parse_log
from embry0.services.slop_meter.score import (
    Event,
    capped_hours,
    core_factor,
    level_of,
    meters,
    outliers,
    size_dampening,
    weekly_capacity,
)
from embry0.services.slop_meter.weights import Weigher, ignore_patterns, ignored, outlier_files, type_weight

START = datetime(2025, 1, 6, 9, 0, tzinfo=UTC)  # a Monday


def _log(*commits: tuple) -> str:
    """git log text in LOG_FORMAT: (sha, at, name, email, body, [(path, add, del)])."""
    assert LOG_FORMAT.startswith("%x1e")
    out = []
    for sha, at, name, email, body, files in commits:
        numstat = "\n".join(f"{a}\t{d}\t{p}" for p, a, d in files)
        out.append(f"\x1e{sha}\x1f{at.isoformat()}\x1f{name}\x1f{email}\x1f{body}\n\x1f\n{numstat}\n")
    return "".join(out)


def test_git_log_text_becomes_commits_with_their_trailers() -> None:
    body = (
        "Add the export\n\n* first step\n* second step\n* third step\n\n"
        "Co-authored-by: Ana <ana@example.com>\nCo-authored-by: Claude <noreply@anthropic.com>\n"
    )
    [c] = parse_log(_log(("a1", START, "Bo", "bo@example.com", body, [("src/x.py", 10, 2), ("logo.png", "-", "-")])))
    assert c.files == (("src/x.py", 10, 2), ("logo.png", 0, 0))
    assert c.coauthors == (("Ana", "ana@example.com"),)
    assert c.ai_coauthor is True and c.bot is False and c.subcommits == 3
    assert c.author == "bo@example.com"


def test_bots_and_logins() -> None:
    assert is_bot("dependabot[bot]", "x@example.com") and is_bot("Codex", "codex@openai.com")
    assert is_bot("x", "49699333+dependabot[bot]@users.noreply.github.com") and is_bot("x", "noreply@github.com")
    assert not is_bot("Ana", "ana@example.com")
    assert login_of_email("12345+Octo-Cat@users.noreply.github.com") == "octo-cat"
    assert login_of_email("octocat@users.noreply.github.com") == "octocat"
    assert login_of_email("ana@example.com") is None


def test_embry0s_own_commits_and_events_add_no_attention() -> None:
    """embry0 commits as its branding identity (#184): its work is a bot's, not a person's attention."""
    rows = [
        (f"h{i}", START + timedelta(hours=2 * i), "Ana", "ana@example.com", "x", [("src/a.py", 10, 0)])
        for i in range(3)
    ]
    rows += [
        (f"e{i}", START + timedelta(hours=2 * i + 1), GIT_AUTHOR_NAME, GIT_AUTHOR_EMAIL, "x", [("src/b.py", 50, 0)])
        for i in range(3)
    ]
    commits = parse_log(_log(*sorted(rows, key=lambda c: c[1])))
    assert {c.sha for c in commits if c.bot} == {"e0", "e1", "e2"}, "embry0's own commits are a bot's"
    login = login_of_email(GIT_AUTHOR_EMAIL)
    events = [Event(login, START + timedelta(hours=8))] if login else []
    assert set(weekly_capacity(commits, events, {})) == {"ana@example.com"}, "embry0 adds no attention"


def test_names_addresses_and_logins_an_owner_lists_are_bots() -> None:
    from embry0.services.slop_meter.history import own_identities

    bots = own_identities(["Release-Bot", " release@acme.example ", "", "  "])
    assert "" not in bots, "a blank entry matches nothing"
    assert is_bot("release-bot", "x@example.com", bots), "a listed name"
    assert is_bot("Rel", "Release@Acme.example", bots), "a listed address, without regard to case"
    assert is_bot("x", GIT_AUTHOR_EMAIL.upper(), bots), "embry0's own identity is always among them"
    assert is_bot("dependabot[bot]", "x@example.com", bots), "the known bots still are"
    assert not is_bot("Ana", "ana@example.com", bots)


def test_commits_read_before_an_identity_was_known_are_marked_when_scored() -> None:
    from embry0.services.slop_meter.history import own_identities, with_bots

    body = "x\n\nCo-authored-by: Release Bot <release@acme.example>\nCo-authored-by: Bo <bo@example.com>\n"
    rows = [
        ("h1", START, "Ana", "ana@example.com", body, [("src/a.py", 10, 0)]),
        ("r1", START + timedelta(hours=1), "Release Bot", "release@acme.example", "x", [("src/b.py", 5, 0)]),
    ]
    commits = parse_log(_log(*rows))
    assert not any(c.bot for c in commits) and commits[0].coauthors == (
        ("Release Bot", "release@acme.example"),
        ("Bo", "bo@example.com"),
    ), "read before the owner listed the bot"
    human, release = with_bots(commits, own_identities(["release@acme.example"]))
    assert release.bot, "the listed bot's commit is a bot's"
    assert not human.bot and human.coauthors == (("Bo", "bo@example.com"),), "it is no human co-author"
    assert human.ai_coauthor, "it co-wrote as an agent does"
    assert with_bots(commits, own_identities()) == commits, "nothing listed, nothing changes"


def test_lines_weigh_by_their_file() -> None:
    assert type_weight("src/a.ts") == 1.0 and type_weight("db/q.sql") == 0.8
    assert type_weight("ui/Page.tsx") == 0.5 and type_weight("ui/a.scss") == 0.3
    assert type_weight("src/a.test.ts") == 0.5 and type_weight("tests/test_a.py") == 0.5
    assert type_weight("examples/demo.go") == 0.5 and type_weight("pkg/a_test.go") == 0.5
    for excluded in (
        "README.md",
        "package.json",
        "vendor/lib/a.go",
        "web/node_modules/x/a.js",
        "a.min.js",
        "t.d.ts",
        "a.js.map",
    ):
        assert type_weight(excluded) == 0.0, excluded


def test_linter_ignore_lists_are_merged_and_matched_like_gitignore() -> None:
    patterns = ignore_patterns(
        {
            ".prettierignore": "# built\ndist/\n!keep.ts\n",
            ".gitattributes": "gen/** linguist-generated=true\n*.pb.go linguist-generated\nother.txt text\n",
            "biome.json": '{"files": {"ignore": ["legacy/**"]}}',
            "pyproject.toml": '[tool.ruff]\nexclude = ["migrations"]\nextend-exclude = ["scripts/old.py"]\n',
            "ruff.toml": "not = [valid toml",
        }
    )
    assert patterns == ["dist/", "gen/**", "*.pb.go", "legacy/**", "migrations", "scripts/old.py"]
    assert ignored("web/dist/app.js", patterns) and ignored("api/x.pb.go", patterns)
    assert ignored("app/migrations/0001.py", patterns) and ignored("scripts/old.py", patterns)
    assert not ignored("dist.py", patterns) and not ignored("src/scripts/old.py", patterns)
    weigh = Weigher(patterns)
    assert weigh("gen/a.ts") == 0.0 and weigh("src/a.ts") == 1.0


def test_an_outlier_file_is_dropped_only_below_its_fair_share() -> None:
    sizes = {f"f{i}.py": 10 for i in range(20)} | {"mid.py": 500, "giant.py": 50_000}
    # Q3 + 1.5·IQR is 10; the fair share is about 2,300 lines a file.
    assert outlier_files(sizes) == {"mid.py"}, "the giant holds the logic and stays"


def test_the_constants_of_the_curves() -> None:
    assert capped_hours(20) == pytest.approx(19.58, abs=0.01)
    assert capped_hours(40) == pytest.approx(36.67, abs=0.01)
    assert capped_hours(80) == pytest.approx(53.33, abs=0.01) == pytest.approx(capped_hours(200), abs=0.01)
    assert size_dampening(2_500) == pytest.approx(0.5 + 0.05 * 0.0356, abs=1e-3)
    assert size_dampening(100_000) == pytest.approx(1.05, abs=1e-3)
    assert size_dampening(1_000_000) == pytest.approx(1.173, abs=1e-3)
    assert core_factor(9, 100) == 0.0 and core_factor(35, 100) == pytest.approx(0.5 * 1.0875)
    assert core_factor(60, 60) == pytest.approx(1.25)
    assert [level_of(s)[1] for s in (0.0, 0.25, 0.5, 0.7, 0.9)] == [
        "Buttered Noodles",
        "Spaghetti Bolognese",
        "Lasagna",
        "Sloppy Joe",
        "Just The Slop",
    ]


def _steady(weeks: int, per_week: int, lines: int, email: str = "dev@example.com") -> list:
    commits = []
    for w in range(weeks):
        for i in range(per_week):
            at = START + timedelta(weeks=w, hours=2 * i)
            commits.append((f"s{w}-{i}", at, "Dev", email, "Work", [("src/a.py", lines, 0)]))
    return commits


def test_a_solo_dev_with_steady_work_is_buttered_noodles() -> None:
    commits = parse_log(_log(*_steady(weeks=10, per_week=8, lines=40)))
    meter = meters(commits, [], {}, Weigher(), {})[""]
    assert meter.score == 0.0 and meter.name == "Buttered Noodles" and not meter.provisional
    assert len(meter.weeks) == 10 and meter.weeks[-1].capacity > meter.weeks[-1].cost


def test_a_weekend_slop_project_is_just_the_slop() -> None:
    drop = [
        (f"d{i}", START + timedelta(hours=6 * i), "Dev", "dev@example.com", "More", [(f"src/m{i}.py", 10_000, 0)])
        for i in range(5)
    ]
    commits = parse_log(_log(*drop))
    meter = meters(commits, [], {}, Weigher(), {})[""]
    # One outlier is judged for a two-day project, and the first commit is never slop:
    # its 10,000 lines stay in the cost and leave the excess; the other 40,000 are slop,
    # less the 200 lines its five commits' hours attend to. The developer has under 10
    # commits, so in this small repository their time counts in full (embry0's rule);
    # the published algorithm counts it for nothing and reads exactly 0.8.
    assert meter.score == pytest.approx(0.796, abs=1e-3) and meter.name == "Sloppy Joe"
    assert not meter.provisional


def test_an_unjudged_outlier_leaves_the_excess_and_marks_the_score_provisional() -> None:
    history = _steady(weeks=60, per_week=2, lines=10)
    late = START + timedelta(weeks=30, hours=1)
    history.append(("big", late, "Dev", "dev@example.com", "Import the parser", [("src/parser.py", 9_000, 0)]))
    commits = parse_log(_log(*sorted(history, key=lambda c: c[1])))
    found = outliers(commits, Weigher(), {})
    assert [o.commit.sha for o in found] == ["big"] and found[0].slop is None and found[0].net == 9_000
    unjudged = meters(commits, [], {}, Weigher(), {})[""]
    as_slop = meters(commits, [], {}, Weigher(), {"big": True})[""]
    not_slop = meters(commits, [], {}, Weigher(), {"big": False})[""]
    assert unjudged.provisional and not as_slop.provisional and not not_slop.provisional
    assert unjudged.score == pytest.approx(not_slop.score) and as_slop.score > not_slop.score


def test_sessions_come_from_commits_coauthors_squashes_and_comments() -> None:
    rows = _steady(weeks=1, per_week=59, lines=1, email="ana@example.com")  # Monday to Friday, two hours apart
    at = START + timedelta(days=5, hours=10)  # Saturday evening, clear of the weekday sessions
    rows += [
        ("sq", at, "Ana", "ana@example.com", "Squash\n\n* one\n* two\n", [("src/b.py", 1, 0)]),
        (
            "ai",
            at + timedelta(hours=3),
            "Ana",
            "ana@example.com",
            "x\n\nCo-authored-by: Claude <claude@anthropic.com>",
            [],
        ),
        (
            "bot",
            at,
            "dependabot[bot]",
            "49699333+dependabot[bot]@users.noreply.github.com",
            "Bump",
            [("src/c.py", 5, 0)],
        ),
    ]
    commits = parse_log(_log(*sorted(rows, key=lambda c: c[1])))
    events = [
        Event("ana", at + timedelta(hours=4, minutes=30)),
        Event("renovate[bot]", at + timedelta(hours=1)),
        Event("stranger", at),
    ]
    by_who = weekly_capacity(commits, events, {"ana": "ana@example.com"})
    assert set(by_who) == {"ana@example.com"}, "bots and drive-by logins add no attention"
    week = commits[0].at.date() - timedelta(days=commits[0].at.weekday())
    # 59 commits of 1 h (two hours apart), a 3 h squash, a 0.8 h AI-assisted commit and a 1 h comment.
    hours = 59 + 3 + 0.8 + 1
    factor = core_factor(61, 61)
    assert by_who["ana@example.com"][week] == pytest.approx(capped_hours(hours * factor) * 40, rel=1e-6)


def test_an_area_has_its_own_lines_and_a_share_of_the_capacity() -> None:
    rows = []
    for w in range(12):
        for i in range(6):
            at = START + timedelta(weeks=w, hours=2 * i)
            rows.append((f"a{w}-{i}", at, "Dev", "dev@example.com", "x", [("api/a.py", 30, 0), ("web/b.ts", 10, 0)]))
    rows.append(("dump", START + timedelta(weeks=11, days=3), "Bot", "ci@example.com", "x", [("web/gen.ts", 1_500, 0)]))
    commits = parse_log(_log(*sorted(rows, key=lambda c: c[1])))
    found = meters(commits, [], {}, Weigher(), {}, area_of=lambda p: p.split("/", 1)[0])
    assert set(found) == {"", "api", "web"}
    assert found["api"].score == 0.0, "the api's lines are within its share of the attention"
    assert found["web"].score > found[""].score > 0.0, "the drop landed in web and is diluted across the repository"
    assert found["web"].weeks[-1].lines == pytest.approx(12 * 6 * 10 + 1_500)


def test_a_small_repository_counts_everyone_in_full() -> None:
    """No author reaches 10 commits: nobody is a drive-by, so a small honest repository reads low."""
    small = [
        (f"s{i}", START + timedelta(days=i, hours=1), "Dev", "dev@example.com", "Work", [(f"src/f{i}.py", 120, 0)])
        for i in range(6)
    ]
    meter = meters(parse_log(_log(*small)), [], {}, Weigher(), {})[""]
    assert meter.score < 0.2 and meter.name == "Buttered Noodles"


def test_a_one_commit_repository_has_its_setup_excused() -> None:
    """A history that spans no time still judges one outlier, and a first commit is never slop."""
    drop = [("only", START, "Dev", "dev@example.com", "Initial import", [("src/app.py", 10_000, 0)])]
    meter = meters(parse_log(_log(*drop)), [], {}, Weigher(), {})[""]
    assert meter.score == 0.0 and not meter.provisional


def test_a_fast_dump_by_one_person_still_reads_as_slop() -> None:
    """Counting everyone in full does not excuse 47,000 lines in six days from one person."""
    dump = [
        (f"m{i}", START + timedelta(hours=3 * i), "Dev", "dev@example.com", "More", [(f"src/m{i}.py", 1_080, 0)])
        for i in range(44)
    ]
    meter = meters(parse_log(_log(*dump)), [], {}, Weigher(), {})[""]
    assert meter.score > 0.6


def test_a_moved_file_counts_only_the_lines_it_changed() -> None:
    """git log -M names a rename old => new, or dir/{old => new}/file; the file is the new path."""
    from embry0.services.slop_meter.history import LOG_ARGS, commit_from

    assert "-M" in LOG_ARGS and "--no-renames" not in LOG_ARGS
    numstat = "\n".join(
        [
            "0\t0\tplatform/{api => leads}/src/Lead.java",
            "3\t1\told/name.py => new/name.py",
            "2\t0\t{ => pkg}/x.py",
            "1\t1\tpkg/{a => }/y.py",
            "4\t0\tsrc/plain.py",
        ]
    )
    c = commit_from("s", START, "Dev", "dev@example.com", "Move", numstat)
    assert c.files == (
        ("platform/leads/src/Lead.java", 0, 0),
        ("new/name.py", 3, 1),
        ("pkg/x.py", 2, 0),
        ("pkg/y.py", 1, 1),
        ("src/plain.py", 4, 0),
    )
