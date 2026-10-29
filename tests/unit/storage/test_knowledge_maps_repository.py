"""KnowledgeMapsRepository (migrations 124 and 133): uploads, marks and sends of each owner's maps; their leads."""

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


async def _put(repo: KnowledgeMapsRepository, now: datetime, owner: str | None = "acme", **over: object) -> None:
    await repo.put(
        slug="shop",
        owner=owner,
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
    meta = await repo.meta("shop", owner="acme")
    assert meta is not None and (meta["node_count"], meta["edge_count"], meta["uploaded_at"]) == (
        1,
        0,
        T0 + timedelta(hours=1),
    )
    stored = await repo.graph_text("shop", owner="acme")
    assert stored is not None and json.loads(stored[0])["nodes"] == [{"id": "a"}]
    assert await repo.detail("shop", "a", owner="acme") == {"narrative": {"does": "A thing."}}
    assert await repo.detail("shop", "b", owner="acme") is None
    assert [(m["slug"], m["owner"]) for m in await repo.summaries()] == [("shop", "acme")]
    assert await repo.delete("shop", owner="acme") is True
    assert await repo.graph_text("shop", owner="acme") is None and await repo.delete("shop", owner="acme") is False


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_each_person_has_one_mark_per_part_and_clearing_it_deletes_it(db_with_migrations: DatabasePool) -> None:
    repo = KnowledgeMapsRepository(db_with_migrations)
    await repo.set_mark("shop", "a", "ann", "keep", "", owner=None, now=T0)
    await repo.set_mark("shop", "a", "ann", "bloat", "dead", owner=None, now=T0 + timedelta(minutes=1))
    await repo.set_mark("shop", "a", "ann", "bloat", "dead", owner=None, now=T0 + timedelta(minutes=5))
    await repo.set_mark("shop", "b", "bo", "unsure", "", owner=None, now=T0)
    marks = await repo.marks("shop", "a", owner=None)
    assert [(m["marked_by"], m["verdict"], m["note"]) for m in marks] == [("ann", "bloat", "dead")]
    assert marks[0]["marked_at"] == T0 + timedelta(minutes=1), "the same mark again keeps when it was made"
    assert len(await repo.marks("shop", owner=None)) == 2
    await repo.clear_mark("shop", "a", "ann", owner=None)
    assert await repo.marks("shop", "a", owner=None) == []


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_work_view_is_each_parts_newest_send_with_its_job(db_with_migrations: DatabasePool) -> None:
    repo = KnowledgeMapsRepository(db_with_migrations)
    await db_with_migrations.execute(
        "INSERT INTO jobs (job_id, status, repo, task, pr_url) VALUES ('job-2', 'completed', 'acme/shop', 't', 'https://pr/2')"
    )
    common = {"slug": "shop", "owner": "acme", "repo": "acme/shop", "sent_by": "ann", "note": "", "lead_id": None}
    await repo.add_send(node_id="a", kind="analysis", job_id="job-1", error=None, now=T0, **common)  # type: ignore[arg-type]
    await repo.add_send(node_id="a", kind="remove", job_id="job-2", error=None, now=T0 + timedelta(minutes=1), **common)  # type: ignore[arg-type]
    await repo.add_send(node_id="b", kind="fix", job_id=None, error="the dispatch failed", now=T0, **common)  # type: ignore[arg-type]
    work = {w["node_id"]: w for w in await repo.work("shop", owner="acme")}
    assert (work["a"]["kind"], work["a"]["job_status"], work["a"]["pr_url"]) == ("remove", "completed", "https://pr/2")
    assert (work["b"]["job_id"], work["b"]["error"]) == (None, "the dispatch failed")
    assert [w["node_id"] for w in await repo.work("shop", "b", owner="acme")] == ["b"]


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


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_two_owners_each_keep_their_own_map_marks_and_sends_under_one_slug(
    db_with_migrations: DatabasePool,
) -> None:
    repo = KnowledgeMapsRepository(db_with_migrations)
    await _put(repo, T0, owner="acme")
    await _put(repo, T0 + timedelta(hours=1), owner="zeta", graph={**GRAPH, "nodes": [{"id": "z"}], "edges": []})
    await _put(repo, T0, owner=None, graph={**GRAPH, "nodes": [], "edges": []})
    assert await repo.owners("shop") == ["acme", "zeta", None], "each owner's upload must be its own map"
    acme, zeta = await repo.graph_text("shop", owner="acme"), await repo.graph_text("shop", owner="zeta")
    assert acme is not None and [n["id"] for n in json.loads(acme[0])["nodes"]] == ["a", "b"], (
        "zeta's upload must not replace acme's map"
    )
    assert zeta is not None and [n["id"] for n in json.loads(zeta[0])["nodes"]] == ["z"]
    await repo.set_mark("shop", "a", "ann", "bloat", "", owner="acme", now=T0)
    await repo.set_mark("shop", "a", "ann", "keep", "", owner="zeta", now=T0)
    assert [m["verdict"] for m in await repo.marks("shop", owner="acme")] == ["bloat"]
    assert [m["verdict"] for m in await repo.marks("shop", owner="zeta")] == ["keep"]
    assert await repo.marks("shop", owner=None) == [], "the unowned map must not see owned maps' marks"
    await repo.add_send(
        slug="shop",
        owner="zeta",
        node_id="z",
        kind="analysis",
        repo="zeta/shop",
        sent_by="ann",
        note="",
        lead_id=None,
        job_id=None,
        error="refused",
        now=T0,
    )
    assert await repo.work("shop", owner="acme") == [] and len(await repo.work("shop", owner="zeta")) == 1
    assert await repo.delete("shop", owner=None) is True
    assert await repo.owners("shop") == ["acme", "zeta"], "deleting the unowned map must keep the owned ones"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_map_given_to_another_owner_takes_its_marks_sends_and_leads(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.repositories.knowledge_maps import OwnerTaken

    repo, leads = KnowledgeMapsRepository(db_with_migrations), LeadsRepository(db_with_migrations)
    await _put(repo, T0, owner=None)
    await _put(repo, T0, owner="zeta")
    await repo.set_mark("shop", "a", "ann", "bloat", "mine", owner=None, now=T0)
    await repo.set_mark("shop", "a", "bo", "keep", "", owner=None, now=T0)
    await repo.set_mark("shop", "a", "ann", "keep", "stale", owner="acme", now=T0)  # a deleted acme map's
    await repo.add_send(
        slug="shop",
        owner=None,
        node_id="a",
        kind="analysis",
        repo="acme/shop",
        sent_by="ann",
        note="",
        lead_id=None,
        job_id=None,
        error=None,
        now=T0,
    )
    weed = Lead(
        kind="weed",
        repo="acme/shop",
        source_key=source_key("shop", "a"),
        title="t",
        summary="s",
        details="d",
        action="a",
        data={"map": "shop", "map_owner": None},
    )
    await leads.upsert([weed], now=T0)

    with pytest.raises(OwnerTaken):
        await repo.set_owner("shop", owner=None, to="zeta")
    assert await repo.set_owner("shop", owner=None, to="acme") is True, "the unowned map must move to acme"
    assert await repo.owners("shop") == ["acme", "zeta"]
    stored = await repo.graph_text("shop", owner="acme")
    assert stored is not None and json.loads(stored[0]) == GRAPH, "the stored graph text is left as it was"
    marks = {(m["marked_by"], m["verdict"], m["note"]) for m in await repo.marks("shop", owner="acme")}
    assert marks == {("ann", "bloat", "mine"), ("bo", "keep", "")}, "the moving map's marks win over stale ones"
    assert len(await repo.work("shop", owner="acme")) == 1 and await repo.work("shop", owner=None) == []
    lead = await leads.get(weed.id)
    assert lead is not None and lead["data"]["map_owner"] == "acme", "the map's leads must name the new owner"
    assert await repo.set_owner("shop", owner=None, to="acme") is False, "there is no unowned map any more"


#: Text an older release could store (Python's JSON reads it, JSONB does not): NaN, a NUL, a lone surrogate.
NOT_JSONB = '{"slug":"org","nodes":[{"id":"a","x":NaN,"label":"a\\u0000b\\ud800"}],"edges":[]}'


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_an_owner_change_never_reads_the_graph_and_points_the_leads_links_at_the_new_owner(
    db_with_migrations: DatabasePool,
) -> None:
    import asyncpg

    db = db_with_migrations
    repo, leads = KnowledgeMapsRepository(db), LeadsRepository(db)
    await db.execute(
        "INSERT INTO knowledge_maps (slug, owner, title, graph) VALUES ('org', NULL, 'Org', $1)", NOT_JSONB
    )

    def weed(node: str, url: str) -> Lead:
        return Lead(
            kind="weed",
            repo="acme/billing",
            source_key=source_key("org", node),
            title="t",
            summary="s",
            details="d",
            action="a",
            url=url,
            data={"map": "org", "map_owner": None},
        )

    base = "https://embry0.example.com/maps/org"
    made = weed("a", f"{base}?owner=&part=a")
    older = weed("b", f"{base}?part=b")  # made before maps had owners
    await leads.upsert([made, older], now=T0)
    try:
        moved = await repo.set_owner("org", owner=None, to="acme")
    except asyncpg.PostgresError as exc:
        pytest.fail(f"an owner change must not parse the stored graph text: {exc}")
    assert moved is True
    stored = await repo.graph_text("org", owner="acme")
    assert stored is not None and stored[0] == NOT_JSONB, "the stored graph text is left byte for byte"
    urls = {lid: (await leads.get(lid) or {}).get("url") for lid in (made.id, older.id)}
    assert urls == {made.id: f"{base}?owner=acme&part=a", older.id: f"{base}?owner=acme&part=b"}, (
        "a lead's link must name the map's new owner"
    )
