"""done_when.on_unmet and salvage.enabled through the settings API, per repository, on a real database (#182).

A value either key does not take is a 422 naming that key, through the
document's PUT and the section's PATCH alike, and nothing is stored. A
repository's layer may set both, ``GET`` lists them among what that layer
may set, and the effective view shows a repository's value as its own while
another repository keeps the deployment's.
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

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
YAML = {"Content-Type": "application/yaml"}
ADMIN = "emb_" + "a" * 43
WIDGETS = "acme/widgets"


@pytest.fixture
async def app(db_with_migrations: DatabasePool) -> AsyncIterator[Any]:
    from embry0.api.app import create_app

    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
    app = create_app(config)
    state = app.state
    state.db = db_with_migrations
    state.settings = SettingsStore(db_with_migrations)
    state.organizations_repo = OrganizationsRepository(db_with_migrations)
    state.repos_repo = ReposRepository(db_with_migrations, default_forge="github")
    await state.organizations_repo.register("acme", "github")
    for repo in (WIDGETS, "acme/gadgets"):
        await state.repos_repo.register(repo)
    row = ApiKeyRow(
        id="key-admin0000001",
        user_id="usr-admin",
        name="admin-key",
        prefix="emb_xxxxxxxx",
        scope="admin",
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=None,
    )
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: row if h == hash_token(ADMIN) else None)
    keys.touch_last_used = AsyncMock()
    state.api_keys_repo = keys
    user = MagicMock(disabled=False, email="alex@example.com")
    state.users_repo = MagicMock(get=AsyncMock(side_effect=lambda uid: user if uid == "usr-admin" else None))
    yield app
    bust_api_key_cache()


def _client(app: Any) -> AsyncClient:
    headers = {"Authorization": f"Bearer {ADMIN}", "X-Requested-With": "XMLHttpRequest"}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers)


@pytest.mark.parametrize(
    ("section", "key", "value", "says"),
    [
        ("done_when", "on_unmet", "draft", "'pause', 'review' or 'fail'"),
        ("done_when", "on_unmet", "Fail", "'pause', 'review' or 'fail'"),
        ("salvage", "enabled", "no", "boolean"),
        ("salvage", "enabled", 0, "boolean"),
    ],
)
async def test_a_value_the_key_does_not_take_is_a_422_naming_it(
    app: Any, section: str, key: str, value: Any, says: str
) -> None:
    text = f"{section}:\n  {key}: {value!r}\n" if isinstance(value, str) else f"{section}:\n  {key}: {value}\n"
    async with _client(app) as client:
        put = await client.put(URL, content=text, headers=YAML, params={"repo": WIDGETS})
        patched = await client.patch(f"{URL}/{section}", json={key: value}, params={"repo": WIDGETS})
        stored = await client.get(URL, params={"repo": WIDGETS})
    for resp in (put, patched):
        assert resp.status_code == 422, f"{section}.{key} {value!r} must be refused: {resp.status_code} {resp.text}"
        detail = resp.json()["detail"]
        assert [p["loc"][:2] for p in detail] == [[section, key]], f"refused naming {section}.{key}: {detail}"
        assert says in detail[0]["msg"], f"the refusal says what the key takes, not that it is unknown: {detail}"
    assert stored.status_code == 200 and stored.json().get("data") in ({}, None), (
        f"a refused value stores nothing: {stored.text}"
    )


async def test_a_repository_layer_may_set_both_and_another_repository_keeps_the_deployment_s(app: Any) -> None:
    async with _client(app) as client:
        put = await client.put(
            URL,
            content="done_when:\n  on_unmet: fail\nsalvage:\n  enabled: false\n",
            headers=YAML,
            params={"repo": WIDGETS},
        )
        layer = await client.get(URL, params={"repo": WIDGETS})
        widgets = await client.get(f"{URL}/effective", params={"repo": WIDGETS})
        gadgets = await client.get(f"{URL}/effective", params={"repo": "acme/gadgets"})
    assert put.status_code == 200, f"a repository's layer may set on_unmet and enabled: {put.text}"
    settable = layer.json()["settable"]
    assert "on_unmet" in settable["done_when"] and "enabled" in settable["salvage"], (
        f"the Policy tab lists what this layer may set: {settable}"
    )

    def values(resp: Any) -> dict[str, tuple[Any, str]]:
        assert resp.status_code == 200, resp.text
        return {
            f"{s['name']}.{k['key']}": (k["value"], k["source"])
            for s in resp.json()["sections"]
            if s["name"] in ("done_when", "salvage")
            for k in s["keys"]
            if k["key"] in ("on_unmet", "enabled")
        }

    assert values(widgets) == {"done_when.on_unmet": ("fail", "repository"), "salvage.enabled": (False, "repository")}
    assert values(gadgets) == {"done_when.on_unmet": ("pause", "default"), "salvage.enabled": (True, "default")}
