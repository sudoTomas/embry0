"""Areas of a monorepo (spec 2026-10-25): which part of a repository a piece of work is about.

A repository names its areas in ``.embry0/areas.yml`` on its default branch,
next to the code they describe::

    areas:
      - name: Billing
        paths: ["services/billing/**", "web/apps/billing/**"]
        logs: ["billing-api"]
        owners: ["octo"]

``paths`` are globs over the repository's files (``**`` any depth, ``*``
within one directory, a trailing ``/`` the whole directory); ``logs`` are
strings that, found in a watcher case's log signature, put the case in the
area; ``owners`` are GitHub logins asked first about the area's work, before
the repository's own. Everything embry0 says about the repository then
carries the area: a proposal, the pull request it opens, the job.
"""

from __future__ import annotations

import base64
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import structlog
import yaml

logger = structlog.get_logger(__name__)

AREAS_PATH = ".embry0/areas.yml"
#: How long a repository's areas are kept before its file is read again.
CACHE_SECONDS = 3600.0
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


@dataclass(frozen=True)
class Area:
    name: str
    paths: tuple[str, ...] = ()
    logs: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return ()
    return tuple(s.strip() for s in value if isinstance(s, str) and s.strip())


def parse_areas(text: str | None) -> list[Area]:
    """The areas a file names; an entry without a name, or a file that is not YAML, names none."""
    if not text:
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    entries = data.get("areas") if isinstance(data, dict) else None
    areas: list[Area] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
            continue
        owners = tuple(o.removeprefix("@") for o in _strings(entry.get("owners")) if _LOGIN.match(o.removeprefix("@")))
        areas.append(Area(entry["name"].strip(), _strings(entry.get("paths")), _strings(entry.get("logs")), owners))
    return areas


def _glob(pattern: str) -> re.Pattern[str]:
    """A path glob as a regex: ``**`` any depth, ``*`` and ``?`` within one directory, a trailing ``/`` all below."""
    pattern = pattern.lstrip("/")
    if pattern.endswith("/"):
        pattern += "**"
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def area_of_path(areas: Iterable[Area], path: str) -> Area | None:
    """The first area whose patterns match ``path``."""
    path = path.lstrip("/")
    return next((a for a in areas if any(_glob(p).match(path) for p in a.paths)), None)


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
    wanted = {label.lower().removeprefix("area:").strip() for label in labels}
    return next((a for a in areas if a.name.lower() in wanted or a.slug in wanted), None)


def titled(title: str, area: Area | None) -> str:
    """``title`` naming its area: ``[Billing] …``, unless a conventional scope already says it (``fix(billing): …``)."""
    if area is None:
        return title
    scope = re.match(r"^\w+\(([^)]*)\)!?:", title)
    if scope and area.slug in re.sub(r"[^a-z0-9]+", "-", scope.group(1).lower()):
        return title
    return f"[{area.name}] {title}"


#: Reads one file of a repository's default branch: (repo, path) to its text, or None.
ReadFile = Callable[[str, str], Awaitable[str | None]]


async def read_contents(get: Callable[[str], Awaitable[Any]], repo: str, path: str) -> str | None:
    """A file of the default branch through a forge client's GET (GitHub's contents API); None when it is not there."""
    resp = await get(f"/repos/{repo}/contents/{path}")
    if resp.status_code != 200:
        return None
    body = resp.json()
    if not isinstance(body, dict) or not isinstance(body.get("content"), str):
        return None
    return base64.b64decode(body["content"]).decode("utf-8", "replace")


async def area_of_change(
    get: Callable[[str], Awaitable[Any]], repo: str, base: str, head: str, reader: AreaReader
) -> Area | None:
    """The area most of the files ``head`` changes from ``base`` fall in, through a forge client's GET (GitHub's compare API).

    None when the repository names no areas, the compare cannot be read, or
    none of the files is in an area. GitHub lists at most 300 files of a
    compare, plenty to say where most of a change is.
    """
    areas = await reader.areas(repo, lambda name, path: read_contents(get, name, path))
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


class AreaReader:
    """Each repository's areas, read from its file and kept ``CACHE_SECONDS``; a file that cannot be read names none."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cache: dict[str, tuple[float, list[Area]]] = {}

    async def areas(self, repo: str, read: ReadFile, *, fresh: bool = False) -> list[Area]:
        """``repo``'s areas, read again when ``fresh`` (a person looking at them now)."""
        key = repo.lower()
        now = self._clock()
        cached = self._cache.get(key)
        if cached and not fresh and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            areas = parse_areas(await read(repo, AREAS_PATH))
        except Exception:  # noqa: BLE001 - a repository we cannot read has no areas
            logger.info("areas_unread", repo=repo, exc_info=True)
            areas = []
        self._cache[key] = (now, areas)
        return areas


#: The process's reader, shared by the proposals feed and the pull requests embry0 opens.
AREAS = AreaReader()
