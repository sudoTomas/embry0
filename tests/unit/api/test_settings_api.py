"""The settings document API (spec 2026-10-28, #160).

Driven through the real app (``create_app``) on a real database, with API
keys so the actor and the tier are the ones production sees, and the
test-only section catalog so nothing depends on which features exist yet.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import Field, field_validator

from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.settings.bootstrap import import_env_once
from embry0.settings.registry import Catalog, Section, SectionModel
from embry0.settings.store import WHY_NOT, SettingsStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from tests.fixtures.settings_sections import BRITTLE, TEST_CATALOG

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
YAML = {"Content-Type": "application/yaml"}
V1 = "# tuned for the week\nsample:\n  per_tick: 4\n"
V2 = "# tuned again\nsample:\n  per_tick: 5\n  mode: 'on'\n"

ADMIN = "emb_" + "a" * 43
OPERATOR = "emb_" + "o" * 43
READER = "emb_" + "r" * 43
RESTRICTED = "emb_" + "x" * 43


def _key(key_id: str, scope: str, user_id: str | None, allowed: tuple[str, ...] | None = None) -> ApiKeyRow:
    return ApiKeyRow(
        id=key_id,
        user_id=user_id,
        name=f"{scope}-key",
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
    hash_token(ADMIN): _key("key-admin0000001", "admin", "usr-admin"),
    hash_token(OPERATOR): _key("key-operator0001", "operate", "usr-admin"),
    hash_token(READER): _key("key-reader000001", "read", None),
    hash_token(RESTRICTED): _key("key-restricted01", "admin", None, ("acme/widgets",)),
}


def _as(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}


def _user(email: str) -> Any:
    user = MagicMock()
    user.disabled = False
    user.email = email
    return user


@pytest.fixture
async def app(db_with_migrations: DatabasePool) -> AsyncIterator[Any]:
    from embry0.api.app import create_app

    bust_api_key_cache()
    with patch.dict(os.environ, {"ASK_REMIND_HOURS": "12"}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
    app = create_app(config)
    state = app.state
    state.db = db_with_migrations
    state.settings = SettingsStore(db_with_migrations, TEST_CATALOG)
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    state.api_keys_repo = keys
    users = MagicMock()
    users.get = AsyncMock(side_effect=lambda uid: _user("alex@example.com") if uid == "usr-admin" else None)
    state.users_repo = users
    yield app
    bust_api_key_cache()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_as(ADMIN)) as c:
        yield c


async def _put(client: AsyncClient, text: str, **kwargs: Any) -> Any:
    headers = {**YAML, **kwargs.pop("headers", {})}
    return await client.put(URL, content=text, headers=headers, **kwargs)


async def _audit(db: DatabasePool, action: str) -> list[dict[str, Any]]:
    rows = await db.fetch("SELECT actor, details FROM audit_log WHERE action = $1 ORDER BY id", action)
    return [dict(r) for r in rows]


# ---- reading and writing -------------------------------------------------------------


async def test_a_put_stores_the_yaml_as_written_and_get_returns_it(client) -> None:
    put = await _put(client, V1)
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["generation"] == 2 and isinstance(body["revision_id"], int)
    assert [c["key"] for c in body["changes"]] == ["sample.per_tick"]

    got = await client.get(URL)
    assert got.status_code == 200, got.text
    assert got.headers["etag"] == '"2"'
    doc = got.json()
    assert (doc["yaml_text"], doc["data"], doc["updated_by"]) == (V1, {"sample": {"per_tick": 4}}, "alex@example.com")
    assert doc["sections"] == ["sample", "other"]
    text = await client.get(URL, params={"format": "yaml"})
    assert (text.text, text.headers["content-type"].split(";")[0]) == (V1, "application/yaml")


async def test_a_json_object_is_stored_as_yaml(client, db_with_migrations: DatabasePool) -> None:
    put = await client.put(URL, json={"sample": {"per_tick": 6}})
    assert put.status_code == 200, put.text
    doc = (await client.get(URL)).json()
    assert (doc["yaml_text"], doc["data"]) == ("sample:\n  per_tick: 6\n", {"sample": {"per_tick": 6}})
    audit = await _audit(db_with_migrations, "settings.updated")
    assert [(a["actor"], a["details"]["keys"], a["details"]["source"]) for a in audit] == [
        ("alex@example.com", ["sample.per_tick"], "person")
    ]


# ---- #160 acceptance: each kind of bad value is a 422 that names the key ----


@pytest.mark.parametrize(
    ("text", "loc", "kind"),
    [
        ("sample:\n  per_tik: 4\n", ["sample", "per_tik"], "extra_forbidden"),
        ("sample:\n  enabled: maybe\n", ["sample", "enabled"], "bool_type"),
        ("sample:\n  per_tick: 50\n", ["sample", "per_tick"], "less_than_equal"),
    ],
    ids=["unknown key", "wrong type", "out of range"],
)
async def test_a_bad_value_is_a_422_that_names_the_key(client, text: str, loc: list[str], kind: str) -> None:
    resp = await _put(client, text)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert [(d["loc"], d["type"]) for d in detail] == [(loc, kind)]
    assert set(detail[0]) == {"loc", "type", "msg"}, "the rejected value is never echoed"
    assert (await client.get(URL)).json()["generation"] == 1, "a refused document stores nothing"


async def test_one_422_names_every_bad_key(client) -> None:
    resp = await _put(client, "sample:\n  per_tik: 4\n  enabled: maybe\n  per_tick: 50\nsampel: {per_tick: 1}\n")
    assert resp.status_code == 422, resp.text
    assert {tuple(d["loc"]) for d in resp.json()["detail"]} == {
        ("sample", "enabled"),
        ("sample", "per_tick"),
        ("sample", "per_tik"),
        ("sampel",),
    }
    assert len(resp.json()["detail"]) == 4


async def test_a_yaml_error_and_an_oversized_body_are_refused(client) -> None:
    broken = await _put(client, "sample:\n  per_tick: [1\n")
    assert broken.status_code == 422 and broken.json()["detail"][0]["type"] == "yaml_syntax", broken.text
    huge = await _put(client, "#" * (256 * 1024 + 1))
    assert huge.status_code == 413, huge.text


# ---- dry run and If-Match --------------------------------------------------------------


async def test_a_dry_run_answers_what_would_change_and_stores_nothing(client) -> None:
    await _put(client, V1)
    resp = await _put(client, V2, params={"dry_run": "1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["would_change"] is True
    assert body["text_diff"].startswith("--- stored\n+++ proposed\n")
    assert {c["key"]: (c["before"], c["after"], c["effective_after"]) for c in body["changes"]} == {
        "sample.per_tick": (4, 5, 5),
        "sample.mode": (None, "on", "on"),
    }
    assert (await client.get(URL)).json()["yaml_text"] == V1
    refused = await _put(client, "sample:\n  per_tick: 50\n", params={"dry_run": "1"})
    assert refused.status_code == 422, "a dry run refuses what a PUT would"


async def test_if_match_refuses_a_write_over_a_newer_version(client) -> None:
    await _put(client, V1)
    stale = (await client.get(URL)).headers["etag"]
    await _put(client, V2)
    resp = await _put(client, "sample:\n  per_tick: 7\n", headers={"If-Match": stale})
    assert resp.status_code == 409, resp.text
    fresh = (await client.get(URL)).headers["etag"]
    assert (await _put(client, "sample:\n  per_tick: 7\n", headers={"If-Match": fresh})).status_code == 200


# ---- who may write ----------------------------------------------------------------------


@pytest.mark.parametrize("token", [READER, OPERATOR, RESTRICTED], ids=["read key", "operate key", "restricted admin"])
async def test_only_an_unrestricted_admin_may_write(client, token: str) -> None:
    resp = await _put(client, V1, headers=_as(token))
    assert resp.status_code == 403, resp.text
    undo = await client.post(f"{URL}/revisions/1/undo", headers=_as(token))
    assert undo.status_code == 403, undo.text
    assert (await client.get(URL)).json()["generation"] == 1


async def test_any_key_may_read(client) -> None:
    await _put(client, V1)
    for path in ("", "/effective", "/schema", "/revisions"):
        resp = await client.get(URL + path, headers=_as(READER))
        assert resp.status_code == 200, f"{path}: {resp.text}"


# ---- #160 acceptance: a PUT followed by an undo restores the previous document ----


async def test_put_then_undo_restores_the_document_and_both_are_revisions(client, db_with_migrations) -> None:
    await _put(client, V1)
    second = (await _put(client, V2, params={"reason": "try the new mode"})).json()

    undo = await client.post(f"{URL}/revisions/{second['revision_id']}/undo")
    assert undo.status_code == 200, undo.text
    assert (await client.get(URL)).json()["yaml_text"] == V1

    revisions = (await client.get(f"{URL}/revisions")).json()["revisions"]
    assert [(r["id"], r["actor"], r["source"]) for r in revisions[:2]] == [
        (undo.json()["revision_id"], "alex@example.com", "restore"),
        (second["revision_id"], "alex@example.com", "person"),
    ]
    assert revisions[1]["reason"] == "try the new mode"
    assert [(r["undoable"], r["why_not"]) for r in revisions] == [(True, None), (False, WHY_NOT), (False, WHY_NOT)]
    assert revisions[1]["what"] == "Changed: sample.per_tick, sample.mode"
    audit = await _audit(db_with_migrations, "settings.change_undone")
    assert [a["details"]["revision_id"] for a in audit] == [second["revision_id"]]


async def test_an_older_change_cannot_be_undone(client) -> None:
    first = (await _put(client, V1)).json()
    await _put(client, V2)
    resp = await client.post(f"{URL}/revisions/{first['revision_id']}/undo")
    assert (resp.status_code, resp.json()["detail"]) == (409, WHY_NOT)
    missing = await client.post(f"{URL}/revisions/999999/undo")
    assert missing.status_code == 404


async def test_one_revision_shows_both_texts_and_its_changes(client) -> None:
    await _put(client, V1)
    second = (await _put(client, V2)).json()
    resp = await client.get(f"{URL}/revisions/{second['revision_id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["before_text"], body["after_text"], body["undoable"]) == (V1, V2, True)
    assert [c["key"] for c in body["changes"]] == ["sample.per_tick", "sample.mode"]
    assert (await client.get(f"{URL}/revisions/999999")).status_code == 404


async def test_embry0_s_own_import_is_shown_to_every_key(app, client) -> None:
    """The boot import is recorded under embry0's own actor, which names no person, so a read key sees it."""
    from embry0.settings.bootstrap import BOOTSTRAP_ACTOR

    await import_env_once(app.state.settings, app.state.config)
    reader = _as(READER)
    listed = (await client.get(f"{URL}/revisions", headers=reader)).json()["revisions"]
    assert [r["actor"] for r in listed] == [BOOTSTRAP_ACTOR], f"a read key must see embry0's own actor: {listed}"
    assert (await client.get(URL, headers=reader)).json()["updated_by"] == BOOTSTRAP_ACTOR
    sample = next(
        s for s in (await client.get(f"{URL}/effective", headers=reader)).json()["sections"] if s["name"] == "sample"
    )
    assert {k["key"]: k["set_by"] for k in sample["keys"]}["remind_hours"] == BOOTSTRAP_ACTOR


