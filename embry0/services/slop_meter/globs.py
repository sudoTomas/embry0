"""How the Slop-O-Meter's settings name files: globs, directory names and test marks (#169).

A glob is matched against a changed file's path, without regard to case.
One with no ``/`` is matched against the file's name, in any directory
(``*.py``, ``test_?*.py``); one with a ``/`` against its whole path from the
repository's root (``db/migrations/*.sql``, ``generated/**``). ``*`` matches
any characters within one name, ``?`` one character, and ``**`` stands
alone between slashes for any number of directories: at the start or in the
middle none included, at the end every file below. A glob holds lowercase
letters, digits, ``.``, ``_``, ``-``, ``+``, ``@``, ``~``, ``*``, ``?`` and
``/``: matching ignores case, so a capital would only be a second spelling
of the same glob.

A directory name is one name matched at any depth, and a test mark one word
of a file's name (:func:`marked`). Every check raises :class:`ValueError`
with a message that never repeats the text it checked.

This module imports nothing of embry0, so a settings section may import it.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable
from typing import Any, Final

MAX_GLOB_LENGTH: Final = 200
MAX_DIR_LENGTH: Final = 100
MAX_MARK_LENGTH: Final = 50

_GLOB_CHARACTERS: Final = re.compile(r"[a-z0-9._+@~*?/-]+\Z")
_DIR_CHARACTERS: Final = re.compile(r"[a-z0-9._+@~-]+\Z")
_MARK_CHARACTERS: Final = re.compile(r"[a-z0-9_+~-]+\Z")


def check_glob(glob: str) -> str:
    """``glob`` when it is one this module matches as the module docstring says; else :class:`ValueError`."""
    if not glob:
        raise ValueError("write a glob, such as *.py or db/migrations/*.sql")
    if len(glob) > MAX_GLOB_LENGTH:
        raise ValueError(f"a glob is at most {MAX_GLOB_LENGTH} characters")
    if not _GLOB_CHARACTERS.match(glob):
        raise ValueError(
            "a glob holds only lowercase letters, digits, '.', '_', '-', '+', '@', '~', '*', '?' and '/' "
            "(matching ignores case)"
        )
    segments = glob.split("/")
    if not all(segments):
        raise ValueError("a glob with a '/' starts at the repository's root: no leading, trailing or doubled '/'")
    if any(segment in (".", "..") for segment in segments):
        raise ValueError("a glob names no '.' or '..' directory")
    if any("**" in segment and segment != "**" for segment in segments):
        raise ValueError("'**' stands alone between slashes, as in generated/** or src/**/*.pb.go")
    if len(segments) == 1 and segments[0] == "**":
        raise ValueError("'**' alone matches every file: write a directory before it, as in generated/**")
    if any(a == b == "**" for a, b in zip(segments, segments[1:], strict=False)):
        raise ValueError("write '**' once in a row: **/**/ matches what **/ does")
    return glob


def check_dir_name(name: str) -> str:
    """``name`` when it is one directory name; else :class:`ValueError`."""
    if not name:
        raise ValueError("write a directory name, such as vendor")
    if len(name) > MAX_DIR_LENGTH:
        raise ValueError(f"a directory name is at most {MAX_DIR_LENGTH} characters")
    if not _DIR_CHARACTERS.match(name) or name in (".", ".."):
        raise ValueError(
            "a directory is one name of lowercase letters, digits, '.', '_', '-', '+', '@' and '~', such as "
            "node_modules: no '/', no wildcard, not '.' or '..' (matching ignores case)"
        )
    return name


def check_mark(mark: str) -> str:
    """``mark`` when it is one word a test file's name may carry before its extension; else :class:`ValueError`."""
    if not mark:
        raise ValueError("write a word, such as test")
    if len(mark) > MAX_MARK_LENGTH:
        raise ValueError(f"a mark is at most {MAX_MARK_LENGTH} characters")
    if not _MARK_CHARACTERS.match(mark):
        raise ValueError(
            "a mark is one word of lowercase letters, digits, '_', '-', '+' and '~', such as spec: no '.', no "
            "wildcard (matching ignores case)"
        )
    return mark


def _segment(text: str) -> str:
    out = []
    for char in text:
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
    return "".join(out)


@functools.lru_cache(maxsize=4096)
def compile_glob(glob: str) -> re.Pattern[str]:
    """``glob`` as a regular expression over a lowercased name (no ``/`` in it) or path (one)."""
    lowered = glob.lower()
    if "/" not in lowered:
        return re.compile(_segment(lowered) + r"\Z")
    segments = lowered.split("/")
    out = []
    for position, segment in enumerate(segments):
        last = position == len(segments) - 1
        if segment == "**":
            out.append(".+" if last else "(?:[^/]+/)*")
        else:
            out.append(_segment(segment) + ("" if last else "/"))
    return re.compile("".join(out) + r"\Z")


