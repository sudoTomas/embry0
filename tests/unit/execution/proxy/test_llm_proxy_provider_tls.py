"""Two providers pinned to one address keep their own TLS: a call to the second never rides a connection verified for
the first's name, so a name the server's certificate does not hold is refused, and its key is never sent."""

from __future__ import annotations

from tests.fixtures.tls_servers import tls_server

# The TLS server listens on 127.0.0.1, which no real enrollment may name: let that one address through.
from tests.unit.execution.proxy.test_llm_proxy import (
    _ADMIN,
    _SANDBOX_TOKEN,
    _fake_servers_on_loopback,  # noqa: F401, E402
    create_llm_proxy_app,
)

_H = {"X-Admin-Token": _ADMIN}
_BEARER = {"Authorization": f"Bearer {_SANDBOX_TOKEN}"}


async def test_a_provider_on_another_s_address_never_rides_its_verified_connection(
    aiohttp_client, aiohttp_server, tmp_path, monkeypatch
):
    # Before the proxy starts: its client trusts the test's CA, whose certificate holds models.test alone.
    tls = await tls_server(aiohttp_server, tmp_path, monkeypatch, "models.test")
    client = await aiohttp_client(create_llm_proxy_app(_ADMIN))

    def server_for(name: str, key: str, credential_id: str) -> dict[str, str]:
        root = f"https://{name}:{tls.port}/v1"
        return {"credential_id": credential_id, "upstream": root, "api_key": key, "address": "127.0.0.1"}

    enrolled = await client.post(
        "/admin/enroll",
        headers=_H,
        json={
            "sandbox_id": "sbx-1",
            "sandbox_token": _SANDBOX_TOKEN,
            "provider_servers": {
                "openai": server_for("models.test", "key-openai", "cred-o"),
                "groq": server_for("wrong.test", "key-groq", "cred-g"),
            },
            # The limits every enrollment carries (#174): the local_models section's defaults.
            "limits": {"max_request_bytes": 8 * 1024 * 1024, "timeout_seconds": 900.0},
        },
    )
    assert enrolled.status == 200, await enrolled.text()
    chat = {"messages": []}
    right = await client.post("/p/openai/v1/chat/completions", json=chat, headers=_BEARER)
    assert right.status == 200, f"the provider whose name the certificate holds was refused: {await right.text()}"
    # A connection verified for models.test is now open to 127.0.0.1: the next provider must not ride it.
    wrong = await client.post("/p/groq/v1/chat/completions", json=chat, headers=_BEARER)
    assert wrong.status == 502, f"a name the certificate does not hold rode another's connection: {wrong.status}"
    assert [s["authorization"] for s in tls.seen] == ["Bearer key-openai"], (
        f"a provider's key reached a server not verified for it: {tls.seen}"
    )
    assert tls.handshakes == ["models.test", "wrong.test"], f"each name its own handshake: {tls.handshakes}"
