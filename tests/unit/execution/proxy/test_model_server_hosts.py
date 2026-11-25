"""Which hosts may be a model server (ADR 0032): never this machine, never the link-local range where a cloud's
metadata service answers; every other host is a deployment admin's to trust."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.execution.proxy import local_llm_proxy

ADMIN = "test-admin-secret-not-real"
TOKEN = "sandbox-bearer-token-at-least-32-chars-long"


def _refused(host: str) -> str | None:
    check = getattr(local_llm_proxy, "refused_server_host", None)
    assert check is not None, "nothing says which hosts may not be a model server (refused_server_host)"
    return check(host)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.8.9.1",
        "::1",
        "[::1]",
        "localhost",
        "LocalHost.",
        "api.localhost",
        "0.0.0.0",
        "::",
        "169.254.169.254",
        "169.254.0.1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
    ],
)
def test_this_machine_and_link_local_are_never_a_model_server(host: str) -> None:
    reason = _refused(host)
    assert reason is not None, f"{host} was taken as a model server"
    assert host in reason, f"the refusal must name the host it refused: {reason}"


@pytest.mark.parametrize(
    "host", ["10.0.0.5", "192.168.1.20", "172.17.0.1", "gpu-box.lan", "models.example.test", "8.8.8.8", "2001:db8::5"]
)
def test_every_other_host_is_the_admin_s_to_trust(host: str) -> None:
    assert _refused(host) is None, f"{host} was refused, but only a deployment admin can name it"


async def test_the_proxy_refuses_an_enrollment_naming_this_machine(aiohttp_client: Any) -> None:
    client = await aiohttp_client(local_llm_proxy.create_local_llm_proxy_app(ADMIN))
    refused = await client.post(
        "/admin/enroll",
        json={
            "sandbox_id": "sbx-1",
            "sandbox_token": TOKEN,
            "local_llm": {"credential_id": "cred-a", "upstream": "http://127.0.0.1:11434/v1", "api_key": ""},
        },
        headers={"X-Admin-Token": ADMIN},
    )
    assert refused.status == 400, f"an enrollment naming 127.0.0.1 was taken: {refused.status}"
    assert "may not be a model server" in await refused.text()
