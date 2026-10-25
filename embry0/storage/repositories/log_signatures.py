"""Durable memory for deterministic log signatures (#289, SH-P3.1).

One row per (signature, repo). The clustering in
:mod:`embry0.services.log_signatures` is stateless and answers "what is in this
window"; this table answers the two questions a single window never could:

* **Is this new?** — ``first_seen_at`` versus the tick's window start.
* **Is this getting worse?** — ``occurrence_count`` over the first/last span,
  against ``last_window_count``.

Deliberately no stored ``rate`` column: a persisted rate is stale the moment the
next line lands, and #312 needs the rate a signature ran at *before* its fix
shipped, which is derivable from the counts. :meth:`SignatureRow.rate_per_hour`
computes it on read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from embry0.services.log_signatures import LogCluster
from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

#: Sample lines retained per signature. Enough for a model to recognise the
#: shape; small enough that a noisy signature cannot bloat the row.
MAX_STORED_SAMPLES = 3

#: Services retained per signature — a guard against an unbounded array if a
#: signature somehow appears across a large fleet.
MAX_STORED_SERVICES = 12


@dataclass(frozen=True)
class SignatureRow:
    """A stored signature, as the ranked table and #312 read it."""

    signature_id: str
    repo: str
    template: str
    first_seen_at: datetime
    last_seen_at: datetime
    occurrence_count: int
    ticks_seen: int
    last_window_count: int
    services: list[str]
    sample_lines: list[str]

    @property
    def span_seconds(self) -> float:
        return max((self.last_seen_at - self.first_seen_at).total_seconds(), 0.0)

    @property
    def rate_per_hour(self) -> float | None:
        """Lifetime occurrences per hour, or None when the span is zero.

        None rather than a large invented number: a signature seen once, or many
        times inside one second, has no meaningful rate yet.
        """
        span = self.span_seconds
        if span <= 0:
            return None
        return self.occurrence_count * 3600.0 / span

    def is_new_since(self, since: datetime) -> bool:
        """Was this signature first seen only within the current window?"""
        return self.first_seen_at >= since


