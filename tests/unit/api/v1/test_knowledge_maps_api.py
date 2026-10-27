"""Knowledge maps API (spec 2026-10-27): upload, read, marks that keep a weed lead, and sending parts to agents."""

from __future__ import annotations

import gzip
import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from embry0.api.v1 import knowledge_maps as maps_api
from embry0.api.v1 import proposals as proposals_api
from embry0.services.knowledge_maps import FORMAT, source_key
from embry0.services.leads import lead_id
from embry0.services.proposals import Decision

HEADERS = {"X-Requested-With": "XMLHttpRequest"}
PROBE = "script:probe.mjs"
PROBE_LEAD = lead_id("weed", "acme/shop", source_key("shop", PROBE))


def _doc(nodes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "slug": "shop",
        "title": "Shop",
        "repo": "acme/shop",
        "generated_at": "2026-10-27T15:00:00Z",
        "nodes": nodes
        if nodes is not None
        else [
            {"id": PROBE, "kind": "script", "label": "probe.mjs", "path": "scripts/probe.mjs", "x": 1, "y": 2},
            {"id": "script:util.mjs", "kind": "script", "label": "util.mjs", "path": "scripts/util.mjs"},
            {"id": "table:orders", "kind": "table", "label": "orders"},
        ],
        "edges": [],
        "details": {PROBE: {"narrative": {"does": "Prints one row."}}},
    }


class FakeMaps:
    def __init__(self) -> None:
        self.maps: dict[str, dict[str, Any]] = {}
        self.marks_: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.sends: list[dict[str, Any]] = []
        self.jobs: dict[str, dict[str, Any]] = {}

    async def put(self, **row: Any) -> None:
        self.maps[row["slug"]] = {**row, "graph_text": json.dumps(row["graph"])}

    async def summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "slug": m["slug"],
                "title": m["title"],
                "repo": m["repo"],
                "uploaded_at": m["now"],
                "node_count": len(m["graph"]["nodes"]),
                "edge_count": 0,
            }
            for m in self.maps.values()
        ]

    async def graph_text(self, slug: str) -> tuple[str, datetime] | None:
        m = self.maps.get(slug)
        return (m["graph_text"], m["now"]) if m else None

    async def detail(self, slug: str, node_id: str) -> dict[str, Any] | None:
        return self.maps[slug]["details"].get(node_id)

    async def set_mark(self, slug: str, node_id: str, by: str, verdict: str, note: str, *, now: datetime) -> None:
        self.marks_[(slug, node_id, by)] = {
            "node_id": node_id,
            "marked_by": by,
            "verdict": verdict,
            "note": note,
            "marked_at": now,
        }

    async def clear_mark(self, slug: str, node_id: str, by: str) -> None:
        self.marks_.pop((slug, node_id, by), None)

    async def marks(self, slug: str, node_id: str | None = None) -> list[dict[str, Any]]:
        return [m for (s, n, _), m in self.marks_.items() if s == slug and node_id in (None, n)]

    async def add_send(self, **row: Any) -> int:
        self.sends.append(row)
        return len(self.sends)

    async def work(self, slug: str, node_id: str | None = None) -> list[dict[str, Any]]:
        newest: dict[str, dict[str, Any]] = {}
        for s in self.sends:
            if s["slug"] == slug and node_id in (None, s["node_id"]):
                newest[s["node_id"]] = {**s, "job_status": self.jobs.get(s["job_id"] or "", {}).get("status")}
        return list(newest.values())


class FakeLeads:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def upsert(self, leads: list[Any], *, now: datetime, areas: Any = None) -> None:
        for lead in leads:
            self.rows[lead.id] = {
                "id": lead.id,
                "kind": lead.kind,
                "repo": lead.repo,
                "source_key": lead.source_key,
                "title": lead.title,
                "summary": lead.summary,
                "details": lead.details,
                "url": lead.url,
                "severity": lead.severity,
                "effort": lead.effort,
                "action": lead.action,
                "area": None,
                "data": {**dict(lead.data), "paths": list(lead.paths), "labels": []},
                "state": "open",
                "first_seen": now,
                "last_seen": now,
            }

    async def close(self, lid: str, *, now: datetime) -> bool:
        row = self.rows.get(lid)
        if row is None or row["state"] != "open":
            return False
        row["state"] = "gone"
        return True

    async def get(self, lid: str) -> dict[str, Any] | None:
        return self.rows.get(lid)

    async def open_by_source_prefix(self, kind: str, prefix: str) -> list[dict[str, Any]]:
        return [
            r
            for r in self.rows.values()
            if r["kind"] == kind and r["state"] == "open" and r["source_key"].startswith(prefix)
        ]


