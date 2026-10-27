"""KnowledgeMapsRepository (migration 124, spec 2026-10-27): uploads, marks and sends; the leads it leans on."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from embry0.services.knowledge_maps import source_key
from embry0.services.leads import Lead
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.knowledge_maps import KnowledgeMapsRepository
from embry0.storage.repositories.leads import LeadsRepository

T0 = datetime(2026, 10, 27, 15, tzinfo=UTC)
GRAPH = {"slug": "shop", "nodes": [{"id": "a"}, {"id": "b"}], "edges": [{"s": "a", "t": "b"}]}


async def _put(repo: KnowledgeMapsRepository, now: datetime, **over: object) -> None:
    await repo.put(
        slug="shop",
        title="Shop",
        repo="acme/shop",
        commit_sha="abc",
        source="a scanner",
        generated_at=T0,
        uploaded_by="key:scanner",
        graph=over.get("graph", GRAPH),  # type: ignore[arg-type]
        details={"a": {"narrative": {"does": "A thing."}}},
        now=now,
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_map_is_kept_as_its_last_upload_and_read_one_part_at_a_time(db_with_migrations: DatabasePool) -> None:
    repo = KnowledgeMapsRepository(db_with_migrations)
    await _put(repo, T0)
    await _put(repo, T0 + timedelta(hours=1), graph={**GRAPH, "nodes": [{"id": "a"}], "edges": []})
    meta = await repo.meta("shop")
    assert meta is not None and (meta["node_count"], meta["edge_count"], meta["uploaded_at"]) == (
        1,
        0,
        T0 + timedelta(hours=1),
    )
    stored = await repo.graph_text("shop")
    assert stored is not None and json.loads(stored[0])["nodes"] == [{"id": "a"}]
    assert await repo.detail("shop", "a") == {"narrative": {"does": "A thing."}}
    assert await repo.detail("shop", "b") is None
    assert [m["slug"] for m in await repo.summaries()] == ["shop"]
    assert await repo.delete("shop") is True
    assert await repo.graph_text("shop") is None and await repo.delete("shop") is False


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_each_person_has_one_mark_per_part_and_clearing_it_deletes_it(db_with_migrations: DatabasePool) -> None:
    repo = KnowledgeMapsRepository(db_with_migrations)
    await repo.set_mark("shop", "a", "ann", "keep", "", now=T0)
    await repo.set_mark("shop", "a", "ann", "bloat", "dead", now=T0 + timedelta(minutes=1))
    await repo.set_mark("shop", "a", "ann", "bloat", "dead", now=T0 + timedelta(minutes=5))
    await repo.set_mark("shop", "b", "bo", "unsure", "", now=T0)
    marks = await repo.marks("shop", "a")
    assert [(m["marked_by"], m["verdict"], m["note"]) for m in marks] == [("ann", "bloat", "dead")]
    assert marks[0]["marked_at"] == T0 + timedelta(minutes=1), "the same mark again keeps when it was made"
    assert len(await repo.marks("shop")) == 2
    await repo.clear_mark("shop", "a", "ann")
    assert await repo.marks("shop", "a") == []


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_work_view_is_each_parts_newest_send_with_its_job(db_with_migrations: DatabasePool) -> None:
    repo = KnowledgeMapsRepository(db_with_migrations)
    await db_with_migrations.execute(
        "INSERT INTO jobs (job_id, status, repo, task, pr_url) VALUES ('job-2', 'completed', 'acme/shop', 't', 'https://pr/2')"
    )
    common = {"slug": "shop", "repo": "acme/shop", "sent_by": "ann", "note": "", "lead_id": None}
    await repo.add_send(node_id="a", kind="analysis", job_id="job-1", error=None, now=T0, **common)  # type: ignore[arg-type]
    await repo.add_send(node_id="a", kind="remove", job_id="job-2", error=None, now=T0 + timedelta(minutes=1), **common)  # type: ignore[arg-type]
    await repo.add_send(node_id="b", kind="fix", job_id=None, error="the dispatch failed", now=T0, **common)  # type: ignore[arg-type]
    work = {w["node_id"]: w for w in await repo.work("shop")}
    assert (work["a"]["kind"], work["a"]["job_status"], work["a"]["pr_url"]) == ("remove", "completed", "https://pr/2")
    assert (work["b"]["job_id"], work["b"]["error"]) == (None, "the dispatch failed")
    assert [w["node_id"] for w in await repo.work("shop", "b")] == ["b"]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_one_maps_weeds_are_found_by_prefix_and_closed_one_at_a_time(db_with_migrations: DatabasePool) -> None:
    leads = LeadsRepository(db_with_migrations)

    def weed(key: str) -> Lead:
        return Lead(kind="weed", repo="acme/shop", source_key=key, title="t", summary="s", details="d", action="a")

    mine, other_map, scout = weed(source_key("shop", "a")), weed(source_key("shop-2", "a")), weed("ops:scripts/x")
    await leads.upsert([mine, other_map, scout], now=T0)
    assert [r["id"] for r in await leads.open_by_source_prefix("weed", "map:shop:")] == [mine.id]
    assert await leads.open_by_source_prefix("weed", "map:sho_:") == [], "an underscore in a slug is not a wildcard"
    assert await leads.close(mine.id, now=T0) is True
    assert await leads.close(mine.id, now=T0) is False
    assert {r["id"] for r in await leads.open_leads()} == {other_map.id, scout.id}, "closing one closes no other"
