"""Audit log read/retention repository (RAV-1056 Phase C).

The write path stays in ``embry0.audit`` (fire-and-forget, errors swallowed);
this repository is the read side: the global audit API's filtered listing and
the retention sweeper's pruning. ``audit_log`` deliberately has no FKs —
audit rows outlive every entity they reference.

A read for a key with a repository allowlist is narrowed in its query, before
it pages and counts (:class:`AuditNarrowing`): each row is read for the
repositories and owners it is about, where its action's entry in
:mod:`embry0.audit.subjects` says it names them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from embry0.audit.actions import names_of
from embry0.audit.subjects import actions_by_source
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import job_repository_sql


@dataclass(frozen=True, slots=True)
class AuditNarrowing:
    """What a key with a repository allowlist reads of the audit log: the rows about repositories it covers.

    A row is read when it names at least one repository and the key covers
    every one it names (``repos``: the lower-cased slugs it covers, old names
    included), whoever wrote it. A row about no repository, an owner's or the
    deployment's, is not read, whoever wrote it: an actor string is not one
    key's (a key minted from a session is recorded under its person's email,
    and keys of one name share ``key:<name>``).
    """

    repos: Sequence[str]


#: A row's details, in the queries below.
_D = "audit_log.details"
#: A settings scope is ``repository:<slug>``, ``organization:<owner>`` or ``deployment`` (embry0.settings.scope).
_REPOSITORY_SCOPE = "repository:"


def _job_repo(field: str) -> str:
    return f"(SELECT {job_repository_sql('j')} FROM jobs j WHERE j.job_id = {_D}->>'{field}')"


#: Where a row names a repository, in SQL over the ``audit_log`` row, by :data:`embry0.audit.subjects.RepoSource`.
_REPO_SQL: dict[str, str] = {
    "repo": f"{_D}->>'repo'",
    "settings_scope": (
        f"CASE WHEN {_D}->>'scope' LIKE '{_REPOSITORY_SCOPE}%'"
        f" THEN substr({_D}->>'scope', {len(_REPOSITORY_SCOPE) + 1}) END"
    ),
    "binding_scope": f"CASE WHEN {_D}->>'scope_kind' = 'repo' THEN {_D}->>'scope_value' END",
    # The column, or the details of a row that names an issue without being one of its rows (a case's acceptance).
    "issue": f"(SELECT i.repo FROM issues i WHERE i.id = COALESCE(audit_log.issue_id, {_D}->>'issue_id'))",
    "job": _job_repo("job_id"),
    "followup_job": _job_repo("followup_job_id"),
    "approval": f"(SELECT m.repo FROM merge_approvals m WHERE m.id = {_D}->>'approval_id')",
    "handoff": f"(SELECT h.repo FROM handoffs h WHERE h.id = {_D}->>'handoff_id')",
}


def _narrowed(narrowing: AuditNarrowing, args: list[Any]) -> tuple[str, str]:
    """The join reading the repositories each row names (lower-cased, without the empty and the missing), and the
    clause keeping the rows ``narrowing`` reads."""
    parts = []
    for source, actions in sorted(actions_by_source().items()):
        args.append(actions)
        parts.append(
            f"CASE WHEN audit_log.action = ANY(${len(args)}::text[]) THEN NULLIF(lower({_REPO_SQL[source]}), '') END"
        )
    join = f" CROSS JOIN LATERAL (SELECT array_remove(ARRAY[{', '.join(parts)}]::text[], NULL) AS repos) s"
    args.append([slug.lower() for slug in narrowing.repos])
    return join, f"(cardinality(s.repos) > 0 AND s.repos <@ ${len(args)}::text[])"


def _where(
    actors: Sequence[str] | None,
    action: str | None,
    since: datetime | None,
    until: datetime | None,
    hidden_actors: Sequence[str] = (),
    narrowing: AuditNarrowing | None = None,
) -> tuple[str, list[Any]]:
    """What follows ``FROM audit_log`` in a filtered read: with ``narrowing``, the join reading what each row is
    about, then the ``WHERE``."""
    clauses: list[str] = []
    args: list[Any] = []
    if actors is not None:
        args.append(list(actors))
        clauses.append(f"actor = ANY(${len(args)})")
    if hidden_actors:
        args.append(list(hidden_actors))
        clauses.append(f"actor <> ALL(${len(args)})")
    if action is not None:
        # A renamed action's filter also finds the rows written under its old names.
        names = names_of(action)
        args.append(action if len(names) == 1 else list(names))
        clauses.append(f"action = ${len(args)}" if len(names) == 1 else f"action = ANY(${len(args)})")
    if since is not None:
        args.append(since)
        clauses.append(f"created_at >= ${len(args)}")
    if until is not None:
        args.append(until)
        clauses.append(f"created_at < ${len(args)}")
    join = ""
    if narrowing is not None:
        join, clause = _narrowed(narrowing, args)
        clauses.append(clause)
    if not clauses:
        return join, args
    return join + " WHERE " + " AND ".join(clauses), args


class AuditRepository:
    def __init__(self, db: DatabasePool):
        self._db = db

    async def list_entries(
        self,
        *,
        actors: Sequence[str] | None = None,
        action: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
        hidden_actors: Sequence[str] = (),
        narrowing: AuditNarrowing | None = None,
    ) -> list[dict[str, Any]]:
        """Newest-first page of audit rows; ``actors=None`` = unrestricted, ``hidden_actors`` left out, and only what
        ``narrowing`` reads when given.

        Explicit column list — trace_id is ops-internal and never leaves the
        repository. ``id DESC`` tiebreak keeps same-timestamp rows stable
        across pages.
        """
        where, args = _where(actors, action, since, until, hidden_actors, narrowing)
        args.extend((limit, offset))
        rows = await self._db.fetch(
            f"""
            SELECT id, action, actor, details, issue_id, created_at
            FROM audit_log{where}
            ORDER BY created_at DESC, id DESC
            LIMIT ${len(args) - 1} OFFSET ${len(args)}
            """,
            *args,
        )
        return [dict(r) for r in rows]

    async def count_entries(
        self,
        *,
        actors: Sequence[str] | None = None,
        action: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        hidden_actors: Sequence[str] = (),
        narrowing: AuditNarrowing | None = None,
    ) -> int:
        where, args = _where(actors, action, since, until, hidden_actors, narrowing)
        row = await self._db.fetchrow(f"SELECT COUNT(*) AS n FROM audit_log{where}", *args)
        return int(row["n"]) if row else 0

    async def actions_present(
        self,
        *,
        actors: Sequence[str] | None = None,
        hidden_actors: Sequence[str] = (),
        narrowing: AuditNarrowing | None = None,
    ) -> set[str]:
        """The action names the rows a read with these filters lists carry: what an Audit filter offers a caller who
        reads only some rows."""
        where, args = _where(actors, None, None, None, hidden_actors, narrowing)
        rows = await self._db.fetch(f"SELECT DISTINCT action FROM audit_log{where}", *args)
        return {str(r["action"]) for r in rows}

    async def latest_entry(self, action: str, *, detail_key: str, detail_value: str) -> dict[str, Any] | None:
        """The newest ``action`` row whose ``details[detail_key]`` equals ``detail_value``, or None.

        idx_audit_log_action covers (action, created_at DESC), the filter and the
        order. Same explicit column list as ``list_entries``.
        """
        row = await self._db.fetchrow(
            """
            SELECT id, action, actor, details, issue_id, created_at
            FROM audit_log
            WHERE action = $1 AND details ->> $2::text = $3
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            action,
            detail_key,
            detail_value,
        )
        return dict(row) if row else None

    async def delete_older_than(self, cutoff: datetime) -> int:
        result = await self._db.execute("DELETE FROM audit_log WHERE created_at < $1", cutoff)
        # result is a command tag like "DELETE 5"
        try:
            return int(str(result).split()[-1])
        except (ValueError, IndexError):
            return 0
