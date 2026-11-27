"""The tallies the metering proxies keep of each run's model calls, as the orchestrator last read them (migration 156).

One row per proxy, per proxy process (``boot_id``) and per tally: a run's, keyed by its run id, or a sandbox's calls
under no run, keyed ``unattributed:<sandbox>:<started>``. Each read replaces the row's snapshot with the newer one,
which is cumulative; a run's end writes the proxy's final word (``final``), which no later read replaces.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import timedelta
from typing import Any

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)


def tally_key(tally: Mapping[str, Any]) -> str:
    """The row key of a proxy's tally: its run id, or its sandbox and the moment it began for calls under no run."""
    run_id = tally.get("run_id")
    if isinstance(run_id, str) and run_id:
        return run_id
    return f"unattributed:{tally.get('sandbox_id')}:{tally.get('started')}"


class RunMeterRepository:
    """Reads and writes ``run_meter``."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def record(
        self, proxy: str, boot_id: str, tallies: Iterable[Mapping[str, Any]], *, final: bool = False
    ) -> int:
        """Keep each of ``tallies``, as ``proxy``'s process ``boot_id`` reported them. A final tally is never replaced
        by a read. Returns how many rows were written."""
        rows = [
            (proxy, boot_id, tally_key(t), t.get("run_id") or None, str(t.get("sandbox_id") or ""), dict(t), final)
            for t in tallies
        ]
        if not rows:
            return 0
        async with self._db.transaction() as conn:
            await conn.executemany(
                """
            INSERT INTO run_meter (proxy, boot_id, tally_key, run_id, sandbox_id, snapshot, final, read_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, now())
            ON CONFLICT (proxy, boot_id, tally_key) DO UPDATE
               SET snapshot = EXCLUDED.snapshot, final = EXCLUDED.final, read_at = EXCLUDED.read_at
             WHERE NOT run_meter.final
            """,
                rows,
            )
        return len(rows)

    async def for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Every tally kept of ``run_id``: one per proxy and proxy process, oldest read first."""
        rows = await self._db.fetch(
            "SELECT proxy, boot_id, snapshot, final, read_at FROM run_meter WHERE run_id = $1 ORDER BY read_at, proxy",
            run_id,
        )
        return [dict(r) for r in rows]

    async def for_runs(self, run_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """:meth:`for_run` for each of ``run_ids``, in one read: by run, each run's tallies oldest read first."""
        rows = await self._db.fetch(
            "SELECT run_id, proxy, boot_id, snapshot, final, read_at FROM run_meter"
            " WHERE run_id = ANY($1::text[]) ORDER BY read_at, proxy",
            list(run_ids),
        )
        kept: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            entry = dict(row)
            kept.setdefault(str(entry.pop("run_id")), []).append(entry)
        return kept

    async def prune(self, older_than: timedelta) -> int:
        """Drop tallies last read longer ago than ``older_than``. Returns how many."""
        status = await self._db.execute("DELETE FROM run_meter WHERE read_at < now() - $1::interval", older_than)
        try:
            return int(str(status).rsplit(" ", 1)[-1])
        except ValueError:
            return 0
