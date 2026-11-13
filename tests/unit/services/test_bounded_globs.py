"""The three glob grammars that compiled to a regex, matched in bounded time (#458's reviews).

The Slop-O-Meter's settings globs, the gitignore-style patterns it reads from
a repository's own ignore and configuration files, and the instruction-file
globs each translated a glob to a regex, and a regex of many ``*`` and
``**`` parts backtracks: a pattern of twelve or fourteen of them against a
long path took from seconds to minutes. Each now builds an automaton
(:mod:`embry0.bounded_patterns`). The regex each translator built on main is
copied here, as it was, as the reference: every answer must be the one it
gave, in bounded time.
"""

from __future__ import annotations

import random
import re
import time

import pytest

# ---- main's three translators, verbatim but for their names -------------------------------------------------


def _main_meter_segment(text: str) -> str:
    out = []
    for char in text:
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
    return "".join(out)


def _main_meter_glob(glob: str) -> re.Pattern[str]:
    lowered = glob.lower()
    if "/" not in lowered:
        return re.compile(_main_meter_segment(lowered) + r"\Z")
    segments = lowered.split("/")
    out = []
    for position, segment in enumerate(segments):
        last = position == len(segments) - 1
        if segment == "**":
            out.append(".+" if last else "(?:[^/]+/)*")
        else:
            out.append(_main_meter_segment(segment) + ("" if last else "/"))
    return re.compile("".join(out) + r"\Z")


def _main_ignore(pattern: str) -> re.Pattern[str]:
    directory = pattern.endswith("/")
    body = pattern.strip("/")
    anchored = pattern.startswith("/") or "/" in body
    out, i = "", 0
    while i < len(body):
        if body.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif body.startswith("**", i):
            out, i = out + ".*", i + 2
        elif body[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif body[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(body[i]), i + 1
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(prefix + out + (r"/.*\Z" if directory else r"(?:/.*)?\Z"))


def _main_instruction(glob: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


# ---- each grammar means what it meant ----------------------------------------------------------------------

PATHS = ["a", "b", "ab", "a.py", "x", "", "a\nb", ".a"]


def _paths(rng: random.Random) -> list[str]:
    return ["/".join(rng.choice(PATHS) for _ in range(rng.randint(1, 4))) for _ in range(10)]


def test_a_meter_settings_glob_means_what_it_meant() -> None:
    from embry0.services.slop_meter.globs import check_glob, compile_glob

    rng = random.Random(4751)
    pieces = ["a", "*", "?", "*.py", "**", "b*", ".a", "x?"]
    compared = 0
    for _ in range(2000):
        glob = "/".join(rng.choice(pieces) for _ in range(rng.randint(1, 4)))
        try:
            check_glob(glob)
        except ValueError:
            continue
        reference = _main_meter_glob(glob)
        for path in _paths(rng):
            target = path if "/" in glob else path.rsplit("/", 1)[-1]
            assert bool(compile_glob(glob).fullmatch(target)) is bool(reference.fullmatch(target)), (
                f"{glob!r} vs {target!r}"
            )
            compared += 1
    assert compared > 3000, compared


def test_an_ignore_pattern_means_what_it_meant() -> None:
    from embry0.services.slop_meter.weights import ignored

    rng = random.Random(4752)
    pieces = ["a", "*", "?", "**", "*.py", "b?", "**/a", "x"]
    for _ in range(2000):
        pattern = "/".join(rng.choice(pieces) for _ in range(rng.randint(1, 3)))
        pattern = rng.choice(["", "/"]) + pattern + rng.choice(["", "/"])
        reference = _main_ignore(pattern)
        for path in _paths(rng):
            assert ignored(path, [pattern]) is bool(reference.match(path.lstrip("/"))), f"{pattern!r} vs {path!r}"


def test_an_instruction_file_glob_means_what_it_meant() -> None:
    from embry0.services.instruction_files import _compile

    rng = random.Random(4753)
    pieces = ["**/", "*", "a", ".md", "/", "b", "CLAUDE"]
    for _ in range(2000):
        glob = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 5)))
        reference = _main_instruction(glob)
        for path in _paths(rng):
            assert bool(_compile(glob).fullmatch(path)) is bool(reference.match(path)), f"{glob!r} vs {path!r}"


# ---- in bounded time ---------------------------------------------------------------------------------------

BOUND = 0.5


