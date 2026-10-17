"""POST /credentials for the tailnet_api_key kind (ADR 0013): validation, metadata, binding scope."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from embry0.safety.redaction import known_secret_values, register_known_secrets

pytestmark = [pytest.mark.requires_postgres]

SECRET = "tskey-api-unit-test-secret-value"


@pytest.fixture(autouse=True)
def _restore_scrub_set() -> Iterator[None]:
    """POST /credentials registers its secret process-wide; put the set back for the modules after this one."""
    before = known_secret_values()
    yield
    register_known_secrets(before, reset=True)


@pytest.fixture
async def client(api_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    db = api_client.app.state.db  # type: ignore[attr-defined]
    await db.execute("TRUNCATE credential_bindings, credential_uses, credentials CASCADE")
    yield api_client
    await db.execute("TRUNCATE credential_bindings, credential_uses, credentials CASCADE")


def _body(**kw: Any) -> dict[str, Any]:
    return {"kind": "tailnet_api_key", "provider": "tailscale", "label": "tailnet", "secret": SECRET, **kw}


async def test_create_stores_the_tailnet_and_never_returns_the_key(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/credentials", json=_body(metadata={"tailnet": "example.com"}))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["kind"], body["provider"], body["host"]) == ("tailnet_api_key", "tailscale", "api.tailscale.com")
    assert body["metadata"] == {"tailnet": "example.com"}
    assert SECRET not in resp.text
    listed = await client.get("/api/v1/credentials")
    assert [c["metadata"] for c in listed.json()] == [{"tailnet": "example.com"}]
    assert SECRET not in listed.text


@pytest.mark.parametrize(
    ("body", "words"),
    [
        (_body(provider="github"), "tailnet_api_key credentials use provider tailscale"),
        (_body(kind="forge_token"), "forge_token credentials must use provider github or gitlab"),
        (_body(kind="llm_api_key"), "llm_api_key credentials must use provider anthropic or xai"),
        (_body(metadata={"tailnet": "has space"}), "metadata.tailnet"),
        (_body(metadata={"tailnet": "../other"}), "metadata.tailnet"),
        (_body(metadata={"tailnet": "example.com", "org": "acme"}), "takes only 'tailnet'"),
        (
            {"kind": "forge_token", "provider": "github", "label": "a", "secret": "ghp_x", "metadata": {"a": "b"}},
            "only accepted for tailnet_api_key",
        ),
    ],
)
async def test_create_refuses_a_mismatch(client: AsyncClient, body: dict[str, Any], words: str) -> None:
    resp = await client.post("/api/v1/credentials", json=body)
    assert resp.status_code == 422, resp.text
    assert words in resp.text
    assert SECRET not in resp.text, "a 422 on a credential route echoed the secret"


async def test_the_host_is_the_tailnet_api(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/credentials", json=_body(host="tailnet.example.com"))
    assert resp.status_code == 422 and "api.tailscale.com" in resp.text


async def test_binds_at_global_scope_only(client: AsyncClient) -> None:
    cid = (await client.post("/api/v1/credentials", json=_body())).json()["id"]
    owner = await client.put(
        "/api/v1/credentials/bindings",
        json={
            "scope_kind": "owner",
            "scope_value": "github:acme",
            "bindings": [{"credential_id": cid, "role": "tailnet"}],
        },
    )
    assert owner.status_code == 422 and "not valid for role 'tailnet'" in owner.text
    repo = await client.put(
        "/api/v1/repos/acme/widgets/credentials", json={"bindings": [{"credential_id": cid, "role": "tailnet"}]}
    )
    assert repo.status_code == 422
    glob = await client.put(
        "/api/v1/credentials/bindings",
        json={"scope_kind": "global", "scope_value": "", "bindings": [{"credential_id": cid, "role": "tailnet"}]},
    )
    assert glob.status_code == 200, glob.text
    assert [(b["role"], b["provider"]) for b in glob.json()["bindings"]] == [("tailnet", "tailscale")]