class FakeProposals:
    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []
        self.answers: dict[str, Decision] = {}

    async def decisions(self) -> dict[str, Decision]:
        return self.answers

    async def record(self, **row: Any) -> None:
        self.recorded.append(row)
        self.answers[row["proposal_id"]] = Decision(
            row["proposal_id"], row["decision"], row["decided_by"], datetime.now()
        )


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.include_router(maps_api.router, prefix="/api/v1")
    st = app.state
    st.knowledge_maps_repo = FakeMaps()
    st.leads_repo = FakeLeads()
    st.proposals_repo = FakeProposals()
    st.config = SimpleNamespace(audit_log_path=None, dashboard_public_url="https://embry0.example.com")
    st.db = None
    st.issues_repo = MagicMock()
    st.issues_repo.create = AsyncMock(return_value="iss-new")
    st.issues_repo.update = AsyncMock()
    st.issue_executor = MagicMock()
    st.issue_executor.execute = AsyncMock(return_value="job-issue")
    st.issue_executor.dispatch_refusal = AsyncMock(return_value=None)
    st.jobs_repo = MagicMock()
    st.jobs_repo.create = AsyncMock(return_value="job-analysis")

    async def no_audit(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(proposals_api, "emit_audit", no_audit)
    return app


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _upload(client: AsyncClient, doc: dict[str, Any] | None = None) -> Any:
    body = gzip.compress(json.dumps(doc or _doc()).encode())
    return await client.put(
        "/api/v1/maps/shop",
        content=body,
        headers={**HEADERS, "Content-Encoding": "gzip", "Content-Type": "application/json"},
    )


async def test_an_upload_is_stored_and_served_as_it_is_with_an_etag(app: FastAPI) -> None:
    async with await _client(app) as client:
        put = await _upload(client)
        got = await client.get("/api/v1/maps/shop")
        again = await client.get("/api/v1/maps/shop", headers={"If-None-Match": got.headers["etag"]})
        listed = (await client.get("/api/v1/maps")).json()
        bad = await client.put("/api/v1/maps/shop", content=json.dumps({"format": "x"}), headers=HEADERS)
        garbled = await client.put(
            "/api/v1/maps/shop", content=b"not gzip", headers={**HEADERS, "Content-Encoding": "gzip"}
        )
        missing = await client.get("/api/v1/maps/nope")
    assert put.status_code == 200 and put.json() == {
        "slug": "shop",
        "nodes": 3,
        "edges": 0,
        "leads": 0,
        "leads_closed": 0,
    }
    assert [n["id"] for n in got.json()["nodes"]] == [PROBE, "script:util.mjs", "table:orders"]
    assert again.status_code == 304
    assert listed["maps"][0]["slug"] == "shop" and listed["maps"][0]["nodes"] == 3
    assert bad.status_code == 422 and "format must be" in bad.json()["detail"]
    assert garbled.status_code == 400 and missing.status_code == 404


async def test_a_bloat_mark_keeps_a_weed_lead_open_and_taking_it_back_closes_it(app: FastAPI) -> None:
    async with await _client(app) as client:
        await _upload(client)
        marked = (
            await client.post(
                "/api/v1/maps/shop/marks",
                json={"node_id": PROBE, "verdict": "bloat", "note": "dead", "by": "ann"},
                headers=HEADERS,
            )
        ).json()
        lead = dict(app.state.leads_repo.rows[PROBE_LEAD])
        counts = (await client.get("/api/v1/maps/shop/marks", params={"by": "ann"})).json()
        kept = (
            await client.post(
                "/api/v1/maps/shop/marks", json={"node_id": PROBE, "verdict": "keep", "by": "ann"}, headers=HEADERS
            )
        ).json()
        no_repo = (
            await client.post(
                "/api/v1/maps/shop/marks",
                json={"node_id": "table:orders", "verdict": "bloat", "by": "ann"},
                headers=HEADERS,
            )
        ).json()
        unknown = await client.post(
            "/api/v1/maps/shop/marks", json={"node_id": "nope", "verdict": "bloat", "by": "ann"}, headers=HEADERS
        )
    assert marked["lead_id"] == PROBE_LEAD and marked["marks"][0]["verdict"] == "bloat"
    assert (lead["kind"], lead["title"], lead["data"]["source"]) == ("weed", "Remove probe.mjs", "map")
    assert lead["url"] == "https://embry0.example.com/maps/shop?part=script%3Aprobe.mjs"
    assert counts["marks"][PROBE] == {"bloat": 1, "keep": 0, "unsure": 0, "mine": "bloat"}
    assert kept["lead_id"] is None and app.state.leads_repo.rows[PROBE_LEAD]["state"] == "gone"
    assert no_repo["lead_id"] is None, "a part in no repository has no lead"
    assert unknown.status_code == 404


async def test_a_part_that_left_the_map_closes_its_lead_on_the_next_upload(app: FastAPI) -> None:
    async with await _client(app) as client:
        await _upload(client)
        await client.post(
            "/api/v1/maps/shop/marks", json={"node_id": PROBE, "verdict": "bloat", "by": "ann"}, headers=HEADERS
        )
        still = (await _upload(client)).json()
        removed = (
            await _upload(
                client, _doc(nodes=[{"id": "script:util.mjs", "label": "util.mjs", "path": "scripts/util.mjs"}])
            )
        ).json()
    assert still["leads"] == 1 and still["leads_closed"] == 0
    assert removed["leads_closed"] == 1 and app.state.leads_repo.rows[PROBE_LEAD]["state"] == "gone"


async def test_sending_a_removal_records_the_bloat_mark_accepts_the_lead_and_starts_it(app: FastAPI) -> None:
    async with await _client(app) as client:
        await _upload(client)
        response = await client.post(
            "/api/v1/maps/shop/send",
            json={"node_ids": [PROBE], "kind": "remove", "by": "ann", "note": "dead probe"},
            headers=HEADERS,
        )
        work = (await client.get("/api/v1/maps/shop/work")).json()["work"]
        part = (await client.get(f"/api/v1/maps/shop/parts/{PROBE}")).json()
    assert response.status_code == 202, response.text
    assert response.json()["results"] == [
        {"node_id": PROBE, "kind": "remove", "lead_id": PROBE_LEAD, "job_id": "job-issue", "error": None}
    ]
    assert app.state.knowledge_maps_repo.marks_[("shop", PROBE, "ann")]["verdict"] == "bloat"
    created = app.state.issues_repo.create.await_args.kwargs
    assert created["labels"] == ["embry0-lead", "lead:weed"] and created["repo"] == "acme/shop"
    assert created["body"].startswith("Remove probe.mjs from acme/shop.") and "do not re-assess" in created["body"]
    assert app.state.proposals_repo.recorded[0]["decision"] == "accept"
    assert app.state.proposals_repo.recorded[0]["decided_by"] == "ann"
    assert work[PROBE]["kind"] == "remove" and work[PROBE]["job_url"] == "https://embry0.example.com/jobs/job-issue"
    assert part["lead"] == {"id": PROBE_LEAD, "state": "open"} and part["work"]["job_id"] == "job-issue"
    assert part["detail"] == {"narrative": {"does": "Prints one row."}} and part["repo"] == "acme/shop"


async def test_a_removal_sent_again_after_its_proposal_was_answered_still_starts(app: FastAPI) -> None:
    async with await _client(app) as client:
        await _upload(client)
        body = {"node_ids": [PROBE], "kind": "remove", "by": "ann"}
        await client.post("/api/v1/maps/shop/send", json=body, headers=HEADERS)
        second = (await client.post("/api/v1/maps/shop/send", json=body, headers=HEADERS)).json()
    assert second["sent"] == 1 and len(app.state.proposals_repo.recorded) == 1, "the yes is recorded once"


async def test_without_a_kind_each_part_goes_for_what_its_senders_mark_says(app: FastAPI) -> None:
    async with await _client(app) as client:
        await _upload(client)
        await client.post(
            "/api/v1/maps/shop/marks", json={"node_id": PROBE, "verdict": "bloat", "by": "ann"}, headers=HEADERS
        )
        result = (
            await client.post(
                "/api/v1/maps/shop/send",
                json={"node_ids": [PROBE, "script:util.mjs", "table:orders", "nope"], "by": "ann"},
                headers=HEADERS,
            )
        ).json()
    by_part = {r["node_id"]: r for r in result["results"]}
    assert by_part[PROBE]["kind"] == "remove" and by_part[PROBE]["job_id"] == "job-issue"
    assert by_part["script:util.mjs"]["kind"] == "analysis" and by_part["script:util.mjs"]["job_id"] == "job-analysis"
    job = app.state.jobs_repo.create.await_args.kwargs
    assert (
        job["pipeline_template"] == "analysis-default" and job["dispatched_by"] == "ann" and job["repo"] == "acme/shop"
    )
    assert "read-only" in job["task"] and "ann sent it from a knowledge map." in job["task"]
    assert app.state.issue_executor.submit_job.call_args.kwargs["kind"] == "map"
    assert (
        "no repository" in by_part["table:orders"]["error"] and by_part["nope"]["error"] == "no such part on this map"
    )
    assert (result["sent"], result["failed"]) == (2, 2)
    sends = app.state.knowledge_maps_repo.sends
    assert {s["node_id"] for s in sends} == {PROBE, "script:util.mjs"}, (
        "a part with no repository or no such part is not recorded"
    )


async def test_a_fix_opens_an_issue_labelled_for_the_map_and_runs_it(app: FastAPI) -> None:
    async with await _client(app) as client:
        await _upload(client)
        result = (
            await client.post(
                "/api/v1/maps/shop/send",
                json={"node_ids": ["script:util.mjs"], "kind": "fix", "by": "ann"},
                headers=HEADERS,
            )
        ).json()
    assert result["results"][0]["job_id"] == "job-issue"
    created = app.state.issues_repo.create.await_args.kwargs
    assert created["title"] == "Fix util.mjs" and created["labels"] == ["embry0-map", "map:shop"]
    assert created["created_by"] == "map-send" and created["body"].startswith(
        "Fix what the evidence below shows about util.mjs"
    )


async def test_a_refused_dispatch_is_recorded_with_its_reason(app: FastAPI) -> None:
    app.state.issue_executor.dispatch_refusal = AsyncMock(
        return_value=SimpleNamespace(public_message="the daily budget is spent")
    )
    async with await _client(app) as client:
        await _upload(client)
        result = (
            await client.post(
                "/api/v1/maps/shop/send",
                json={"node_ids": ["script:util.mjs"], "kind": "analysis", "by": "ann"},
                headers=HEADERS,
            )
        ).json()
    assert result["results"][0]["error"] == "the daily budget is spent"
    assert app.state.knowledge_maps_repo.sends[0]["error"] == "the daily budget is spent"


async def test_a_whole_system_keeps_its_mark_on_the_map_and_is_never_sent_for_removal(app: FastAPI) -> None:
    system = {"id": "sys:billing", "kind": "system", "label": "billing", "repo": "acme/billing"}
    system_lead = lead_id("weed", "acme/billing", source_key("shop", "sys:billing"))
    async with await _client(app) as client:
        await _upload(client, _doc(nodes=[*_doc()["nodes"], system]))
        # a lead opened for it before the rule closes at the next mark
        app.state.leads_repo.rows[system_lead] = {
            "id": system_lead,
            "kind": "weed",
            "state": "open",
            "source_key": source_key("shop", "sys:billing"),
        }
        marked = (
            await client.post(
                "/api/v1/maps/shop/marks",
                json={"node_id": "sys:billing", "verdict": "bloat", "by": "ann"},
                headers=HEADERS,
            )
        ).json()
        removal = (
            await client.post(
                "/api/v1/maps/shop/send",
                json={"node_ids": ["sys:billing"], "kind": "remove", "by": "ann"},
                headers=HEADERS,
            )
        ).json()
        default = (
            await client.post(
                "/api/v1/maps/shop/send", json={"node_ids": ["sys:billing"], "by": "ann"}, headers=HEADERS
            )
        ).json()
        part = (await client.get("/api/v1/maps/shop/parts/sys:billing")).json()
    assert marked["lead_id"] is None and app.state.leads_repo.rows[system_lead]["state"] == "gone"
    assert "not a file in one" in removal["results"][0]["error"] and removal["results"][0]["job_id"] is None
    assert default["results"][0]["kind"] == "analysis" and default["results"][0]["job_id"] == "job-analysis"
    assert part["removable"] is False and part["repo"] == "acme/billing"
