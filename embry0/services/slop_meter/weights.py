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

import json
import re
import tomllib
from collections.abc import Iterable, Mapping
from functools import lru_cache
from statistics import quantiles
from typing import Any

from embry0.services.slop_meter.globs import GlobIndex, marked
from embry0.settings.sections.slop_meter import SLOP_METER, SlopMeterSettings

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
    """Every pattern the repository's linters and formatters ignore, merged; a missing or malformed file adds none."""
    patterns: list[str] = []
    for name in IGNORE_FILES:
        if files.get(name):
            patterns += _ignore_lines(str(files[name]))
    if files.get(".gitattributes"):
        patterns += _attributes(str(files[".gitattributes"]))
    if files.get("biome.json"):
        try:
            biome = json.loads(str(files["biome.json"]))
        except ValueError:
            biome = {}
        section = biome.get("files") if isinstance(biome, dict) else None
        if isinstance(section, dict):
            patterns += _strings(section.get("ignore"))
    for name, path in (("pyproject.toml", ("tool", "ruff")), ("ruff.toml", ()), (".ruff.toml", ())):
        if not files.get(name):
            continue
        try:
            data: Any = tomllib.loads(str(files[name]))
        except (tomllib.TOMLDecodeError, ValueError):
            continue
        for key in path:
            data = data.get(key) if isinstance(data, dict) else None
        if isinstance(data, dict):
            patterns += _strings(data.get("exclude")) + _strings(data.get("extend-exclude"))
    return patterns


@lru_cache(maxsize=4096)
def _compiled(pattern: str) -> re.Pattern[str]:
    """A gitignore-style pattern as a regex over file paths.

    Anchored at the root when it holds a slash (other than a trailing one),
    else matched at any depth; ``*`` and ``?`` stay within a directory, ``**``
    crosses them; a match covers everything below it, and a trailing slash
    matches directories only.
    """
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


def ignored(path: str, patterns: Iterable[str]) -> bool:
    """Whether ``path`` (or a directory above it) matches one of ``patterns``."""
    path = path.lstrip("/")
    return any(_compiled(pattern).match(path) for pattern in patterns)


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
        self, patterns: Iterable[str] = (), excluded: Iterable[str] = (), rules: FileRules = DEFAULT_RULES
    ) -> None:
        self._patterns = tuple(patterns)
        self._excluded = frozenset(excluded)
        self._rules = rules
        self._cache: dict[str, float] = {}

    @property
    def rules(self) -> FileRules:
        return self._rules

    def __call__(self, path: str) -> float:
        cached = self._cache.get(path)
        if cached is None:
            if path in self._excluded or (self._patterns and ignored(path, self._patterns)):
                cached = 0.0
            else:
                cached = self._rules.weight(path)
            self._cache[path] = cached
        return cached
