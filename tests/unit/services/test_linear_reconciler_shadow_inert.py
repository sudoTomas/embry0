"""Shadow mode is inert — proven on Postgres, table by table (#284).

The default mode of the reconciler is ``shadow``, and the whole point of
shipping it that way is the promise "it decides and changes nothing". This
test holds the promise to the database and to the wire: the real
``LinearSyncService`` (over a fake HTTP transport), the real ``BudgetGate`` and
the real repositories sweep a realistic candidate set three times, and then

* every table in the schema has the same row count and content hash as before;
* Linear saw GraphQL **queries** only — not one mutation.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from embry0.orchestration.budget_gate import BudgetGate, DispatchDecision
from embry0.services.linear_reconciler import ACTOR, LinearDispatchReconciler, ReconcilerSettings
from embry0.services.linear_sync import LinearSyncService
from embry0.settings.sections.budget import BUDGET
from embry0.settings.store import SettingsStore, section_reader
from embry0.storage.repositories.budget_spend import BudgetSpendRepository
from embry0.storage.repositories.dispatch_ledger import DispatchLedgerRepository
from embry0.storage.repositories.issues import IssuesRepository
from embry0.storage.repositories.jobs import JobsRepository
from embry0.trackers import TriggerContext

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _node(key: str, *, state: str = "started", summary: str = "repo: o/r") -> dict[str, Any]:
    return {
        "id": f"uuid-{key}",
        "identifier": key,
        "title": f"Ticket {key}",
        "description": "body",
        "url": f"https://linear.app/x/issue/{key}",
        "labels": {"nodes": [{"name": "embry0"}]},
        "state": {"type": state},
        "project": {"id": "p", "name": "Proj", "description": summary, "content": None},
        "team": {"key": "ENG"},
    }


_TICKETS = [
    _node("ENG-1"),  # untracked -> would dispatch
    _node("ENG-2", summary=""),  # unmapped project
    _node("ENG-3", state="duplicate"),  # the list filter is not trusted: state is re-checked
    _node("ENG-4"),  # tracked, job running
    _node("ENG-5"),  # tracked, failed, label re-applied after the failure -> would dispatch
    _node("ENG-6"),  # tracked, completed job whose PR awaits review (a failed job's PR is continued, #349)
    _node("ENG-7"),  # tracked, stale `triaging` without a job -> would reopen, then dispatch
    _node("ENG-8"),  # tracked, open, no job -> would dispatch
]


class _Linear:
    """A fake Linear behind a real httpx client: answers queries, records everything."""

    def __init__(self) -> None:
        self.operations: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"].strip()
        self.operations.append(query.split("(")[0].strip())
        if query.startswith("mutation"):
            return httpx.Response(200, json={"data": {"commentCreate": {"success": True}}})
        if "TriggerLabeledIssues" in query:
            page = {"nodes": _TICKETS, "pageInfo": {"hasNextPage": False, "endCursor": None}}
            return httpx.Response(200, json={"data": {"issues": page}})
        assert "IssueLabelHistory" in query, query
        history = {
            "nodes": [{"createdAt": "2099-01-01T00:00:00.000Z", "addedLabelIds": ["lbl-e"]}],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
        issue = {"labels": {"nodes": [{"id": "lbl-e", "name": "embry0"}]}, "history": history}
        return httpx.Response(200, json={"data": {"issue": issue}})


class _GatedExecutor:
    """The executor surface the dispatch path touches, over the real BudgetGate."""

    def __init__(self, gate: BudgetGate) -> None:
        self._gate = gate
        self.executed: list[str] = []

    async def dispatch_refusal(self, *, actor: str | None = None) -> DispatchDecision | None:
        decision = await self._gate.check(actor=actor)
        return None if decision.allowed else decision

    async def execute(self, issue_id: str, **kw: Any) -> str:
        self.executed.append(issue_id)
        raise AssertionError("shadow mode must never start a job")


async def _snapshot(db: Any) -> dict[str, tuple[int, str]]:
    tables = await db.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    snapshot: dict[str, tuple[int, str]] = {}
    for record in tables:
        name = record["table_name"]
        row = await db.fetchrow(
            f"SELECT COUNT(*) AS n, md5(COALESCE(string_agg(t::text, '|' ORDER BY t::text), '')) AS h FROM \"{name}\" t"  # noqa: S608 - table names come from information_schema
        )
        snapshot[name] = (int(row["n"]), str(row["h"]))
    return snapshot


async def _seed(db: Any, issues: IssuesRepository, jobs: JobsRepository) -> None:
    async def _tracked(key: str, status: str, job_status: str | None, pr_url: str | None = None) -> None:
        issue_id = await issues.create(title=key, repo="o/r", tracker="linear", tracker_key=key)
        if job_status is not None:
            job_id = await jobs.create(repo="o/r", task="t", issue_id=issue_id, dispatched_by="alice")
            await jobs.update(job_id, status="running")
            if job_status != "running":
                await jobs.update(job_id, status=job_status, pr_url=pr_url)
        if status != "open":
            await issues.update(issue_id, status=status)

    await _tracked("ENG-4", "triaging", "running")
    await _tracked("ENG-5", "open", "failed")
    await _tracked("ENG-6", "open", "completed", pr_url="https://github.com/o/r/pull/6")
    await _tracked("ENG-7", "triaging", None)
    await _tracked("ENG-8", "open", None)
    await db.execute("UPDATE issues SET updated_at = NOW() - INTERVAL '2 hours' WHERE tracker_key = 'ENG-7'")


async def test_three_shadow_sweeps_change_no_table_and_send_no_mutation(db_with_migrations) -> None:
    db = db_with_migrations
    issues, jobs = IssuesRepository(db), JobsRepository(db)
    await _seed(db, issues, jobs)
    linear = _Linear()
    executor = _GatedExecutor(BudgetGate(section_reader(SettingsStore(db), BUDGET), BudgetSpendRepository(db)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(linear)) as http:
        sync = LinearSyncService("lin_api_key", {}, http_client=http)

        def _context(labels: set[str]) -> TriggerContext:
            return TriggerContext(
                issues_repo=issues,
                issue_executor=executor,
                jobs_repo=jobs,
                dispatch_ledger=DispatchLedgerRepository(db),
                trigger_labels=labels,
                actor=ACTOR,
            )

        async def _resolve_sync() -> LinearSyncService:
            return sync

        async def _resolve_labels() -> set[str]:
            return {"embry0"}

        reconciler = LinearDispatchReconciler(
            resolve_sync=_resolve_sync,
            resolve_trigger_labels=_resolve_labels,
            make_context=_context,
            settings=ReconcilerSettings(),  # the shipped defaults: shadow
        )

        before = await _snapshot(db)
        with capture_logs() as logs:
            results = [await reconciler.run_once() for _ in range(3)]
        after = await _snapshot(db)

    assert len(before) > 20  # the whole schema was hashed, not a hand-picked few tables
    assert after == before
    assert executor.executed == []
    assert linear.operations and all(op.startswith("query") for op in linear.operations), linear.operations

    # ... and it still took every decision — identically on each sweep, logged once.
    for result in results:
        assert result["mode"] == "shadow" and result["dispatched"] == [] and result["reopened"] == []
        assert result["would_dispatch"] == ["ENG-1", "ENG-5", "ENG-7", "ENG-8"]
        assert result["would_reopen"] == ["ENG-7"]
    decisions = {
        e["identifier"]: (e["event"].removeprefix("linear_reconcile_"), e["reason"])
        for e in logs
        if e["event"].startswith("linear_reconcile_would") and e["event"] != "linear_reconcile_would_reopen"
    }
    assert decisions == {
        "ENG-1": ("would_dispatch", "untracked"),
        "ENG-2": ("would_skip", "unmapped_repo"),
        "ENG-3": ("would_skip", "state:duplicate"),
        "ENG-4": ("would_skip", "dedupe:live_job"),
        "ENG-5": ("would_dispatch", "label_reapplied_after_failed_job"),
        "ENG-6": ("would_skip", "dedupe:open_pr"),
        "ENG-7": ("would_dispatch", "tracked_without_job"),
        "ENG-8": ("would_dispatch", "tracked_without_job"),
    }
    assert sum(e["event"].startswith("linear_reconcile_would") for e in logs) == 9  # 8 tickets + 1 reopen, once each