def matches(glob: str, path: str) -> bool:
    """Whether ``glob`` matches ``path``, a file's path from the repository's root, lowercased."""
    target = path if "/" in glob else path.rsplit("/", 1)[-1]
    return compile_glob(glob).fullmatch(target) is not None


def marked(name: str, marks: frozenset[str]) -> bool:
    """Whether a lowercased file ``name`` carries one of ``marks`` just before its extension: ``a.test.ts``,
    ``b.spec.js``. A name with no extension carries none, and nor does ``a.test.helper.ts``: the word must be
    the last part of the name before its extension."""
    stem, dot, extension = name.rpartition(".")
    if not dot or not extension:
        return False
    word = stem.rpartition(".")[2]
    return bool(stem) and "." in stem and word in marks


def specificity(glob: str) -> tuple[int, ...]:
    """How specific ``glob`` is, larger the more specific; where several globs match one file, the most specific
    is the one that counts (:class:`GlobIndex`).

    A glob with a ``/`` is more specific than any glob without one. Between two
    with a ``/``: the one with more segments free of wildcards; then the one
    with fewer ``**`` segments; then the one with fewer other segments holding a
    wildcard; then the one with more segments; then the one with more literal
    characters. So ``src/main.py`` is more specific than ``src/**/main.py``,
    ``src/*/main.py`` than ``src/**/main.py``, and ``src/legacy/**`` than
    ``src/**``. Between two without a ``/``: the one with more literal
    characters, so ``*.min.js`` is more specific than ``*.js``. A literal
    character is any but ``*``, ``?`` and ``/``.
    """
    lowered = glob.lower()
    literal = sum(1 for char in lowered if char not in "*?/")
    if "/" not in lowered:
        return (0, literal, 0, 0, 0, 0)
    segments = lowered.split("/")
    plain = sum(1 for segment in segments if not any(char in segment for char in "*?"))
    deep = sum(1 for segment in segments if segment == "**")
    wild = len(segments) - plain - deep
    return (1, plain, -deep, -wild, len(segments), literal)


#: A glob's place among others: :func:`specificity`, the most specific first, then the glob's text.
_Order = tuple[tuple[int, ...], str]


def _order(glob: str) -> _Order:
    """Sorts the most specific glob first; between two of one specificity, the first in alphabetical order."""
    return tuple(-part for part in specificity(glob)), glob.lower()


_WILD: Final = re.compile(r"[*?]+")
#: The length of the runs of literal characters a glob is filed under when nothing more selective is there.
GRAM: Final = 3
#: The most globs a list may hold that :class:`GlobIndex` can file under nothing: no literal first directory,
#: no literal segment, no extension and no three literal characters in a row (``*/*``, ``*x*``). Each of
#: them is checked against every path.
MAX_BROAD_GLOBS: Final = 16


def _plain(text: str) -> bool:
    return not any(char in text for char in "*?")


def _extension_of(text: str) -> str | None:
    """The extension a name matching ``text`` (a glob of one name) must have, or None when it may have any: the
    part after the last ``.`` of the literal after the glob's last wildcard."""
    tail = _WILD.split(text)[-1]
    return tail.rpartition(".")[2] if "." in tail else None


def _runs(text: str) -> list[str]:
    """The runs of literal characters of a glob, within its segments."""
    return [run for segment in text.split("/") for run in _WILD.split(segment) if len(run) >= GRAM]


def bucket(glob: str) -> tuple[str, str]:
    """Where :class:`GlobIndex` files ``glob``: ``(kind, key)``.

    A name: ``exact``, under itself. A glob of one name with an extension
    (``*.py``, ``test_?*.py``): ``extension``. Another with three literal
    characters in a row (``*fixture*``): ``gram``, under one of its runs. A
    glob with a ``/`` whose first segment is a name: ``head``; else one with a
    segment that is a name (``**/vendored/**``): ``segment``, under its
    longest; else one whose last segment has an extension (``*/*.py``):
    ``path-extension``; else three literal characters: ``path-gram``. Any
    other glob is ``broad``, checked against every path.
    """
    lowered = glob.lower()
    if "/" not in lowered:
        if _plain(lowered):
            return "exact", lowered
        extension = _extension_of(lowered)
        if extension is not None:
            return "extension", extension
        return ("gram", "") if _runs(lowered) else ("broad", "")
    segments = lowered.split("/")
    if _plain(segments[0]):
        return "head", segments[0]
    literal = [segment for segment in segments if _plain(segment)]
    if literal:
        return "segment", max(reversed(literal), key=len)
    extension = _extension_of(segments[-1]) if segments[-1] != "**" else None
    if extension is not None:
        return "path-extension", extension
    return ("path-gram", "") if _runs(lowered) else ("broad", "")


def broad(glob: str) -> bool:
    """Whether :class:`GlobIndex` checks ``glob`` against every path (:func:`bucket`)."""
    return bucket(glob)[0] == "broad"