# ---- effective values and the schema -----------------------------------------------------


async def test_effective_values_say_where_each_came_from(app, client) -> None:
    await import_env_once(app.state.settings, app.state.config)
    await _put(client, (await client.get(URL)).json()["yaml_text"] + "  per_tick: 4\n")

    resp = await client.get(f"{URL}/effective")
    assert resp.status_code == 200, resp.text
    sample = next(s for s in resp.json()["sections"] if s["name"] == "sample")
    keys = {k["key"]: k for k in sample["keys"]}
    assert (keys["remind_hours"]["value"], keys["remind_hours"]["source"]) == (12.0, "bootstrap")
    assert (keys["remind_hours"]["env"], keys["remind_hours"]["env_still_set"]) == ("ASK_REMIND_HOURS", True)
    assert (keys["per_tick"]["value"], keys["per_tick"]["source"], keys["per_tick"]["set_by"]) == (
        4,
        "document",
        "alex@example.com",
    )
    assert keys["per_tick"]["overridable"] == ["organization"]
    assert (keys["mode"]["value"], keys["mode"]["default"], keys["mode"]["source"]) == ("shadow", "shadow", "default")
    assert keys["mode"]["set_by"] is None and keys["mode"]["env"] is None


async def test_the_schema_names_each_section_and_its_variables(client) -> None:
    resp = await client.get(f"{URL}/schema")
    assert resp.status_code == 200, resp.text
    schema = resp.json()
    assert set(schema["properties"]) == {"version", "managed_by", "sample", "other"}
    assert schema["x-embry0-sections"]["sample"]["env"]["remind_hours"] == "ASK_REMIND_HOURS"
    assert schema["x-embry0-sections"]["sample"]["repo_keys"] == ["mode"]