def _timed(match: object, *args: object) -> tuple[object, float]:
    started = time.perf_counter()
    found = match(*args)  # type: ignore[operator]
    return found, time.perf_counter() - started


def test_a_meter_settings_glob_is_matched_in_bounded_time() -> None:
    """The review's case: 71 characters the settings check accepts, 10.5 s against a 29-segment path."""
    from embry0.services.slop_meter.globs import GlobIndex, check_glob, matches

    glob = "**/*/" * 14 + "a"
    assert check_glob(glob) == glob, "the settings check takes it"
    path = "/".join(["x"] * 28) + "/b"
    found, took = _timed(matches, glob, path)
    assert not found and took < BOUND, (
        f"{len(glob)}-character settings glob, {path.count('/') + 1} segments: {took:.2f}s"
    )
    index = GlobIndex([(glob, 1)])
    found, took = _timed(index.best, path)
    assert found is None and took < BOUND, f"the index took {took:.2f}s"


@pytest.mark.parametrize("pattern", ["**/" * 12 + "a", "*a" * 12 + "b", "**/" + "*a*" * 10 + "b/"])
def test_an_ignore_pattern_from_a_repository_is_matched_in_bounded_time(pattern: str) -> None:
    from embry0.services.slop_meter.weights import ignored

    path = "/".join(["a" * 20] * 25)
    found, took = _timed(ignored, path, [pattern])
    assert found is False and took < BOUND, f"a repository's ignore pattern {pattern[:24]!r}... took {took:.2f}s"


def test_an_instruction_file_glob_is_matched_in_bounded_time() -> None:
    from embry0.services.instruction_files import _compile

    glob = "**/*/" * 14 + "a"
    path = "/".join(["x"] * 28) + "/b"
    found, took = _timed(_compile(glob).fullmatch, path)
    assert not found and took < BOUND, f"an instruction-file glob took {took:.2f}s"


# ---- memory, by size -----------------------------------------------------------------------------------------


def test_a_pattern_that_makes_many_states_holds_little_memory() -> None:
    """The fourth review's case: '*a', '?' after '?', then '*', in a repository's ignore file, against long names.
    Its sets of states grow without bound, each large; what the matcher remembers is bounded by size."""
    import tracemalloc

    from embry0.services.slop_meter.weights import ignored

    pattern = "*a" + "?" * 197 + "*"  # 200 characters, as long as a repository's ignore line may be
    rng = random.Random(1)
    names = ["".join(rng.choice("ab") for _ in range(260)) for _ in range(100)]
    reference = _main_ignore(pattern)
    tracemalloc.start()
    started = time.perf_counter()
    try:
        answers = [ignored(name, [pattern]) for name in names]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    took = time.perf_counter() - started
    assert peak < 40e6, f"matching 100 names held {peak / 1e6:.0f} MB"
    assert took < 30, f"matching 100 names took {took:.1f}s"
    assert answers == [bool(reference.match(name)) for name in names], "the answers are not main's"


def test_a_repository_s_ignore_patterns_are_capped_in_length_and_number() -> None:
    from embry0.services.slop_meter.weights import MAX_IGNORE_PATTERN_LENGTH, MAX_IGNORE_PATTERNS, ignore_patterns

    lines = ["a" * (MAX_IGNORE_PATTERN_LENGTH + 1), "kept/"] + [f"dir{i}/" for i in range(MAX_IGNORE_PATTERNS + 50)]
    found = ignore_patterns({".prettierignore": "\n".join(lines)})
    assert found[0] == "kept/" and len(found) == MAX_IGNORE_PATTERNS, (found[:2], len(found))
    assert all(len(p) <= MAX_IGNORE_PATTERN_LENGTH for p in found)


# ---- which patterns go to Python's regex ---------------------------------------------------------------------


