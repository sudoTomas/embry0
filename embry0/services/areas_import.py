"""The one-time import of each repository's old ``.embry0/areas.yml`` into embry0 (spec 2026-11-10).

A repository's areas used to be read from that file on its default branch.
They are a document embry0 keeps now, and the file is never read again but
here, once per repository: migration 145 listed, as ``pending`` in
``repo_areas_imports``, every repository registered when it ran, and this
reads each of those once, after the boot, in the background. A repository
registered later is never read. The next minor release deletes this module,
and with it the last read of embry0 configuration from a target repository.

What is stored is what embry0 already acted on: the file as written when the
document's schema accepts it and reads every glob the way the old reader
did; otherwise what the old reader (:func:`parse_areas`) read from it,
written again under a header that says which entries or values were left
out and why. The old reader matched a ``[`` as itself, where the document's
grammar opens a character class, so a ``[`` is written ``[[]`` (a Next.js
route directory, ``app/[id]/``, keeps meaning that directory). A stored
document is never overwritten: one written by a person first wins. The
document and the ledger's ``imported`` commit together, so when two
orchestrators read a repository at once the one that stored it is the one
the ledger names. A read that fails, or a repository the forge does not
show embry0's credential, is read again at a later pass, up to
:data:`~embry0.storage.repositories.repo_areas_imports.MAX_ATTEMPTS` reads.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import structlog
import yaml

from embry0.forge_logins import GITHUB_LOGIN_RE
from embry0.services.areas import Area
from embry0.services.areas_document import (
    MAX_AREAS,
    MAX_LOG,
    MAX_LOGS,
    MAX_NAME,
    MAX_OWNERS,
    MAX_PATHS,
    DocumentInvalid,
    DocumentTooLarge,
    check,
    check_area,
    dump,
    expand,
    glob_problem,
    load_yaml,
)
from embry0.storage.repositories.repo_areas import AreasChanged

logger = structlog.get_logger(__name__)

#: Where a repository named its areas before embry0 kept them.
AREAS_PATH: Final = ".embry0/areas.yml"
#: Who an imported document is recorded as written by.
ACTOR: Final = "embry0:areas-import"
#: Repositories read at once.
READS_AT_ONCE: Final = 4
#: Seconds between two passes while a repository is still to be read.
PASS_EVERY_SECONDS: Final = 3600.0


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return ()
    return tuple(s.strip() for s in value if isinstance(s, str) and s.strip())


def parse_areas(text: str | None) -> list[Area]:
    """The areas the old file names, as the reader before spec 2026-11-10 read them: an entry without a name,
    or a file that is not YAML, names none; an owner that is not a login is dropped."""
    if not text:
        return []
    try:
        data = yaml.safe_load(text)
    except (yaml.YAMLError, ValueError, RecursionError):  # an int past the digit limit, a bad date, deep nesting
        return []
    entries = data.get("areas") if isinstance(data, dict) else None
    areas: list[Area] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
            continue
        owners = tuple(
            o.removeprefix("@") for o in _strings(entry.get("owners")) if GITHUB_LOGIN_RE.fullmatch(o.removeprefix("@"))
        )
        areas.append(Area(entry["name"].strip(), _strings(entry.get("paths")), _strings(entry.get("logs")), owners))
    return areas


@dataclass(frozen=True)
class Imported:
    """What an old file is stored as: the text, and what of the old reader's areas was left out (empty when the
    file is stored as written)."""

    text: str
    left_out: list[str] = field(default_factory=list)


def _path(glob: str) -> str:
    """A glob of the old reader in the document's form: no leading ``/`` (the old reader dropped it), and a ``[``
    written ``[[]``, the class that names it, as the old reader matched it as itself."""
    return glob.lstrip("/").replace("[", "[[]")


def _brackets(paths: Iterable[str]) -> bool:
    return any("[" in p for p in paths)


def _salvaged(areas: list[Area]) -> tuple[dict[str, Any], list[str]]:
    """The old reader's ``areas`` as the document's values, and a line for each entry or value left out."""
    entries: list[dict[str, Any]] = []
    left_out: list[str] = []
    slugs: set[str] = set()
    for area in areas:
        if len(entries) >= MAX_AREAS:
            left_out.append(f"{area.name}: past the {MAX_AREAS} areas a document holds")
            continue
        if len(area.name) > MAX_NAME or "\n" in area.name or not area.slug:
            left_out.append(f"an area whose name is not one line of 1 to {MAX_NAME} characters with a letter or digit")
            continue
        if area.slug in slugs:
            left_out.append(f"{area.name}: a second area labelled area:{area.slug}")
            continue
        paths: list[str] = []
        for glob in area.paths:
            form = expand(_path(glob))
            if "\n" in form or "\r" in form or glob_problem(form) is not None:
                left_out.append(f"{area.name}: the path {glob!r}, which is not a glob embry0 reads")
            elif form not in paths:
                paths.append(form)
        if len(paths) > MAX_PATHS:
            left_out.append(f"{area.name}: the paths past the first {MAX_PATHS}")
            paths = paths[:MAX_PATHS]
        logs = [s for s in area.logs if len(s) <= MAX_LOG and "\n" not in s and "\r" not in s]
        if len(logs) != len(area.logs):
            left_out.append(f"{area.name}: log strings longer than {MAX_LOG} characters or of several lines")
        if len(logs) > MAX_LOGS:
            left_out.append(f"{area.name}: the log strings past the first {MAX_LOGS}")
            logs = logs[:MAX_LOGS]
        owners = list(dict.fromkeys(area.owners))
        if len(owners) > MAX_OWNERS:
            left_out.append(f"{area.name}: the owners past the first {MAX_OWNERS}")
            owners = owners[:MAX_OWNERS]
        entry, problems = check_area({"name": area.name, "paths": paths, "logs": logs, "owners": owners}, ("areas",))
        if entry is None:
            left_out.append(f"{area.name}: {problems[0].msg if problems else 'not an area embry0 reads'}")
            continue
        slugs.add(area.slug)
        entries.append(entry)
    return {"areas": entries}, left_out


def import_text(text: str, *, blob_sha: str | None, today: str) -> Imported | None:
    """What to store for the old file ``text``, or None when it names no area embry0 acted on."""
    try:
        data = check(load_yaml(text))
    except (DocumentInvalid, DocumentTooLarge):
        data = None
    if data is not None and not _brackets(p for area in data["areas"] for p in area["paths"]):
        return Imported(text) if data["areas"] else None
    areas = parse_areas(text)
    if not areas:
        return None
    values, left_out = _salvaged(areas)
    if not values["areas"]:
        return None
    header = [
        f"Imported by embry0 on {today} from this repository's {AREAS_PATH}"
        + (f" (blob {blob_sha})." if blob_sha else "."),
        "embry0 keeps these areas now and no longer reads that file. The file's comments were not kept.",
    ]
    if _brackets(p for area in areas for p in area.paths):
        header.append("A '[' in a path is written [[], which names the character itself, as the file meant it.")
    if left_out:
        header.append("Left out, as embry0 could not read them:")
        header += [f"- {line}" for line in left_out]
    return Imported(dump(check(values), header="\n".join(header)), left_out)


#: Reads one file of a repository's default branch: (repo) to its text and blob sha, None when it is not there.
ReadFile = Callable[[str], Awaitable[tuple[str, str | None] | None]]


async def import_once(
    *,
    ledger: Any,
    store: Any,
    repos: Any,
    read: ReadFile,
    record: Callable[[str, dict[str, Any]], Awaitable[None]],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, str]:
    """Read every repository the ledger still lists once and store what its old file named; each one's outcome.

    Never raises for one repository: a failure is that repository's
    ``failed``, tried again at the next start.
    """
    due = await ledger.due()
    gate = asyncio.Semaphore(READS_AT_ONCE)
    outcomes: dict[str, str] = {}

    async def one(repo: str) -> None:
        async with gate:
            try:
                outcome, detail, sha = await _import_one(
                    repo, ledger=ledger, store=store, repos=repos, read=read, record=record, clock=clock
                )
            except Exception as exc:  # noqa: BLE001 - one repository's failure never stops the rest
                outcome, detail, sha = "failed", f"{type(exc).__name__}: {str(exc)[:300]}", None
                logger.warning("areas_import_failed", repo=repo, error=detail)
            if outcome != "imported":  # an import is recorded with the document it stored
                await ledger.record(repo, outcome, detail=detail, blob_sha=sha)
            outcomes[repo] = outcome

    await asyncio.gather(*(one(r) for r in due))
    if due:
        logger.info("areas_import_done", read=len(due), outcomes=sorted(set(outcomes.values())))
    return outcomes


async def import_passes(
    *,
    ledger: Any,
    every: float = PASS_EVERY_SECONDS,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    **run: Any,
) -> None:
    """:func:`import_once`, then again every ``every`` seconds while the ledger lists a repository still to read:
    a read that failed is retried at a later pass until it is read or has failed
    :data:`~embry0.storage.repositories.repo_areas_imports.MAX_ATTEMPTS` times."""
    while True:
        await import_once(ledger=ledger, **run)
        if not await ledger.due():
            return
        await sleep(every)


async def _import_one(
    repo: str,
    *,
    ledger: Any,
    store: Any,
    repos: Any,
    read: ReadFile,
    record: Callable[[str, dict[str, Any]], Awaitable[None]],
    clock: Callable[[], datetime],
) -> tuple[str, str, str | None]:
    registered = await repos.get(repo)
    if registered is None:
        return "none", "the repository is no longer registered", None
    if (await store.get(repo)).stored:
        return "none", "embry0 already keeps areas for it", None
    if str(registered.get("forge") or "github") != "github":
        return "none", "only a GitHub repository's areas file was ever read", None
    found = await read(repo)
    if found is None:
        return "none", "no areas file", None
    text, sha = found
    imported = import_text(text, blob_sha=sha, today=clock().date().isoformat())
    if imported is None:
        return "none", "the file names no area embry0 could read", sha
    reason = f"imported from {AREAS_PATH}" + (f" (blob {sha})" if sha else "")
    detail = "; ".join(imported.left_out)[:2000]

    async def ledger_says_imported(conn: Any) -> None:
        await ledger.record(repo, "imported", detail=detail, blob_sha=sha, conn=conn)

    try:
        saved = await store.save(
            repo,
            imported.text,
            load_yaml(imported.text),
            expected={0},
            actor=ACTOR,
            source="import",
            reason=reason,
            also=ledger_says_imported,
        )
    except AreasChanged:
        # Someone stored areas first: a person, or another orchestrator's import, whose ledger row already says
        # so, and then recording this changes nothing.
        return "none", "a person wrote areas first", sha
    await record(repo, {"repo": repo, "revision_id": saved.revision_id, "left_out": len(imported.left_out)})
    logger.info("areas_imported", repo=repo, left_out=len(imported.left_out))
    return "imported", detail, sha
