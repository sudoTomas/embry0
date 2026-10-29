"""Knowledge maps belong to an owner (#180): who may see a map, change it, and how an address names it.

Driven through the real app (``create_app``) on a real database, with API
keys, so the tier, the repository allowlist and the refusals are the ones
production applies. A key covers the owners of the repositories on its
allowlist; a map that belongs to no one is an unrestricted admin's alone.
"""

from __future__ import annotations

import gzip
import json
import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.services.knowledge_maps import FORMAT, source_key
from embry0.services.leads import lead_id
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.knowledge_maps import KnowledgeMapsRepository
from embry0.storage.repositories.leads import LeadsRepository

pytestmark = pytest.mark.requires_postgres

ADMIN = "emb_" + "a" * 43
OPERATOR = "emb_" + "o" * 43
ACME = "emb_" + "c" * 43
ZETA = "emb_" + "z" * 43
ACME_ADMIN = "emb_" + "d" * 43
ZETA_ADMIN = "emb_" + "y" * 43

PROBE = "script:probe.mjs"


def _key(key_id: str, name: str, scope: str, allowed: tuple[str, ...] | None) -> ApiKeyRow:
    return ApiKeyRow(
        id=key_id,
        user_id=None,
        name=name,
        prefix="emb_xxxxxxxx",
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=allowed,
    )


KEYS = {
    hash_token(ADMIN): _key("key-admin0000001", "admin-key", "admin", None),
    hash_token(OPERATOR): _key("key-operator0001", "operator-key", "operate", None),
    hash_token(ACME): _key("key-acme00000001", "acme-key", "operate", ("Acme/Widgets",)),
    hash_token(ZETA): _key("key-zeta00000001", "zeta-key", "operate", ("zeta/gears",)),
    hash_token(ACME_ADMIN): _key("key-acmeadmin001", "acme-admin-key", "admin", ("acme/widgets",)),
    hash_token(ZETA_ADMIN): _key("key-zetaadmin001", "zeta-admin-key", "admin", ("zeta/gears",)),
}


def _as(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}


def _doc(slug: str, repo: str | None, *node_ids: str, **over: Any) -> dict[str, Any]:
    nodes = [
        {"id": n, "kind": "script", "label": n, "path": f"scripts/{n}"}
        if n.startswith("script:")
        else {"id": n, "kind": "system", "label": n, "repo": "acme/billing"}
        for n in node_ids
    ]
    return {"format": FORMAT, "slug": slug, "title": slug.title(), "repo": repo, "nodes": nodes, "edges": [], **over}


@pytest.fixture
async def app(db_with_migrations: DatabasePool) -> AsyncIterator[Any]:
    from embry0.api.app import create_app

    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
    app = create_app(config)
    state = app.state
    state.db = db_with_migrations
    state.knowledge_maps_repo = KnowledgeMapsRepository(db_with_migrations)
    state.leads_repo = LeadsRepository(db_with_migrations)
    state.proposals_repo = None
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    state.api_keys_repo = keys
    state.users_repo = None
    state.issue_executor = MagicMock()
    state.issue_executor.dispatch_refusal = AsyncMock(return_value=None)
    state.jobs_repo = MagicMock()
    state.jobs_repo.create = AsyncMock(return_value="job-analysis")
    state.issues_repo = MagicMock()
    state.issues_repo.create = AsyncMock(return_value="iss-new")
    state.issues_repo.update = AsyncMock()
    state.issue_executor.execute = AsyncMock(return_value="job-issue")
    yield app
    bust_api_key_cache()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _upload(client: AsyncClient, token: str, doc: dict[str, Any], query: str = "") -> Any:
    return await client.put(
        f"/api/v1/maps/{doc['slug']}{query}",
        content=gzip.compress(json.dumps(doc).encode()),
        headers={**_as(token), "Content-Encoding": "gzip", "Content-Type": "application/json"},
    )


async def _mark(client: AsyncClient, token: str, path: str, node_id: str, verdict: str = "bloat") -> Any:
    body = {"node_id": node_id, "verdict": verdict, "by": "ann@example.com"}
    return await client.post(f"/api/v1/maps/{path}", json=body, headers=_as(token))


async def _listed(client: AsyncClient, token: str) -> list[tuple[str, str | None]]:
    maps = (await client.get("/api/v1/maps", headers=_as(token))).json()["maps"]
    return sorted(((m["slug"], m.get("owner", "missing")) for m in maps), key=lambda m: (m[0], m[1] or ""))