def test_only_a_pattern_that_can_backtrack_goes_to_the_automaton() -> None:
    from embry0.services.instruction_files import _compile
    from embry0.services.slop_meter.globs import compile_glob
    from embry0.services.slop_meter.weights import _compiled

    for glob in ("*.py", "src/**/*.ts", "generated/**", "test_?*.py", "db/migrations/*.sql", "**/*.pb.go"):
        assert compile_glob(glob).uses_regex, f"{glob!r} cannot backtrack and is matched by the automaton"
    for line in ("node_modules/", "*.min.js", "**/generated/**", "/public/static/", "packages/*/dist/"):
        assert _compiled(line).uses_regex, f"{line!r} cannot backtrack and is matched by the automaton"
    for glob in ("**/CLAUDE.md", "**/*.md", ".github/copilot-instructions.md"):
        assert _compile(glob).uses_regex, f"{glob!r} cannot backtrack and is matched by the automaton"
    for glob in ("*a*b", "**/a/**/b", "**/*/" * 3 + "a"):
        assert not compile_glob(glob).uses_regex, f"{glob!r} can backtrack and is matched by the regex"
    assert not _compile("**/.claude/rules/**/*.md").uses_regex
    # A line ending inside a name ends in '[^/]*(?:/.*)?', which every path without a newline matches one way.
    for line in ("*.bak*", "*.bak-*", "*.sqlite*", "*.pyc*", "*.tfstate.*", "**/*.generated.*", "npm-debug.log*"):
        assert _compiled(line).uses_regex, f"{line!r} cannot backtrack and is matched by the automaton"
    for line in ("*.bak*/", "a*b*c", "**/a/**/b", "*a*b*c*d*"):
        assert not _compiled(line).uses_regex, f"{line!r} can backtrack and is matched by the regex"
    # Of those that can backtrack, RE2 takes every one whose DFA is small, as a JS monorepo's lines (the fifth
    # review's), and the automaton the rest.
    for line in (*JS_MONOREPO_LINES, "a*b*c", "**/a/**/b"):
        assert _compiled(line).engine in ("re", "re2"), f"{line!r}, whose DFA is small, is the automaton's"
    assert sum(_compiled(line).engine == "re2" for line in JS_MONOREPO_LINES) == 15
    assert compile_glob("**/*/" * 14 + "a").engine == "re2" and _compile("**/.claude/rules/**/*.md").engine == "re2"
    assert _compiled(LINE_BUILT_TO_WALK).engine == "automaton", "a line whose DFA grows with the text went to RE2"


def test_a_pattern_of_many_optional_parts_goes_to_the_automaton() -> None:
    """The fourth review's case: one '*' and 24 optional parts, 0.73 s in Python's regex, doubling per part."""
    from embry0.bounded_patterns import NOT_SLASH, Pattern, lit, opt, star

    n = 26
    pattern = Pattern(*[opt(lit("a"))] * n, *[lit("a")] * n, star(NOT_SLASH), lit("b"))
    found, took = _timed(pattern.fullmatch, "a" * (2 * n) + "c")
    assert not found and took < BOUND, f"one '*' and {n} optional parts took {took:.2f}s"
    # Small enough for the check to read, and still too many ways to split a text: each part counts.
    small = Pattern(*[opt(lit("a"))] * 19, *[lit("a")] * 19, star(NOT_SLASH), lit("b"))
    assert not small.uses_regex, "a pattern of 19 optional parts and a '*' is matched by the regex"


def test_every_pattern_the_regex_takes_is_matched_in_bounded_time() -> None:
    """Random patterns the check sends to Python's regex, against texts built to make a backtracking matcher
    work: each answers in a small multiple of the text's length."""
    from embry0.bounded_patterns import ANY, NOT_SLASH, Pattern, lit, opt, plus, seq, star

    rng = random.Random(4755)
    pieces = [lambda: lit(rng.choice("ab/")), lambda: NOT_SLASH, lambda: ANY]
    regex_taken = 0
    for _ in range(600):
        parts = []
        for _ in range(rng.randint(1, 5)):
            inner = seq(*[rng.choice(pieces)() for _ in range(rng.randint(1, 2))])
            parts.append(rng.choice([inner, star(inner), plus(inner), opt(inner)]))
        pattern = Pattern(*parts)
        if pattern._regex is None:
            continue
        regex_taken += 1
        for text in ("a" * 3000 + "c", "a/" * 1500 + "c", "ab" * 1500 + "/", "/" * 3000 + "\n"):
            found, took = _timed(pattern.fullmatch, text)
            assert took < BOUND, f"{pattern._regex.pattern!r} took {took:.2f}s on {text[:6]!r}..."  # type: ignore[union-attr]
    assert regex_taken > 100, regex_taken


