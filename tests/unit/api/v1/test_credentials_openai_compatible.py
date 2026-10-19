"""POST /credentials for the OpenAI-compatible LLM providers: enablement, kind, host, base URL."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from embry0.safety.redaction import known_secret_values, register_known_secrets

pytestmark = [pytest.mark.requires_postgres]

SECRET = "sk-unit-test-openai-compatible-key"


@pytest.fixture(autouse=True)
def _restore_scrub_set() -> Iterator[None]:
    """POST /credentials registers its secret process-wide; put the set back for the modules after this one."""
    before = known_secret_values()
    yield
    register_known_secrets(before, reset=True)


@pytest.fixture
async def client(api_client: AsyncClient, monkeypatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openai,deepseek,custom")
    db = api_client.app.state.db  # type: ignore[attr-defined]
    await db.execute("TRUNCATE credential_bindings, credential_uses, credentials CASCADE")
    yield api_client
    await db.execute("TRUNCATE credential_bindings, credential_uses, credentials CASCADE")


def _body(**kw: Any) -> dict[str, Any]:
    return {"kind": "llm_api_key", "provider": "openai", "label": "api-key", "secret": SECRET, **kw}


async def test_an_enabled_provider_takes_an_api_key_on_its_own_host(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/credentials", json=_body())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["kind"], body["provider"], body["host"]) == ("llm_api_key", "openai", "api.openai.com")
    assert SECRET not in resp.text


async def test_a_custom_key_carries_its_base_url_and_its_host(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/credentials",
        json=_body(provider="custom", label="ollama", metadata={"base_url": "http://10.0.0.5:11434/v1/"}),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["host"] == "10.0.0.5"
    assert body["metadata"] == {"base_url": "http://10.0.0.5:11434/v1"}, "stored without the trailing slash"


async def test_a_provider_that_is_not_enabled_is_refused(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/credentials", json=_body(provider="groq"))
    assert resp.status_code == 422
    assert "not enabled" in resp.text and "OPENAI_COMPATIBLE_PROVIDERS" in resp.text


@pytest.mark.parametrize(
    ("body", "words"),
    [
        (_body(kind="llm_oauth"), "takes an API key only"),
        (_body(provider="custom"), "needs metadata.base_url"),
        (_body(provider="custom", metadata={"base_url": "ftp://host/v1"}), "http(s) URL"),
        (_body(provider="custom", metadata={"base_url": "http://user:pw@host/v1"}), "must not carry credentials"),
        (_body(provider="custom", metadata={"base_url": "http://h/v1", "model": "x"}), "takes only 'base_url'"),
        (_body(metadata={"base_url": "http://h/v1"}), "only accepted for tailnet_api_key and custom"),
    ],
)
async def test_create_refuses_a_mismatch(client: AsyncClient, body: dict[str, Any], words: str) -> None:
    resp = await client.post("/api/v1/credentials", json=body)
    assert resp.status_code == 422, resp.text
    assert words in resp.text
    assert SECRET not in resp.text, "a 422 on a credential route echoed the secret"


async def test_the_host_must_be_the_providers(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/credentials", json=_body(host="api.example.com"))
    assert resp.status_code == 422 and "api.openai.com" in resp.text


async def test_a_globally_bound_key_is_what_a_repo_resolves(client: AsyncClient) -> None:
    cid = (await client.post("/api/v1/credentials", json=_body(provider="deepseek"))).json()["id"]
    bound = await client.put(
        "/api/v1/credentials/bindings",
        json={"scope_kind": "global", "scope_value": "", "bindings": [{"credential_id": cid, "role": "llm"}]},
    )
    assert bound.status_code == 200, bound.text
    resolved = await client.get(
        "/api/v1/credentials/resolve", params={"repo": "acme/widgets", "role": "llm", "provider": "deepseek"}
    )
    assert resolved.status_code == 200, resolved.text
    assert (resolved.json()["id"], resolved.json()["source"]) == (cid, "registry")