# ---- a rollback, checks that raise, and what the database refuses --------------------------


class _SampleLater(SectionModel):
    """``sample`` as a newer release has it: one more key."""

    enabled: bool = Field(default=False, description="Run the sample loop.")
    per_tick: int = Field(default=3, ge=1, le=20, description="Items the sample loop handles each tick.")
    burst: int = Field(default=1, ge=1, description="A key the newer release added.")


class _Later(SectionModel):
    hours: str = Field(default="09-18", description="Working hours, written back as HH-HH.")

    @field_validator("hours")
    @classmethod
    def _two_digits(cls, value: str) -> str:
        start, _, end = value.partition("-")
        return f"{int(start):02d}-{int(end):02d}"


class _Gone(SectionModel):
    limit: int = Field(default=1, description="A section the newer release knows.")


SAMPLE_LATER = Section(name="sample", model=_SampleLater, summary="Sample, newer.")
LATER = Section(name="later", model=_Later, summary="A newer section.")
NEWER = Catalog((SAMPLE_LATER, LATER, Section(name="gone", model=_Gone, summary="Another newer section.")))
WRITTEN_BY_NEWER = "# written by a newer release\nsample:\n  per_tick: 4\n  burst: 2\nlater:\n  hours: 8-17\ngone:\n"


async def test_after_a_rollback_the_stored_document_can_still_be_written(app, client, db_with_migrations) -> None:
    app.state.settings = SettingsStore(db_with_migrations, NEWER)
    assert (await _put(client, WRITTEN_BY_NEWER)).status_code == 200
    app.state.settings = SettingsStore(db_with_migrations, TEST_CATALOG)  # the release before it

    resp = await _put(client, WRITTEN_BY_NEWER.replace("per_tick: 4", "per_tick: 5"))

    assert resp.status_code == 200, f"a rollback made the stored document unwritable: {resp.text}"
    assert (resp.json()["unknown_sections"], resp.json()["unknown_keys"]) == (["gone", "later"], ["sample.burst"])
    app.state.settings = SettingsStore(db_with_migrations, NEWER)  # and forward again
    snapshot = await app.state.settings.snapshot()
    assert (snapshot.get(SAMPLE_LATER).burst, snapshot.get(LATER).hours) == (2, "08-17"), (
        "what the newer release wrote survives the round trip"
    )