def test_every_pattern_ending_in_a_tail_the_regex_takes_is_matched_in_bounded_time() -> None:
    """Random patterns ending as an ignore line does ('*' and the rest of the path below it), or nearly: those the
    check sends to Python's regex answer in a small multiple of the text's length, as the automaton does."""
    from embry0.bounded_patterns import ANY, NOT_SLASH, Pattern, lit, opt, plus, seq, star

    rng = random.Random(4762)
    pieces = [lambda: lit(rng.choice("ab/.")), lambda: NOT_SLASH, lambda: ANY]
    tails = [
        lambda: [star(NOT_SLASH), opt(lit("/"), star(ANY))],  # a tail
        lambda: [opt(lit("/"), star(ANY))],  # the rest of the path below
        lambda: [star(NOT_SLASH), opt(lit("/"), star(NOT_SLASH))],  # one directory more only: not a tail
        lambda: [star(lit("a")), opt(lit("/"), star(ANY))],  # a 'b' ends it: not a tail
        lambda: [star(NOT_SLASH), lit("/"), star(ANY)],  # a directory: not a tail
    ]
    texts = [
        "a" * 3000 + "c", ".a" * 1500 + "/", "a/" * 1500 + "c", ".b" * 1500, "ab" * 1500 + "\n" + "a/b", "/" * 3000 + "\n",
    ]  # fmt: skip
    taken = 0
    for _ in range(400):
        parts = []
        for _ in range(rng.randint(1, 4)):
            inner = seq(*[rng.choice(pieces)() for _ in range(rng.randint(1, 2))])
            parts.append(rng.choice([inner, star(inner), plus(inner), opt(inner)]))
        parts += rng.choice(tails)()
        pattern, automaton = Pattern(*parts), Pattern(*parts, prefer_regex=False, use_re2=False)
        if not pattern.uses_regex:
            continue
        taken += 1
        for text in texts:
            found, took = _timed(pattern.fullmatch, text)
            assert took < BOUND, f"{pattern._regex.pattern!r} took {took:.2f}s on {text[:6]!r}..."  # type: ignore[union-attr]
            assert found is automaton.fullmatch(text), (pattern._regex.pattern, text[:6])  # type: ignore[union-attr]
    assert taken > 100, taken


def test_the_instruction_files_of_a_tree_are_main_s() -> None:
    """The globs Python's regex takes are matched one by one, the rest as one automaton: the files are main's."""
    from embry0.services.instruction_files import INSTRUCTION_FILE_GLOBS, FileRules

    rng = random.Random(4756)
    words = ["src", ".claude", "rules", "skills", ".cursor", "docs", "a"]
    names = ["CLAUDE.md", "AGENTS.md", "x.md", "y.mdc", "SKILL.md", "README.md"]
    paths = [
        "/".join(rng.choice(words) for _ in range(rng.randint(0, 5))) + "/" + rng.choice(names) for _ in range(3000)
    ]
    paths = [p.lstrip("/") for p in paths]
    for globs in (INSTRUCTION_FILE_GLOBS, ("**/*.md", "src/**/x.md"), ("docs/**/*.md", "**/.claude/**/*.md", "a")):
        expected = sorted(
            p for p in paths if any(p.startswith(f"{g}/") or _main_instruction(g).match(p) for g in globs)
        )
        assert FileRules(tuple(globs), frozenset()).select(paths) == expected, globs


def test_an_instruction_file_whose_path_re2_cannot_read_is_still_chosen() -> None:
    """The globs RE2 takes read a path holding a lone surrogate with their automata."""
    from embry0.services.instruction_files import FileRules, _compile

    glob = "**/.claude/rules/**/*.md"
    assert _compile(glob).engine == "re2"
    paths = [".claude/rules/\ud800/x.md", "a/.claude/rules/y.md", ".claude/rules/\ud800/x.txt"]
    assert FileRules((glob,), frozenset()).select(paths) == [".claude/rules/\ud800/x.md", "a/.claude/rules/y.md"]


# ---- a scoring pass's budget of work for its ignore lines ----------------------------------------------------

