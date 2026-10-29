"""The content pack API (#176) against a real Postgres: draft, preview, activate, put, restore, reset, and refusals.

The admin gate is checked on the route table and on a stub app with the real
auth chain, since the shared client runs in dev mode.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from embry0.services.progression_pack import default_text

pytestmark = [pytest.mark.requires_postgres]

DEFAULT = default_text()
COPPER = DEFAULT.replace("{id: bronze, name: Bronze,", "{id: bronze, name: Copper,")
YAML = {"Content-Type": "application/yaml"}
PACK = "/api/v1/progression/pack"


@pytest.fixture
async def client(api_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    db = api_client.app.state.db  # type: ignore[attr-defined]
    await db.execute("TRUNCATE progression_packs, progression_pack_defaults")
    await db.execute("DELETE FROM audit_log WHERE action LIKE 'progression.%'")
    yield api_client
    await db.execute("TRUNCATE progression_packs, progression_pack_defaults")


def _tier(body: dict[str, Any]) -> str:
    return str(next(t["name"] for t in body["tiers"] if t["id"] == "bronze"))


async def _audits(client: AsyncClient) -> list[tuple[str, dict[str, Any]]]:
    rows = await client.app.state.db.fetch(  # type: ignore[attr-defined]
        "SELECT action, details FROM audit_log WHERE action LIKE 'progression.%' ORDER BY id"
    )
    return [(r["action"], r["details"] if isinstance(r["details"], dict) else json.loads(r["details"])) for r in rows]


# ---- reading ----


async def test_with_nothing_stored_the_pack_is_the_shipped_default(client: AsyncClient) -> None:
    resp = await client.get(PACK)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert resp.headers["etag"] == '"0"'
    assert (body["version"], body["follows_default"], body["fallback"], body["default_moved"]) == (0, True, None, False)
    assert body["text"] == DEFAULT and body["hash"] == body["default_hash"]
    assert body["document"]["timezone"] == "UTC"
    pack = (await client.get("/api/v1/progression")).json()["pack"]
    assert (pack["revision"], pack["follows_default"]) == (0, True)
    yaml_resp = await client.get(PACK, params={"format": "yaml"})
    assert yaml_resp.text == DEFAULT and yaml_resp.headers["content-type"].startswith("application/yaml")
    default = (await client.get(f"{PACK}/default")).json()
    assert (default["text"], default["hash"]) == (DEFAULT, body["hash"])
    vocabulary = (await client.get(f"{PACK}/vocabulary")).json()
    assert "merged_under_cost" in vocabulary["metrics"] and "properties" in vocabulary["schema"]
    assert vocabulary["metrics"]["merged_under_cost"]["params"]["properties"]["max_usd"]["type"] == "number"


# ---- the lifecycle: draft, preview, activate ----


AGENT = "pack-preview-agent"


@pytest.fixture
async def one_run(client: AsyncClient) -> AsyncIterator[None]:
    """One successful run of fifty source edits, so the preview has a character to re-price."""
    db = client.app.state.db  # type: ignore[attr-defined]
    at = datetime(2026, 3, 2, 10, tzinfo=UTC)
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, budget_overrun_usd, created_at, started_at, finished_at, "
        "updated_at) VALUES ('pack-preview-job', 'acme/widgets', 't', 'completed', 0, $1, $1, $1, $1)",
        at,
    )
    await db.execute(
        "INSERT INTO traces (trace_id, job_id, agent_type, model, result, tools_called, skill_actions, created_at) "
        "VALUES ('pack-preview-trace', 'pack-preview-job', $1, 'm', 'success', '{}'::jsonb, $2::jsonb, $3)",
        AGENT,
        {"edit:source": 50},
        at,
    )
    yield
    await db.execute("DELETE FROM traces WHERE trace_id = 'pack-preview-trace'")
    await db.execute("DELETE FROM jobs WHERE job_id = 'pack-preview-job'")


async def test_draft_preview_then_activate(client: AsyncClient, one_run: None) -> None:
    draft_text = COPPER.replace("actions: {edit: 1400,", "actions: {edit: 140000,")
    assert draft_text != COPPER
    saved = await client.put(f"{PACK}/draft", content=draft_text, headers=YAML)
    assert saved.status_code == 200, saved.text
    draft = saved.json()
    assert draft["sections"] == ["rates", "tiers"] and draft["replaced_version"] is None
    assert saved.headers["etag"] == f'"{draft["version"]}"'
    assert _tier((await client.get("/api/v1/progression")).json()) == "Bronze", "a draft changes nothing anyone sees"

    got = (await client.get(f"{PACK}/draft")).json()
    assert (got["version"], got["text"], got["problems"]) == (draft["version"], draft_text, None)

    preview = await client.get("/api/v1/progression/preview")
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert _tier(shown) == "Copper", "the preview reads the history under the draft"
    assert shown["draft"] == {"version": draft["version"], "hash": draft["hash"], "parent_version": 0, "stale": False}
    assert shown["active"]["version"] == 0
    assert set(shown["changes"]) == {"characters", "account", "achievements_unlocked"}
    agent = next(c for c in shown["changes"]["characters"] if c["agent_type"] == AGENT)
    assert (agent["before"]["xp"], agent["after"]["xp"]) == (50 * 1400, 50 * 140000), "re-priced under the draft"
    assert agent["after"]["total_level"] > agent["before"]["total_level"]
    assert set(agent["after"]) == {"level", "total_level", "xp"}
    assert shown["changes"]["account"]["after"]["xp"] > shown["changes"]["account"]["before"]["xp"]
    assert _tier((await client.get("/api/v1/progression")).json()) == "Bronze", "a preview changes nothing either"

    stale = await client.post(f"{PACK}/draft/activate", headers={"If-Match": '"999"'})
    assert stale.status_code == 409, stale.text
    activated = await client.post(
        f"{PACK}/draft/activate", params={"reason": "copper"}, headers={"If-Match": f'"{draft["version"]}"'}
    )
    assert activated.status_code == 200, activated.text
    live = activated.json()["version"]
    assert live > draft["version"] and activated.json()["changed"] is True, "the draft goes live as the newest version"
    assert _tier((await client.get("/api/v1/progression")).json()) == "Copper", "the pack in effect at the next read"
    current = await client.get(PACK)
    assert (current.json()["version"], current.json()["follows_default"]) == (live, False)
    assert current.headers["etag"] == f'"{live}"'
    assert (await client.get(f"{PACK}/draft")).status_code == 404
    assert [a for a, _ in await _audits(client)] == ["progression.pack_draft_saved", "progression.pack_activated"]


async def test_activating_a_draft_saved_over_an_earlier_pack_is_a_409(client: AsyncClient) -> None:
    """A draft saved over version 1 must not undo a file applied since (version 2) unseen (review of #211)."""
    first = await client.put(PACK, content=COPPER, headers=YAML)
    assert first.status_code == 200, first.text
    saved = await client.put(f"{PACK}/draft", content=COPPER.replace("Copper", "Pewter"), headers=YAML)
    assert saved.status_code == 200, saved.text
    draft = saved.json()
    applied = await client.put(PACK, params={"source": "file"}, content=COPPER.replace("Copper", "Tin"), headers=YAML)
    assert applied.status_code == 200, applied.text
    got = (await client.get(f"{PACK}/draft")).json()
    assert (got.get("parent_version"), got.get("stale")) == (first.json()["version"], True), got
    refused = await client.post(f"{PACK}/draft/activate", headers={"If-Match": f'"{draft["version"]}"'})
    assert refused.status_code == 409, f"activating over a newer pack must be refused: {refused.text}"
    assert f"version {applied.json()['version']} is in effect now" in refused.json()["detail"]
    assert _tier((await client.get("/api/v1/progression")).json()) == "Tin", "the file applied since stays in effect"


async def test_a_second_preview_while_one_runs_is_a_429(client: AsyncClient) -> None:
    saved = await client.put(f"{PACK}/draft", content=COPPER, headers=YAML)
    assert saved.status_code == 200, saved.text
    lock = asyncio.Lock()
    await lock.acquire()  # a preview is being computed
    client.app.state.progression_preview_lock = lock  # type: ignore[attr-defined]
    try:
        # A preview that waited for the lock instead of refusing would hang here.
        busy = await asyncio.wait_for(client.get("/api/v1/progression/preview"), timeout=10)
    finally:
        lock.release()
    assert busy.status_code == 429, busy.text
    assert busy.headers.get("retry-after") == "5"
    assert (await client.get("/api/v1/progression/preview")).status_code == 200, "once it is done, a preview runs"


async def test_a_draft_can_be_discarded(client: AsyncClient) -> None:
    saved = await client.put(f"{PACK}/draft", content=COPPER, headers=YAML)
    assert saved.status_code == 200, saved.text
    version = saved.json()["version"]
    assert (await client.request("DELETE", f"{PACK}/draft", headers={"If-Match": '"1000"'})).status_code == 409, (
        "a stale If-Match does not discard the draft"
    )
    deleted = await client.delete(f"{PACK}/draft")
    assert deleted.json() == {"deleted_version": version}
    assert (await client.delete(f"{PACK}/draft")).status_code == 404
    assert (await client.get("/api/v1/progression/preview")).status_code == 404
    assert ("progression.pack_draft_deleted", {"version": version}) in await _audits(client)


# ---- the one-shot put, dry run, reset, undo ----


async def test_a_dry_run_answers_the_diff_and_stores_nothing(client: AsyncClient) -> None:
    resp = await client.put(PACK, params={"dry_run": 1}, content=COPPER, headers=YAML)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["would_change"], body["sections"], body["version"]) == (True, ["tiers"], 0)
    assert (
        "-  - {id: bronze, name: Bronze," in body["text_diff"]
        and "+  - {id: bronze, name: Copper," in body["text_diff"]
    )
    assert isinstance(body["warnings"], list)
    assert (await client.get(PACK)).json()["version"] == 0, "a dry run stores nothing"
    assert await _audits(client) == []


