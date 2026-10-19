"""org_repo_facts and org_discovery_runs: what discovery found under an owner (ADR 0010, migration 98)."""

from __future__ import annotations

import secrets
from typing import Any

from embry0.services.org_discovery import RepoFacts
from embry0.storage.database import DatabasePool

_FACT_COLUMNS = (
    "owner, repo, run_id, archived, private, fork, default_branch, language, pushed_at, open_issues, facts, seen_at"
)
_RUN_COLUMNS = "id, owner, status, repos_total, repos_seen, summary, error, started_at, finished_at"


def _fact(row: Any) -> dict[str, Any]:
    return {
        "owner": row["owner"],
        "repo": row["repo"],
        "run_id": row["run_id"],
        "archived": bool(row["archived"]),
        "private": bool(row["private"]),
        "fork": bool(row["fork"]),
        "default_branch": row["default_branch"],
        "language": row["language"],
        "pushed_at": row["pushed_at"].isoformat() if row["pushed_at"] else None,
        "open_issues": row["open_issues"],
        "facts": dict(row["facts"] or {}),
        "seen_at": row["seen_at"].isoformat() if row["seen_at"] else None,
    }


def _run(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "owner": row["owner"],
        "status": row["status"],
        "repos_total": row["repos_total"],
        "repos_seen": row["repos_seen"],
        "summary": dict(row["summary"] or {}),
        "error": row["error"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
    }


# How many recent delivery ids a fact row remembers, so that a redelivery
# (the same X-GitHub-Delivery) is not counted twice. GitHub redelivers on
# request, from its recent deliveries; the bound keeps a busy repository's
# row small.
WEBHOOK_DELIVERY_IDS_KEPT = 50

#: The error a run gets when the orchestrator restarted while it was going.
INTERRUPTED = "interrupted: the orchestrator restarted before the run finished"


class OrgReposRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def upsert_fact(self, facts: RepoFacts, *, run_id: str) -> None:
        """Replace the repository's facts with what this discovery run found.

        The keys the webhook receiver writes (:meth:`note_webhook_event`) are
        kept: a rescan knows nothing about deliveries, so it must not erase the
        record of them.
        """
        await self._db.execute(
            """
            INSERT INTO org_repo_facts (owner, repo, run_id, archived, private, fork, default_branch, language, pushed_at, open_issues, facts, seen_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, NOW())
            ON CONFLICT (repo) DO UPDATE SET
                owner = EXCLUDED.owner, run_id = EXCLUDED.run_id, archived = EXCLUDED.archived,
                private = EXCLUDED.private, fork = EXCLUDED.fork, default_branch = EXCLUDED.default_branch,
                language = EXCLUDED.language, pushed_at = EXCLUDED.pushed_at, open_issues = EXCLUDED.open_issues,
                facts = EXCLUDED.facts || COALESCE(
                    (SELECT jsonb_object_agg(key, value) FROM jsonb_each(org_repo_facts.facts)
                      WHERE key IN ('webhook_events', 'last_webhook_event', 'webhook_delivery_ids')),
                    '{}'::jsonb
                ),
                seen_at = NOW()
            """,
            facts.owner,
            facts.repo,
            run_id,
            facts.archived,
            facts.private,
            facts.fork,
            facts.default_branch,
            facts.language,
            facts.pushed_at,
            facts.open_issues,
            dict(facts.facts),
        )

    async def refresh_state(self, repo: str, state: dict[str, Any]) -> None:
        """Merge ``state``, what embry0's own tables say now, into the facts
        of a repository the newest run did not list (ADR 0016).

        ``run_id`` and ``seen_at`` stay: they say when the forge last listed
        the repository, and the needs list reads a row stamped by an older
        run as one that left the listing.
        """
        await self._db.execute(
            "UPDATE org_repo_facts SET facts = facts || $2::jsonb WHERE repo = $1",
            repo,
            dict(state),
        )

    async def note_webhook_event(
        self,
        owner: str,
        repo: str,
        *,
        event: str,
        action: str,
        delivery_id: str | None,
        registered: bool,
    ) -> bool:
        """A delivery arrived for a repository under a registered owner.

        Written on the repository's fact row, created bare when discovery has
        not seen the repository yet, so the needs list can say that the forge
        is already talking about a repository embry0 does not maintain:
        ``registered`` as the receiver left it, ``webhook_events`` counting the
        deliveries, ``last_webhook_event`` naming the latest one. Discovery's
        own facts and ``seen_at`` are left as they were.

        A delivery is counted once per ``delivery_id``: GitHub resends a
        delivery with the same ``X-GitHub-Delivery``, so the row keeps the
        last :data:`WEBHOOK_DELIVERY_IDS_KEPT` ids in ``webhook_delivery_ids``
        and a redelivery of one of them leaves the count alone. A delivery
        without an id is counted every time.

        Returns whether this delivery created the row: the first word embry0
        has of a repository no discovery run listed, which the needs list
        reads as ``not_registered`` (ADR 0011).
        """
        last = {"event": event, "action": action, "delivery_id": delivery_id}
        state: dict[str, Any] = {"registered": registered}
        if registered:
            state["active"] = True
        first: dict[str, Any] = {**state, "webhook_events": 1, "last_webhook_event": last}
        if delivery_id is not None:
            first["webhook_delivery_ids"] = [delivery_id]
        # xmax is 0 on a row this statement inserted, and set on one it updated.
        created = await self._db.fetchval(
            """
            INSERT INTO org_repo_facts (owner, repo, facts, seen_at)
            VALUES ($1, $2, $3::jsonb, NOW())
            ON CONFLICT (repo) DO UPDATE SET
                facts = org_repo_facts.facts || $4::jsonb || CASE
                    WHEN $5::text IS NULL THEN jsonb_build_object(
                        'webhook_events', COALESCE((org_repo_facts.facts->>'webhook_events')::int, 0) + 1
                    )
                    WHEN COALESCE(org_repo_facts.facts->'webhook_delivery_ids', '[]'::jsonb) ? $5::text
                        THEN '{}'::jsonb
                    ELSE jsonb_build_object(
                        'webhook_events', COALESCE((org_repo_facts.facts->>'webhook_events')::int, 0) + 1,
                        'webhook_delivery_ids', (
                            SELECT jsonb_agg(id ORDER BY n) FROM (
                                SELECT id, n
                                  FROM jsonb_array_elements(
                                      COALESCE(org_repo_facts.facts->'webhook_delivery_ids', '[]'::jsonb)
                                      || jsonb_build_array($5::text)
                                  ) WITH ORDINALITY AS kept(id, n)
                                 ORDER BY n DESC
                                 LIMIT $6::int
                            ) AS recent
                        )
                    )
                END
            RETURNING (xmax = 0)
            """,
            owner.lower(),
            repo,
            first,
            {**state, "last_webhook_event": last},
            delivery_id,
            WEBHOOK_DELIVERY_IDS_KEPT,
        )
        return bool(created)

    async def list_for_owner(self, owner: str, *, include_archived: bool = True) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            f"SELECT {_FACT_COLUMNS} FROM org_repo_facts WHERE owner = $1 AND ($2::boolean OR NOT archived) ORDER BY pushed_at DESC NULLS LAST, repo",
            owner.lower(),
            include_archived,
        )
        return [_fact(r) for r in rows]

    async def start_run(self, owner: str) -> dict[str, Any] | None:
        """Open a running run for ``owner``, or return None while one is running.

        The check and the insert hold the owner's advisory transaction lock
        (``org_discovery:<owner>``), so two starts for one owner, a person's
        and the schedule's (ADR 0016) or two processes', never both open one.
        """
        owner = owner.lower()
        run_id = f"odr-{secrets.token_hex(6)}"
        async with self._db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"org_discovery:{owner}")
            row = await conn.fetchrow(
                f"""
                INSERT INTO org_discovery_runs (id, owner, status)
                SELECT $1, $2, 'running'
                 WHERE NOT EXISTS (SELECT 1 FROM org_discovery_runs WHERE owner = $2 AND status = 'running')
                RETURNING {_RUN_COLUMNS}
                """,
                run_id,
                owner,
            )
        return _run(row) if row else None

    async def fail_interrupted_runs(self) -> list[str]:
        """Close every run still marked running as an error; the ids closed.

        A run lives in the orchestrator process that started it, so at boot
        none is still going: one marked running was cut off by the restart.
        Left alone it would read as in progress for good, and hold off every
        later run for its owner, a person's and the schedule's. Called once
        at startup, as the jobs a restart orphaned are failed then.
        """
        rows = await self._db.fetch(
            "UPDATE org_discovery_runs SET status = 'error', error = $1, finished_at = NOW() "
            "WHERE status = 'running' RETURNING id",
            INTERRUPTED,
        )
        return [str(r["id"]) for r in rows]

    async def progress(self, run_id: str, *, seen: int, total: int) -> None:
        await self._db.execute(
            "UPDATE org_discovery_runs SET repos_seen = $2, repos_total = $3 WHERE id = $1", run_id, seen, total
        )

    async def finish_run(
        self, run_id: str, *, summary: dict[str, Any] | None = None, error: str | None = None
    ) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            f"""
            UPDATE org_discovery_runs
               SET status = $2, summary = $3::jsonb, error = $4, finished_at = NOW()
             WHERE id = $1
            RETURNING {_RUN_COLUMNS}
            """,
            run_id,
            "error" if error else "done",
            dict(summary or {}),
            error,
        )
        return _run(row) if row else None

    async def running_run(self, owner: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            f"SELECT {_RUN_COLUMNS} FROM org_discovery_runs WHERE owner = $1 AND status = 'running' ORDER BY started_at DESC LIMIT 1",
            owner.lower(),
        )
        return _run(row) if row else None

    async def list_runs(self, owner: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            f"SELECT {_RUN_COLUMNS} FROM org_discovery_runs WHERE owner = $1 ORDER BY started_at DESC LIMIT $2",
            owner.lower(),
            limit,
        )
        return [_run(r) for r in rows]