async def _audit(db: DatabasePool, action: str) -> list[dict[str, Any]]:
    rows = await db.fetch("SELECT actor, details FROM audit_log WHERE action = $1 ORDER BY id", action)
    return [
        {"actor": r["actor"], "details": json.loads(r["details"]) if isinstance(r["details"], str) else r["details"]}
        for r in rows
    ]


async def test_a_key_of_another_owner_can_neither_change_a_map_nor_see_it(client: AsyncClient, app: Any) -> None:
    put = await _upload(client, ADMIN, _doc("shop", "acme/widgets", PROBE))
    assert put.status_code == 200, put.text
    own = await _upload(client, ZETA, _doc("shop", "zeta/gears", "script:gear.mjs"))
    assert own.status_code == 200, own.text
    acme = await client.get("/api/v1/maps/shop", params={"owner": "acme"}, headers=_as(ADMIN))
    assert acme.status_code == 200 and [n["id"] for n in acme.json()["nodes"]] == [PROBE], (
        f"another owner's key uploading its own map called shop must not replace acme's: {acme.text[:200]}"
    )
    assert await _listed(client, ZETA) == [("shop", "zeta")], "a key must list only the maps of owners it covers"
    for path in ("shop", f"shop/parts/{PROBE}", "shop/marks", "shop/work"):
        read = await client.get(f"/api/v1/maps/{path}", params={"owner": "acme"}, headers=_as(ZETA))
        assert read.status_code == 404, f"a key must not read another owner's map at {path}: {read.status_code}"

    refused = {
        "overwrite": await _upload(client, ZETA, _doc("shop", None, "sys:billing", owner="acme")),
        "mark": await _mark(client, ZETA, "shop/marks?owner=acme", PROBE),
        "send": await client.post(
            "/api/v1/maps/shop/send?owner=acme",
            json={"node_ids": [PROBE], "kind": "analysis", "by": "ann@example.com"},
            headers=_as(ZETA),
        ),
        "delete": await client.delete("/api/v1/maps/shop?owner=acme", headers=_as(ZETA_ADMIN)),
        "give away": await client.put(
            "/api/v1/maps/shop/owner?owner=acme", json={"owner": "zeta"}, headers=_as(ZETA_ADMIN)
        ),
    }
    for what, response in refused.items():
        assert response.status_code == 403, f"{what}: {response.status_code} {response.text}"
        assert response.json()["detail"].startswith("repo_not_allowed:") and "acme/*" in response.json()["detail"], (
            f"{what} must be refused as a repository is, naming the owner: {response.json()['detail']}"
        )
    app.state.jobs_repo.create.assert_not_awaited()
    still = await client.get("/api/v1/maps/shop", params={"owner": "acme"}, headers=_as(ADMIN))
    assert still.status_code == 200, "a refused delete must leave the map"
    rows = await _audit(app.state.db, "apikey.repo_refused")
    assert {r["details"]["repo"] for r in rows} == {"acme/*"}, "each refusal is audited with the owner it refused"
    assert len(rows) == len(refused) and {r["actor"] for r in rows} == {"key:zeta-key", "key:zeta-admin-key"}


