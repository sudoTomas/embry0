"""How much attention a changed line demands, by its file (spec 2026-10-26, slop-o-meter).

After Slop-O-Meter's published slop score algorithm
(https://github.com/slop-o-meter/slop-o-meter, docs/slop-score-algorithm.md):
a line weighs by its file's type, a share of that for tests and examples,
nothing for non-code, vendored, generated or linter-ignored files, and
nothing for an outlier file smaller than its fair share. What each kind of
file weighs, which directories are vendored and what a test is are the
settings document's ``slop_meter`` section (#169), read as a
:class:`FileRules`; which ignore lists are read and the outlier rule are the
algorithm's.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from functools import lru_cache
from statistics import quantiles
from typing import Any, Final

import structlog

from embry0.bounded_patterns import ANY, NOT_SLASH, Memory, Node, Pattern, any_re2_fullmatch, lit, opt, seq, star
from embry0.json_text import json_object
from embry0.services.slop_meter.globs import GlobIndex, marked
from embry0.settings.sections.slop_meter import SLOP_METER, SlopMeterSettings

logger = structlog.get_logger(__name__)

#: The most path weights one :class:`FileRules` keeps.
_KEPT_MAX = 200_000


class FileRules:
    """What a changed line of each file weighs, from the ``slop_meter`` section (:meth:`of`).

    The globs of ``weights`` and ``test_files`` are compiled and indexed once
    (:class:`embry0.services.slop_meter.globs.GlobIndex`): where several
    match a file, the most specific counts. Each path's weight is kept, so one
    run weighs a path once however often it is asked. Build one per run: the
    kept weights are its own.
    """

    def __init__(
        self,
        weights: Mapping[str, float],
        *,
        excluded_dirs: Iterable[str],
        test_dirs: Iterable[str],
        test_files: Iterable[str],
        test_marks: Iterable[str],
        test_weight: float,
    ) -> None:
        self._weights: GlobIndex[float] = GlobIndex((glob, float(weight)) for glob, weight in weights.items())
        self._tests: GlobIndex[bool] = GlobIndex((glob, True) for glob in test_files)
        self.excluded_dirs = frozenset(name.lower() for name in excluded_dirs)
        self.test_dirs = frozenset(name.lower() for name in test_dirs)
        self.test_marks = frozenset(mark.lower() for mark in test_marks)
        self.test_weight = float(test_weight)
        self._kept: dict[str, float] = {}

    @classmethod
    def of(cls, settings: SlopMeterSettings) -> FileRules:
        return cls(
            settings.weights,
            excluded_dirs=settings.excluded_dirs,
            test_dirs=settings.test_dirs,
            test_files=settings.test_files,
            test_marks=settings.test_marks,
            test_weight=settings.test_weight,
        )

    @property
    def evaluations(self) -> int:
        """How many globs these rules have checked a path against so far."""
        return self._weights.evaluations + self._tests.evaluations

    def weight(self, path: str) -> float:
        """A path's weight before ignore lists and outliers: its glob's, a share for tests, 0 when excluded."""
        kept = self._kept.get(path)
        if kept is None:
            if len(self._kept) >= _KEPT_MAX:
                self._kept.clear()  # rules kept for long (the defaults) never grow without bound
            kept = self._kept[path] = self._weigh(path)
        return kept

    def _weigh(self, path: str) -> float:
        lowered = path.lower()
        parts = lowered.split("/")
        if any(p in self.excluded_dirs for p in parts[:-1]):
            return 0.0
        weight = self._weights.best(lowered) or 0.0
        if weight and (
            any(p in self.test_dirs for p in parts[:-1])
            or marked(parts[-1], self.test_marks)
            or self._tests.best(lowered) is not None
        ):
            weight *= self.test_weight
        return weight


#: The rules of the section's defaults: what every reading used before the section, and still uses where
#: nothing is written.
DEFAULT_RULES = FileRules.of(SLOP_METER.defaults())


def type_weight(path: str, rules: FileRules = DEFAULT_RULES) -> float:
    """A path's weight before ignore lists and outliers, under ``rules`` (the defaults when none is given)."""
    return rules.weight(path)


# ---- ignore lists ----

#: The ignore files a linter or formatter reads (gitignore-style globs), at the repository root.
IGNORE_FILES = (".prettierignore", ".eslintignore", ".stylelintignore")
#: The longest pattern read from a repository's ignore or configuration files, and how many are read: a longer
#: one is left out, and the files' patterns past the count too. They are the repository's own text, and matching
#: one costs time in its length for every file the meter scores.
MAX_IGNORE_PATTERN_LENGTH = 200  # as a settings glob (slop_meter.globs.MAX_GLOB_LENGTH)
MAX_IGNORE_PATTERNS = 256
CONFIG_FILES = (*IGNORE_FILES, ".gitattributes", "biome.json", "pyproject.toml", "ruff.toml", ".ruff.toml")