_Entry = tuple[_Order, re.Pattern[str], Any]


class GlobIndex[V]:
    """Globs, each with a value, indexed so a path is checked against the few that could match it.

    :meth:`best` answers the value of the most specific glob that matches a
    path (:func:`specificity`; a tie goes to the glob first in alphabetical
    order), or None. Each glob is filed once (:func:`bucket`) and checked only
    against the paths it could match: a name against itself; a glob of one name
    with an extension against names with it; a glob with a literal first
    directory against paths under it; one with a literal segment against paths
    holding that segment; one whose last segment has an extension against
    names with it; one with three literal characters in a row against paths
    that hold them. Only a broad glob (:func:`broad`, at most
    :data:`MAX_BROAD_GLOBS` in a list the section takes) is checked against
    every path. ``evaluations`` counts the globs checked.
    """

    def __init__(self, entries: Iterable[tuple[str, V]]) -> None:
        self._exact: dict[str, tuple[_Order, V]] = {}
        self._suffixes: dict[str, list[tuple[_Order, str, V]]] = {}
        self._names: dict[str, dict[str, list[_Entry]]] = {"extension": {}, "gram": {}}
        self._paths: dict[str, dict[str, list[_Entry]]] = {
            "head": {},
            "segment": {},
            "path-extension": {},
            "path-gram": {},
        }
        self._broad_names: list[_Entry] = []
        self._broad_paths: list[_Entry] = []
        self.evaluations = 0
        for glob, value in sorted(entries, key=lambda entry: _order(entry[0])):
            lowered = glob.lower()
            order = _order(lowered)
            kind, key = bucket(lowered)
            if kind == "exact":
                self._exact.setdefault(lowered, (order, value))
            elif kind == "extension" and lowered.startswith("*") and _plain(lowered[1:]):
                self._suffixes.setdefault(key, []).append((order, lowered[1:], value))
            elif kind in ("gram", "path-gram"):
                table = self._names["gram"] if kind == "gram" else self._paths["path-gram"]
                # Filed under the run's gram that holds the fewest globs so far, so similar globs spread out.
                grams = {run[i : i + GRAM] for run in _runs(lowered) for i in range(len(run) - GRAM + 1)}
                gram = min(sorted(grams), key=lambda g: len(table.get(g, ())))
                table.setdefault(gram, []).append((order, compile_glob(lowered), value))
            elif kind == "broad":
                target = self._broad_paths if "/" in lowered else self._broad_names
                target.append((order, compile_glob(lowered), value))
            else:
                table = self._names[kind] if kind == "extension" else self._paths[kind]
                table.setdefault(key, []).append((order, compile_glob(lowered), value))

    def _first(self, candidates: Iterable[_Entry], text: str) -> tuple[_Order, V] | None:
        for order, pattern, value in candidates:
            self.evaluations += 1
            if pattern.fullmatch(text) is not None:
                return order, value
        return None

    def _best_of(self, lists: Iterable[Iterable[_Entry]], text: str) -> tuple[_Order, V] | None:
        found = [hit for hit in (self._first(entries, text) for entries in lists) if hit is not None]
        return min(found, key=lambda item: item[0]) if found else None

    @staticmethod
    def _grams(text: str) -> set[str]:
        return {text[i : i + GRAM] for i in range(len(text) - GRAM + 1)}

    def best(self, path: str) -> V | None:
        """The value of the most specific glob matching ``path``, lowercased and from the repository's root."""
        segments = path.split("/")
        name = segments[-1]
        extension = name.rpartition(".")[2] if "." in name else None
        paths = self._paths
        lists: list[Iterable[_Entry]] = [self._broad_paths]
        if len(segments) > 1:
            lists.append(paths["head"].get(segments[0], ()))
        if paths["segment"]:
            lists.extend(paths["segment"].get(segment, ()) for segment in set(segments))
        if extension is not None:
            lists.append(paths["path-extension"].get(extension, ()))
        if paths["path-gram"]:
            lists.extend(paths["path-gram"].get(gram, ()) for gram in self._grams(path))
        hit = self._best_of(lists, path)
        if hit is not None:
            # A glob with a '/' is more specific than any without one.
            return hit[1]
        found: list[tuple[_Order, V]] = []
        exact = self._exact.get(name)
        if exact is not None:
            self.evaluations += 1
            found.append(exact)
        if extension is not None:
            for order, suffix, value in self._suffixes.get(extension, ()):
                self.evaluations += 1
                if name.endswith(suffix):
                    found.append((order, value))
                    break
        names: list[Iterable[_Entry]] = [self._broad_names]
        if extension is not None:
            names.append(self._names["extension"].get(extension, ()))
        if self._names["gram"]:
            names.extend(self._names["gram"].get(gram, ()) for gram in self._grams(name))
        hit = self._best_of(names, name)
        if hit is not None:
            found.append(hit)
        return min(found, key=lambda item: item[0])[1] if found else None