async def test_a_map_of_no_owner_is_an_admins_alone_until_an_admin_gives_it_one(client: AsyncClient, app: Any) -> None:
    org = _doc("org", None, "sys:billing")
    put = await _upload(client, ADMIN, org)
    assert put.status_code == 200, put.text
    assert put.json().get("owner", "missing") is None, "a map naming neither an owner nor a repo belongs to no one"
    by_operator = await _upload(client, OPERATOR, org)
    assert by_operator.status_code == 403 and by_operator.json()["detail"].startswith("map_has_no_owner:"), (
        f"only an admin may upload a map of no owner: {by_operator.status_code} {by_operator.text}"
    )
    assert await _listed(client, OPERATOR) == [], "a map of no owner must not be listed to an operator"
    assert (await client.get("/api/v1/maps/org", headers=_as(OPERATOR))).status_code == 404
    assert (await _mark(client, OPERATOR, "org/marks", "sys:billing")).status_code == 404
    marked = await _mark(client, OPERATOR, "org/marks?owner=", "sys:billing")
    assert marked.status_code == 403 and marked.json()["detail"].startswith("map_has_no_owner:"), marked.text
    assert (await client.get("/api/v1/maps/org", headers=_as(ACME_ADMIN))).status_code == 404, (
        "an admin key limited to an allowlist must not see a map of no owner"
    )
    limited = await _mark(client, ACME_ADMIN, "org/marks?owner=", "sys:billing")
    assert limited.status_code == 403 and limited.json()["detail"].startswith("repo_not_allowed:"), limited.text

    assert await _listed(client, ADMIN) == [("org", None)], "an admin sees the map of no owner"
    assert (await _mark(client, ADMIN, "org/marks", "sys:billing")).status_code == 200
    given = await client.put("/api/v1/maps/org/owner", json={"owner": "Acme"}, headers=_as(ADMIN))
    assert given.status_code == 200 and given.json() == {"slug": "org", "owner": "acme", "changed": True}, given.text
    assert await _audit(app.state.db, "map.owner_changed") == [
        {"actor": "key:admin-key", "details": {"slug": "org", "before": None, "after": "acme"}}
    ], "an owner change must write one audit row naming who, from and to"
    for token in (OPERATOR, ACME):
        got = await client.get("/api/v1/maps/org", headers=_as(token))
        assert got.status_code == 200 and got.json()["owner"] == "acme", f"acme's map once given: {got.text[:200]}"
    part = (await client.get("/api/v1/maps/org/parts/sys:billing", headers=_as(OPERATOR))).json()
    assert [m["verdict"] for m in part["marks"]] == ["bloat"], "the admin's mark must move with the map"
    again = await client.put("/api/v1/maps/org/owner", json={"owner": "acme"}, headers=_as(ADMIN))
    assert again.json()["changed"] is False and len(await _audit(app.state.db, "map.owner_changed")) == 1
    not_admin = await client.put("/api/v1/maps/org/owner", json={"owner": None}, headers=_as(OPERATOR))
    assert not_admin.status_code == 403, "only an admin gives a map an owner"


async def test_two_owners_each_have_a_map_of_one_slug(client: AsyncClient, app: Any) -> None:
    assert (await _upload(client, ADMIN, _doc("shop", "acme/widgets", PROBE))).status_code == 200
    assert (await _upload(client, ADMIN, _doc("shop", "zeta/gears", PROBE, "script:gear.mjs"))).status_code == 200
    assert await _listed(client, ADMIN) == [("shop", "acme"), ("shop", "zeta")], (
        "two owners' uploads of one slug must be two maps"
    )
    ambiguous = await client.get("/api/v1/maps/shop", headers=_as(ADMIN))
    assert ambiguous.status_code == 409 and "acme, zeta" in ambiguous.json()["detail"], (
        f"an address that fits two maps must say which owners to name: {ambiguous.status_code}"
    )
    for owner, repo in (("acme", "acme/widgets"), ("zeta", "zeta/gears")):
        got = await client.get("/api/v1/maps/shop", params={"owner": owner}, headers=_as(ADMIN))
        assert (got.json()["owner"], got.json()["repo"]) == (owner, repo), f"?owner={owner} names {owner}'s map"

    assert (await _mark(client, ADMIN, "shop/marks?owner=acme", PROBE)).json()["lead_id"]
    acme_lead = lead_id("weed", "acme/widgets", source_key("shop", PROBE))
    again = await _upload(client, ADMIN, _doc("shop", "zeta/gears", "script:gear.mjs"))
    assert again.json()["leads_closed"] == 0, "an upload must close only its own map's leads"
    lead = await app.state.leads_repo.get(acme_lead)
    assert lead["state"] == "open" and lead["url"].endswith("/maps/shop?owner=acme&part=script%3Aprobe.mjs")
    zeta_marks = (await client.get("/api/v1/maps/shop/marks", params={"owner": "zeta"}, headers=_as(ADMIN))).json()
    assert zeta_marks["marks"] == {}, "acme's marks must not show on zeta's map"
    assert (await _upload(client, ADMIN, _doc("shop", None))).status_code == 200
    taken = await client.put("/api/v1/maps/shop/owner?owner=", json={"owner": "acme"}, headers=_as(ADMIN))
    assert taken.status_code == 409, "a map cannot move to an owner that has one of its slug"


