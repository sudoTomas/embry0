"""POST /credentials for the OpenAI-compatible LLM providers: enablement, kind, host, base URL."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from embry0.safety.redaction import known_secret_values, register_known_secrets
from tests.fixtures.model_servers import fake_dns
from tests.fixtures.provider_catalogue import enable_providers

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
    enable_providers(monkeypatch, "openai", "deepseek", "custom")
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
    assert "not enabled" in resp.text and "provider catalogue" in resp.text


@pytest.mark.parametrize(
    ("body", "words"),
    [
        (_body(kind="llm_oauth"), "takes an API key only"),
        (_body(provider="custom"), "needs metadata.base_url"),
        (_body(provider="custom", metadata={"base_url": "ftp://host/v1"}), "http(s) URL"),
        (_body(provider="custom", metadata={"base_url": "http://user:pw@host/v1"}), "must not carry credentials"),
        (_body(provider="custom", metadata={"base_url": "http://h/v1", "model": "x"}), "takes only 'base_url'"),
        # ADR 0032: this machine and the link-local range are never a model server.
        (_body(provider="custom", metadata={"base_url": "http://169.254.169.254/v1"}), "may not be a model server"),
        (_body(provider="custom", metadata={"base_url": "http://127.0.0.1:11434/v1"}), "may not be a model server"),
        (_body(provider="custom", metadata={"base_url": "http://localhost:11434/v1"}), "may not be a model server"),
        (_body(provider="custom", metadata={"base_url": "http://[fe80::1]:8000/v1"}), "may not be a model server"),
        # The same, spelled as the resolver reads it.
        (_body(provider="custom", metadata={"base_url": "http://2130706433:11434/v1"}), "may not be a model server"),
        (_body(provider="custom", metadata={"base_url": "http://0x7f000001/v1"}), "may not be a model server"),
        (_body(provider="custom", metadata={"base_url": "http://127.1/v1"}), "may not be a model server"),
        (_body(provider="custom", metadata={"base_url": "http://169.254.43518/v1"}), "may not be a model server"),
        (_body(provider="custom", metadata={"base_url": "http://0xa9fea9fe/v1"}), "may not be a model server"),
        (_body(metadata={"base_url": "http://h/v1"}), "only accepted for tailnet_api_key and custom"),
    ],
)
async def test_create_refuses_a_mismatch(client: AsyncClient, body: dict[str, Any], words: str) -> None:
    resp = await client.post("/api/v1/credentials", json=body)
    assert resp.status_code == 422, resp.text
    assert words in resp.text
    assert SECRET not in resp.text, "a 422 on a credential route echoed the secret"


@pytest.mark.parametrize("name", ["localtest.me", "127.0.0.1.nip.io", "metadata.google.internal"])
async def test_create_refuses_a_name_that_resolves_to_this_machine_or_link_local(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    fake_dns(
        monkeypatch,
        {
            "localtest.me": ["127.0.0.1"],
            "127.0.0.1.nip.io": ["127.0.0.1"],
            "metadata.google.internal": ["169.254.169.254"],
        },
    )
    resp = await client.post(
        "/api/v1/credentials", json=_body(provider="custom", metadata={"base_url": f"http://{name}:11434/v1"})
    )
    assert resp.status_code == 422, f"a credential at {name} was written: {resp.status_code} {resp.text}"
    assert "may not be a model server" in resp.text and name in resp.text, resp.text
    assert SECRET not in resp.text, "a 422 on a credential route echoed the secret"


async def test_create_takes_a_name_that_resolves_to_another_host(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dns(monkeypatch, {"gpu-box.lan": ["10.0.0.9"]})
    resp = await client.post(
        "/api/v1/credentials", json=_body(provider="custom", metadata={"base_url": "http://gpu-box.lan:11434/v1"})
    )
    assert resp.status_code == 201, f"an admin's own server was refused: {resp.text}"


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
