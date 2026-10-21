"""PUT /credentials/bindings with the bodies the dashboard's bindings drawer sends.

frontend/src/components/credentials/BindingsDrawer.tsx binds a credential at the
role its kind binds with (frontend/src/lib/bindingRoles.ts). These are the exact
bodies its tests assert, sent to the API on a real database: the backend takes
them, and still refuses the ones the drawer used to send.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from embry0.safety.redaction import known_secret_values, register_known_secrets

pytestmark = [pytest.mark.requires_postgres]

LLM_ACCOUNTS = [
    {"kind": "llm_api_key", "provider": "anthropic", "label": "claude-key", "secret": "sk-ant-unit-drawer-key"},
    {"kind": "llm_oauth", "provider": "anthropic", "label": "claude-oauth", "secret": "sk-ant-oat-unit-drawer"},
    {"kind": "llm_api_key", "provider": "xai", "label": "grok-key", "secret": "xai-unit-drawer-key"},
    {"kind": "llm_api_key", "provider": "openai", "label": "gpt", "secret": "sk-unit-drawer-openai-key"},
]


@pytest.fixture(autouse=True)
def _restore_scrub_set() -> Iterator[None]:
    """POST /credentials registers its secret process-wide; put the set back for the modules after this one."""
    before = known_secret_values()
    yield
    register_known_secrets(before, reset=True)


@pytest.fixture
async def client(api_client: AsyncClient, monkeypatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openai")
    db = api_client.app.state.db  # type: ignore[attr-defined]
    await db.execute("TRUNCATE credential_bindings, credential_uses, credentials CASCADE")
    yield api_client
    await db.execute("TRUNCATE credential_bindings, credential_uses, credentials CASCADE")


async def _create(client: AsyncClient, body: dict[str, Any]) -> str:
    resp = await client.post("/api/v1/credentials", json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _scope(scope_kind: str, scope_value: str, cid: str, role: str) -> dict[str, Any]:
    return {"scope_kind": scope_kind, "scope_value": scope_value, "bindings": [{"credential_id": cid, "role": role}]}


@pytest.mark.parametrize("account", LLM_ACCOUNTS, ids=lambda a: f"{a['provider']}-{a['kind']}")
async def test_the_drawers_global_llm_body_is_taken(client: AsyncClient, account: dict[str, Any]) -> None:
    cid = await _create(client, account)
    resp = await client.put("/api/v1/credentials/bindings", json=_scope("global", "", cid, "llm"))
    assert resp.status_code == 200, resp.text
    rows = [(b["credential_id"], b["role"], b["provider"]) for b in resp.json()["bindings"]]
    assert rows == [(cid, "llm", account["provider"])]


@pytest.mark.parametrize("account", LLM_ACCOUNTS, ids=lambda a: f"{a['provider']}-{a['kind']}")
async def test_the_drawers_owner_llm_body_is_what_the_owners_repos_resolve(
    client: AsyncClient, account: dict[str, Any]
) -> None:
    cid = await _create(client, account)
    resp = await client.put("/api/v1/credentials/bindings", json=_scope("owner", "github:acme", cid, "llm"))
    assert resp.status_code == 200, resp.text
    resolved = await client.get(
        "/api/v1/credentials/resolve", params={"repo": "acme/widgets", "role": "llm", "provider": account["provider"]}
    )
    assert resolved.status_code == 200, resolved.text
    assert (resolved.json()["id"], resolved.json()["source"]) == (cid, "registry")
    assert f"owner:github:acme -> {account['label']}" in resolved.json()["trace"]


async def test_the_drawers_tailnet_body_is_taken(client: AsyncClient) -> None:
    cid = await _create(
        client,
        {
            "kind": "tailnet_api_key",
            "provider": "tailscale",
            "label": "tailnet",
            "secret": "tskey-api-unit-drawer",
            "metadata": {"tailnet": "example.com"},
        },
    )
    resp = await client.put("/api/v1/credentials/bindings", json=_scope("global", "", cid, "tailnet"))
    assert resp.status_code == 200, resp.text
    assert [b["role"] for b in resp.json()["bindings"]] == ["tailnet"]


@pytest.mark.parametrize(
    ("scope_kind", "scope_value", "role", "words"),
    [
        # What the drawer sent for every credential before: role forge.
        ("global", "", "forge", "role 'forge' needs a credential of kind ['forge_token'], got 'llm_api_key'"),
        # The scope it offered an LLM account that role llm is never looked up at.
        ("host", "api.anthropic.com", "llm", "scope 'host' is not valid for role 'llm'"),
        # The owner it prefilled from the LLM provider.
        ("owner", "anthropic:acme", "llm", "owner scope_value must be 'github:<owner>' or 'gitlab:<owner>'"),
    ],
)
async def test_the_bodies_the_drawer_no_longer_sends_are_refused(
    client: AsyncClient, scope_kind: str, scope_value: str, role: str, words: str
) -> None:
    cid = await _create(client, LLM_ACCOUNTS[0])
    resp = await client.put("/api/v1/credentials/bindings", json=_scope(scope_kind, scope_value, cid, role))
    assert resp.status_code == 422, resp.text
    assert words in resp.text
    assert (await client.get("/api/v1/credentials")).json()[0]["binding_count"] == 0
