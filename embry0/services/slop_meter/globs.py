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
from typing import Final

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