COMMON_LINES = [
    "node_modules/", "dist/", "build/", "*.min.js", "coverage/", "**/generated/**", "vendor/", "*.lock",
    "/public/static/", "docs/**/*.md", "*.snap", ".next/", "out/", "**/*.pb.go", "tmp/", "*.log",
    "storybook-static/", "**/fixtures/**", "*.svg", "packages/*/dist/",
    # found in real ignore files; the last five are the automaton's
    "*.bak*", "*.bak-*", "*.sqlite*", "*.pyc*", "*.tfstate.*", "*.DISABLED-*",
    "**/*.*proj.user", "**/a/**/b", "a*b*c", "src/**/test/**/*.snap", "*a*b*c*d*e*f*",
]  # fmt: skip
#: The fifth review's: a JS monorepo's lines, '**/' and two or more '*'.
JS_MONOREPO_LINES = [
    "**/*.*.*.*.js", "**/*-*-*-*-*.json", "**/__generated__/**/*.graphql.*", "**/*.stories.*", "**/*.module.*.css",
    "**/*.test.*.snap", "**/*-*.min.*", "**/e2e/**/*.spec.*", "**/*.d.*.ts", "**/*_*_*.*", "**/*.*-*.*", "**/*.v2.*.*",
    "packages/*/src/**/*.*.ts", "**/ui/**/*-*/*.css", "**/*.config.*.js", "apps/*/**/*.*.*.tsx",
]  # fmt: skip
LINE_BUILT_TO_WALK = "*a" + "?" * 197 + "*"  # 200 characters, as long as a repository's ignore line may be
SHORTER_LINE_BUILT_TO_WALK = "*a" + "?" * 60 + "*"


def _tree(rng: random.Random, count: int) -> list[str]:
    words = ["src", "lib", "app", "docs", "test", "internal", "pkg", "web", "api", "generated", "vendor", "dist"]
    names = ["index.ts", "README.md", "a.min.js", "main.go", "x_test.py", "schema.pb.go", "lock.json", "y.svg"]
    names += ["db.sqlite3", "x.bak.py", "a.tfstate.backup", "z.csproj.user", "abc.txt"]
    return [
        "/".join(rng.choice(words) for _ in range(rng.randint(1, 8))) + "/" + rng.choice(names) for _ in range(count)
    ]


def _names(seed: int, count: int) -> list[str]:
    """Names of 260 characters of 'a' and 'b': a line built to walk is at its worst on them."""
    rng = random.Random(seed)
    return ["".join(rng.choice("ab") for _ in range(260)) for _ in range(count)]


def _dropped(logs: list[dict[str, object]]) -> list[dict[str, object]]:
    return [e for e in logs if e["event"] == "slop_meter_ignore_line_dropped"]


def _js_tree(rng: random.Random, count: int) -> list[str]:
    words = ["my", "user", "auth", "button", "card", "list", "page", "v2", "test", "spec", "stories", "module", "min"]
    words += ["d", "config", "data", "api", "ui", "e2e", "__generated__", "packages", "src", "apps", "graphql"]
    exts = [".ts", ".tsx", ".js", ".json", ".css", ".graphql", ".snap", ".map"]

    def name() -> str:
        return rng.choice(["-", "_", "."]).join(rng.choice(words) for _ in range(rng.randint(1, 5)))

    return ["/".join(name() for _ in range(rng.randint(1, 8))) + rng.choice(exts) for _ in range(count)]


def test_a_common_repository_s_ignore_lines_are_never_dropped_and_ignore_what_they_did() -> None:
    """The benchmark set, lines from real ignore files and a JS monorepo's, over 20,000 tree paths and 20,000 of
    a JS monorepo: no line is dropped and every answer is main's, so no score of such a repository changes. Not
    on names built to make a line walk either."""
    from structlog.testing import capture_logs

    from embry0.services.slop_meter.weights import IgnoreLines, _compiled

    common = COMMON_LINES + JS_MONOREPO_LINES
    engines = [_compiled(line).engine for line in common]
    assert engines.count("re2") == 20 and "automaton" not in engines, "the lines RE2 takes are tested"
    paths = _tree(random.Random(3), 20_000) + _js_tree(random.Random(4769), 20_000)
    lines = IgnoreLines(common, repo="acme/web")
    references = [_main_ignore(line) for line in common]
    with capture_logs() as logs:
        answers = [lines.ignored(path) for path in paths]
        for name in _names(4763, 300):
            lines.ignored(name)
    assert lines.dropped == [] and not _dropped(logs), lines.dropped
    assert answers == [any(r.match(path) for r in references) for path in paths], "an answer differs from main's"


