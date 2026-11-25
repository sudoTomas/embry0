"""Repositories whose old areas file was never imported (#175).

From commit 45d07a6f (#458, 2026-11-12) until pull request #706 removed it,
every start of embry0 read each registered repository's old areas file from
its default branch, once, and stored it as the document embry0 keeps
(migration 145, ADR 0030). That import is gone: no code reads embry0
configuration from a target repository any more. Migration 145 listed every
repository registered when it ran as ``pending``; a read that failed stayed
``failed`` and was tried again at a later start, up to
:data:`MAX_ATTEMPTS` reads, after which the import gave up on it.

At startup :func:`unread` lists the repositories whose file was never read,
as long as each is still registered and has no areas document yet (matched
as the areas store matches a repository: whatever the case, and under an old
or a new name, ``REPO_RENAMES``). They fall in two groups, with different
fixes: ``due`` (``pending``, or ``failed`` fewer than :data:`MAX_ATTEMPTS`
times), which one start of a build carrying the import reads, and
``exhausted`` (``failed`` :data:`MAX_ATTEMPTS` times or more), which no build
reads again. :func:`warn_if_areas_never_imported` logs ``areas_import_skipped``
at error level with both counts and the fix for each, and writes one audit
row. Writing or mapping a repository's areas, or removing it from the
registry, takes it off the list.

The lifespan runs the check in the background, and its queries stop after
:data:`STATEMENT_TIMEOUT` seconds, so a start never waits on it: a lock on
the ledger or a slow plan delays the warning, not the start. A check that
times out logs ``areas_import_check_timed_out`` at error level, with what to
look at, so a deployment whose ledger is locked at every start still hears of
it; one that fails otherwise is logged, and nothing else happens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import asyncpg
import structlog

from embry0.storage.repo_renames import repo_slug_variants

if TYPE_CHECKING:
    from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

#: The log event. Stable: alerts and log queries key on it.
EVENT: Final = "areas_import_skipped"
#: The log event of a check whose queries ran past :data:`STATEMENT_TIMEOUT`. Stable, as :data:`EVENT`.
TIMED_OUT: Final = "areas_import_check_timed_out"
#: How many repositories of each group the log line names; the counts are always whole.
NAMED: Final = 20
#: How many failed reads the import made of a repository before it stopped trying (the ledger's ``attempts``).
MAX_ATTEMPTS: Final = 24
#: How long, in seconds, each of the check's queries may run, a wait for a lock included.
STATEMENT_TIMEOUT: Final = 30.0
#: The builds that carry the import. There are no release tags: these are commits of main.
IMPORTING_BUILDS: Final = (
    "a build of main from commit 45d07a6f (#458, 2026-11-12) to the commit before pull request #706"
)
#: What an operator does about each group.
REMEDY_DUE: Final = (
    f"start {IMPORTING_BUILDS} once, which reads their old areas files, then this one again; "
    "or write or map their areas (docs/areas.md)"
)
REMEDY_TIMED_OUT: Final = (
    "the check of the repositories whose old areas file was never read gave up at its timeout: see what holds a "
    "lock on repo_areas_imports (pg_locks), or ANALYZE it; it runs again at the next start (docs/areas.md, the old "
    "file)"
)
REMEDY_EXHAUSTED: Final = (
    f"write or map their areas (docs/areas.md): their reads failed {MAX_ATTEMPTS} times, and no build reads them again"
)

_LEDGER_SQL: Final = (
    "SELECT repo, outcome, attempts FROM repo_areas_imports "
    "WHERE outcome = 'pending' OR outcome = 'failed' ORDER BY repo"
)
#: The names, lower-cased, among ``$1`` that a table holds a row of.
_NAMES_SQL: Final = "SELECT DISTINCT lower(repo) AS name FROM {table} WHERE lower(repo) = ANY($1::text[])"


@dataclass(frozen=True)
class Unread:
    """The registered repositories, with no areas document, whose old areas file was never read."""

    due: list[str] = field(default_factory=list)
    exhausted: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.due) + len(self.exhausted)

    def remedy(self) -> str:
        said = []
        if self.due:
            said.append(f"for the {len(self.due)} never read or still due a read: {REMEDY_DUE}")
        if self.exhausted:
            said.append(f"for the {len(self.exhausted)} whose reads were given up on: {REMEDY_EXHAUSTED}")
        return "; ".join(said)


def _spellings(repo: str) -> set[str]:
    """Every name, lower-cased, a row of ``repo`` may carry: as the areas store reads it."""
    return {name.lower() for name in repo_slug_variants(repo)} | {repo.lower()}


async def _fetch(conn: Any, timeout: float) -> tuple[list[Any], set[str], set[str]]:
    await conn.execute(f"SET LOCAL statement_timeout = '{max(1, int(timeout * 1000))}ms'")
    ledger = list(await conn.fetch(_LEDGER_SQL))
    if not ledger:
        return [], set(), set()
    names = sorted({name for row in ledger for name in _spellings(str(row["repo"]))})
    registered = {str(r["name"]) for r in await conn.fetch(_NAMES_SQL.format(table="repos"), names)}
    with_areas = {str(r["name"]) for r in await conn.fetch(_NAMES_SQL.format(table="repo_areas"), names)}
    return ledger, registered, with_areas


async def unread(db: DatabasePool, *, timeout: float = STATEMENT_TIMEOUT) -> Unread:
    """The registered repositories, with no areas document, whose old areas file was never read, by group.
    Each query stops after ``timeout`` seconds (``asyncpg.QueryCanceledError``)."""
    async with db.transaction() as conn:
        ledger, registered, with_areas = await _fetch(conn, timeout)
    due: list[str] = []
    exhausted: list[str] = []
    for row in ledger:
        repo = str(row["repo"])
        names = _spellings(repo)
        if not names & registered or names & with_areas:
            continue
        if row["outcome"] == "failed" and int(row["attempts"] or 0) >= MAX_ATTEMPTS:
            exhausted.append(repo)
        else:
            due.append(repo)
    return Unread(due=due, exhausted=exhausted)


async def warn_if_areas_never_imported(
    db: DatabasePool | None, *, audit_log_path: Path | str | None = None, timeout: float = STATEMENT_TIMEOUT
) -> Unread:
    """Log, at error level, the repositories whose old areas file was never imported, and audit it once; them.
    Never raises: a check that fails is logged, and the start goes on."""
    if db is None:
        return Unread()
    try:
        found = await unread(db, timeout=timeout)
    except asyncpg.QueryCanceledError:
        # A lock held on the ledger, or a plan slower than the timeout: said at error level, as the warning it
        # stands in for is, so a deployment whose check times out at every start still hears of it.
        logger.error(TIMED_OUT, timeout_seconds=timeout, remedy=REMEDY_TIMED_OUT)
        return Unread()
    except Exception:
        logger.warning("areas_import_check_failed", exc_info=True)
        return Unread()
    if not found.count:
        return found
    remedy = found.remedy()
    logger.error(
        EVENT,
        count=found.count,
        due=len(found.due),
        exhausted=len(found.exhausted),
        due_repos=found.due[:NAMED],
        exhausted_repos=found.exhausted[:NAMED],
        remedy=remedy,
    )
    from embry0.audit.helpers import emit_audit

    count, due, exhausted = found.count, len(found.due), len(found.exhausted)
    try:
        await emit_audit(
            db,
            "repo.areas_import_skipped",
            actor="embry0",
            # The counts alone: the row is the deployment's, which a key with an allowlist does not read, and the
            # repositories are in the log line.
            details={"count": count, "due": due, "exhausted": exhausted, "remedy": remedy},
            audit_log_path=audit_log_path,
        )
    except Exception:
        logger.warning("areas_import_check_audit_failed", exc_info=True)
    return found
