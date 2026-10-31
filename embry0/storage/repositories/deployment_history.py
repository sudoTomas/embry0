"""What a deployment's own records say about its past: whether it existed before the current start.

A release that turns a feature's default off must not turn it off where it
already ran (the settings import's ``ReliedOn``,
:mod:`embry0.settings.bootstrap`). Every deployment that existed before the
release ran it, since it was on by default, so the evidence is any record a
deployment in use holds and a new one cannot: a registered repository, a job,
or a lead of the kinds the caller names, created before ``before``, the
moment the release first asked (the import records it once).

Only records older than that moment count, so nothing made after the release
started is mistaken for its past: a repository a new deployment registers
while a failed check waits for its retry, or before its next start, is not
evidence. On a new deployment the moment comes before its tables exist (the
lifespan reads the database's clock before the migrations run), so no record
can be older; the tables are empty then anyway, since no migration writes a
job or a lead, and the one that inserts repositories (52) copies them from
tables that are empty on a new database. ``repos.created_at`` and
``jobs.created_at`` are the database's clock; ``leads.first_seen`` is the
leads loop's, which stores nothing in its first five minutes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol


class _Reads(Protocol):
    async def fetchval(self, query: str, *args: Any) -> Any: ...


_EXISTED = """
SELECT EXISTS (SELECT 1 FROM repos WHERE created_at < $2)
    OR EXISTS (SELECT 1 FROM jobs WHERE created_at < $2)
    OR EXISTS (SELECT 1 FROM leads WHERE kind = ANY($1::text[]) AND first_seen < $2)
"""


class DeploymentHistory:
    """What ``db`` held before ``before``: ``db`` is a pool, or the connection of a transaction already
    open (the settings import's)."""

    def __init__(self, db: _Reads, *, before: datetime) -> None:
        self._db = db
        self.before = before

    async def existed_before(self, *, lead_kinds: Sequence[str] = ()) -> bool:
        """Whether this deployment held, before ``before``, a registered repository, a job, or a lead of
        one of ``lead_kinds``.

        A lead is kept even after its repository is removed, and leads are
        never deleted, so a deployment that only ever ran a feature storing
        leads still counts.
        """
        return bool(await self._db.fetchval(_EXISTED, list(lead_kinds), self.before))
