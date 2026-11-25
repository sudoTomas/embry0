"""Repositories whose old areas file was never imported (#175).

Until this release, the first start of the release that moved a
repository's areas into embry0 (migration 145, ADR 0030) read each
registered repository's old areas file from its default branch, once, and
stored it as the document embry0 keeps. That import is gone: no code reads
embry0 configuration from a target repository any more. A deployment
upgrading straight past that release keeps the rows migration 145 listed as
``pending``, and a read that kept failing stays ``failed``: those
repositories' old files were never read, so embry0 names no areas for them.

At startup :func:`unread` lists them, as long as each is still registered and
has no areas document yet, and :func:`warn_if_areas_never_imported` logs
``areas_import_skipped`` at error level with the count and the fix, and writes
one audit row. Writing or mapping a repository's areas, or removing it from the
registry, takes it off the list. It never stops a start.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

#: The log event. Stable: alerts and log queries key on it.
EVENT: Final = "areas_import_skipped"
#: How many repositories the log line names; the count is always whole.
NAMED: Final = 20
#: What an operator does about it.
REMEDY: Final = (
    "start the last release that imported each repository's old areas file once, then this one again; or write or "
    "map these repositories' areas (docs/areas.md)"
)

_UNREAD_SQL: Final = (
    "SELECT i.repo FROM repo_areas_imports i "
    "WHERE i.outcome IN ('pending', 'failed') "
    "AND EXISTS (SELECT 1 FROM repos r WHERE r.repo = i.repo) "
    "AND NOT EXISTS (SELECT 1 FROM repo_areas a WHERE a.repo = i.repo) "
    "ORDER BY i.repo"
)


async def unread(db: DatabasePool) -> list[str]:
    """The registered repositories, with no areas document, whose old areas file was never read."""
    return [str(row["repo"]) for row in await db.fetch(_UNREAD_SQL)]


async def warn_if_areas_never_imported(
    db: DatabasePool | None, *, audit_log_path: Path | str | None = None
) -> list[str]:
    """Log, at error level, the repositories whose old areas file was never imported, and audit it once; them.
    Never raises: a check that fails is logged, and the start goes on."""
    if db is None:
        return []
    try:
        repos = await unread(db)
    except Exception:
        logger.warning("areas_import_check_failed", exc_info=True)
        return []
    if not repos:
        return []
    count = len(repos)
    logger.error(EVENT, count=count, repos=repos[:NAMED], remedy=REMEDY)
    from embry0.audit.helpers import emit_audit

    try:
        await emit_audit(
            db,
            "repo.areas_import_skipped",
            actor="embry0",
            # The count alone: the row is the deployment's, which a key with an allowlist does not read, and the
            # repositories are in the log line.
            details={"count": count, "remedy": REMEDY},
            audit_log_path=audit_log_path,
        )
    except Exception:
        logger.warning("areas_import_check_audit_failed", exc_info=True)
    return repos