@pytest.mark.parametrize("line", [LINE_BUILT_TO_WALK, SHORTER_LINE_BUILT_TO_WALK], ids=["200", "63"])
def test_a_line_built_to_walk_is_dropped_for_the_pass_and_the_rest_still_apply(line: str) -> None:
    """A line that makes the automaton walk without remembering is dropped once the pass's work passes its
    budget, with one log line naming the repository and the line's position, never its text; what it
    remembered goes back to the pass's memory, and the other lines still apply."""
    from structlog.testing import capture_logs

    from embry0.services.slop_meter.weights import IGNORE_MEMORY, IgnoreLines, _compiled

    # Beside it, two lines the regex takes and one the automaton does (its DFA is too large for RE2: any 'c' starts
    # a new try), which works out a few steps of its own: names of 'a' and 'b' hold no 'c'.
    neighbour = "*c" + "?" * 40 + "*"
    assert _compiled(neighbour).engine == "automaton"
    lines = IgnoreLines(["node_modules/", line, "*.min.js", neighbour], repo="acme/web")
    with capture_logs() as logs:
        started = time.perf_counter()
        for name in _names(4758, 2000):
            lines.ignored(name)
            assert lines._memory.spent <= IGNORE_MEMORY
        took = time.perf_counter() - started
        assert lines.ignored("web/node_modules/x.js") and lines.ignored("a/b.min.js"), "the other lines still apply"
        assert lines.ignored("c" + "x" * 40) and lines._work[3] > 0, "the automaton's other line still applies"
    dropped = _dropped(logs)
    assert lines.dropped == [1] and len(dropped) == 1, (lines.dropped, dropped)
    assert (dropped[0]["repo"], dropped[0]["position"], dropped[0]["lines"]) == ("acme/web", 2, 4), dropped[0]
    assert line not in repr(dropped[0]) and "?" * 20 not in repr(dropped[0]), "the log names the text"
    kept = lines._copies[3]
    assert kept is not None and lines._memory.spent == kept._cost, "the dropped line kept what it remembered"
    assert took < 2, f"2,000 names took {took:.1f}s with the line dropped (each was 1 to 4 ms with it)"


def test_a_dropped_line_gives_back_what_it_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    """A line's copy remembers within the pass's memory; dropped, it gives all of it back for the lines kept."""
    from embry0 import bounded_patterns
    from embry0.services.slop_meter.weights import IgnoreLines

    monkeypatch.setattr(bounded_patterns, "_PATTERN_BUDGET", 10**9)  # it remembers until the pass's memory is full
    lines = IgnoreLines([SHORTER_LINE_BUILT_TO_WALK], repo="acme/web")
    held = 0
    for name in _names(4767, 500):
        held = lines._memory.spent
        lines.ignored(name)
        if lines.dropped:
            break
    assert lines.dropped == [0] and held > 10_000, f"dropped {lines.dropped}, holding {held} before"
    assert lines._memory.spent == 0, f"the dropped line still holds {lines._memory.spent} of the pass's memory"


def test_a_pass_that_stays_within_its_budget_drops_nothing() -> None:
    """A line built to walk costs what it costs until the pass's lines have done the budget's work between them:
    a pass of a few names keeps it, and answers as main did."""
    from embry0.services.slop_meter.weights import IGNORE_PASS_WORK, IgnoreLines

    lines = IgnoreLines(["node_modules/", LINE_BUILT_TO_WALK], repo="acme/web")
    reference = _main_ignore(LINE_BUILT_TO_WALK)
    names = _names(4765, 3)
    assert [lines.ignored(name) for name in names] == [bool(reference.match(name)) for name in names]
    assert lines.dropped == [] and 0 < lines._pass_work <= IGNORE_PASS_WORK, (lines.dropped, lines._pass_work)


def test_a_path_only_the_automaton_reads_is_read_by_a_line_s_copy_and_counted() -> None:
    """A path holding a newline, for a line whose regex reads none, and a lone surrogate, which RE2 cannot read
    (UTF-8 cannot hold it), are read by the line's automaton, whose work the pass counts: the answers are main's."""
    from embry0.services.slop_meter.weights import IgnoreLines, _compiled

    regex_line, re2_line = "*.bak*", "**/*.module.*.css"
    assert (_compiled(regex_line).engine, _compiled(re2_line).engine) == ("re", "re2")
    assert not _compiled(regex_line).regex_reads_newlines
    for path, line in (("a/b.bak\nc", regex_line), ("x\ud800/y.module.a.css", re2_line), ("y.module.a.css", re2_line)):
        lines = IgnoreLines([line], repo="acme/web")
        assert lines.ignored(path) is bool(_main_ignore(line).match(path)), (line, path)
        assert (lines._work[0] > 0) is (path != "y.module.a.css"), (line, path, lines._work)