async def test_an_address_without_an_owner_works_while_it_names_one_map(client: AsyncClient, app: Any) -> None:
    assert (await _upload(client, ADMIN, _doc("shop", "acme/widgets", PROBE))).status_code == 200
    got = await client.get("/api/v1/maps/shop", headers=_as(OPERATOR))
    assert got.status_code == 200 and got.json().get("owner") == "acme", (
        f"the only map called shop must answer at its old address, and say its owner: {got.text[:200]}"
    )
    part = await client.get(f"/api/v1/maps/shop/parts/{PROBE}", headers=_as(OPERATOR))
    assert part.status_code == 200 and part.json()["map"].get("owner") == "acme", part.text
    assert (await _mark(client, OPERATOR, "shop/marks", PROBE)).status_code == 200
    assert (await client.get("/api/v1/maps/shop/marks", headers=_as(OPERATOR))).json()["marks"][PROBE]["bloat"] == 1
    sent = await client.post(
        "/api/v1/maps/shop/send",
        json={"node_ids": [PROBE], "kind": "analysis", "by": "ann@example.com"},
        headers=_as(OPERATOR),
    )
    assert sent.status_code == 202 and sent.json()["sent"] == 1, sent.text
    assert (await client.get("/api/v1/maps/shop/work", headers=_as(OPERATOR))).json()["work"][PROBE]["job_id"]

    assert (await _upload(client, ADMIN, _doc("shop", "zeta/gears", PROBE))).status_code == 200
    assert (await client.get("/api/v1/maps/shop", headers=_as(OPERATOR))).status_code == 409, (
        "once two maps share the slug, an address without an owner must not pick one for a caller who sees both"
    )
    mine = await client.get("/api/v1/maps/shop", headers=_as(ACME))
    assert mine.status_code == 200 and mine.json()["owner"] == "acme", (
        "a key that sees one of them still reaches it at the old address"
    )
    assert (await _mark(client, ACME, "shop/marks", PROBE, "keep")).status_code == 200
    mismatch = await _upload(client, ADMIN, _doc("shop", "zeta/gears", PROBE), query="?owner=acme")
    assert mismatch.status_code == 422, "an address naming another owner than the document is refused"


def _part(node_id: str, repo: str) -> dict[str, Any]:
    """A file of ``repo`` on a map: an agent can remove it, so a bloat mark makes a lead in ``repo``."""
    return {"id": node_id, "kind": "script", "label": node_id, "path": f"scripts/{node_id}", "repo": repo}


async def _lead(app: Any, lid: str) -> dict[str, Any]:
    row = await app.state.leads_repo.get(lid)
    assert row is not None, f"no lead {lid}"
    return dict(row)


async def test_a_key_limited_to_one_repository_replaces_only_its_repositorys_maps(
    client: AsyncClient, app: Any
) -> None:
    org = {**_doc("org", None, owner="acme"), "nodes": [_part("pay.mjs", "acme/billing")]}
    assert (await _upload(client, ADMIN, org)).status_code == 200
    lid = (await _mark(client, ADMIN, "org/marks?owner=acme", "pay.mjs")).json()["lead_id"]
    assert (await _upload(client, ADMIN, _doc("billing", "acme/billing", PROBE))).status_code == 200

    over_org = await _upload(client, ACME, _doc("org", "acme/widgets", PROBE))
    assert over_org.status_code == 403, (
        f"a key limited to acme/widgets must not replace acme's organization map: {over_org.status_code} {over_org.text}"
    )
    assert (await _lead(app, lid))["state"] == "open", "a refused upload must close no lead"
    over_repo = await _upload(client, ACME, _doc("billing", "acme/widgets", PROBE))
    assert over_repo.status_code == 403 and "acme/billing" in over_repo.json()["detail"], (
        f"a key limited to acme/widgets must not replace acme/billing's map: {over_repo.text}"
    )
    kept = await client.get("/api/v1/maps/billing", headers=_as(ADMIN))
    assert kept.json()["repo"] == "acme/billing"


async def test_an_upload_closes_no_lead_in_a_repository_the_key_may_not_act_on(client: AsyncClient, app: Any) -> None:
    shop = {**_doc("shop", "acme/widgets", PROBE)}
    shop["nodes"] = [*shop["nodes"], _part("pay.mjs", "acme/billing")]
    assert (await _upload(client, ADMIN, shop)).status_code == 200
    lid = (await _mark(client, ADMIN, "shop/marks", "pay.mjs")).json()["lead_id"]
    again = await _upload(client, ACME, _doc("shop", "acme/widgets", PROBE))
    assert again.status_code == 200, again.text
    assert again.json()["leads_closed"] == 0 and (await _lead(app, lid))["state"] == "open", (
        "a key limited to acme/widgets must not close a lead in acme/billing by uploading without its part"
    )


