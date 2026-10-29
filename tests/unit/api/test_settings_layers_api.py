"""Settings layers through the API: ``?owner=`` and ``?repo=`` (spec 2026-10-29, #161).

Driven through the real app on a real database, with API keys so the tier
and the repository allowlist are the ones production checks, and the
test-only sections so nothing depends on which features exist yet.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.organizations import OrganizationsRepository
from embry0.storage.repositories.repos import ReposRepository
from tests.fixtures.settings_sections import LAYERED_CATALOG

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
YAML = {"Content-Type": "application/yaml"}

ADMIN = "emb_" + "a" * 43
OPERATOR = "emb_" + "o" * 43
READER = "emb_" + "r" * 43
WIDGETS_ONLY = "emb_" + "w" * 43


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
    hash_token(WIDGETS_ONLY): _key("key-widgets00001", "admin", None, ("Acme/Widgets",)),
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
    await state.organizations_repo.register("globex", "github")
    for repo in ("Acme/Widgets", "acme/gadgets", "globex/gizmos"):
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


# ---- writing and reading a layer -------------------------------------------------------


async def test_an_organization_layer_is_its_own_document(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, "sample:\n  per_tick: 4\n")
        put = await _put(client, "# acme\nsample:\n  per_tick: 6\n", owner="Acme", headers={"If-Match": '"0"'})
        layer = await client.get(URL, params={"owner": "acme"})
        deployment = await client.get(URL)
    assert put.status_code == 200, put.text
    assert put.json()["scope"] == {"layer": "organization", "owner": "acme", "repo": None}
    assert layer.status_code == 200, layer.text
    body = layer.json()
    assert (body["yaml_text"], body["data"], body["generation"]) == (
        "# acme\nsample:\n  per_tick: 6\n",
        {"sample": {"per_tick": 6}},
        1,
    ), f"?owner= did not name the organization's own document: {body}"
    assert layer.headers["etag"] == '"1"'
    assert body["settable"] == {"sample": ["per_tick", "remind_hours"], "other": [], "window": ["high", "low"]}
    assert deployment.json()["yaml_text"] == "sample:\n  per_tick: 4\n", "the deployment's document is untouched"


async def test_a_key_the_layer_may_not_set_is_a_422_naming_it(app: Any) -> None:
    async with _client(app) as client:
        org = await _put(client, "sample:\n  enabled: true\n  per_tick: 6\nother:\n  limit: 1\n", owner="acme")
        repo = await _put(client, "sample:\n  per_tick: 6\n", repo="acme/widgets")
        stored = await client.get(URL, params={"owner": "acme"})
    assert org.status_code == 422, f"an organization set a key its section keeps for the deployment: {org.text}"
    assert [(d["loc"], d["type"]) for d in org.json()["detail"]] == [
        (["sample", "enabled"], "not_overridable"),
        (["other", "limit"], "not_overridable"),
    ]
    assert repo.status_code == 422, repo.text
    assert repo.json()["detail"] == [
        {
            "loc": ["sample", "per_tick"],
            "type": "not_overridable",
            "msg": "A repository cannot set 'per_tick'. In 'sample' it can set mode.",
        }
    ]
    assert stored.json()["generation"] == 0, "nothing was stored"


async def test_a_layer_value_that_conflicts_with_what_it_inherits_is_refused(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, "window:\n  high: 50\n")
        resp = await _put(client, "window:\n  low: 70\n", owner="acme")
    assert resp.status_code == 422, resp.text
    assert [(d["loc"], d["type"]) for d in resp.json()["detail"]] == [(["window"], "conflicts_with_inherited")]


async def test_a_layer_names_one_registered_organization_or_repository(app: Any) -> None:
    async with _client(app) as client:
        both = await _put(client, "", owner="acme", repo="acme/widgets")
        bad = await _put(client, "", owner="acme/widgets")
        unknown_org = await _put(client, "sample:\n  per_tick: 6\n", owner="initech")
        unknown_repo = await _put(client, "sample:\n  mode: 'on'\n", repo="acme/nothing")
        read_unknown = await client.get(URL, params={"owner": "initech"})
    assert (both.status_code, both.json()["detail"]) == (422, "Name one layer: owner or repo, not both.")
    assert bad.status_code == 422 and "is not an organization" in bad.json()["detail"]
    assert unknown_org.status_code == 404, unknown_org.text
    assert unknown_repo.status_code == 404, unknown_repo.text
    assert read_unknown.status_code == 404, read_unknown.text


async def test_a_repository_is_named_whatever_its_case(app: Any) -> None:
    async with _client(app) as client:
        put = await _put(client, "sample:\n  mode: 'on'\n", repo="ACME/widgets")
        got = await client.get(URL, params={"repo": "acme/Widgets"})
    assert put.status_code == 200, put.text
    assert got.json()["data"] == {"sample": {"mode": "on"}}
    assert got.json()["scope"] == {"layer": "repository", "owner": "acme", "repo": "acme/widgets"}


# ---- #161 acceptance: an organization override changes its own repositories only ----


async def test_the_effective_view_shows_each_layer_and_what_it_overrides(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, "sample:\n  per_tick: 4\n")
        await _put(client, "sample:\n  per_tick: 6\n", owner="acme")
        await _put(client, "sample:\n  mode: 'on'\n", repo="acme/widgets")
        widgets = await client.get(f"{URL}/effective", params={"repo": "acme/widgets"})
        gizmos = await client.get(f"{URL}/effective", params={"repo": "globex/gizmos"})
        acme = await client.get(f"{URL}/effective", params={"owner": "acme"})
        deployment = await client.get(f"{URL}/effective")
        mismatch = await client.get(f"{URL}/effective", params={"owner": "globex", "repo": "acme/widgets"})

    def keys(resp: Any) -> dict[str, dict[str, Any]]:
        assert resp.status_code == 200, resp.text
        return {k["key"]: k for s in resp.json()["sections"] if s["name"] == "sample" for k in s["keys"]}

    per_tick, mode = keys(widgets)["per_tick"], keys(widgets)["mode"]
    assert (per_tick["value"], per_tick["source"], per_tick["inherited"]) == (6, "organization", 6), (
        f"acme/widgets did not read acme's per_tick: {per_tick}"
    )
    assert (mode["value"], mode["source"], mode["inherited"]) == ("on", "repository", "shadow")
    assert [(layer["layer"], layer["set"], layer["value"]) for layer in per_tick["layers"]] == [
        ("deployment", True, 4),
        ("organization", True, 6),
        ("repository", False, None),
    ]
    assert per_tick["layers"][1]["set_by"] == "alex@example.com"
    assert widgets.json()["scope"] == {"layer": "repository", "owner": "acme", "repo": "acme/widgets"}
    assert (keys(gizmos)["per_tick"]["value"], keys(gizmos)["per_tick"]["source"]) == (4, "document"), (
        "another organization's repository read acme's value"
    )
    assert (keys(acme)["per_tick"]["value"], keys(acme)["per_tick"]["inherited"]) == (6, 4)
    assert keys(deployment)["per_tick"]["value"] == 4 and "layers" not in keys(deployment)["per_tick"]
    assert mismatch.status_code == 422, mismatch.text


async def test_a_deployment_change_names_the_layer_values_it_overrules(app: Any) -> None:
    async with _client(app) as client:
        await _put(client, "window:\n  high: 80\n")
        await _put(client, "window:\n  low: 70\n", owner="acme")
        preview = await _put(client, "window:\n  high: 50\n", dry_run="1")
        saved = await _put(client, "window:\n  high: 50\n")
        effective = await client.get(f"{URL}/effective", params={"owner": "acme"})
    acme = {"layer": "organization", "owner": "acme", "repo": None}
    expected = [{"scope": acme, "key": "window.low", "reason": "high must not be below low"}]
    assert preview.json()["layers_refused"] == expected, preview.text
    assert preview.json()["layers_refused_total"] == 1
    assert saved.status_code == 200 and saved.json()["layers_refused"] == expected, saved.text
    low = next(k for s in effective.json()["sections"] if s["name"] == "window" for k in s["keys"] if k["key"] == "low")
    assert (low["value"], low["layers"][1]["refused"]) == (10, "high must not be below low")


# ---- who may write -------------------------------------------------------------------


async def test_only_an_admin_writes_a_layer_and_an_allowlist_only_its_repositories(app: Any) -> None:
    async with _client(app, READER) as reader, _client(app, OPERATOR) as operator, _client(app, WIDGETS_ONLY) as one:
        assert (await _put(reader, "sample:\n  per_tick: 6\n", owner="acme")).status_code == 403
        assert (await _put(operator, "sample:\n  per_tick: 6\n", owner="acme")).status_code == 403
        org = await _put(one, "sample:\n  per_tick: 6\n", owner="acme")
        deployment = await _put(one, "sample:\n  per_tick: 6\n")
        own = await _put(one, "sample:\n  mode: 'on'\n", repo="acme/widgets")
        other = await _put(one, "sample:\n  mode: 'on'\n", repo="acme/gadgets")
        read = await reader.get(URL, params={"repo": "acme/widgets"})
    assert org.status_code == 403, f"a key limited to one repository wrote its whole organization: {org.text}"
    assert deployment.status_code == 403, deployment.text
    assert own.status_code == 200, f"a key whose allowlist holds the repository could not write its layer: {own.text}"
    assert other.status_code == 403, other.text
    assert (read.status_code, read.json()["data"]) == (200, {"sample": {"mode": "on"}})


async def test_undo_is_authorized_for_the_document_it_changes(app: Any) -> None:
    async with _client(app) as admin, _client(app, WIDGETS_ONLY) as one:
        org = (await _put(admin, "sample:\n  per_tick: 6\n", owner="acme")).json()
        repo = (await _put(admin, "sample:\n  mode: 'on'\n", repo="acme/widgets")).json()
        refused = await one.post(f"{URL}/revisions/{org['revision_id']}/undo")
        undone = await one.post(f"{URL}/revisions/{repo['revision_id']}/undo")
        listed = await admin.get(f"{URL}/revisions", params={"owner": "acme"})
        detail = await admin.get(f"{URL}/revisions/{org['revision_id']}")
        repo_layer = await admin.get(URL, params={"repo": "acme/widgets"})
    assert refused.status_code == 403, refused.text
    assert undone.status_code == 200, undone.text
    assert undone.json()["scope"]["repo"] == "acme/widgets"
    assert repo_layer.json()["data"] == {}, "the undo put back the empty layer"
    assert [r["id"] for r in listed.json()["revisions"]] == [org["revision_id"]]
    assert detail.json()["scope"] == {"layer": "organization", "owner": "acme", "repo": None}


async def test_the_layers_are_listed_and_audited_with_their_scope(app: Any, db_with_migrations: DatabasePool) -> None:
    async with _client(app) as client:
        await _put(client, "sample:\n  per_tick: 6\n  remind_hours: 2\n", owner="acme")
        await _put(client, "sample:\n  mode: 'on'\n", repo="acme/widgets")
        listed = await client.get(f"{URL}/layers")
    assert listed.status_code == 200, listed.text
    assert [(row["layer"], row["owner"], row["repo"], row["keys"]) for row in listed.json()["layers"]] == [
        ("organization", "acme", None, ["sample.per_tick", "sample.remind_hours"]),
        ("repository", "acme", "acme/widgets", ["sample.mode"]),
    ]
    rows = await db_with_migrations.fetch("SELECT details FROM audit_log WHERE action = 'settings.updated' ORDER BY id")
    assert [r["details"]["scope"] for r in rows] == ["organization:acme", "repository:acme/widgets"]


async def test_who_wrote_a_layer_is_shown_as_the_audit_log_shows_actors(app: Any) -> None:
    """An admin sees every actor; a read key, which wrote nothing, sees none of them (as ``GET /audit``)."""
    async with _client(app) as admin, _client(app, READER) as reader:
        await _put(admin, "sample:\n  per_tick: 6\n", owner="acme")
        as_admin = (await admin.get(f"{URL}/layers")).json()["layers"]
        listed = (await reader.get(f"{URL}/layers")).json()["layers"]
        layer = (await reader.get(URL, params={"owner": "acme"})).json()
        effective = (await reader.get(f"{URL}/effective", params={"owner": "acme"})).json()
        effective_admin = (await admin.get(f"{URL}/effective", params={"owner": "acme"})).json()
    assert [row["updated_by"] for row in as_admin] == ["alex@example.com"]
    assert [row["updated_by"] for row in listed] == [None], "a read key must not learn who wrote a layer"
    assert layer["updated_by"] is None
    shown = {(k["set_by"], layer["set_by"]) for s in effective["sections"] for k in s["keys"] for layer in k["layers"]}
    assert shown == {(None, None)}, f"a read key learned who set a value: {shown}"
    per_tick = next(k for s in effective_admin["sections"] for k in s["keys"] if k["key"] == "per_tick")
    assert (per_tick["set_by"], per_tick["layers"][1]["set_by"]) == ("alex@example.com", "alex@example.com")


async def test_a_dry_run_of_a_layer_refuses_a_key_it_may_not_set(app: Any) -> None:
    async with _client(app) as client:
        resp = await _put(client, "sample:\n  enabled: true\n", owner="acme", dry_run="1")
    assert resp.status_code == 422, f"a dry run of an organization's layer accepted a key it may not set: {resp.text}"
    assert [(d["loc"], d["type"]) for d in resp.json()["detail"]] == [(["sample", "enabled"], "not_overridable")]


async def test_undo_of_a_layer_is_checked_against_what_it_inherits_now(app: Any) -> None:
    """Putting back an organization's low that the deployment's high has since moved below is refused."""
    async with _client(app) as client:
        await _put(client, "window:\n  high: 80\n")
        await _put(client, "window:\n  low: 70\n", owner="acme")
        second = (await _put(client, "window:\n  low: 20\n", owner="acme")).json()
        await _put(client, "window:\n  high: 50\n")
        undo = await client.post(f"{URL}/revisions/{second['revision_id']}/undo")
        layer = await client.get(URL, params={"owner": "acme"})
    assert undo.status_code == 422, f"undo put back a low above the high it inherits now: {undo.text}"
    assert [d["type"] for d in undo.json()["detail"]] == ["revision_invalid", "conflicts_with_inherited"]
    assert layer.json()["data"] == {"window": {"low": 20}}, "nothing was put back"


async def test_a_renamed_repository_s_layer_is_opened_under_its_old_slug_and_saved_under_its_new_one(
    app: Any, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from embry0.storage import repo_renames

    text = "sample:\n  mode: 'on'\nwindow:\n  high: 70\n"
    await db_with_migrations.execute(
        "INSERT INTO settings_documents (scope, yaml_text, data, generation) VALUES ($1, $2, $3::jsonb, 4)",
        "repository:acme/old-widgets",
        text,
        {"sample": {"mode": "on"}, "window": {"high": 70}},
    )
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {"acme/old-widgets": "acme/widgets"})
    async with _client(app) as client:
        opened = await client.get(URL, params={"repo": "acme/widgets"})
        saved = await _put(
            client, text.replace("70", "60"), repo="acme/widgets", headers={"If-Match": opened.headers["etag"]}
        )
        effective = await client.get(f"{URL}/effective", params={"repo": "acme/widgets"})
    assert (opened.json()["yaml_text"], opened.headers["etag"]) == (text, '"4"'), (
        "the renamed repository's layer opened as an empty one"
    )
    assert saved.status_code == 200, saved.text
    values = {(s["name"], k["key"]): k["value"] for s in effective.json()["sections"] for k in s["keys"]}
    assert (values[("sample", "mode")], values[("window", "high")]) == ("on", 60), (
        "a key the save kept stopped applying"
    )
    stored = await db_with_migrations.fetch("SELECT scope FROM settings_documents WHERE scope LIKE 'repository:%'")
    assert [r["scope"] for r in stored] == ["repository:acme/widgets"], "the save moved the layer to its new slug"
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {})
    async with _client(app) as client:
        after = await client.get(f"{URL}/effective", params={"repo": "acme/widgets"})
        listed = await client.get(f"{URL}/layers")
        history = await client.get(f"{URL}/revisions", params={"repo": "acme/widgets"})
    assert [r["what"] for r in history.json()["revisions"]][1] == (
        "No setting changed: moved from repository:acme/old-widgets: REPO_RENAMES maps it to acme/widgets"
    ), "the history says the layer moved"
    values = {(s["name"], k["key"]): k["value"] for s in after.json()["sections"] for k in s["keys"]}
    assert (values[("sample", "mode")], values[("window", "high")]) == ("on", 60), (
        "with the rename entry gone, the repository's layer stopped applying"
    )
    assert [(row["repo"], row["stored_as"]) for row in listed.json()["layers"]] == [("acme/widgets", None)]


async def test_each_layer_says_whether_it_is_kept_in_a_file(app: Any) -> None:
    """``managed_by`` is a key of each document: a layer kept in a file says so for itself (#230)."""
    async with _client(app) as client:
        put = await _put(client, "managed_by: file\nsample:\n  per_tick: 6\n", owner="acme")
        layer = await client.get(URL, params={"owner": "acme"})
        deployment = await client.get(URL)
    assert put.status_code == 200, put.text
    managed = next(c for c in put.json()["changes"] if c["key"] == "managed_by")
    assert "effective_before" not in managed and "effective_after" not in managed, "no loop reads managed_by"
    assert (layer.json()["managed_by"], deployment.json()["managed_by"]) == ("file", None), (
        "managed_by was not answered for the layer it belongs to"
    )