def _ignore_lines(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith(("#", "!")):
            out.append(line)
    return out


def _attributes(text: str) -> list[str]:
    """``.gitattributes`` patterns marked generated or vendored."""
    out = []
    for raw in text.splitlines():
        parts = raw.split()
        if len(parts) >= 2 and not parts[0].startswith("#"):
            attrs = parts[1:]
            if any(a in ("linguist-generated", "linguist-generated=true", "linguist-vendored", "linguist-vendored=true") for a in attrs):  # fmt: skip
                out.append(parts[0])
    return out


def _strings(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def ignore_patterns(files: Mapping[str, str | None]) -> list[str]:
    """Every pattern the repository's linters and formatters ignore, merged; a missing or malformed file adds none.

    At most :data:`MAX_IGNORE_PATTERNS`, each at most :data:`MAX_IGNORE_PATTERN_LENGTH` characters.
    """
    patterns: list[str] = []
    for name in IGNORE_FILES:
        if files.get(name):
            patterns += _ignore_lines(str(files[name]))
    if files.get(".gitattributes"):
        patterns += _attributes(str(files[".gitattributes"]))
    if files.get("biome.json"):
        section = (json_object(str(files["biome.json"])) or {}).get("files")
        if isinstance(section, dict):
            patterns += _strings(section.get("ignore"))
    for name, path in (("pyproject.toml", ("tool", "ruff")), ("ruff.toml", ()), (".ruff.toml", ())):
        if not files.get(name):
            continue
        try:
            data: Any = tomllib.loads(str(files[name]))
        except (tomllib.TOMLDecodeError, ValueError, RecursionError):  # RecursionError: nesting past the limit
            continue
        for key in path:
            data = data.get(key) if isinstance(data, dict) else None
        if isinstance(data, dict):
            patterns += _strings(data.get("exclude")) + _strings(data.get("extend-exclude"))
    return [p for p in patterns if len(p) <= MAX_IGNORE_PATTERN_LENGTH][:MAX_IGNORE_PATTERNS]


@lru_cache(maxsize=4096)
def _compiled(pattern: str) -> Pattern:
    """A gitignore-style pattern over file paths, matched in bounded time (:mod:`embry0.bounded_patterns`): the
    patterns are a repository's own, from its ignore and configuration files.

    Anchored at the root when it holds a slash (other than a trailing one),
    else matched at any depth; ``*`` and ``?`` stay within a directory, ``**``
    crosses them; a match covers everything below it, and a trailing slash
    matches directories only.
    """
    directory = pattern.endswith("/")
    body = pattern.strip("/")
    anchored = pattern.startswith("/") or "/" in body
    parts: list[Node] = [] if anchored else [opt(star(ANY), lit("/"))]
    i = 0
    while i < len(body):
        if body.startswith("**/", i):
            parts.append(opt(star(ANY), lit("/")))
            i += 3
        elif body.startswith("**", i):
            parts.append(star(ANY))
            i += 2
        elif body[i] == "*":
            parts.append(star(NOT_SLASH))
            i += 1
        elif body[i] == "?":
            parts.append(NOT_SLASH)
            i += 1
        else:
            parts.append(lit(body[i]))
            i += 1
    parts.append(seq(lit("/"), star(ANY)) if directory else opt(lit("/"), star(ANY)))
    return Pattern(*parts)


#: The work a scoring pass's ignore lines may do, as their automata count it
#: (:meth:`embry0.bounded_patterns.Pattern.fullmatch_counted`: for each character read by working out where the
#: states move, the number of states; a step read from memory, and a line Python's regex takes, count none).
#: Once the pass's lines have done more than :data:`IGNORE_PASS_WORK` between them, every line that has done more
#: than :data:`IGNORE_LINE_WORK` is dropped for the rest of the pass, and so is a line that passes it later. A
#: common line works out a few hundred to a few thousand steps, a number its pattern sets whatever the repository
#: holds, and then reads from memory; a line built to outgrow its memory walks on, at about 100 ns a state for
#: each character. So a pass's lines do at most about :data:`IGNORE_PASS_WORK` plus :data:`IGNORE_LINE_WORK` for
#: each line, and a path's worth more each: well under a second.
IGNORE_PASS_WORK: Final = 1_000_000
IGNORE_LINE_WORK: Final = 20_000
#: What the pass's automata may remember together (:class:`embry0.bounded_patterns.Memory`; about 40 bytes each).
IGNORE_MEMORY: Final = 500_000


class IgnoreLines:
    """A repository's ignore lines for one scoring pass, matched within a budget of work
    (:data:`IGNORE_PASS_WORK`).

    A path is matched against the lines Python's regex takes, then those RE2
    takes (the path encoded once for them all), then the rest, which the
    automaton walks: the order of their cost. A line the automaton walks, and
    any line for a path only it can read, is matched by a copy of its
    automaton the pass keeps (:meth:`embry0.bounded_patterns.Pattern.run_copy`),
    so its work depends only on the paths of the pass, in their order: the
    same pass drops the same lines at the same paths, whatever else was
    matched before. A line dropped is logged once, naming the repository and
    the line's position among its ignore lines (never its text: it is the
    repository's own), and the score uses the lines still kept.
    """

    def __init__(self, patterns: Iterable[str], *, repo: str = "") -> None:
        self.patterns = tuple(patterns)
        self.repo = repo
        self._compiled = [_compiled(pattern) for pattern in self.patterns]
        self._memory = Memory(IGNORE_MEMORY)
        self._copies: list[Pattern | None] = [None] * len(self.patterns)
        self._kept = [True] * len(self.patterns)
        self._work = [0] * len(self.patterns)  # each line's
        self._pass_work = 0  # every line's, dropped ones too
        #: The positions dropped (from 0), in the order they were.
        self.dropped: list[int] = []
        self._sort()

    def _sort(self) -> None:
        """The lines still kept, by what matches them."""
        kept = [(index, pattern) for index, pattern in enumerate(self._compiled) if self._kept[index]]
        self._by_regex = tuple(
            (index, pattern.regex, pattern.regex_reads_newlines) for index, pattern in kept if pattern.regex
        )
        self._by_re2 = tuple((index, pattern.re2) for index, pattern in kept if pattern.re2 is not None)
        self._re2_programs = tuple(program for _, program in self._by_re2)
        self._by_automaton = tuple(index for index, pattern in kept if pattern.engine == "automaton")

    def __bool__(self) -> bool:
        return bool(self.patterns)

    def ignored(self, path: str) -> bool:
        """Whether ``path`` (or a directory above it) matches one of the lines still kept."""
        path = path.lstrip("/")
        newline = "\n" in path
        for index, regex, reads_newlines in self._by_regex:
            if newline and not reads_newlines:
                if self._counted(index, path):
                    return True
            elif regex.fullmatch(path) is not None:
                return True
        if self._by_re2:
            try:
                utf8 = path.encode()
            except UnicodeEncodeError:  # a lone surrogate: the automaton reads the path
                if any(self._counted(index, path) for index, _ in self._by_re2):
                    return True
            else:
                if any_re2_fullmatch(self._re2_programs, utf8):
                    return True
        return any(self._counted(index, path) for index in self._by_automaton)

    def _counted(self, index: int, path: str) -> bool:
        """Whether line ``index``'s copy matches ``path``, its work counted against the pass's budget."""
        if not self._kept[index]:
            return False
        copy = self._copies[index]
        if copy is None:
            copy = self._copies[index] = self._compiled[index].run_copy(self._memory)
        found, work = copy.fullmatch_counted(path)
        if work:
            self._work[index] += work
            self._pass_work += work
            if self._pass_work > IGNORE_PASS_WORK:
                over = [line for line, kept in enumerate(self._kept) if kept and self._work[line] > IGNORE_LINE_WORK]
                for line in over:
                    self._drop(line)  # it answers for this path, the last it reads
                if over:
                    self._sort()
        return found

    def _drop(self, index: int) -> None:
        self._kept[index] = False
        self._copies[index] = None  # what it remembered goes back to the pass's memory
        self.dropped.append(index)
        logger.warning(
            "slop_meter_ignore_line_dropped",
            repo=self.repo or None,
            position=index + 1,
            lines=len(self.patterns),
            work=self._work[index],
        )


def ignored(path: str, patterns: Iterable[str]) -> bool:
    """Whether ``path`` (or a directory above it) matches one of ``patterns``."""
    path = path.lstrip("/")
    return any(_compiled(pattern).fullmatch(path) for pattern in patterns)


# ---- outlier files ----


def outlier_files(sizes: Mapping[str, int]) -> set[str]:
    """The files the IQR rule excludes: above ``Q3 + 1.5·IQR`` of the sizes, and no larger than the fair share.

    ``sizes`` are current line counts of the files that weigh anything. A
    statistically large file larger than ``total / files`` holds the project's
    logic and is kept.
    """
    values = [v for v in sizes.values() if v > 0]
    if len(values) < 4:
        return set()
    q1, _q2, q3 = quantiles(values, n=4)
    threshold = q3 + 1.5 * (q3 - q1)
    fair_share = sum(values) / len(values)
    return {path for path, size in sizes.items() if size > threshold and size <= fair_share}


class Weigher:
    """A repository's line weights: by ``rules`` (its ``slop_meter`` section), ignore lists, and outlier files."""

    def __init__(
        self,
        patterns: Iterable[str] | IgnoreLines = (),
        excluded: Iterable[str] = (),
        rules: FileRules = DEFAULT_RULES,
    ) -> None:
        # One pass's ignore lines, shared by the weighers of that pass so a line dropped in one stays dropped.
        self._ignore = patterns if isinstance(patterns, IgnoreLines) else IgnoreLines(patterns)
        self._excluded = frozenset(excluded)
        self._rules = rules
        self._cache: dict[str, float] = {}

    @property
    def rules(self) -> FileRules:
        return self._rules

    def __call__(self, path: str) -> float:
        cached = self._cache.get(path)
        if cached is None:
            if path in self._excluded or (self._ignore and self._ignore.ignored(path)):
                cached = 0.0
            else:
                cached = self._rules.weight(path)
            self._cache[path] = cached
        return cached