async def test_a_key_limited_to_one_repository_deletes_or_moves_only_its_repositorys_maps(
    client: AsyncClient, app: Any
) -> None:
    for doc in (_doc("org", None, owner="acme"), _doc("billing", "acme/billing"), _doc("widgets", "acme/widgets")):
        assert (await _upload(client, ADMIN, doc)).status_code == 200
    refused = {
        "delete the organization map": await client.delete("/api/v1/maps/org", headers=_as(ACME_ADMIN)),
        "delete acme/billing's map": await client.delete("/api/v1/maps/billing", headers=_as(ACME_ADMIN)),
        "set the organization map's owner": await client.put(
            "/api/v1/maps/org/owner", json={"owner": "acme"}, headers=_as(ACME_ADMIN)
        ),
    }
    for what, response in refused.items():
        assert response.status_code == 403, f"an admin key limited to acme/widgets must not {what}: {response.text}"
    assert await _listed(client, ADMIN) == [("billing", "acme"), ("org", "acme"), ("widgets", "acme")]
    assert (await client.delete("/api/v1/maps/widgets", headers=_as(ACME_ADMIN))).status_code == 200


async def test_a_maps_parts_live_in_its_owners_repositories(client: AsyncClient, app: Any) -> None:
    foreign = {**_doc("shop", "acme/widgets"), "nodes": [_part("gear.mjs", "zeta/gears")]}
    refused = await _upload(client, ADMIN, foreign)
    assert refused.status_code == 422 and "zeta/gears" in refused.json()["detail"], (
        f"a map must not hold a part of another owner's repository: {refused.status_code} {refused.text}"
    )
    beyond = {**_doc("widgets", "acme/widgets"), "nodes": [_part("pay.mjs", "acme/billing")]}
    limited = await _upload(client, ACME, beyond)
    assert limited.status_code == 403 and "acme/billing" in limited.json()["detail"], (
        f"a key limited to acme/widgets must not upload a part of acme/billing: {limited.status_code} {limited.text}"
    )


async def test_a_map_never_shows_or_moves_another_maps_lead(client: AsyncClient, app: Any) -> None:
    part = _part("pay.mjs", "acme/billing")
    assert (await _upload(client, ADMIN, {**_doc("org", None), "nodes": [part]})).status_code == 200
    assert (await _upload(client, ADMIN, {**_doc("org", None, owner="acme"), "nodes": [part]})).status_code == 200
    lid = (await _mark(client, ADMIN, "org/marks?owner=acme", "pay.mjs")).json()["lead_id"]
    view = (await client.get("/api/v1/maps/org/parts/pay.mjs", params={"owner": ""}, headers=_as(ADMIN))).json()
    assert view["lead"] is None, "the unowned map's part view must not show the lead acme's map made"
    await _mark(client, ADMIN, "org/marks?owner=", "pay.mjs", "keep")
    await _mark(client, ADMIN, "org/marks?owner=", "pay.mjs", "bloat")
    lead = await _lead(app, lid)
    assert (lead["state"], lead["data"]["map_owner"]) == ("open", "acme"), (
        f"marks on another map of the slug must not close or take acme's lead: {lead['state']} {lead['data']}"
    )


async def test_a_write_that_names_no_owner_never_names_a_map_the_caller_cannot_see(
    client: AsyncClient, app: Any
) -> None:
    assert (await _upload(client, ADMIN, _doc("shop", "acme/widgets", PROBE))).status_code == 200
    writes = {
        "mark": await _mark(client, ZETA, "shop/marks", PROBE),
        "send": await client.post(
            "/api/v1/maps/shop/send", json={"node_ids": [PROBE], "by": "ann@example.com"}, headers=_as(ZETA)
        ),
        "delete": await client.delete("/api/v1/maps/shop", headers=_as(ZETA_ADMIN)),
        "set the owner": await client.put("/api/v1/maps/shop/owner", json={"owner": "zeta"}, headers=_as(ZETA_ADMIN)),
    }
    for what, response in writes.items():
        assert response.status_code == 404 and "acme" not in response.text, (
            f"a {what} without ?owner= must not say another owner has the slug: {response.status_code} {response.text}"
        )
    assert await _audit(app.state.db, "apikey.repo_refused") == []


