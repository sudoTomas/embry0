"""Some keys of one section through the API: ``PATCH /config/settings/{section}`` (#159).

A form field on another page (a background model, a budget cap) changes one
setting without sending the whole document. Driven through the real app on a
real database, with API keys so the tier and the repository allowlist are the
ones production checks, and the test-only sections so nothing depends on
which features exist yet. Each rule a ``PUT`` follows is pinned for the
``PATCH`` too.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import Field

from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.settings.registry import Catalog, Section, SectionModel
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.organizations import OrganizationsRepository
from embry0.storage.repositories.repos import ReposRepository
from tests.fixtures.settings_sections import LAYERED_CATALOG, SAMPLE, WINDOW

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
YAML = {"Content-Type": "application/yaml"}

ADMIN = "emb_" + "a" * 43
OPERATOR = "emb_" + "o" * 43
READER = "emb_" + "r" * 43
WIDGETS_ONLY = "emb_" + "w" * 43

#: A document an operator wrote by hand: comments above, beside and between its keys.
COMMENTED = (
    "# Tuned for the week of the launch.\n"
    "sample:\n"
    "  # Four keeps the queue short.\n"
    "  per_tick: 4  # was 3\n"
    "  mode: 'on'\n"
    "\n"
    "# The second section.\n"
    "other:\n"
    "  limit: 2\n"
)

#: What a PUT answers, which a PATCH answers too, with ``managed_by`` beside it.
PUT_ANSWER = {
    "scope",
    "generation",
    "revision_id",
    "changes",
    "unknown_sections",
    "unknown_keys",
    "layers_refused",
    "layers_refused_total",
}


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
    hash_token(WIDGETS_ONLY): _key("key-widgets00001", "admin", None, ("acme/widgets",)),
}


def _as(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
async def app(db_with_migrations: DatabasePool) -> AsyncIterator[Any]:
    from embry0.api.app import create_app

    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
    app = create_app(config)
    state = app.state
    state.db = db_with_migrations
    state.settings = SettingsStore(db_with_migrations, LAYERED_CATALOG)
    state.organizations_repo = OrganizationsRepository(db_with_migrations)
    state.repos_repo = ReposRepository(db_with_migrations, default_forge="github")
    await state.organizations_repo.register("acme", "github")
    for repo in ("acme/widgets", "acme/gadgets"):
        await state.repos_repo.register(repo)
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    state.api_keys_repo = keys
    user = MagicMock(disabled=False, email="alex@example.com")
    users = MagicMock()
    users.get = AsyncMock(side_effect=lambda uid: user if uid == "usr-admin" else None)
    state.users_repo = users
    yield app
    bust_api_key_cache()


def _client(app: Any, token: str = ADMIN) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_as(token))


async def _put(client: AsyncClient, text: str, **params: Any) -> Any:
    headers = params.pop("headers", {})
    return await client.put(URL, content=text, headers={**YAML, **headers}, params=params)


async def _patch(client: AsyncClient, section: str, values: Any, **params: Any) -> Any:
    headers = params.pop("headers", {})
    return await client.patch(f"{URL}/{section}", json=values, headers=headers, params=params)


async def _audit(db: DatabasePool) -> list[dict[str, Any]]:
    rows = await db.fetch("SELECT actor, details FROM audit_log WHERE action = 'settings.updated' ORDER BY id")
    return [dict(r) for r in rows]


def _effective(resp: Any, section: str) -> dict[str, dict[str, Any]]:
    assert resp.status_code == 200, resp.text
    return {k["key"]: k for s in resp.json()["sections"] if s["name"] == section for k in s["keys"]}


# ---- the stored text: comments and layout stay ------------------------------------------


async def test_a_key_is_changed_where_it_stands_and_every_comment_stays(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, COMMENTED)
        resp = await _patch(client, "sample", {"per_tick": 6})
        doc = await client.get(URL)
    assert resp.status_code == 200, f"a one-key change was not accepted: {resp.status_code} {resp.text}"
    assert doc.json()["yaml_text"] == COMMENTED.replace("per_tick: 4", "per_tick: 6"), (
        f"the change did not keep the document's comments and layout:\n{doc.json()['yaml_text']}"
    )
    assert doc.json()["data"] == {"sample": {"per_tick": 6, "mode": "on"}, "other": {"limit": 2}}
    body = resp.json()
    assert set(body) == PUT_ANSWER | {"managed_by"}, f"a PATCH must answer as a PUT does: {sorted(body)}"
    assert body["generation"] == int(doc.headers["etag"].strip('"')) and isinstance(body["revision_id"], int)
    assert [(c["key"], c["before"], c["after"], c["effective_after"]) for c in body["changes"]] == [
        ("sample.per_tick", 4, 6, 6)
    ]
    assert body["scope"] == {"layer": "deployment", "owner": None, "repo": None}


async def test_null_takes_a_key_out_and_it_reads_its_default_again(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, COMMENTED)
        resp = await _patch(client, "sample", {"mode": None})
        doc = await client.get(URL)
        effective = await client.get(f"{URL}/effective")
    assert resp.status_code == 200, f"null did not unset the key: {resp.status_code} {resp.text}"
    assert doc.json()["yaml_text"] == COMMENTED.replace("  mode: 'on'\n", ""), (
        f"taking a key out must remove its line and nothing else:\n{doc.json()['yaml_text']}"
    )
    assert doc.json()["data"] == {"sample": {"per_tick": 4}, "other": {"limit": 2}}
    change = resp.json()["changes"][0]
    assert (change["key"], change["set_before"], change["set_after"], change["effective_after"]) == (
        "sample.mode",
        True,
        False,
        "shadow",
    )
    mode = _effective(effective, "sample")["mode"]
    assert (mode["value"], mode["source"]) == ("shadow", "default"), f"the key did not read its default: {mode}"


async def test_a_new_key_goes_at_the_end_of_its_section_and_a_new_section_at_the_end(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, COMMENTED)
        added = await _patch(client, "sample", {"remind_hours": 2.5})
        appended = await _patch(client, "window", {"low": 20})
        doc = await client.get(URL)
    assert (added.status_code, appended.status_code) == (200, 200), f"{added.text} {appended.text}"
    expected = COMMENTED.replace("  mode: 'on'\n", "  mode: 'on'\n  remind_hours: 2.5\n") + "\nwindow:\n  low: 20\n"
    assert doc.json()["yaml_text"] == expected, f"the keys were not written into the text:\n{doc.json()['yaml_text']}"


async def test_keys_left_as_they_are_record_nothing(app: Any, db_with_migrations: DatabasePool) -> None:
    """A value written another way (double quotes, a list on its lines) is still the same value: the text
    is left as written, and nothing is recorded."""
    text = '# by hand\nsample:\n  mode: "on"  # double quotes\n  labels:\n    - a  # first\n'
    async with _client(app) as client:
        await _put(client, text)
        before = await client.get(URL)
        same = await _patch(client, "sample", {"mode": "on", "labels": ["a"], "remind_hours": None})
        empty = await _patch(client, "sample", {})
        after = await client.get(URL)
    for resp in (same, empty):
        assert resp.status_code == 200, resp.text
        assert (resp.json()["revision_id"], resp.json()["changes"]) == (None, []), (
            f"a change that changes nothing was recorded: {resp.json()}"
        )
    assert after.headers["etag"] == before.headers["etag"], "a change of nothing wrote the document"
    assert after.json()["yaml_text"] == text, "a value already held was written again another way"
    assert len(await _audit(db_with_migrations)) == 1, "only the PUT is audited"


async def test_a_number_equal_to_the_stored_one_records_nothing(app: Any, db_with_migrations: DatabasePool) -> None:
    """A form that sends ``12`` for a number stored as ``12.0`` changes no setting, so nothing is recorded."""
    text = "sample:\n  remind_hours: 12.0  # half a day\n"
    async with _client(app) as client:
        await _put(client, text)
        before = await client.get(URL)
        resp = await _patch(client, "sample", {"remind_hours": 12})
        after = await client.get(URL)
    assert resp.status_code == 200, resp.text
    assert (resp.json()["revision_id"], resp.json()["changes"]) == (None, []), (
        f"a value the section reads as the stored one was recorded as a change: {resp.json()}"
    )
    assert (after.headers["etag"], after.json()["yaml_text"]) == (before.headers["etag"], text)
    assert len(await _audit(db_with_migrations)) == 1, "only the PUT is audited"


# ---- the same rules as a PUT ---------------------------------------------------------------


async def test_a_change_is_a_revision_with_its_reason_and_an_audit_row(app: Any, db_with_migrations) -> None:
    async with _client(app) as client:
        await _put(client, COMMENTED)
        resp = await _patch(client, "sample", {"per_tick": 7}, reason="a busy week")
        newest = (await client.get(f"{URL}/revisions")).json()["revisions"][0]
        undo = await client.post(f"{URL}/revisions/{resp.json()['revision_id']}/undo")
        restored = await client.get(URL)
    assert resp.status_code == 200, resp.text
    assert (newest["id"], newest["source"], newest["reason"], newest["actor"], newest["what"]) == (
        resp.json()["revision_id"],
        "person",
        "a busy week",
        "alex@example.com",
        "Changed: sample.per_tick",
    ), f"the change was not recorded as a person's revision: {newest}"
    assert undo.status_code == 200, f"the change could not be undone like any other: {undo.text}"
    assert restored.json()["yaml_text"] == COMMENTED
    audit = await _audit(db_with_migrations)
    assert [(a["actor"], a["details"]) for a in audit[1:2]] == [
        (
            "alex@example.com",
            {
                "scope": "deployment",
                "revision_id": resp.json()["revision_id"],
                "generation": resp.json()["generation"],
                "keys": ["sample.per_tick"],
                "source": "person",
                "reason": "a busy week",
            },
        )
    ], f"the change was not audited as a PUT is: {audit}"


async def test_every_bad_key_is_one_422_that_never_echoes_a_value(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, COMMENTED)
        before = await client.get(URL)
        resp = await _patch(
            client, "sample", {"per_tik": 5, "per_tick": 50, "enabled": "s3cret-ish-value", "labelz": None}
        )
        section = await _patch(client, "sampel", {"per_tick": 5})
        after = await client.get(URL)
    assert resp.status_code == 422, f"bad keys were accepted: {resp.status_code} {resp.text}"
    detail = resp.json()["detail"]
    assert sorted((tuple(d["loc"]), d["type"]) for d in detail) == [
        (("sample", "enabled"), "bool_type"),
        (("sample", "labelz"), "extra_forbidden"),
        (("sample", "per_tick"), "less_than_equal"),
        (("sample", "per_tik"), "extra_forbidden"),
    ], f"the 422 must name every bad key at once, a key taken out included: {detail}"
    assert all(set(d) == {"loc", "type", "msg"} for d in detail), "a problem carries no input"
    assert "s3cret-ish-value" not in resp.text, "a rejected value was echoed"
    typo = next(d for d in detail if d["loc"] == ["sample", "per_tik"])
    assert typo["msg"] == "'per_tik' is not a setting of 'sample'. Did you mean 'per_tick'?"
    assert section.status_code == 422, section.text
    assert section.json()["detail"] == [
        {
            "loc": ["sampel"],
            "type": "unknown_section",
            "msg": "'sampel' is not a settings section. Did you mean 'sample'?",
        }
    ]
    assert after.headers["etag"] == before.headers["etag"], "a refused change stored something"


async def test_a_change_is_checked_with_the_keys_it_meets(app: Any) -> None:
    """The keys of a section are checked together, here ``high`` against ``low``, as a PUT checks them."""
    async with _client(app) as client:
        await _put(client, "window:\n  low: 30\n")
        resp = await _patch(client, "window", {"high": 20})
        dry = await _patch(client, "window", {"high": 20}, dry_run="1")
    assert resp.status_code == 422, f"a value that clashes with its section's other key was stored: {resp.text}"
    assert [(d["loc"], d["type"], d["msg"]) for d in resp.json()["detail"]] == [
        (["window", "high"], "value_error", "high must not be below low")
    ]
    assert dry.status_code == 422, "a dry run refuses what the change would"


async def test_the_whole_document_it_makes_is_checked(app: Any, db_with_migrations: DatabasePool) -> None:
    """A value this release now refuses elsewhere in the document refuses the change, as a PUT of the
    same text is refused; taking that value out is the way on."""

    class _LooseOther(SectionModel):
        limit: int = Field(default=5, description="Before the bound came in.")

    loose = Catalog((SAMPLE, Section(name="other", model=_LooseOther, summary="Looser."), WINDOW))
    app.state.settings = SettingsStore(db_with_migrations, loose)
    async with _client(app) as client:
        assert (await _put(client, "sample:\n  per_tick: 4\nother:\n  limit: -1\n")).status_code == 200
    app.state.settings = SettingsStore(db_with_migrations, LAYERED_CATALOG)
    async with _client(app) as client:
        refused = await _patch(client, "sample", {"per_tick": 5})
        fixed = await _patch(client, "other", {"limit": None})
        then = await _patch(client, "sample", {"per_tick": 5})
    assert refused.status_code == 422, f"the change stored a document this release refuses: {refused.text}"
    assert [(d["loc"], d["type"]) for d in refused.json()["detail"]] == [(["other", "limit"], "greater_than_equal")]
    assert (fixed.status_code, then.status_code) == (200, 200), f"{fixed.text} {then.text}"


async def test_if_match_refuses_a_change_over_a_newer_version_and_without_it_nothing_is_lost(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, COMMENTED)
        stale = (await client.get(URL)).headers["etag"]
        moved = await _patch(client, "sample", {"per_tick": 5})
        refused = await _patch(client, "other", {"limit": 3}, headers={"If-Match": stale})
        dry = await _patch(client, "other", {"limit": 3}, dry_run="1", headers={"If-Match": stale})
        fresh = (await client.get(URL)).headers["etag"]
        accepted = await _patch(client, "other", {"limit": 3}, headers={"If-Match": fresh})
        doc = await client.get(URL)
    assert moved.status_code == 200, f"a change without If-Match goes into what is stored: {moved.text}"
    assert refused.status_code == 409, f"a stale If-Match was not refused: {refused.status_code} {refused.text}"
    assert "changed since you read them" in refused.json()["detail"]
    assert dry.status_code == 409, f"a dry run answered {dry.status_code} for a stale If-Match"
    assert accepted.status_code == 200, accepted.text
    assert doc.json()["data"] == {"sample": {"per_tick": 5, "mode": "on"}, "other": {"limit": 3}}, (
        "one change of keys lost another"
    )


async def test_a_dry_run_says_what_would_change_and_stores_nothing(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, COMMENTED)
        resp = await _patch(client, "sample", {"per_tick": 6}, dry_run="1")
        doc = await client.get(URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["would_change"] is True and body["managed_by"] is None
    assert "-  per_tick: 4  # was 3\n+  per_tick: 6  # was 3\n" in body["text_diff"], body["text_diff"]
    assert [(c["key"], c["before"], c["after"]) for c in body["changes"]] == [("sample.per_tick", 4, 6)]
    assert doc.json()["yaml_text"] == COMMENTED, "a dry run stored the change"


@pytest.mark.parametrize(
    ("body", "kind"),
    [(b"[1, 2]", "not_a_mapping"), (b'{"per_tick": 1, "per_tick": 2}', "duplicate_key"), (b"{per_tick", "json_syntax")],
    ids=["not an object", "a key twice", "not JSON"],
)
async def test_a_body_that_is_not_one_object_of_keys_is_a_422(app: Any, body: bytes, kind: str) -> None:
    async with _client(app) as client:
        resp = await client.patch(f"{URL}/sample", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 422, f"{kind}: {resp.status_code} {resp.text}"
    assert [d["type"] for d in resp.json()["detail"]] == [kind]


async def test_a_change_that_makes_the_document_too_large_is_a_413(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, "# " + "x" * (200 * 1024) + "\nsample:\n  per_tick: 4\n")
        before = await client.get(URL)
        resp = await _patch(client, "sample", {"labels": ["y" * 30_000] * 3})
        after = await client.get(URL)
    assert resp.status_code == 413, f"a document past 256 KiB was stored: {resp.status_code}"
    assert after.headers["etag"] == before.headers["etag"]


# ---- layers ------------------------------------------------------------------------------------


async def test_a_layer_takes_a_key_it_may_set_and_refuses_one_it_may_not(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, "sample:\n  per_tick: 4\n")
        org = await _patch(client, "sample", {"per_tick": 6}, owner="acme", headers={"If-Match": '"0"'})
        repo = await _patch(client, "sample", {"mode": "on"}, repo="acme/widgets")
        refused = await _patch(client, "sample", {"enabled": True}, owner="acme")
        unset_refused = await _patch(client, "sample", {"mode": None}, owner="acme")
        widgets = await client.get(f"{URL}/effective", params={"repo": "acme/widgets"})
        layer = await client.get(URL, params={"owner": "acme"})
        deployment = await client.get(URL)
    assert org.status_code == 200, f"an organization's layer did not take a key it may set: {org.text}"
    assert org.json()["scope"] == {"layer": "organization", "owner": "acme", "repo": None}
    assert repo.status_code == 200, repo.text
    assert (layer.json()["data"], layer.json()["generation"]) == ({"sample": {"per_tick": 6}}, 1)
    keys = _effective(widgets, "sample")
    assert (keys["per_tick"]["value"], keys["per_tick"]["source"]) == (6, "organization")
    assert (keys["mode"]["value"], keys["mode"]["source"]) == ("on", "repository")
    assert deployment.json()["yaml_text"] == "sample:\n  per_tick: 4\n", "the deployment's document was touched"
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == [
        {
            "loc": ["sample", "enabled"],
            "type": "not_overridable",
            "msg": "An organization cannot set 'enabled'. In 'sample' it can set per_tick, remind_hours.",
        }
    ]
    assert unset_refused.status_code == 422, "taking out a key the layer may not set, and has not, is a mistake"
    assert [(d["loc"], d["type"]) for d in unset_refused.json()["detail"]] == [(["sample", "mode"], "not_overridable")]


async def test_null_in_a_layer_hands_the_key_back_to_what_it_inherits(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, "sample:\n  per_tick: 4\n")
        await _patch(client, "sample", {"per_tick": 6, "remind_hours": 2}, owner="acme")
        resp = await _patch(client, "sample", {"per_tick": None}, owner="acme")
        acme = await client.get(f"{URL}/effective", params={"owner": "acme"})
    assert resp.status_code == 200, resp.text
    per_tick = _effective(acme, "sample")["per_tick"]
    assert (per_tick["value"], per_tick["source"]) == (4, "document"), f"the layer still set per_tick: {per_tick}"
    assert resp.json()["changes"][0]["effective_after"] == 4, "for a layer, the value after is what it inherits"


async def test_a_layer_value_that_clashes_with_what_it_inherits_is_refused(app: Any) -> None:
    async with _client(app) as client:
        await _patch(client, "window", {"high": 50})
        resp = await _patch(client, "window", {"low": 70}, owner="acme")
    assert resp.status_code == 422, resp.text
    assert [(d["loc"], d["type"]) for d in resp.json()["detail"]] == [(["window"], "conflicts_with_inherited")]


async def test_a_deployment_change_names_the_layer_values_it_overrules(app: Any) -> None:
    async with _client(app) as client:
        await _patch(client, "window", {"high": 80})
        await _patch(client, "window", {"low": 70}, owner="acme")
        preview = await _patch(client, "window", {"high": 50}, dry_run="1")
        saved = await _patch(client, "window", {"high": 50})
    acme = {"layer": "organization", "owner": "acme", "repo": None}
    expected = [{"scope": acme, "key": "window.low", "reason": "high must not be below low"}]
    assert (preview.json()["layers_refused"], preview.json()["layers_refused_total"]) == (expected, 1), preview.text
    assert saved.status_code == 200 and saved.json()["layers_refused"] == expected, saved.text


async def test_only_an_admin_changes_keys_and_an_allowlist_only_its_repositories(app: Any) -> None:
    async with _client(app, READER) as reader, _client(app, OPERATOR) as operator, _client(app, WIDGETS_ONLY) as one:
        read = await _patch(reader, "sample", {"per_tick": 6})
        operate = await _patch(operator, "sample", {"per_tick": 6})
        deployment = await _patch(one, "sample", {"per_tick": 6})
        org = await _patch(one, "sample", {"per_tick": 6}, owner="acme")
        other = await _patch(one, "sample", {"mode": "on"}, repo="acme/gadgets")
        own = await _patch(one, "sample", {"mode": "on"}, repo="acme/widgets")
    async with _client(app) as admin:
        unknown = await _patch(admin, "sample", {"per_tick": 6}, owner="initech")
        both = await _patch(admin, "sample", {"per_tick": 6}, owner="acme", repo="acme/widgets")
        stored = await admin.get(URL)
    for name, resp in {"read": read, "operate": operate, "deployment": deployment, "org": org, "other": other}.items():
        assert resp.status_code == 403, f"{name}: {resp.status_code} {resp.text}"
    assert own.status_code == 200, f"a key whose allowlist holds the repository could not change its layer: {own.text}"
    assert unknown.status_code == 404, unknown.text
    assert (both.status_code, both.json()["detail"]) == (422, "Name one layer: owner or repo, not both.")
    assert stored.json()["data"] == {}, "a refused key changed the deployment's document"


# ---- a document kept in a file, and one a newer release wrote -----------------------------------


async def test_a_document_kept_in_a_file_is_changed_as_a_put_changes_it_and_says_so(app: Any) -> None:
    """A PUT refuses nothing for ``managed_by: file``; a change of keys neither, and the answer tells the
    caller, which has not read the document, so it can say the next apply of the file puts it back."""
    async with _client(app) as client:
        await _put(client, "managed_by: file\n" + COMMENTED, source="file")
        dry = await _patch(client, "sample", {"per_tick": 6}, dry_run="1")
        resp = await _patch(client, "sample", {"per_tick": 6})
        doc = await client.get(URL)
        newest = (await client.get(f"{URL}/revisions")).json()["revisions"][0]
    assert resp.status_code == 200, resp.text
    assert (dry.json()["managed_by"], resp.json()["managed_by"]) == ("file", "file"), (
        "the answer must say the document is kept in a file"
    )
    assert doc.json()["managed_by"] == "file", "a change of keys turned managed_by off"
    assert doc.json()["yaml_text"] == "managed_by: file\n" + COMMENTED.replace("per_tick: 4", "per_tick: 6")
    assert (newest["source"], newest["what"]) == ("person", "Changed: sample.per_tick")


async def test_a_key_or_section_a_newer_release_wrote_is_kept_and_can_be_taken_out(
    app: Any, db_with_migrations
) -> None:
    class _SampleLater(SectionModel):
        per_tick: int = Field(default=3, ge=1, le=20, description="Items each tick.")
        burst: int = Field(default=1, ge=1, description="A key the newer release added.")

    class _Later(SectionModel):
        hours: str = Field(default="09-18", description="A section the newer release added.")

    newer = Catalog(
        (
            Section(name="sample", model=_SampleLater, summary="Newer."),
            Section(name="later", model=_Later, summary="New."),
        )
    )
    text = "sample:\n  per_tick: 4\n  burst: 2\nlater:\n  hours: '08-17'\n"
    app.state.settings = SettingsStore(db_with_migrations, newer)
    async with _client(app) as client:
        assert (await _put(client, text)).status_code == 200
    app.state.settings = SettingsStore(db_with_migrations, LAYERED_CATALOG)  # the release before it
    async with _client(app) as client:
        kept = await _patch(client, "sample", {"per_tick": 5})
        changed = await _patch(client, "sample", {"burst": 3})
        section = await _patch(client, "later", {"hours": "09-17"})
        dropped = await _patch(client, "sample", {"burst": None})
        doc = await client.get(URL)
    assert kept.status_code == 200, f"after a rollback a key could not be changed: {kept.text}"
    assert (kept.json()["unknown_sections"], kept.json()["unknown_keys"]) == (["later"], ["sample.burst"])
    assert changed.status_code == 422 and changed.json()["detail"][0]["type"] == "extra_forbidden", changed.text
    assert section.status_code == 422, section.text
    assert section.json()["detail"] == [
        {
            "loc": ["later"],
            "type": "unknown_section",
            "msg": "'later' is not a section this embry0 knows, so it cannot change it.",
        }
    ]
    assert dropped.status_code == 200, f"a key a newer release wrote could not be taken out: {dropped.text}"
    assert doc.json()["yaml_text"] == "sample:\n  per_tick: 5\nlater:\n  hours: '08-17'\n"