class LogSignaturesRepository:
    """Upsert/read operations for the ``log_signatures`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def record_window(
        self,
        clusters: Sequence[LogCluster],
        *,
        repo: str | None = None,
        observed_at: datetime | None = None,
    ) -> int:
        """Fold one tick's clusters into the table. Returns rows written.

        ``first_seen_at`` only ever moves **earlier** and ``last_seen_at`` only
        ever moves **later**, via LEAST/GREATEST — so a tick whose window
        overlaps an earlier one cannot rewrite history, and an out-of-order
        replay cannot make a signature look newer than it is.

        Counts accumulate; ``last_window_count`` is replaced, because it means
        "how loud was this in the most recent tick", not a running total.

        Services union rather than overwrite. Samples are only replaced when the
        row has none, so the first concrete examples of a signature survive the
        churn of later windows.
        """
        if not clusters:
            return 0
        scope = repo or ""
        written = 0
        for cluster in clusters:
            first_seen = cluster.first_seen or observed_at
            last_seen = cluster.last_seen or observed_at
            if first_seen is None or last_seen is None:
                # Without any timestamp there is no honest first/last to store —
                # skip rather than stamping NOW() and inventing a span.
                logger.debug(
                    "log_signature_skipped_no_timestamp",
                    signature_id=cluster.signature_id,
                    repo=scope or None,
                )
                continue
            # The list itself: the pool's JSONB codec encodes it once. A
            # json.dumps here stored a JSON *string* (#382).
            samples = [str(line) for line in cluster.samples[:MAX_STORED_SAMPLES]]
            services = cluster.services[:MAX_STORED_SERVICES]
            await self._db.execute(
                """
                INSERT INTO log_signatures (
                    signature_id, repo, template, first_seen_at, last_seen_at,
                    occurrence_count, ticks_seen, last_window_count, services, sample_lines
                )
                -- $6 and $7 are both the cluster's count, passed twice on
                -- purpose: reusing one placeholder for a BIGINT column and an
                -- INTEGER column makes asyncpg refuse to prepare the statement
                -- ("inconsistent types deduced for parameter $6").
                VALUES ($1, $2, $3, $4, $5, $6, 1, $7, $8, $9::jsonb)
                ON CONFLICT (signature_id, repo) DO UPDATE SET
                    template          = EXCLUDED.template,
                    first_seen_at     = LEAST(log_signatures.first_seen_at, EXCLUDED.first_seen_at),
                    last_seen_at      = GREATEST(log_signatures.last_seen_at, EXCLUDED.last_seen_at),
                    occurrence_count  = log_signatures.occurrence_count + EXCLUDED.occurrence_count,
                    ticks_seen        = log_signatures.ticks_seen + 1,
                    last_window_count = EXCLUDED.last_window_count,
                    services          = (
                        SELECT COALESCE(array_agg(DISTINCT s), '{}')
                          FROM unnest(log_signatures.services || EXCLUDED.services) AS s
                    ),
                    sample_lines      = CASE
                        WHEN log_signatures.sample_lines = '[]'::jsonb THEN EXCLUDED.sample_lines
                        ELSE log_signatures.sample_lines
                    END,
                    updated_at        = NOW()
                """,
                cluster.signature_id,
                scope,
                cluster.template,
                first_seen,
                last_seen,
                cluster.count,  # $6 occurrence_count (BIGINT)
                cluster.count,  # $7 last_window_count (INTEGER) — see the note above
                services,
                samples,
            )
            written += 1
        return written

    async def mark_analysed(
        self, counts: Mapping[str, int], *, repo: str | None = None, verdict: str, at: datetime
    ) -> None:
        """Remember that an analysis read these signatures, what it found, and at what volume.

        ``counts`` maps each signature id in the analysed window to its count
        there. The watcher's novelty gate reads this back (:meth:`clean_since`)
        to not pay again for a signature an analysis found nothing in a moment ago.
        """
        if not counts:
            return
        await self._db.execute(
            """
            UPDATE log_signatures AS s
               SET analysed_at = $3, analysed_verdict = $4, analysed_count = c.count
              FROM unnest($2::text[], $5::int[]) AS c(signature_id, count)
             WHERE s.repo = $1 AND s.signature_id = c.signature_id
            """,
            repo or "",
            list(counts),
            at,
            verdict,
            [int(v) for v in counts.values()],
        )

    async def clean_since(
        self, signature_ids: Sequence[str], *, repo: str | None = None, since: datetime
    ) -> dict[str, int]:
        """The signatures among these whose last analysis, at or after ``since``, found no issue: id to the count it read."""
        if not signature_ids:
            return {}
        rows = await self._db.fetch(
            """
            SELECT signature_id, analysed_count FROM log_signatures
             WHERE repo = $1 AND signature_id = ANY($2::text[])
               AND analysed_verdict = 'no_issue' AND analysed_at >= $3
            """,
            repo or "",
            list(signature_ids),
            since,
        )
        return {r["signature_id"]: int(r["analysed_count"] or 0) for r in rows}

    async def get(self, signature_id: str, *, repo: str | None = None) -> SignatureRow | None:
        row = await self._db.fetchrow(
            "SELECT * FROM log_signatures WHERE signature_id = $1 AND repo = $2",
            signature_id,
            repo or "",
        )
        return _row(row) if row else None

    async def known_ids(self, signature_ids: Sequence[str], *, repo: str | None = None) -> set[str]:
        """Which of these signatures has this repo seen before?

        One round trip for the whole window — the ranked table flags every
        signature as new or recurring, and doing that per signature would be a
        query per cluster.
        """
        if not signature_ids:
            return set()
        rows = await self._db.fetch(
            "SELECT signature_id FROM log_signatures WHERE repo = $1 AND signature_id = ANY($2::text[])",
            repo or "",
            list(signature_ids),
        )
        return {r["signature_id"] for r in rows}

    async def rows_for(self, signature_ids: Sequence[str], *, repo: str | None = None) -> dict[str, SignatureRow]:
        """The stored rows for these signatures, keyed by signature id.

        The richer sibling of :meth:`known_ids`: #290 and #291 need
        ``last_window_count`` and ``last_seen_at`` to tell a spike from steady
        traffic, and a regression from a signature that never went away.
        "Have I seen this?" answers neither.

        Call it **before** :meth:`record_window`, which replaces
        ``last_window_count`` with this window's own count — fold first and
        every signature compares equal to itself.
        """
        if not signature_ids:
            return {}
        rows = await self._db.fetch(
            "SELECT * FROM log_signatures WHERE repo = $1 AND signature_id = ANY($2::text[])",
            repo or "",
            list(signature_ids),
        )
        return {r["signature_id"]: _row(r) for r in rows}

    async def top(self, *, repo: str | None = None, limit: int = 25) -> list[SignatureRow]:
        rows = await self._db.fetch(
            """
            SELECT * FROM log_signatures
             WHERE repo = $1
             ORDER BY occurrence_count DESC, signature_id
             LIMIT $2
            """,
            repo or "",
            limit,
        )
        return [_row(r) for r in rows]


def _row(row: Any) -> SignatureRow:
    # Decoded by the pool's JSONB codec. A tolerant json.loads of strings used
    # to sit here and hid that every row was double-encoded (#382).
    samples = list(row["sample_lines"] or [])
    return SignatureRow(
        signature_id=row["signature_id"],
        repo=row["repo"],
        template=row["template"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        occurrence_count=int(row["occurrence_count"]),
        ticks_seen=int(row["ticks_seen"]),
        last_window_count=int(row["last_window_count"]),
        services=list(row["services"] or []),
        sample_lines=[str(s) for s in samples],
    )