async def test_an_owner_change_keeps_a_map_with_its_repositorys_owner(client: AsyncClient, app: Any) -> None:
    assert (await _upload(client, ADMIN, _doc("widgets", "acme/widgets", PROBE))).status_code == 200
    for to in ("zeta", None):
        moved = await client.put("/api/v1/maps/widgets/owner", json={"owner": to}, headers=_as(ADMIN))
        assert moved.status_code == 422, f"acme/widgets's map must stay acme's, not go to {to}: {moved.text}"
    assert (await _upload(client, ADMIN, _doc("org", None, "sys:billing", owner="acme"))).status_code == 200
    away = await client.put("/api/v1/maps/org/owner", json={"owner": "zeta"}, headers=_as(ADMIN))
    assert away.status_code == 422 and "acme/billing" in away.json()["detail"], (
        f"a map whose parts are in acme's repositories must not go to zeta: {away.text}"
    )
    assert await _listed(client, ADMIN) == [("org", "acme"), ("widgets", "acme")]


async def test_an_admin_key_limited_to_one_owner_cannot_give_its_map_away(client: AsyncClient, app: Any) -> None:
    assert (await _upload(client, ADMIN, _doc("widgets", "acme/widgets", PROBE))).status_code == 200
    for to, shown in (("zeta", "zeta/*"), (None, "(no owner)")):
        moved = await client.put("/api/v1/maps/widgets/owner", json={"owner": to}, headers=_as(ACME_ADMIN))
        assert moved.status_code == 403 and moved.json()["detail"].startswith("repo_not_allowed:"), (
            f"an admin key limited to acme/widgets must not give acme's map to {to}: {moved.status_code} {moved.text}"
        )
        assert shown in moved.json()["detail"]


async def _shared_lead(client: AsyncClient) -> str:
    """The unowned map shop holds the open lead of acme/widgets's pay.mjs; acme's map shop has the same part."""
    part = _part("pay.mjs", "acme/widgets")
    assert (await _upload(client, ADMIN, {**_doc("shop", None), "nodes": [part]})).status_code == 200
    assert (await _upload(client, ADMIN, {**_doc("shop", "acme/widgets"), "nodes": [part]})).status_code == 200
    lid = (await _mark(client, ADMIN, "shop/marks?owner=", "pay.mjs")).json()["lead_id"]
    assert lid, "the unowned map's bloat mark must open the lead"
    return str(lid)


async def _remove(client: AsyncClient, token: str, together: bool) -> dict[str, Any]:
    body = {"node_ids": ["pay.mjs"], "kind": "remove", "together": together, "by": "ann@example.com"}
    sent = await client.post("/api/v1/maps/shop/send?owner=acme", json=body, headers=_as(token))
    assert sent.status_code == 202, sent.text
    return dict(sent.json())


async def test_a_batch_removal_leaves_out_a_part_whose_lead_another_map_holds(client: AsyncClient, app: Any) -> None:
    lid = await _shared_lead(client)
    batch = await _remove(client, OPERATOR, together=True)
    assert batch["results"][0]["job_id"] is None and batch["jobs"] == [], (
        f"a batch removal must leave out a part whose lead another map holds: {batch}"
    )
    assert "held by another map" in str(batch["results"][0]["error"])
    app.state.issues_repo.create.assert_not_awaited()
    lead = await _lead(app, lid)
    assert (lead["state"], lead["data"]["map_owner"]) == ("open", None), "the holding map keeps its lead"


async def test_a_held_lead_names_its_map_only_to_a_caller_who_may_see_it(client: AsyncClient, app: Any) -> None:
    await _shared_lead(client)
    for together in (False, True):
        hidden = str((await _remove(client, OPERATOR, together))["results"][0]["error"])
        assert "an admin must release it" in hidden and "?owner=" not in hidden and "map of this slug" not in hidden, (
            f"an operator must not learn of a map it cannot open (together={together}): {hidden}"
        )
        shown = str((await _remove(client, ADMIN, together))["results"][0]["error"])
        assert "the map of this slug with ?owner= holds this part's lead" in shown, (
            f"an admin is told which map holds the lead (together={together}): {shown}"
        )
    app.state.issues_repo.create.assert_not_awaited()


async def test_the_served_map_says_its_owner_last(client: AsyncClient, app: Any) -> None:
    assert (await _upload(client, ADMIN, _doc("shop", "acme/widgets", PROBE))).status_code == 200
    text = (await client.get("/api/v1/maps/shop", headers=_as(ADMIN))).text
    assert text.endswith(',"owner":"acme"}'), f"the owner key must come last, so a reader keeps it: {text[-60:]}"