async def test_a_check_that_raises_keyerror_is_a_422_not_a_500(app, db_with_migrations) -> None:
    app.state.settings = SettingsStore(db_with_migrations, Catalog((BRITTLE,)))
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_as(ADMIN)) as raw:
        resp = await raw.put(URL, content="brittle:\n  zone: Mars/Base\n", headers=YAML)
    assert resp.status_code == 422, f"a validator's KeyError escaped as {resp.status_code}"
    assert [(d["loc"], d["type"]) for d in resp.json()["detail"]] == [(["brittle", "zone"], "check_failed")]


async def test_a_nul_character_is_a_422_not_a_database_error(app) -> None:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_as(ADMIN)) as raw:
        resp = await raw.put(URL, json={"sample": {"labels": ["a\u0000b"]}})
    assert resp.status_code == 422, f"Postgres was handed a NUL character and answered {resp.status_code}"
    assert [d["type"] for d in resp.json()["detail"]] == ["nul_character"]


# ---- If-Match --------------------------------------------------------------------------------


async def test_a_dry_run_honours_if_match(client) -> None:
    await _put(client, V1)
    stale = (await client.get(URL)).headers["etag"]
    await _put(client, V2)
    resp = await _put(client, V1, params={"dry_run": "1"}, headers={"If-Match": stale})
    assert resp.status_code == 409, f"a dry run answered {resp.status_code} for a stale If-Match"


