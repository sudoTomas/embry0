"""How much attention a changed line demands, by its file (spec 2026-10-26, slop-o-meter).

After Slop-O-Meter's published slop score algorithm
(https://github.com/slop-o-meter/slop-o-meter, docs/slop-score-algorithm.md):
a line weighs by its file's type, half for tests and examples, nothing for
non-code, vendored, generated or linter-ignored files, and nothing for an
outlier file smaller than its fair share.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable, Mapping
from functools import lru_cache
from statistics import quantiles
from typing import Any

#: A test or example file's weight, as a share of its type's.
NON_PRODUCTION_WEIGHT = 0.5

_CORE = (
    "py", "pyi", "ts", "mts", "cts", "js", "mjs", "cjs", "go", "rs", "java", "kt", "kts", "scala", "c", "h",
    "cc", "cpp", "cxx", "hpp", "hh", "cs", "rb", "php", "swift", "m", "mm", "ex", "exs", "erl", "clj", "cljs",
    "hs", "ml", "fs", "lua", "pl", "pm", "r", "jl", "dart", "zig", "nim", "groovy", "sh", "bash", "zsh",
    "ps1", "tf", "sol", "proto", "graphql", "gql",
)  # fmt: skip
_UI = ("tsx", "jsx", "vue", "svelte", "astro", "html", "htm", "erb", "hbs", "ejs", "jinja", "j2", "twig", "liquid")
_STYLE = ("css", "scss", "sass", "less", "styl", "pcss")
TYPE_WEIGHTS: dict[str, float] = {
    **dict.fromkeys(_CORE, 1.0),
    "sql": 0.8,
    **dict.fromkeys(_UI, 0.5),
    **dict.fromkeys(_STYLE, 0.3),
}

_VENDORED_DIRS = ("vendor", "node_modules", "third_party", "third-party", "external", "deps", "bower_components")
_GENERATED = re.compile(r"(\.min\.(js|css)|\.bundle\.js|\.map|\.d\.ts)\Z")
_NON_PRODUCTION_DIRS = (
    "__tests__",
    "tests",
    "test",
    "spec",
    "specs",
    "examples",
    "example",
    "demos",
    "demo",
    "samples",
    "sample",
    "fixtures",
    "testdata",
)
_NON_PRODUCTION_FILE = re.compile(r"(\.(test|spec|stories|story)\.[^.]+|_test\.(go|py|rb|exs?)|_spec\.rb)\Z|(\A|/)test_[^/]+\.py\Z")  # fmt: skip

#: The ignore files a linter or formatter reads (gitignore-style globs), at the repository root.
IGNORE_FILES = (".prettierignore", ".eslintignore", ".stylelintignore")
CONFIG_FILES = (*IGNORE_FILES, ".gitattributes", "biome.json", "pyproject.toml", "ruff.toml", ".ruff.toml")


def _extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def type_weight(path: str) -> float:
    """A path's weight before ignore lists and outliers: its type's, halved for tests and examples, 0 when excluded."""
    lowered = path.lower()
    parts = lowered.split("/")
    if any(p in _VENDORED_DIRS for p in parts[:-1]) or _GENERATED.search(lowered):
        return 0.0
    weight = TYPE_WEIGHTS.get(_extension(lowered), 0.0)
    if weight and (any(p in _NON_PRODUCTION_DIRS for p in parts[:-1]) or _NON_PRODUCTION_FILE.search(lowered)):
        weight *= NON_PRODUCTION_WEIGHT
    return weight


# ---- ignore lists ----


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
    """A repository's line weights: by type, ignore lists, and outlier files."""

    def __init__(self, patterns: Iterable[str] = (), excluded: Iterable[str] = ()) -> None:
        self._patterns = tuple(patterns)
        self._excluded = frozenset(excluded)
        self._cache: dict[str, float] = {}

    def __call__(self, path: str) -> float:
        cached = self._cache.get(path)
        if cached is None:
            if path in self._excluded or (self._patterns and ignored(path, self._patterns)):
                cached = 0.0
            else:
                cached = type_weight(path)
            self._cache[path] = cached
        return cached
