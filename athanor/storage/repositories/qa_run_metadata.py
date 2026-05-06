"""Repository for qa_run_metadata — affected-set / dep-graph snapshot per QA run.

One row per QA run, written by ``qa_orchestrator_node`` after the fan-out
resolves. Surfaces the run-level diff decision to the dashboard's
"Affected set" view (Phase 5D):

  - ``apps_to_qa``: the apps that actually ran
  - ``apps_skipped``: every declared app NOT in ``apps_to_qa``
  - ``force_all_apps``: whether the diff path was bypassed
  - ``changed_files``: the diff that drove selection
  - ``base_branch``: the diff base (typically the PR target branch)
  - ``dep_graph``: list of ``{"source": ..., "target": ...}`` package
    edges (currently empty — extracting the workspace dep graph is a
    follow-up; the list view works without it)
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from athanor.storage.database import DatabasePool


@dataclass(frozen=True, slots=True)
class QARunMetadata:
    """One row from qa_run_metadata.

    ``dep_graph`` is a list of ``{"source": <pkg>, "target": <pkg>}`` dicts.
    Empty for runs persisted by Phase 5D (provider does not yet expose
    edges); reserved for a follow-up that wires the npm-workspaces-turbo
    dep graph through.
    """

    job_id: str
    apps_to_qa: list[str]
    apps_skipped: list[str]
    force_all_apps: bool
    changed_files: list[str]
    base_branch: str
    dep_graph: list[dict[str, str]]


class QARunMetadataRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def upsert(
        self,
        *,
        job_id: str,
        apps_to_qa: list[str],
        apps_skipped: list[str],
        force_all_apps: bool,
        changed_files: list[str],
        base_branch: str,
        dep_graph: list[dict[str, str]],
    ) -> None:
        """Insert or replace the row for ``job_id``.

        Idempotent: re-running QA on the same job (e.g. a retry path that
        re-enters qa_orchestrator_node) replaces the metadata rather than
        duplicating. ``job_id`` is the PK + FK to jobs(job_id).
        """
        sql = """
        INSERT INTO qa_run_metadata
            (job_id, apps_to_qa, apps_skipped, force_all_apps,
             changed_files, base_branch, dep_graph)
        VALUES ($1, $2::text[], $3::text[], $4, $5::text[], $6, $7::jsonb)
        ON CONFLICT (job_id) DO UPDATE SET
            apps_to_qa = EXCLUDED.apps_to_qa,
            apps_skipped = EXCLUDED.apps_skipped,
            force_all_apps = EXCLUDED.force_all_apps,
            changed_files = EXCLUDED.changed_files,
            base_branch = EXCLUDED.base_branch,
            dep_graph = EXCLUDED.dep_graph
        """
        await self._db.execute(
            sql,
            job_id,
            list(apps_to_qa),
            list(apps_skipped),
            bool(force_all_apps),
            list(changed_files),
            base_branch,
            json.dumps(dep_graph),
        )

    async def get(self, job_id: str) -> QARunMetadata | None:
        """Fetch the metadata row for ``job_id``, or ``None`` if missing."""
        sql = """
        SELECT job_id, apps_to_qa, apps_skipped, force_all_apps,
               changed_files, base_branch, dep_graph
        FROM qa_run_metadata
        WHERE job_id = $1
        """
        row = await self._db.fetchrow(sql, job_id)
        if row is None:
            return None

        # dep_graph is JSONB — asyncpg returns it as a Python str OR (with the
        # default codec) a dict/list. Normalise to list[dict[str, str]].
        raw = row["dep_graph"]
        if raw is None:
            dep_graph: list[dict[str, str]] = []
        elif isinstance(raw, str):
            dep_graph = json.loads(raw) or []
        else:
            dep_graph = list(raw)

        return QARunMetadata(
            job_id=row["job_id"],
            apps_to_qa=list(row["apps_to_qa"]),
            apps_skipped=list(row["apps_skipped"]),
            force_all_apps=bool(row["force_all_apps"]),
            changed_files=list(row["changed_files"]),
            base_branch=row["base_branch"],
            dep_graph=dep_graph,
        )