async def test_if_match_star_matches_any_version(client) -> None:
    resp = await _put(client, V1, headers={"If-Match": "*"})
    assert resp.status_code == 200, f"If-Match: * answered {resp.status_code}: {resp.text}"


async def test_a_weak_etag_never_matches(client) -> None:
    current = (await client.get(URL)).headers["etag"]
    resp = await _put(client, V1, headers={"If-Match": f"W/{current}"})
    assert resp.status_code == 409, f"a weak tag matched (If-Match compares strongly): {resp.status_code}"


async def test_undo_answers_like_a_put(client) -> None:
    await _put(client, V1)
    second = (await _put(client, V2)).json()
    undo = (await client.post(f"{URL}/revisions/{second['revision_id']}/undo")).json()
    assert set(undo) == set(second), f"undo's answer is not a PUT's: {sorted(undo)} against {sorted(second)}"
    assert {"generation", "revision_id", "changes", "unknown_sections", "unknown_keys"} <= set(undo)


async def test_after_a_rollback_a_json_client_can_send_back_the_stored_values(app, client, db_with_migrations) -> None:
    """What #162's CLI and MCP do: read GET's ``data``, change a key, PUT it as JSON."""
    app.state.settings = SettingsStore(db_with_migrations, NEWER)
    assert (await _put(client, WRITTEN_BY_NEWER)).status_code == 200
    app.state.settings = SettingsStore(db_with_migrations, TEST_CATALOG)
    data = (await client.get(URL)).json()["data"]
    data["sample"]["per_tick"] = 5

    resp = await client.put(URL, json=data)

    assert resp.status_code == 200, f"the stored values' own form was refused after a rollback: {resp.text}"


async def test_after_a_rollback_a_dry_run_and_an_undo_compare_with_the_stored_text(
    app, client, db_with_migrations
) -> None:
    """Production passes the stored text to the dry run and to undo, not only to a PUT."""
    app.state.settings = SettingsStore(db_with_migrations, NEWER)
    await _put(client, WRITTEN_BY_NEWER)
    second = (await _put(client, WRITTEN_BY_NEWER.replace("per_tick: 4", "per_tick: 5"))).json()
    app.state.settings = SettingsStore(db_with_migrations, TEST_CATALOG)

    dry = await _put(client, WRITTEN_BY_NEWER.replace("per_tick: 4", "per_tick: 6"), params={"dry_run": "1"})
    assert dry.status_code == 200, f"the dry run refused what a PUT accepts: {dry.text}"
    undo = await client.post(f"{URL}/revisions/{second['revision_id']}/undo")
    assert undo.status_code == 200, f"undo refused what a PUT accepts: {undo.text}"


async def test_an_undo_of_a_text_too_large_is_a_422_not_a_500(app, db_with_migrations) -> None:
    store: SettingsStore = app.state.settings
    await store.save(V1, {"sample": {"per_tick": 4}}, actor="alex@example.com")
    current = {"yaml_text": V1, "data": {"sample": {"per_tick": 4}}, "schema_version": 1}
    huge = {"yaml_text": "#" * (256 * 1024 + 1), "data": {}, "schema_version": 1}
    revision_id = await db_with_migrations.fetchval(
        "INSERT INTO config_revisions (scope, surface, before, after, actor, source) "
        "VALUES ('deployment', 'settings', $1::jsonb, $2::jsonb, 'alex@example.com', 'person') RETURNING id",
        huge,
        current,
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_as(ADMIN)) as raw:
        resp = await raw.post(f"{URL}/revisions/{revision_id}/undo")
    assert resp.status_code == 422, f"undoing to a text over the size limit answered {resp.status_code}"


# ---- review of #211: shared body reader, JSON keys, who changed it ----


async def test_a_chunked_body_past_the_limit_is_refused_while_it_streams(client) -> None:
    """No Content-Length: the body is read in pieces and refused once it passes 256 KiB, never held whole."""

    sent: list[int] = []

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(64):  # 64 x 16 KiB = 1 MiB
            sent.append(1)
            yield b"#" * (16 * 1024)

    resp = await client.put(URL, content=chunks(), headers=YAML)
    assert resp.status_code == 413, f"a chunked body past the limit must be a 413: {resp.status_code} {resp.text}"
    assert "content-length" not in {k.lower() for k in resp.request.headers}, "the body must go without a length"
    assert len(sent) < 64, f"the body was read whole ({len(sent)} of 64 pieces) before it was refused"


