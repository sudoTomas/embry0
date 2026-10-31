"""The repositories the scout and the Slop-O-Meter read before their enrollment became explicit (#165).

Until then each read the registered, active repositories the world map
showed (``world.regions()``): those with a job created in the last 90 days,
with owners, or with an enabled watched stack. The one-time import
(:mod:`embry0.services.background_enrollment`) enrolls exactly those, as they
stood at a moment it records once: a job counts only when it was created in
the 90 days before that moment, and a repository only when it was registered
before it. A repository a new deployment registers after its first start, or
one an existing deployment registers while the import waits for a retry, is
therefore never enrolled by the old rule.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Protocol

#: How far back a job enrolled its repository under the old rule.
WINDOW_DAYS: Final = 90

_BEFORE = f"""
WITH shown AS (
    SELECT lower(repo) AS repo FROM jobs
    WHERE repo IS NOT NULL
      AND created_at > $1::timestamptz - INTERVAL '{WINDOW_DAYS} days'
      AND created_at < $1::timestamptz
    UNION
    SELECT lower(repo) FROM repo_preferences
    WHERE CASE WHEN jsonb_typeof(owners) = 'array' THEN jsonb_array_length(owners) ELSE 0 END > 0
    UNION
    SELECT lower(repo) FROM watcher_stacks WHERE enabled
)
SELECT r.repo FROM repos r
WHERE r.active AND r.created_at < $1::timestamptz AND lower(r.repo) IN (SELECT repo FROM shown)
ORDER BY lower(r.repo), r.repo
"""


class _Reads(Protocol):
    async def fetch(self, query: str, *args: Any) -> Any: ...


async def shown_before(db: _Reads, before: datetime) -> list[str]:
    """The registered, active repositories the old rule read at ``before``, as the registry spells them."""
    return [str(row["repo"]) for row in await db.fetch(_BEFORE, before)]


async def registered(db: _Reads) -> dict[str, bool]:
    """Every registered repository now, as the registry spells it, to whether it is active."""
    rows = await db.fetch("SELECT repo, active FROM repos ORDER BY repo")
    return {str(row["repo"]): bool(row["active"]) for row in rows}


_INACTIVE_HELD = """
SELECT r.repo FROM repos r
WHERE NOT r.active AND lower(r.repo) IN (SELECT lower(repo) FROM slop_meter_repos)
ORDER BY lower(r.repo), r.repo
"""


async def inactive_held(db: _Reads) -> list[str]:
    """The inactive registered repositories the Slop-O-Meter keeps rows for, as the registry spells them.

    The old rule read no inactive repository, and the meter kept what it had
    read of one; enrolling them keeps that, since an inactive repository is
    never read and loses nothing.
    """
    return [str(row["repo"]) for row in await db.fetch(_INACTIVE_HELD)]