def test_a_line_dropped_answers_no_more_whatever_reads_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A line of the regex or of RE2 that its automaton read past the budget (no budget at all here) is dropped
    for every path after, not only for the paths its automaton reads."""
    from embry0.services.slop_meter import weights
    from embry0.services.slop_meter.weights import IgnoreLines

    monkeypatch.setattr(weights, "IGNORE_PASS_WORK", 0)
    monkeypatch.setattr(weights, "IGNORE_LINE_WORK", 0)
    for line, read_by_its_automaton, path in (
        ("*.bak*", "a/b.bak\nc", "x.bak"),
        ("**/*.module.*.css", "x\ud800/y.module.a.css", "y.module.a.css"),
    ):
        lines = IgnoreLines([line], repo="acme/web")
        assert lines.ignored(path), "the line applies before it is dropped"
        lines.ignored(read_by_its_automaton)
        assert lines.dropped == [0] and not lines.ignored(path), (line, lines.dropped)


def test_many_lines_built_to_walk_are_all_dropped_in_bounded_time() -> None:
    """As many lines as a repository may have, each built to walk: every one is dropped, once, and the pass's
    work stays bounded."""
    from structlog.testing import capture_logs

    from embry0.services.slop_meter.weights import MAX_IGNORE_PATTERNS, IgnoreLines

    built = [f"*{c}" + "?" * 60 + "*" + "?" * k for c in "ab" for k in range(MAX_IGNORE_PATTERNS // 2)]
    lines = IgnoreLines(built, repo="acme/web")
    with capture_logs() as logs:
        started = time.perf_counter()
        for name in _names(4764, 500):
            lines.ignored(name)
        took = time.perf_counter() - started
    assert sorted(lines.dropped) == list(range(len(built))), f"{len(lines.dropped)} of {len(built)} dropped"
    assert sorted(e["position"] for e in _dropped(logs)) == list(range(1, len(built) + 1)), "one log line each"
    assert took < 10, f"{len(built)} lines over 500 names took {took:.1f}s"


def test_a_pass_drops_the_same_line_at_the_same_path_whatever_was_remembered_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pass matches with copies of its own: a second pass drops the line at the same path, with the shared
    pattern warm, and with the memory every pattern shares full."""
    from embry0 import bounded_patterns
    from embry0.services.slop_meter.weights import IgnoreLines, _compiled

    names = _names(4759, 300)

    def dropped_after() -> int:
        lines = IgnoreLines([LINE_BUILT_TO_WALK], repo="acme/web")
        for count, name in enumerate(names, 1):
            lines.ignored(name)
            if lines.dropped:
                return count
        return 0

    first = dropped_after()
    assert first, "the line was never dropped"
    for name in names[:20]:
        _compiled(LINE_BUILT_TO_WALK).fullmatch(name)
    assert dropped_after() == first, "a second pass dropped the line at another path"
    monkeypatch.setattr(bounded_patterns._SHARED, "spent", bounded_patterns._SHARED.limit)
    assert dropped_after() == first, "a pass with the shared memory full dropped the line at another path"


def test_a_scoring_pass_s_two_weighers_share_the_budget() -> None:
    """weigher_for weighs every path twice (the outliers, then the score): a line dropped in the first stays
    dropped in the second, so both read the same lines."""
    from datetime import UTC, datetime

    from embry0.services.slop_meter.history import Commit
    from embry0.services.slop_meter.loop import weigher_for

    at = datetime(2026, 11, 13, tzinfo=UTC)
    commits = [
        Commit(sha=f"c{i}", at=at, author_name="a", author_email="a@example.com", message="m", files=((name, 10, 0),))
        for i, name in enumerate(name + ".py" for name in _names(4760, 400))
    ]
    weigh = weigher_for(commits, {".prettierignore": "node_modules/\n" + LINE_BUILT_TO_WALK}, repo="acme/web")
    assert weigh._ignore.dropped == [1], weigh._ignore.dropped
    assert weigh("web/node_modules/a.py") == 0.0 and weigh("web/app.py") > 0