async def test_a_json_key_written_twice_is_a_422(client) -> None:
    resp = await client.put(
        URL,
        content=b'{"sample": {"per_tick": 1}, "sample": {"per_tick": 2}}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422, f"JSON would keep only the second 'sample' silently: {resp.text}"
    assert [item["type"] for item in resp.json()["detail"]] == ["duplicate_key"]


async def test_who_changed_the_document_is_shown_as_the_audit_log_shows_actors(client) -> None:
    """An admin sees every actor; anyone else sees their own, and null for anyone else's (as ``GET /audit``)."""
    await _put(client, V1)  # written with the admin key, by alex@example.com
    revision_id = (await client.get(f"{URL}/revisions")).json()["revisions"][0]["id"]

    reader = _as(READER)  # a read key with no owner: its own actor is key:read-key
    listed = (await client.get(f"{URL}/revisions", headers=reader)).json()["revisions"]
    assert [r["actor"] for r in listed] == [None], "a read key must not learn who changed the document"
    assert (await client.get(f"{URL}/revisions/{revision_id}", headers=reader)).json()["actor"] is None
    assert (await client.get(URL, headers=reader)).json()["updated_by"] is None
    effective = (await client.get(f"{URL}/effective", headers=reader)).json()
    assert {k["set_by"] for s in effective["sections"] for k in s["keys"]} == {None}

    own = (await client.get(f"{URL}/revisions", headers=_as(OPERATOR))).json()["revisions"]
    assert [r["actor"] for r in own] == ["alex@example.com"], "anyone sees their own changes"
    admin = (await client.get(f"{URL}/revisions/{revision_id}")).json()
    assert admin["actor"] == "alex@example.com", "an admin sees every actor"


# ---- #162: a document kept in a file -----------------------------------------------------


async def test_managed_by_file_is_served_and_turning_it_on_is_a_named_change(client) -> None:
    """The dashboard reads managed_by from GET to make its editor read-only; the history names the switch."""
    await _put(client, V1)
    assert (await client.get(URL)).json()["managed_by"] is None, "a document edited on the dashboard says null"
    on = await _put(client, "managed_by: file\n" + V1, params={"source": "file"})
    assert on.status_code == 200, on.text
    assert [c["key"] for c in on.json()["changes"]] == ["managed_by"]
    switch = on.json()["changes"][0]
    assert (switch["before"], switch["after"], switch["set_before"], switch["set_after"]) == (None, "file", False, True)
    assert "effective_after" not in switch, "no loop reads managed_by, so it has no value in effect to show"
    doc = (await client.get(URL)).json()
    assert (doc["managed_by"], doc["unknown_sections"]) == ("file", []), "managed_by is not a section nobody knows"
    newest = (await client.get(f"{URL}/revisions")).json()["revisions"][0]
    assert (newest["what"], newest["source"]) == ("Changed: managed_by", "file")


async def test_offline_check_prints_what_the_api_s_dry_run_refuses_with(client, monkeypatch, tmp_path, capsys) -> None:
    """`embry0 settings check` and `diff` must read alike: the real 422 of a dry run, printed the way
    `diff` prints it, is line for line what the offline check prints for the same file."""
    from argparse import Namespace

    from embry0.cli.settings import REFUSED, problem_lines, run

    bad = "sampel:\n  x: 1\nsample:\n  enabled: maybe\n  per_tick: 50\n  zone: Mars/Base\nother:\n  limit: -1\n"
    resp = await _put(client, bad, params={"dry_run": 1, "source": "file"})
    assert resp.status_code == 422, resp.text
    from_api = problem_lines(resp.json()["detail"])
    assert len(from_api) == 5, from_api

    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)  # the catalog the app was built with
    path = tmp_path / "settings.yaml"
    path.write_text(bad, encoding="utf-8")
    assert run(Namespace(action="check", file=str(path))) == REFUSED
    offline = [line.strip() for line in capsys.readouterr().err.splitlines()[1:]]
    assert offline == from_api, "check must print exactly the problems the API refuses the same file with"
