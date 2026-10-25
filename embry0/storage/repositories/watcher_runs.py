"""Watcher-runs repository — audit trail for the log-watcher/proposer (RAV-657)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

WATCHER_ACTIONS = frozenset(
    {
        "skipped_quiet",
        "skipped_backlog",
        "skipped_duplicate",
        # SH-P2.4: analysis withheld — the last attempt on this same window was
        # unparseable and nothing has changed since (see unparseable_backoff).
        "skipped_unparseable",
        "no_issue",
        "proposed",
        # #283: the analysis proposed a ticket while the draft cap was full.
        # Nothing was filed; the finding waits as a healing case and the row
        # becomes `proposed` when a slot frees (see mark_filed).
        "deferred",
        # #283: the draft cap was full and the day's budget of full-cap
        # analyses was spent, so this tick looked at its window but did not
        # pay to analyse it. Distinct from skipped_backlog (nothing could be
        # recorded) and never mistakable for a quiet no_issue.
        "skipped_analysis_cap",
        # SH-P3.3 (#291): the window was clustered and compared against the
        # signatures on record, and nothing in it was new, spiking or
        # regressed — so no model was paid to read it. Distinct from
        # skipped_quiet (too few lines to judge) and from no_issue (a model did
        # read it and found nothing).
        "no_change",
        # Spec 2026-10-25: every notable signature in the window was read by an
        # analysis within the stack's skip_known_hours that found no issue (or
        # has an open case), and none outgrew what that analysis read. No model
        # was paid. Distinct from no_change, where nothing was notable at all.
        "skipped_known",
        "error",
    }
)
# Ticks whose window was CONSUMED — the only ones that advance the query
# window (SH-P2.4). skipped_duplicate belongs here: the analysis ran and its
# proposal was recognised as already filed. skipped_quiet/skipped_backlog and
# error ticks reached no conclusion, so the next tick covers their logs again.
#
# no_change belongs here too (#291), and the reasoning is load-bearing enough
# to write down, because all of this set's consumers want the same answer:
#
#   * The query window must advance. A no_change tick examined its window and
#     finished with it. If it did not advance, the window would widen on every
#     quiet tick and the line counts would grow mechanically — which the spike
#     test would then read as a spike, paying for exactly the analysis this
#     issue exists to avoid.
#   * The dead-man gauge must refresh. Embry0WatcherNoVerdict3h asks "is the
#     loop still detecting?", and a tick that clustered its window and decided
#     nothing changed IS the loop working. Leaving no_change out would make
#     that alert fire on every healthy quiet night.
#   * Lost-log-time accounting and the window-clamp warning both key on "did a
#     tick consume this stretch of logs" — which a no_change tick did.
#
# The risk this creates — a bug that turned every tick into no_change would
# keep the dead-man quiet — is covered by Embry0WatcherNeverAnalyses24h in
# ~/containers/monitoring, which watches for no PAID analysis at all.
# skipped_known consumes its window for the same reasons as no_change.
ANALYSED_ACTIONS = frozenset({"no_issue", "proposed", "deferred", "skipped_duplicate", "no_change", "skipped_known"})
# Ticks on which the loop REACHED A VERDICT about its stack — the set the
# dead-man gauge (embry0_watcher_last_verdict_tick_timestamp_seconds, hence
# Embry0WatcherNoVerdict3h) and its startup seed advance on. A superset of
# ANALYSED_ACTIONS by exactly skipped_quiet: a quiet tick fetched its window
# and found too few lines to be worth a model's judgement, and on a stack that
# simply logs nothing for a night that IS the loop detecting — nothing. Until
# 2026-09-27 the dead-man read the analysed gauge, so a self-watching stack
# paged "no verdict in 3h" on every quiet night, while a busy application
# stack never showed it because it is never quiet.
#
# It is deliberately NOT analysed: a quiet tick consumes nothing, so the query
# window keeps widening until it holds watcher_min_log_lines (see
# last_analysed_window_end) and no log time is written off as lost. The hole
# this opens — a LogQL that matches nothing, forever, looks exactly like a
# calm stack — is Embry0WatcherOnlyQuiet2d's (~/containers/monitoring), which
# reads the two gauges against each other: verdicts fresh, no analysis in 2d.
VERDICT_ACTIONS = ANALYSED_ACTIONS | {"skipped_quiet"}
# Rows that stand for a finding the watcher already holds — filed, or waiting
# for a draft slot (#283). Dedup and the briefing's known-issues list read both,
# so a deferred finding is neither deferred twice nor re-proposed reworded.
_FINDING_ACTIONS = ["proposed", "deferred"]
# Leads the ``detail`` of an error tick whose analysis stayed unparseable after
# its retry. The watcher writes it and unparseable_backoff() matches on it —
# one constant so the two cannot drift.
UNPARSEABLE_DETAIL_PREFIX = "unparseable analysis output"


class WatcherRunsRepository:
    """Insert/read operations for the watcher_runs table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def create(
        self,
        window_start: datetime,
        window_end: datetime,
        action: str,
        log_lines: int = 0,
        detail: str | None = None,
        job_id: str | None = None,
        fingerprint: str | None = None,
        tracker: str | None = None,
        tracker_key: str | None = None,
        tracker_url: str | None = None,
        rationale: str | None = None,
        briefing_failures: list[str] | None = None,
        retried_job_id: str | None = None,
        analysis_cost_usd: float | None = None,
        unanalysed_seconds: int = 0,
        log_lines_truncated: bool = False,
        analysed_under_full_cap: bool = False,
        repo: str = "",
    ) -> str:
        if action not in WATCHER_ACTIONS:
            # #347: this is the tick's LAST write, after the logs were fetched
            # and the analysis paid for. Raising here lost the whole row, so an
            # action added in one place and not here made the watcher look as
            # if it had stopped recording. Keep the row as an error that names
            # the unknown action; the window then stays where it was.
            logger.error("watcher_run_unknown_action", action=action)
            unknown = f"unknown watcher action {action!r}"
            detail = f"{unknown}: {detail}" if detail else unknown
            action = "error"
        run_id = f"wr-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO watcher_runs (
                id, window_start, window_end, log_lines, action, detail,
                job_id, fingerprint, tracker, tracker_key, tracker_url, rationale,
                briefing_failures, retried_job_id, analysis_cost_usd,
                unanalysed_seconds, log_lines_truncated, analysed_under_full_cap, repo
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
            """,
            run_id,
            window_start,
            window_end,
            log_lines,
            action,
            detail,
            job_id,
            fingerprint,
            tracker,
            tracker_key,
            tracker_url,
            rationale,
            list(briefing_failures or []),
            retried_job_id,
            analysis_cost_usd,
            unanalysed_seconds,
            log_lines_truncated,
            analysed_under_full_cap,
            repo,
        )
        logger.info("watcher_run_recorded", run_id=run_id, action=action, log_lines=log_lines, repo=repo)
        return run_id

    async def list_recent(self, limit: int = 50, repo: str | None = None) -> list[dict[str, Any]]:
        """Newest ticks. ``repo=None`` spans every stack (the dashboard's view);
        a value narrows to one stack."""
        rows = await self._db.fetch(
            "SELECT * FROM watcher_runs WHERE ($2::text IS NULL OR repo = $2) ORDER BY created_at DESC LIMIT $1",
            limit,
            repo,
        )
        return [dict(r) for r in rows]

    async def adopt_unscoped_rows(self, repo: str) -> int:
        """Claim the rows written before stacks existed for ``repo``.

        Migration 82 leaves them at '' because no migration can know which
        stack the deployment was watching; the orchestrator does, from its
        configured watcher repo. Idempotent, and a no-op on a fresh database.
        """
        if not repo:
            return 0
        adopted = 0
        for statement in (
            "UPDATE watcher_runs SET repo = $1 WHERE repo = ''",
            "UPDATE watcher_known_patterns SET repo = $1 WHERE repo = ''",
            "UPDATE watcher_tick_attempts SET repo = $1 WHERE repo = ''",
        ):
            result = await self._db.execute(statement, repo)
            adopted += int(str(result).split()[-1]) if result else 0
        if adopted:
            logger.info("watcher_rows_adopted", repo=repo, rows=adopted)
        return adopted

    # ---- loop scheduling anchors (#335) -------------------------------------

    async def last_tick_at(self, repo: str | None = None) -> datetime | None:
        """When the most recent tick was recorded, whatever its action.

        Manual ticks (``POST /watcher/run``) count too: the loop's promise is
        a tick per interval, not a *scheduled* tick per interval.
        """
        return cast(
            "datetime | None",
            await self._db.fetchval(
                "SELECT MAX(created_at) FROM watcher_runs WHERE ($1::text IS NULL OR repo = $1)", repo
            ),
        )

    async def last_tick_attempt_at(self, repo: str = "") -> datetime | None:
        """When this stack's loop last STARTED a tick — see :meth:`record_tick_attempt`."""
        return cast(
            "datetime | None",
            await self._db.fetchval("SELECT MAX(attempted_at) FROM watcher_tick_attempts WHERE repo = $1", repo),
        )

    async def record_tick_attempt(self, at: datetime, repo: str = "") -> None:
        """Leave a trace that a tick is starting, before it does anything.

        A run row is only written when a tick ends, so a tick killed during
        its analysis job would otherwise be invisible to the next process,
        which would find a tick overdue and start another job — once per
        restart. The mark only moves forward.
        """
        if at.tzinfo is None:
            raise ValueError("record_tick_attempt needs a timezone-aware datetime")
        await self._db.execute(
            """
            INSERT INTO watcher_tick_attempts (attempted_at, repo) VALUES ($1, $2)
            ON CONFLICT (repo) DO UPDATE
                SET attempted_at = GREATEST(watcher_tick_attempts.attempted_at, EXCLUDED.attempted_at)
            """,
            at,
            repo,
        )

    async def get(self, run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow("SELECT * FROM watcher_runs WHERE id = $1", run_id)
        return dict(row) if row else None

    async def full_cap_analyses_since(self, *, hours: int = 24, repo: str | None = None) -> int:
        """Analysis JOBS paid for while the draft cap was full, in the window.

        #283 made a full-cap tick analyse its window instead of skipping
        before Loki was queried. That spend needs a bound the draft cap used
        to provide for free, and the bound has to survive a restart, so it is
        counted from rows rather than memory. Jobs, not ticks: a tick that
        retried an unparseable analysis paid twice (``retried_job_id``).
        """
        row = await self._db.fetchrow(
            """
            SELECT COUNT(job_id) + COUNT(retried_job_id) AS n FROM watcher_runs
            WHERE analysed_under_full_cap AND created_at > NOW() - make_interval(hours => $1)
              AND ($2::text IS NULL OR repo = $2)
            """,
            hours,
            repo,
        )
        return int(row["n"]) if row else 0

    async def mark_filed(self, run_id: str, *, tracker: str, key: str, url: str | None = None) -> bool:
        """Turn a ``deferred`` row into the ``proposed`` row of its ticket (#283).

        The finding was analysed once; filing it later is the same proposal,
        not a second one, so the row is converted rather than duplicated — a
        second ``proposed`` row would give one finding two cases at the next
        backfill. ``created_at`` is kept: the age window of the cap and of the
        dedup still counts from when the finding was seen. Only a ``deferred``
        row is touched; False means there was nothing to convert.
        """
        result = await self._db.execute(
            """
            UPDATE watcher_runs SET action = 'proposed', tracker = $2, tracker_key = $3, tracker_url = $4
            WHERE id = $1 AND action = 'deferred'
            """,
            run_id,
            tracker,
            key,
            url,
        )
        return bool(result) and str(result).split()[-1] == "1"

    async def last_analysed_window_end(self, repo: str | None = None) -> datetime | None:
        """End of the most recent window that was analysed to a verdict.

        SH-P2.4: the next tick's query window starts here, so the time the
        analysis job takes (or a run of skipped/errored ticks) leaves no hole
        between windows. ``None`` until the first analysed tick.
        """
        row = await self._db.fetchrow(
            "SELECT MAX(window_end) AS window_end FROM watcher_runs "
            "WHERE action = ANY($1::text[]) AND ($2::text IS NULL OR repo = $2)",
            sorted(ANALYSED_ACTIONS),
            repo,
        )
        return row["window_end"] if row else None

    async def last_tick_times(self, repo: str | None = None) -> dict[str, datetime | None]:
        """When the last tick of any kind, the last ANALYSED one and the last
        one that reached a VERDICT were recorded.

        #285: seeds the tick-timestamp gauges at startup — without it every
        restart reads as "never ticked" to the dead-man alert until the next
        tick lands, up to a full interval later. ``last_verdict_at`` is the
        dead-man's own seed: keyed on VERDICT_ACTIONS, so a restart after a
        quiet night does not re-arm Embry0WatcherNoVerdict3h that the live
        quiet ticks had silenced.

        The first value is ``last_tick_at()``; all three are read in one
        statement because the seeding needs them together on the startup path.
        """
        row = await self._db.fetchrow(
            "SELECT MAX(created_at) AS last_tick_at, "
            "MAX(created_at) FILTER (WHERE action = ANY($1::text[])) AS last_analysed_at, "
            "MAX(created_at) FILTER (WHERE action = ANY($2::text[])) AS last_verdict_at FROM watcher_runs "
            "WHERE ($3::text IS NULL OR repo = $3)",
            sorted(ANALYSED_ACTIONS),
            sorted(VERDICT_ACTIONS),
            repo,
        )
        return {
            "last_tick_at": row["last_tick_at"] if row else None,
            "last_analysed_at": row["last_analysed_at"] if row else None,
            "last_verdict_at": row["last_verdict_at"] if row else None,
        }

    async def unparseable_backoff(self, repo: str | None = None) -> dict[str, int] | None:
        """Back-off state after an unparseable-output error, else ``None``.

        SH-P2.4: errored ticks do not advance the window, so a window whose
        analysis keeps coming back unparseable would be re-analysed (two jobs)
        on every tick. Looks at the most recent tick that attempted anything
        (an ``error`` or an analysed action — skipped ticks are transparent):
        only when that is an unparseable-output error does it return the line
        count that tick saw and how many ticks were withheld since. Any other
        error (Loki down, failed job, Linear) returns ``None`` — transient
        failures are retried on the very next tick.
        """
        row = await self._db.fetchrow(
            """
            SELECT w.detail, w.log_lines,
                   (SELECT COUNT(*) FROM watcher_runs s
                     WHERE s.action = 'skipped_unparseable' AND s.created_at > w.created_at
                       AND ($2::text IS NULL OR s.repo = $2)) AS skipped_since
            FROM watcher_runs w
            WHERE (w.action = 'error' OR w.action = ANY($1::text[]))
              AND ($2::text IS NULL OR w.repo = $2)
            ORDER BY w.created_at DESC LIMIT 1
            """,
            sorted(ANALYSED_ACTIONS),
            repo,
        )
        if row is None or not str(row["detail"] or "").startswith(UNPARSEABLE_DETAIL_PREFIX):
            return None
        return {"log_lines": int(row["log_lines"]), "skipped_since": int(row["skipped_since"])}

    async def open_proposal_count(self, days: int = 7, repo: str | None = None) -> int:
        """Proposals filed by the watcher (drafts awaiting human review).

        A draft leaves 'open' when the human accepts it — acceptance means
        labeling the Linear ticket `embry0`, which dispatches a pipeline run;
        we approximate 'still open' as 'proposed and never dispatched', i.e.
        no embry0 issue exists whose title carries the draft's identifier.

        RAV-1073: a draft cancelled in Linear (rather than accepted) is
        invisible to us, so it would pin the cap forever. Bound the count to
        the last ``days`` days so stale drafts age out of the cap.
        """
        row = await self._db.fetchrow(
            """
            SELECT COUNT(*) AS n FROM watcher_runs w
            WHERE w.action = 'proposed'
              AND w.tracker_key IS NOT NULL
              AND w.released_at IS NULL
              AND w.created_at > NOW() - make_interval(days => $1)
              AND ($2::text IS NULL OR w.repo = $2)
              AND NOT EXISTS (
                  SELECT 1 FROM issues i
                  WHERE i.title LIKE '[' || w.tracker_key || ']%'
              )
            """,
            days,
            repo,
        )
        return int(row["n"]) if row else 0

    async def fingerprint_exists(self, fingerprint: str, days: int = 7, repo: str | None = None) -> bool:
        """Whether this fingerprint was already proposed within the window.

        RAV-1073: bounded to ``days`` so a genuinely recurring problem is
        re-filable once its original draft is long closed/fixed, while
        within-window dedup (a reworded re-detection) still holds.
        """
        row = await self._db.fetchrow(
            """
            SELECT 1 FROM watcher_runs
            WHERE fingerprint = $1 AND action = ANY($3::text[])
              AND created_at > NOW() - make_interval(days => $2)
              AND ($4::text IS NULL OR repo = $4)
            LIMIT 1
            """,
            fingerprint,
            days,
            _FINDING_ACTIONS,
            repo,
        )
        return row is not None

    async def recent_proposal_titles(self, days: int = 7, limit: int = 10, repo: str | None = None) -> list[str]:
        """Titles of recently filed proposals (newest first).

        RAV-1067: fed back into the analysis prompt as known issues so the
        agent can semantically dedup a reworded re-detection of a still-live
        problem. Titles ride the ``detail`` column of ``action='proposed'``
        rows (older rows predate that convention and are skipped via the
        NULL filter).
        """
        rows = await self._db.fetch(
            """
            SELECT detail FROM watcher_runs
            WHERE action = ANY($3::text[]) AND detail IS NOT NULL
              AND created_at > NOW() - make_interval(days => $1)
              AND ($4::text IS NULL OR repo = $4)
            ORDER BY created_at DESC LIMIT $2
            """,
            days,
            limit,
            _FINDING_ACTIONS,
            repo,
        )
        return [str(r["detail"]) for r in rows]

    async def recent_findings(self, days: int = 7, limit: int = 20, repo: str | None = None) -> list[dict[str, Any]]:
        """Recent substantive verdicts (proposed/no_issue) with their reasoning.

        RAV-1138: fed into the analysis briefing as prior-findings memory so
        the agent sees WHY earlier windows were judged the way they were,
        instead of re-deriving every recurring pattern from scratch. Rows
        without a rationale predate the convention and carry ``None``.
        """
        rows = await self._db.fetch(
            """
            SELECT action, detail, fingerprint, rationale, created_at
            FROM watcher_runs
            WHERE action IN ('proposed', 'deferred', 'no_issue')
              AND created_at > NOW() - make_interval(days => $1)
              AND ($3::text IS NULL OR repo = $3)
            ORDER BY created_at DESC LIMIT $2
            """,
            days,
            limit,
            repo,
        )
        return [dict(r) for r in rows]

    async def open_proposal_tickets(self, days: int = 7, repo: str | None = None) -> list[dict[str, Any]]:
        """Identifiers of drafts currently counted by ``open_proposal_count``.

        RAV-1106/RAV-1134: the reconciliation pass checks these against
        Linear each tick and releases slots for cancelled/completed drafts —
        the same predicate as the cap query, so what gets reconciled is
        exactly what pins the cap.
        """
        rows = await self._db.fetch(
            """
            SELECT DISTINCT w.tracker, w.tracker_key FROM watcher_runs w
            WHERE w.action = 'proposed'
              AND w.tracker_key IS NOT NULL
              AND w.released_at IS NULL
              AND w.created_at > NOW() - make_interval(days => $1)
              AND ($2::text IS NULL OR w.repo = $2)
              AND NOT EXISTS (
                  SELECT 1 FROM issues i
                  WHERE i.title LIKE '[' || w.tracker_key || ']%'
              )
            """,
            days,
            repo,
        )
        return [{"tracker": r["tracker"], "key": str(r["tracker_key"])} for r in rows]

    async def release_ticket(self, tracker: str | None, key: str) -> int:
        """Free the cap slot(s) held by a draft that died in Linear.

        Mirrors the manual incident fix (2026-07-29: cancelled RAV-1097 kept
        the watcher blind for 30h). The slot is released by stamping
        ``released_at``, which the open-proposal predicate excludes. The
        ticket id itself is NEVER cleared (#278): it is the only link from a
        proposal to its ticket, and NULLing it here cost 9 of the first 30
        proposals their Linear id. Already-released rows are left alone, so a
        repeat pass reports 0.
        """
        result = await self._db.execute(
            """
            UPDATE watcher_runs SET released_at = NOW()
            WHERE tracker_key = $1 AND ($2::text IS NULL OR tracker = $2) AND released_at IS NULL
            """,
            key,
            tracker,
        )
        released = int(str(result).split()[-1]) if result else 0
        if released:
            logger.info("watcher_draft_released", tracker=tracker, key=key, rows=released)
        return released