async def test_put_reset_and_restore_with_their_versions_and_audits(client: AsyncClient) -> None:
    put = await client.put(PACK, params={"source": "file", "reason": "rename"}, content=COPPER, headers=YAML)
    assert put.status_code == 200, put.text
    first = put.json()
    assert (first["changed"], first["source"], first["previous_version"]) == (True, "file", None)
    pack = (await client.get("/api/v1/progression")).json()["pack"]
    assert (pack["revision"], pack["follows_default"]) == (first["version"], False), "/progression names its version"
    again = (await client.put(PACK, params={"source": "file"}, content=COPPER, headers=YAML)).json()
    assert (again["changed"], again["version"]) == (False, first["version"]), "an unchanged file makes no version"

    stale = await client.post(f"{PACK}/reset", headers={"If-Match": '"0"'})
    assert stale.status_code == 409 and "version" in stale.json()["detail"]
    reset = await client.post(f"{PACK}/reset", headers={"If-Match": f'"{first["version"]}"'})
    assert reset.status_code == 200, reset.text
    assert (reset.json()["follows_default"], reset.json()["source"]) == (True, "reset")
    assert _tier((await client.get("/api/v1/progression")).json()) == "Bronze"

    restored = await client.post(f"{PACK}/versions/{first['version']}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["source"] == "restore" and restored.json()["version"] > reset.json()["version"]
    assert _tier((await client.get("/api/v1/progression")).json()) == "Copper", "undo puts the earlier pack back"
    assert (await client.post(f"{PACK}/versions/424242/restore")).status_code == 404

    versions = (await client.get(f"{PACK}/versions")).json()["versions"]
    assert [(v["source"], v["status"]) for v in versions] == [
        ("restore", "active"),
        ("reset", "superseded"),
        ("file", "superseded"),
    ]
    assert "text" not in versions[0], "the list carries no text"
    one = (await client.get(f"{PACK}/versions/{first['version']}")).json()
    assert (one["text"], one["reason"]) == (COPPER, "rename")
    assert [a for a, _ in await _audits(client)] == [
        "progression.pack_activated",
        "progression.pack_reset",
        "progression.pack_activated",
    ]
    assert (await _audits(client))[2][1]["source"] == "restore"


async def test_a_json_body_is_accepted(client: AsyncClient) -> None:
    current = await client.get(PACK)
    assert current.status_code == 200, current.text
    document = json.loads(json.dumps(current.json()["document"]))
    document["tiers"][0]["name"] = "Copper"
    resp = await client.put(PACK, json=document)
    assert resp.status_code == 200, resp.text
    stored = (await client.get(PACK)).json()
    assert stored["text"].startswith("version: 1\n") and stored["document"]["tiers"][0]["name"] == "Copper"


# ---- refusals ----


async def test_an_invalid_pack_is_refused_by_key_without_its_values(client: AsyncClient) -> None:
    bad = (
        COPPER.replace("max_level: 99", "max_level: 9999")
        .replace("timezone: UTC", "timezone: Mars/Olympus_Mons")
        .replace("version: 1\n", "version: 1\nsecret_sauce: 'hunter2-value'\n")
    )
    for path, method in ((PACK, "PUT"), (f"{PACK}/draft", "PUT")):
        resp = await client.request(method, path, content=bad, headers=YAML)
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert all(set(item) == {"loc", "type", "msg"} for item in detail), detail
        locs = [tuple(item["loc"]) for item in detail]
        assert ("curve", "max_level") in locs and ("secret_sauce",) in locs, locs
        assert "hunter2-value" not in resp.text and "9999" not in resp.text, "a refusal never echoes a value"
    dry = await client.put(PACK, params={"dry_run": 1}, content=bad, headers=YAML)
    assert dry.status_code == 422, "a dry run is refused the same way"
    assert (await client.get(PACK)).json()["version"] == 0 and (await client.get(f"{PACK}/draft")).status_code == 404


async def test_a_timezone_postgres_does_not_know_is_a_422_on_timezone(client: AsyncClient) -> None:
    resp = await client.put(PACK, content=COPPER.replace("timezone: UTC", "timezone: localtime"), headers=YAML)
    assert resp.status_code == 422, resp.text
    assert [item["loc"] for item in resp.json()["detail"]] == [["timezone"]]


async def test_a_pack_past_the_limit_is_refused_before_it_is_read(client: AsyncClient) -> None:
    big = DEFAULT + "#" * (256 * 1024)
    resp = await client.put(PACK, content=big, headers=YAML)
    assert resp.status_code == 413, resp.text
    assert (await client.put(f"{PACK}/draft", content=big, headers=YAML)).status_code == 413


async def test_a_chunked_pack_past_the_limit_is_refused_while_it_streams(client: AsyncClient) -> None:
    """No Content-Length: the body is read in pieces and refused once it passes 256 KiB."""
    sent: list[int] = []

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(64):  # 64 x 16 KiB = 1 MiB, four times the limit
            sent.append(1)
            yield b"#" * (16 * 1024)

    resp = await client.put(PACK, content=chunks(), headers=YAML)
    assert resp.status_code == 413, resp.text
    assert "content-length" not in {k.lower() for k in resp.request.headers}, "the body must go without a length"
    assert len(sent) < 64, f"the body was read whole ({len(sent)} of 64 pieces) before it was refused"
    assert (await client.get(PACK)).json()["version"] == 0


async def test_a_malformed_if_match_is_a_422_and_a_weak_tag_never_matches(client: AsyncClient) -> None:
    assert (await client.put(PACK, content=COPPER, headers={**YAML, "If-Match": "zero"})).status_code == 422
    assert (await client.put(PACK, content=COPPER, headers={**YAML, "If-Match": 'W/"0"'})).status_code == 409
    assert (await client.put(PACK, content=COPPER, headers={**YAML, "If-Match": '"5", "0"'})).status_code == 200


async def test_a_stored_pack_this_release_refuses_is_reported_and_never_served(client: AsyncClient) -> None:
    db = client.app.state.db  # type: ignore[attr-defined]
    await db.execute(
        "INSERT INTO progression_packs (status, schema_version, text, document, source) "
        "VALUES ('active', 1, 'x: 1\n', '{\"x\": 1}'::jsonb, 'person')"
    )
    resp = await client.get(PACK)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fallback"] and body["hash"] == body["default_hash"], "the shipped default is in effect, and says why"
    assert ["x"] in [p["loc"] for p in body["fallback"]], "the fallback names the key this release refuses"
    progression = await client.get("/api/v1/progression")
    assert progression.status_code == 200 and _tier(progression.json()) == "Bronze"
    in_effect = await client.get(PACK, params={"format": "yaml"})
    assert in_effect.text == DEFAULT, "the YAML is the pack in effect, the shipped default, not the refused text"
    assert in_effect.headers.get("x-embry0-pack-refused") == str(body["version"]), (
        "and it says which version is refused"
    )


# ---- tiers ----


def test_writes_and_the_preview_are_admin_tier_and_reads_are_not() -> None:
    from embry0.api.app import create_app
    from embry0.api.deps import _admin_dependency
    from tests.fixtures.routes import app_routes

    gated: dict[tuple[str, str], bool] = {}
    for route in app_routes(create_app()):
        if not route.path.startswith(("/api/v1/progression/pack", "/api/v1/progression/preview")):
            continue
        calls = {d.call for d in route.dependant.dependencies}
        for method in route.methods or ():
            gated[(method, route.path)] = _admin_dependency in calls
    assert gated == {
        ("GET", "/api/v1/progression/pack"): False,
        ("PUT", "/api/v1/progression/pack"): True,
        ("GET", "/api/v1/progression/pack/default"): False,
        ("GET", "/api/v1/progression/pack/default/diff"): False,
        ("GET", "/api/v1/progression/pack/vocabulary"): False,
        ("GET", "/api/v1/progression/pack/versions"): False,
        ("GET", "/api/v1/progression/pack/versions/{version}"): False,
        ("POST", "/api/v1/progression/pack/versions/{version}/restore"): True,
        ("POST", "/api/v1/progression/pack/reset"): True,
        ("GET", "/api/v1/progression/pack/draft"): False,
        ("PUT", "/api/v1/progression/pack/draft"): True,
        ("DELETE", "/api/v1/progression/pack/draft"): True,
        ("POST", "/api/v1/progression/pack/draft/activate"): True,
        ("GET", "/api/v1/progression/preview"): True,
    }


def test_an_operator_session_reads_and_is_refused_every_write() -> None:
    """The real auth chain, outside dev mode: an operator gets 200 on reads and 403 on writes."""
    import os
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from embry0.api.deps import bust_user_gate_cache, require_auth
    from embry0.api.session import SESSION_COOKIE, SessionData, encode_session
    from embry0.api.v1 import progression_pack as pack_v1
    from embry0.config import Embry0Config
    from embry0.services.progression_pack_store import PackStore

    secret = "s" * 40
    with patch.dict(os.environ, {}, clear=True):
        cfg = Embry0Config(
            _env_file=None,
            auth_dev_mode=False,
            api_key="master-key",
            auth0_issuer="https://t.example.com/",
            auth0_client_id="cid",
            auth0_client_secret="sec",
            session_secret=secret,
            sso_public_origin="https://e.example.com",
            sso_roles_claim="https://example.com/roles",
        )
    app = FastAPI()
    app.state.config = cfg
    app.state.db = None
    repo = MagicMock()
    repo.active = AsyncMock(return_value=None)
    repo.draft = AsyncMock(return_value=None)
    repo.versions = AsyncMock(return_value=[])
    repo.writing = MagicMock(side_effect=AssertionError("an operator must never reach a write"))
    app.state.progression_packs = PackStore(repo)
    user = MagicMock(session_epoch=1, disabled=False)
    app.state.users_repo = MagicMock(get_by_sub=AsyncMock(return_value=user))
    app.include_router(pack_v1.router, prefix="/api/v1", dependencies=[require_auth])

    def cookie(tier: str) -> str:
        return encode_session(
            secret, SessionData(sub="auth0|u", email="u@example.com", name="U", roles=[], tier=tier, epoch=1)
        )

    headers = {"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/yaml"}
    writes = [
        ("PUT", PACK),
        ("PUT", f"{PACK}/draft"),
        ("DELETE", f"{PACK}/draft"),
        ("POST", f"{PACK}/draft/activate"),
        ("POST", f"{PACK}/reset"),
        ("POST", f"{PACK}/versions/1/restore"),
        ("GET", "/api/v1/progression/preview"),
    ]
    bust_user_gate_cache()
    try:
        operator = TestClient(app)
        operator.cookies.set(SESSION_COOKIE, cookie("operator"))
        assert operator.get(PACK).status_code == 200
        assert operator.get(f"{PACK}/versions").status_code == 200
        for method, path in writes:
            assert operator.request(method, path, content=COPPER, headers=headers).status_code == 403, path
    finally:
        bust_user_gate_cache()


def test_who_made_a_version_is_shown_as_the_audit_log_shows_actors() -> None:
    """An operator sees their own name on a version and null for anyone else's, as ``GET /audit`` shows actors."""
    import os
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from embry0.api.deps import bust_user_gate_cache, require_auth
    from embry0.api.session import SESSION_COOKIE, SessionData, encode_session
    from embry0.api.v1 import progression_pack as pack_v1
    from embry0.config import Embry0Config
    from embry0.services.progression_pack_store import PackStore, read_text
    from embry0.storage.repositories.progression_packs import StoredPack

    secret = "s" * 40
    with patch.dict(os.environ, {}, clear=True):
        cfg = Embry0Config(
            _env_file=None,
            auth_dev_mode=False,
            api_key="master-key",
            auth0_issuer="https://t.example.com/",
            auth0_client_id="cid",
            auth0_client_secret="sec",
            session_secret=secret,
            sso_public_origin="https://e.example.com",
            sso_roles_claim="https://example.com/roles",
        )
    at = datetime(2026, 10, 1, tzinfo=UTC)
    document = read_text(COPPER).document

    def row(version: int, status: str, by: str) -> StoredPack:
        return StoredPack(version, status, 1, False, COPPER, document, None, "person", "", by, at, at)  # type: ignore[arg-type]

    mine, theirs = row(2, "active", "u@example.com"), row(1, "superseded", "other@example.com")
    repo = MagicMock()
    repo.active = AsyncMock(return_value=mine)
    repo.active_version = AsyncMock(return_value=2)
    repo.draft = AsyncMock(return_value=row(3, "draft", "other@example.com"))
    repo.versions = AsyncMock(return_value=[mine, theirs])
    repo.get = AsyncMock(side_effect=lambda v: {2: mine, 1: theirs}.get(v))
    repo.default_text = AsyncMock(return_value=None)
    app = FastAPI()
    app.state.config = cfg
    app.state.db = None
    app.state.progression_packs = PackStore(repo)
    app.state.users_repo = MagicMock(get_by_sub=AsyncMock(return_value=MagicMock(session_epoch=1, disabled=False)))
    app.include_router(pack_v1.router, prefix="/api/v1", dependencies=[require_auth])

    def cookie(tier: str) -> str:
        return encode_session(
            secret, SessionData(sub="auth0|u", email="u@example.com", name="U", roles=[], tier=tier, epoch=1)
        )

    bust_user_gate_cache()
    try:
        operator = TestClient(app)
        operator.cookies.set(SESSION_COOKIE, cookie("operator"))
        listed = operator.get(f"{PACK}/versions").json()["versions"]
        assert [v["created_by"] for v in listed] == ["u@example.com", None], (
            "a non-admin must see only their own name on a version"
        )
        assert operator.get(f"{PACK}/versions/1").json()["created_by"] is None
        assert operator.get(f"{PACK}/draft").json()["created_by"] is None
        assert operator.get(PACK).json()["created_by"] == "u@example.com"
        bust_user_gate_cache()
        admin = TestClient(app)
        admin.cookies.set(SESSION_COOKIE, cookie("admin"))
        assert [v["created_by"] for v in admin.get(f"{PACK}/versions").json()["versions"]] == [
            "u@example.com",
            "other@example.com",
        ], "an admin sees every actor"
    finally:
        bust_user_gate_cache()


# ---- the rollup (#176, PR 3): a pack change is read from the raw rows, then gets a rollup of its own ----


async def _read_then_settle(client: AsyncClient) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """The first read under the pack in effect, the read after its background write, and the rollups stored."""
    first = (await client.get("/api/v1/progression")).json()
    await client.app.state.progression.settle()  # type: ignore[attr-defined]
    second = (await client.get("/api/v1/progression")).json()
    rows = await client.app.state.db.fetch("SELECT pack_hash FROM progression_rollups")  # type: ignore[attr-defined]
    return first, second, [r["pack_hash"] for r in rows]


async def test_activate_reset_and_restore_each_read_raw_then_build_their_own_rollup(client: AsyncClient) -> None:
    _first, settled, stored = await _read_then_settle(client)
    assert settled["rollup"] is not None and stored == [settled["pack"]["hash"]], "the shipped pack's rollup"
    previous = settled["pack"]["hash"]

    async def changed(step: str) -> str:
        nonlocal previous
        first, second, stored = await _read_then_settle(client)
        current = first["pack"]["hash"]
        assert current != previous, f"{step} left the pack's hash as it was"
        assert first["rollup"] is None, f"after {step} the first read used another pack's rollup"
        assert second["rollup"] is not None and stored == [current], (
            f"after {step} the new pack's rollup was not built, alone: {stored}"
        )
        previous = current
        return current

    draft = (await client.put(f"{PACK}/draft", content=COPPER, headers=YAML)).json()
    activated = await client.post(f"{PACK}/draft/activate", headers={"If-Match": f'"{draft["version"]}"'})
    assert activated.status_code == 200, activated.text
    copper = await changed("an activation")
    reset = await client.post(f"{PACK}/reset", headers={"If-Match": f'"{activated.json()["version"]}"'})
    assert reset.status_code == 200, reset.text
    await changed("a reset")
    restored = await client.post(f"{PACK}/versions/{activated.json()['version']}/restore")
    assert restored.status_code == 200, restored.text
    assert await changed("a restore") == copper, "the restored version prices as it did"


async def test_the_preview_never_reads_or_builds_a_rollup(client: AsyncClient) -> None:
    """Both sides of a preview fold the raw rows: a day the rollup holds stale is no change the draft makes."""
    db = client.app.state.db  # type: ignore[attr-defined]
    await _read_then_settle(client)
    before = [dict(r) for r in await db.fetch("SELECT pack_hash, built_at FROM progression_rollups")]
    assert len(before) == 1
    # Recorded after the rollup was built, for a day it holds: the rollup leaves it out until the nightly rebuild.
    at = datetime(2026, 3, 2, 10, tzinfo=UTC)
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, budget_overrun_usd, created_at, started_at, finished_at, "
        "updated_at) VALUES ('pack-late-job', 'acme/widgets', 't', 'completed', 0, $1, $1, $1, $1)",
        at,
    )
    await db.execute(
        "INSERT INTO traces (trace_id, job_id, agent_type, model, result, tools_called, skill_actions, created_at) "
        "VALUES ('pack-late-trace', 'pack-late-job', $1, 'm', 'success', '{}'::jsonb, $2::jsonb, $3)",
        AGENT,
        {"edit:source": 50},
        at,
    )
    try:
        draft_text = DEFAULT.replace("actions: {edit: 1400,", "actions: {edit: 140000,")
        assert (await client.put(f"{PACK}/draft", content=draft_text, headers=YAML)).status_code == 200
        preview = await client.get("/api/v1/progression/preview")
        assert preview.status_code == 200, preview.text
        agent = next(c for c in preview.json()["changes"]["characters"] if c["agent_type"] == AGENT)
        assert agent["before"] is not None and (agent["before"]["xp"], agent["after"]["xp"]) == (
            50 * 1400,
            50 * 140000,
        ), f"the preview's active side was read from a stale rollup: {agent}"
        await client.app.state.progression.settle()  # type: ignore[attr-defined]
        after = [dict(r) for r in await db.fetch("SELECT pack_hash, built_at FROM progression_rollups")]
        assert after == before, f"the preview wrote a rollup: {after}"
    finally:
        await db.execute("DELETE FROM traces WHERE trace_id = 'pack-late-trace'")
        await db.execute("DELETE FROM jobs WHERE job_id = 'pack-late-job'")
