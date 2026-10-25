"""Fakes for the leads tests: a forge client answering from a table, and an in-memory leads store."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import httpx

from embry0.execution.forge_client import Listing
from embry0.services.leads import Lead


class FakeForge:
    """GET/POST answers by path; a path not in the table is a 404."""

    def __init__(self, answers: dict[str, Any] | None = None, *, graphql: Any = None) -> None:
        self.answers = dict(answers or {})
        self.graphql = graphql
        self.asked: list[tuple[str, dict[str, Any]]] = []

    def _response(self, path: str) -> httpx.Response:
        answer = self.answers.get(path)
        if answer is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if isinstance(answer, tuple):
            return httpx.Response(answer[0], json=answer[1])
        return httpx.Response(200, json=answer)

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        self.asked.append((path, dict(kwargs.get("params") or {})))
        return self._response(path)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        self.asked.append((path, kwargs.get("json") or {}))
        if path == "/graphql" and self.graphql is not None:
            return httpx.Response(200, json=self.graphql)
        return self._response(path)

    async def get_repo(self, repo: str) -> dict[str, Any] | None:
        resp = await self.get(f"/repos/{repo}")
        return resp.json() if resp.status_code == 200 else None

    async def get_check_runs(self, repo: str, sha: str) -> dict[str, Any] | None:
        resp = await self.get(f"/repos/{repo}/commits/{sha}/check-runs")
        return resp.json() if resp.status_code == 200 else None

    async def get_combined_status(self, repo: str, sha: str) -> dict[str, Any] | None:
        resp = await self.get(f"/repos/{repo}/commits/{sha}/status")
        return resp.json() if resp.status_code == 200 else None

    async def paginate_listing(self, path: str, **kwargs: Any) -> Listing:
        answer = self.answers.get(path)
        if isinstance(answer, Listing):
            return answer
        return Listing(list(answer or []), complete=answer is not None)


class MemoryLeads:
    """``LeadsRepository`` in memory: what the loop stores, and when it closed it."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def upsert(self, leads: Sequence[Lead], *, now: datetime, areas: dict[str, str | None] | None = None) -> None:
        for lead in leads:
            old = self.rows.get(lead.id)
            self.rows[lead.id] = {
                "id": lead.id,
                "kind": lead.kind,
                "repo": lead.repo,
                "source_key": lead.source_key,
                "title": lead.title,
                "severity": lead.severity,
                "area": (areas or {}).get(lead.id),
                "state": "open",
                "first_seen": old["first_seen"] if old else now,
                "last_seen": now,
            }

    async def mark_gone(self, repo: str, kind: str, seen: Any, *, now: datetime) -> int:
        gone = 0
        for row in self.rows.values():
            if row["repo"] == repo and row["kind"] == kind and row["state"] == "open" and row["id"] not in seen:
                row["state"], gone = "gone", gone + 1
        return gone

    async def known_ids(self, repo: str, kind: str) -> set[str]:
        return {r["id"] for r in self.rows.values() if r["repo"] == repo and r["kind"] == kind}

    async def open_sources(self, repo: str, kind: str) -> dict[str, str]:
        return {
            r["id"]: r["source_key"]
            for r in self.rows.values()
            if r["repo"] == repo and r["kind"] == kind and r["state"] == "open"
        }

    async def new_since(self, repo: str, kind: str, since: datetime) -> int:
        return sum(1 for r in self.rows.values() if r["repo"] == repo and r["kind"] == kind and r["first_seen"] > since)

    def open(self, kind: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self.rows.values() if r["state"] == "open" and (kind is None or r["kind"] == kind)]
