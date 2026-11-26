"""Where embry0 may connect to a model server (ADR 0032, ``server_address``): never this machine, never the
link-local range where a cloud's metadata service answers, whatever the host's spelling and whatever a name resolves
to; every other host is a deployment admin's to trust, and a call connects to the address that was checked."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from embry0.execution.proxy import local_llm_proxy
from tests.fixtures.model_servers import fake_dns

ADMIN = "test-admin-secret-not-real"
TOKEN = "sandbox-bearer-token-at-least-32-chars-long"


def _module() -> Any:
    try:
        return importlib.import_module("embry0.execution.proxy.server_address")
    except ImportError:
        pytest.fail("nothing reads where a model server is the way the resolver does (server_address)")


#: Loopback, unspecified and link-local, in every spelling the resolver reads.
REFUSED_SPELLINGS = [
    "127.0.0.1",
    "127.8.9.1",
    "2130706433",
    "0x7f000001",
    "0x7f.0.0.1",
    "0177.0.0.1",
    "127.1",
    "0",
    "0.0.0.0",
    "::",
    "::1",
    "[::1]",
    "::ffff:127.0.0.1",
    "169.254.169.254",
    "2852039166",
    "0xa9fea9fe",
    "169.254.43518",
    "fe80::1",
    "::ffff:169.254.169.254",
    "localhost",
    "LocalHost.",
    "api.localhost",
]


@pytest.mark.parametrize("host", REFUSED_SPELLINGS)
def test_this_machine_and_link_local_are_refused_in_every_spelling(host: str) -> None:
    reason = _module().refused_server_host(host)
    assert reason is not None, f"{host} was taken as a model server"
    assert host in reason, f"the refusal must name the host it refused: {reason}"


#: Names that resolve to this machine or a metadata service.
REFUSED_NAMES = {
    "localtest.me": ["127.0.0.1"],
    "127.0.0.1.nip.io": ["127.0.0.1"],
    "metadata.google.internal": ["169.254.169.254"],
    "v6.example.test": ["::1"],
    "mixed.example.test": ["10.0.0.5", "127.0.0.1"],
}


@pytest.mark.parametrize("name", list(REFUSED_NAMES))
def test_a_name_any_of_whose_addresses_is_refused_is_refused(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    server_address = _module()
    fake_dns(monkeypatch, REFUSED_NAMES)
    url = f"https://{name}:8443/v1"
    reason = server_address.server_refusal(url)
    assert reason is not None and name in reason, f"{name} resolves to a refused address and was taken: {reason}"
    with pytest.raises(server_address.RefusedServer):
        server_address.pin_server(url)


@pytest.mark.parametrize(
    "host", ["10.0.0.5", "10.1", "192.168.1.20", "172.17.0.1", "8.8.8.8", "2001:db8::5", "gpu-box.lan"]
)
def test_every_other_host_is_the_admin_s_to_trust(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    server_address = _module()
    fake_dns(monkeypatch, {"gpu-box.lan": ["10.0.0.9"]})
    assert server_address.refused_server_host(host) is None, f"{host} was refused, but only an admin names it"
    where = f"[{host}]" if ":" in host else host
    assert server_address.server_refusal(f"http://{where}/v1") is None


def test_a_name_is_pinned_to_one_of_its_checked_addresses_ipv4_first(monkeypatch: pytest.MonkeyPatch) -> None:
    server_address = _module()
    asked = fake_dns(monkeypatch, {"gpu-box.lan": ["2001:db8::9", "10.0.0.9"]})
    pinned = server_address.pin_server("http://gpu-box.lan:8000/v1")
    assert (pinned.url, pinned.address) == ("http://gpu-box.lan:8000/v1", "10.0.0.9"), pinned
    assert asked == ["gpu-box.lan"], f"one lookup, the one checked: {asked}"


def test_a_name_that_resolves_to_nothing_is_not_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    server_address = _module()
    fake_dns(monkeypatch, {})
    with pytest.raises(server_address.UnresolvedServer):
        server_address.pin_server("http://nowhere.test/v1")
    assert server_address.server_refusal("http://nowhere.test/v1") is None, (
        "a name that does not resolve here is checked again where a call connects, not refused at a write"
    )


def test_an_ipv4_mapped_address_is_pinned_as_its_ipv4_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """``::ffff:10.0.0.5`` is 10.0.0.5: the call goes there over IPv4, as every IPv4 server's does."""
    fake_dns(monkeypatch, {})
    assert _module().pin_server("http://[::ffff:10.0.0.5]:8000/v1").address == "10.0.0.5"


def test_a_literal_address_is_pinned_without_a_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    server_address = _module()
    asked = fake_dns(monkeypatch, {})
    assert server_address.pin_server("http://10.0.0.5:11434/v1").address == "10.0.0.5"
    assert asked == [], f"an address was looked up: {asked}"


@pytest.mark.parametrize(
    ("url", "address", "connect_to", "host", "sni"),
    [
        (
            "https://models.test:8443/v1/models",
            "10.0.0.7",
            "https://10.0.0.7:8443/v1/models",
            "models.test:8443",
            "models.test",
        ),
        ("http://models.test/v1/models", "10.0.0.7", "http://10.0.0.7/v1/models", "models.test", None),
        (
            "https://models.test/v1/models",
            "2001:db8::7",
            "https://[2001:db8::7]/v1/models",
            "models.test",
            "models.test",
        ),
        ("https://10.0.0.7/v1/models", "10.0.0.7", "https://10.0.0.7/v1/models", "10.0.0.7", None),
    ],
)
def test_a_pinned_request_connects_to_the_address_and_keeps_the_name(
    url: str, address: str, connect_to: str, host: str, sni: str | None
) -> None:
    got, headers, extensions = _module().pinned_request(url, address)
    assert got == connect_to, got
    assert headers == {"Host": host}, headers
    assert extensions.get("sni_hostname") == sni, extensions


async def test_the_proxy_refuses_an_enrollment_naming_this_machine(aiohttp_client: Any) -> None:
    client = await aiohttp_client(local_llm_proxy.create_local_llm_proxy_app(ADMIN))
    refused = await client.post(
        "/admin/enroll",
        json={
            "sandbox_id": "sbx-1",
            "sandbox_token": TOKEN,
            "local_llm": {
                "credential_id": "cred-a",
                "upstream": "http://models.test:11434/v1",
                "api_key": "",
                "address": "127.0.0.1",
            },
        },
        headers={"X-Admin-Token": ADMIN},
    )
    assert refused.status == 400, f"an enrollment at 127.0.0.1 was taken: {refused.status}"
    assert "may not be a model server" in await refused.text()
