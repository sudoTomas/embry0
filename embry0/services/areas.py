"""Areas of a monorepo (spec 2026-10-25): which part of a repository a piece of work is about.

embry0 keeps each repository's areas as a document of its own (spec
2026-11-10, :mod:`embry0.services.areas_store`); the repository carries no
file for them::

    areas:
      - name: Billing
        paths: ["services/billing/**", "web/apps/billing/**"]
        logs: ["billing-api"]
        owners: ["octo"]

``paths`` are globs over the repository's files, in qa.yaml's grammar
(:func:`embry0.workflows.qa._glob_match.compile_glob`: ``**`` any depth as
a whole segment, ``*`` and ``?`` within one name); ``logs`` are strings
that, found in a watcher case's log signature, put the case in the area;
``owners`` are logins asked first about the area's work, before the
repository's own. Everything embry0 says about the repository then carries
the area: a proposal, the pull request it opens, the job. This module is
the matching, pure: what an area is and which one a file, a change, a case
or a label is in.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from embry0.workflows.qa._glob_match import Glob, GlobPatternError, compile_glob


def _folded(text: str) -> str:
    """``text`` NFC-normalised, case-folded and normalised again: how names are compared."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def slug_of(name: str) -> str:
    """The slug an area's name labels work with (``area:<slug>``): its letter and digit runs, in any script,
    case-folded, joined by ``-`` (``Платежи`` is ``платежи``).

    The name is NFC-normalised before and after folding, so a precomposed
    ``é`` and an ``e`` with a combining accent are one letter, and a mark
    stays with the letter it follows (``İstanbul`` folds to an ``i`` with a
    combining dot, one word).
    """
    folded = _folded(name)
    words: list[str] = []
    word = ""
    for ch in folded:
        if ch.isalnum() or (word and unicodedata.category(ch).startswith("M")):
            word += ch
        elif word:
            words.append(word)
            word = ""
    if word:
        words.append(word)
    return "-".join(words)


@dataclass(frozen=True)
class Area:
    name: str
    paths: tuple[str, ...] = ()
    logs: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()

    @property
    def slug(self) -> str:
        return slug_of(self.name)


def digest(areas: Iterable[Area]) -> str:
    """A short digest of ``areas`` as matching reads them: the same for the same areas in the same order."""
    text = json.dumps([[a.name, a.paths, a.logs, a.owners] for a in areas], separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


@functools.lru_cache(maxsize=4096)
def _compiled(pattern: str) -> Glob | None:
    """``pattern`` as qa.yaml's grammar compiles it, a trailing ``/`` read as everything below; None for a glob
    this release does not read (a newer one wrote it), which then matches nothing."""
    pattern = pattern.lstrip("/")
    if pattern.endswith("/"):
        pattern += "**"
    try:
        return compile_glob(pattern)
    except GlobPatternError:
        return None


def area_of_path(areas: Iterable[Area], path: str) -> Area | None:
    """The first area whose patterns match ``path``."""
    path = path.lstrip("/")
    return next(
        (a for a in areas if any((glob := _compiled(p)) is not None and glob.matches(path) for p in a.paths)), None
    )


def area_of_paths(areas: Iterable[Area], paths: Iterable[str]) -> Area | None:
    """The area most of ``paths`` fall in; None when none falls in any."""
    areas = list(areas)
    counted = Counter(a.name for p in paths if (a := area_of_path(areas, p)) is not None)
    if not counted:
        return None
    name = counted.most_common(1)[0][0]
    return next(a for a in areas if a.name == name)


def area_of_logs(areas: Iterable[Area], *texts: str | None) -> Area | None:
    """The first area one of whose log strings is found in ``texts`` (a case's signature, its title)."""
    haystack = "\n".join(t for t in texts if t).lower()
    if not haystack:
        return None
    return next((a for a in areas if any(s.lower() in haystack for s in a.logs)), None)


def area_of_labels(areas: Iterable[Area], labels: Iterable[str]) -> Area | None:
    """The first area a label names, by its name or its slug (``Billing``, ``billing``, ``area:billing``)."""
    wanted = {_folded(label).removeprefix("area:").strip() for label in labels}
    return next((a for a in areas if _folded(a.name) in wanted or a.slug in wanted), None)


def titled(title: str, area: Area | None) -> str:
    """``title`` naming its area: ``[Billing] …``, unless a conventional scope already says it (``fix(billing): …``)."""
    if area is None:
        return title
    scope = re.match(r"^\w+\(([^)]*)\)!?:", title)
    if scope and area.slug in slug_of(scope.group(1)):
        return title
    return f"[{area.name}] {title}"


async def area_of_change(
    get: Callable[[str], Awaitable[Any]], repo: str, base: str, head: str, areas: Iterable[Area]
) -> Area | None:
    """The area most of the files ``head`` changes from ``base`` fall in, through a forge client's GET (GitHub's
    compare API).

    None when ``areas`` is empty (the compare is not asked for), the compare
    cannot be read, or none of the files is in an area. GitHub lists at most
    300 files of a compare, plenty to say where most of a change is.
    """
    areas = list(areas)
    if not areas:
        return None
    resp = await get(f"/repos/{repo}/compare/{quote(base, safe='')}...{quote(head, safe='')}")
    if resp.status_code != 200:
        return None
    body = resp.json()
    files = body.get("files") if isinstance(body, dict) else None
    return area_of_paths(
        areas,
        [f["filename"] for f in files or [] if isinstance(f, dict) and isinstance(f.get("filename"), str)],
    )
