"""Hosts (ADR 0013): the reachability probe, the tailnet device list and the import.

The tailnet API is served by ``httpx.MockTransport`` from a canned device
list, and the TCP connect is a fake, so nothing here leaves the process.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from structlog.testing import capture_logs

from embry0.services import hosts as hosts_service
from embry0.services.hosts import (
    Host,
    ProbeError,
    TailnetAccess,
    TailnetAPIError,
    TailnetDevice,
    device_from_api,
    import_tailnet,
    list_tailnet_devices,
    match_device,
    parse_endpoint,
    probe_host,
)

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
KEY = "tskey-api-unit-test-secret-value"

CANNED_DEVICES: list[dict[str, Any]] = [
    {
        "id": "1001",
        "nodeId": "nWeb1CNTRL",
        "name": "web-1.example.ts.net",
        "hostname": "web-1",
        "addresses": ["100.64.0.10", "fd7a:115c:a1e0::10"],
        "os": "linux",
        "tags": ["tag:server"],
        "lastSeen": "2026-10-16T11:59:00Z",
        "connectedToControl": True,
    },
    {
        "id": "1002",
        "nodeId": "nDb1CNTRL",
        "name": "db-1.example.ts.net",
        "hostname": "Database Box",
        "addresses": ["100.64.0.11"],
        "os": "linux",
        "tags": [],
        "lastSeen": "2026-10-14T08:00:00Z",
        "connectedToControl": False,
    },
    {
        # No connectedToControl: a lastSeen within the window counts as online.
        "id": "1003",
        "name": "worker-1.example.ts.net",
        "hostname": "worker-1",
        "addresses": ["100.64.0.12"],
        "os": "linux",
        "lastSeen": "2026-10-16T11:58:00Z",
    },
    {"id": "1004", "name": "", "hostname": ""},  # nameless: dropped
]


def _devices() -> list[TailnetDevice]:
    devices = [device_from_api(d, now=NOW) for d in CANNED_DEVICES]
    return [d for d in devices if d is not None]


def _host(**kw: Any) -> Host:
    base: dict[str, Any] = {"id": "host-0123456789ab", "name": "web-1", "address": "", "transport": "tailnet"}
    return Host(**{**base, **kw})


def _api(devices: list[dict[str, Any]], *, status: int = 200, seen: list[httpx.Request] | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={"devices": devices} if status == 200 else {"message": "no"})

    return httpx.MockTransport(handler)


# ---- endpoints ----


@pytest.mark.parametrize(
    ("address", "transport", "expected"),
    [
        ("web-1", "ssh", ("web-1", 22)),
        ("web-1:2222", "ssh", ("web-1", 2222)),
        ("[fd00::1]:2222", "ssh", ("fd00::1", 2222)),
        ("web-1", "http", ("web-1", 80)),
        ("web-1:8080", "http", ("web-1", 8080)),
        ("https://web-1.example.com/health", "http", ("web-1.example.com", 443)),
        ("http://web-1.example.com:8081", "http", ("web-1.example.com", 8081)),
    ],
)
def test_parse_endpoint(address: str, transport: str, expected: tuple[str, int]) -> None:
    assert parse_endpoint(address, transport) == expected


@pytest.mark.parametrize(
    ("address", "transport"),
    [
        ("", "ssh"),
        ("web 1", "ssh"),
        ("web-1:0", "ssh"),
        ("web-1:99999", "ssh"),
        ("web-1:ssh", "ssh"),
        ("https://web-1", "ssh"),
        ("ftp://web-1", "http"),
        ("web-1/path", "http"),
        ("web-1", "tailnet"),
    ],
)
def test_parse_endpoint_refuses(address: str, transport: str) -> None:
    with pytest.raises(ProbeError):
        parse_endpoint(address, transport)


# ---- the device list ----


def test_devices_parse_into_names_addresses_and_connection_state() -> None:
    web, db, worker = _devices()
    assert (web.name, web.fqdn, web.address, web.online) == ("web-1", "web-1.example.ts.net", "100.64.0.10", True)
    assert web.tags == ("tag:server",)
    assert (db.name, db.online) == ("db-1", False), "the short name comes from the MagicDNS name, not the OS hostname"
    assert worker.online is True, "a lastSeen within the window counts as online when the API omits the flag"
    stale = device_from_api({"name": "old.example.ts.net", "lastSeen": "2026-10-16T11:00:00Z"}, now=NOW)
    assert stale is not None and stale.online is False
    unknown = device_from_api({"name": "quiet.example.ts.net"}, now=NOW)
    assert unknown is not None and unknown.online is None


async def test_list_devices_reads_one_url_with_the_key_as_bearer() -> None:
    seen: list[httpx.Request] = []
    access = TailnetAccess(api_key=KEY, tailnet="example.com")
    devices = await list_tailnet_devices(access, transport=_api(CANNED_DEVICES, seen=seen), now=NOW)
    assert [d.name for d in devices] == ["web-1", "db-1", "worker-1"]
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert str(seen[0].url) == "https://api.tailscale.com/api/v2/tailnet/example.com/devices"
    assert seen[0].headers["Authorization"] == f"Bearer {KEY}"


async def test_list_devices_defaults_to_the_keys_own_tailnet() -> None:
    seen: list[httpx.Request] = []
    await list_tailnet_devices(TailnetAccess(api_key=KEY), transport=_api([], seen=seen))
    assert seen[0].url.path == "/api/v2/tailnet/-/devices"


@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "the key was refused (HTTP 401)"), (403, "the key was refused (HTTP 403)"), (500, "HTTP 500")],
)
async def test_list_devices_reports_a_refusal_by_status(status: int, reason: str) -> None:
    with pytest.raises(TailnetAPIError) as caught:
        await list_tailnet_devices(TailnetAccess(api_key=KEY), transport=_api([], status=status))
    assert caught.value.reason == reason
    assert KEY not in str(caught.value)


async def test_a_transport_error_never_quotes_the_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect, header was {request.headers['Authorization']}")

    with pytest.raises(TailnetAPIError) as caught:
        await list_tailnet_devices(TailnetAccess(api_key=KEY), transport=httpx.MockTransport(handler))
    assert caught.value.reason == "unreachable (ConnectError)"
    assert KEY not in str(caught.value) and caught.value.__cause__ is None


# ---- an OAuth client secret ----

CLIENT_SECRET = "tskey-client-kUnitTest1CNTRL-unit-test-secret-value"
EXCHANGED = "exchanged-access-token"


def _oauth_api(
    devices: list[dict[str, Any]],
    *,
    token_status: int = 200,
    token_body: Any = None,
    seen: list[httpx.Request] | None = None,
) -> Any:
    """The token endpoint and the device list. The list answers any bearer, so the asserts decide."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/api/v2/oauth/token":
            if token_status != 200:
                return httpx.Response(token_status, json={"message": "API token invalid"})
            body = {"access_token": EXCHANGED, "token_type": "Bearer", "expires_in": 3600}
            return httpx.Response(200, json=body if token_body is None else token_body)
        return httpx.Response(200, json={"devices": devices})

    return httpx.MockTransport(handler)


async def test_an_oauth_client_secret_is_exchanged_for_an_access_token_first() -> None:
    seen: list[httpx.Request] = []
    access = TailnetAccess(api_key=CLIENT_SECRET)
    devices = await list_tailnet_devices(access, transport=_oauth_api(CANNED_DEVICES, seen=seen), now=NOW)
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/api/v2/oauth/token"),
        ("GET", "/api/v2/tailnet/-/devices"),
    ], "an OAuth client secret must be exchanged before the device list is read"
    assert parse_qs(seen[0].content.decode()) == {"client_id": ["kUnitTest1CNTRL"], "client_secret": [CLIENT_SECRET]}
    assert seen[1].headers["Authorization"] == f"Bearer {EXCHANGED}", "the list is read with the exchanged token"
    assert [d.name for d in devices] == ["web-1", "db-1", "worker-1"]


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (400, "the OAuth client was refused (HTTP 400)"),
        (401, "the OAuth client was refused (HTTP 401)"),
        (403, "the OAuth client was refused (HTTP 403)"),
        (500, "the token exchange failed (HTTP 500)"),
    ],
)
async def test_a_failed_exchange_reports_its_status_and_reads_nothing(status: int, reason: str) -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(TailnetAPIError) as caught:
        await list_tailnet_devices(
            TailnetAccess(api_key=CLIENT_SECRET), transport=_oauth_api([], token_status=status, seen=seen)
        )
    assert caught.value.reason == reason
    assert CLIENT_SECRET not in str(caught.value)
    assert [r.url.path for r in seen] == ["/api/v2/oauth/token"], "no device read after a failed exchange"


@pytest.mark.parametrize("body", [{}, {"access_token": ""}, {"access_token": 7}, ["not", "an", "object"]])
async def test_a_token_answer_without_an_access_token_is_an_error(body: Any) -> None:
    with pytest.raises(TailnetAPIError) as caught:
        await list_tailnet_devices(TailnetAccess(api_key=CLIENT_SECRET), transport=_oauth_api([], token_body=body))
    assert caught.value.reason == "the token answer has no access token"


async def test_a_transport_error_in_the_exchange_never_quotes_the_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect, body was {request.content!r}")

    with pytest.raises(TailnetAPIError) as caught:
        await list_tailnet_devices(TailnetAccess(api_key=CLIENT_SECRET), transport=httpx.MockTransport(handler))
    assert caught.value.reason == "unreachable (ConnectError)"
    assert CLIENT_SECRET not in str(caught.value) and caught.value.__cause__ is None


def test_the_key_stays_out_of_repr() -> None:
    access = TailnetAccess(api_key=KEY, tailnet="example.com", credential_id="cred-0123456789ab", label="tailnet")
    assert KEY not in repr(access)


# ---- matching ----


def test_match_by_address_then_by_name() -> None:
    devices = _devices()
    assert match_device(_host(name="anything", address="100.64.0.11"), devices) is devices[1]
    assert match_device(_host(name="anything", address="fd7a:115c:a1e0::10"), devices) is devices[0]
    assert match_device(_host(name="anything", address="db-1.example.ts.net"), devices) is devices[1]
    assert match_device(_host(name="WORKER-1", address=""), devices) is devices[2]
    assert match_device(_host(name="database box", address=""), devices) is devices[1]
    assert match_device(_host(name="ghost", address="100.64.0.99"), devices) is None


# ---- the probe ----


async def _fetch(access: TailnetAccess) -> list[TailnetDevice]:
    return _devices()


async def test_tailnet_probe_reachable_when_connected() -> None:
    result = await probe_host(_host(), tailnet=TailnetAccess(api_key=KEY), fetch_devices=_fetch, now=NOW)
    assert result.reachable is True
    assert result.last_seen_at == datetime(2026, 10, 16, 11, 59, tzinfo=UTC)
    assert result.device is not None and result.device["name"] == "web-1"


async def test_tailnet_probe_unreachable_when_offline_or_missing() -> None:
    offline = await probe_host(_host(name="db-1"), tailnet=TailnetAccess(api_key=KEY), fetch_devices=_fetch, now=NOW)
    assert offline.reachable is False and "offline" in offline.detail
    assert offline.last_seen_at == datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
    missing = await probe_host(_host(name="ghost"), tailnet=TailnetAccess(api_key=KEY), fetch_devices=_fetch, now=NOW)
    assert missing.reachable is False and missing.device is None and "no device" in missing.detail


async def test_tailnet_probe_needs_a_key() -> None:
    with pytest.raises(ProbeError, match="tailnet_api_key"):
        await probe_host(_host(), tailnet=None, fetch_devices=_fetch)


async def test_tailnet_probe_never_logs_the_key() -> None:
    with capture_logs() as logs:
        await probe_host(_host(), tailnet=TailnetAccess(api_key=KEY), fetch_devices=_fetch, now=NOW)
    assert logs, "the probe logged nothing, so this test would pass on nothing"
    assert KEY not in repr(logs)


async def test_tcp_probe_reachable_when_the_port_answers() -> None:
    dialed: list[tuple[str, int, float]] = []

    async def connect(host: str, port: int, timeout: float) -> None:
        dialed.append((host, port, timeout))

    host = _host(transport="ssh", address="web-1.example.com:2222")
    result = await probe_host(host, connect=connect, now=NOW)
    assert dialed == [("web-1.example.com", 2222, hosts_service.PROBE_TIMEOUT_SECONDS)]
    assert result.reachable is True and result.last_seen_at == NOW


@pytest.mark.parametrize(
    ("error", "words"),
    [(ConnectionRefusedError(), "ConnectionRefusedError"), (TimeoutError(), "timed out")],
)
async def test_tcp_probe_unreachable_on_refusal_or_timeout(error: BaseException, words: str) -> None:
    async def connect(host: str, port: int, timeout: float) -> None:
        raise error

    result = await probe_host(_host(transport="http", address="https://web-1.example.com"), connect=connect, now=NOW)
    assert result.reachable is False and result.last_seen_at is None
    assert words in result.detail and "web-1.example.com:443" in result.detail


async def test_tcp_probe_refuses_an_address_with_no_endpoint() -> None:
    async def connect(host: str, port: int, timeout: float) -> None:  # pragma: no cover - must not be called
        raise AssertionError("dialed")

    with pytest.raises(ProbeError):
        await probe_host(_host(transport="ssh", address=""), connect=connect)


async def test_the_real_connector_reaches_a_listening_port() -> None:
    """tcp_connect against a socket this test opens: reachable, then refused once it is closed."""
    import asyncio

    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        result = await probe_host(_host(transport="ssh", address=f"127.0.0.1:{port}"), timeout=2.0)
    assert result.reachable is True, result.detail
    server.close()
    await server.wait_closed()
    closed = await probe_host(_host(transport="ssh", address=f"127.0.0.1:{port}"), timeout=2.0)
    assert closed.reachable is False, closed.detail


# ---- the import ----


class MemStore:
    def __init__(self) -> None:
        self.hosts: dict[str, Host] = {}
        self.writes = 0

    async def get_by_name(self, name: str) -> Host | None:
        return next((h for h in self.hosts.values() if h.name.lower() == name.lower()), None)

    async def create(self, **kw: Any) -> Host:
        self.writes += 1
        host = Host(id=f"host-{len(self.hosts):012x}", **kw)
        self.hosts[host.id] = host
        return host

    async def update(self, host_id: str, fields: dict[str, Any]) -> Host | None:
        self.writes += 1
        host = self.hosts[host_id]
        for key, value in fields.items():
            setattr(host, key, value)
        return host


async def test_import_creates_then_is_idempotent() -> None:
    store = MemStore()
    first = await import_tailnet(store, _devices())
    assert [(r["name"], r["outcome"]) for r in first] == [
        ("db-1", "created"),
        ("web-1", "created"),
        ("worker-1", "created"),
    ]
    web = await store.get_by_name("web-1")
    assert web is not None
    assert (web.transport, web.source, web.address, web.status, web.tags) == (
        "tailnet",
        "tailnet",
        "100.64.0.10",
        "active",
        ["tag:server"],
    )
    db = await store.get_by_name("db-1")
    assert db is not None and db.status == "unreachable"
    writes = store.writes
    second = await import_tailnet(store, _devices())
    assert {r["outcome"] for r in second} == {"unchanged"}
    assert store.writes == writes, "a second identical import wrote something"


async def test_import_refreshes_only_what_the_tailnet_owns() -> None:
    store = MemStore()
    await import_tailnet(store, _devices())
    web = await store.get_by_name("web-1")
    assert web is not None
    web.roles, web.tags, web.log_source = ["app"], ["prod"], {"kind": "journald", "selector": "widgets.service"}
    moved = [dict(CANNED_DEVICES[0], addresses=["100.64.0.20"], connectedToControl=False)]
    result = await import_tailnet(store, [d for d in (device_from_api(x, now=NOW) for x in moved) if d])
    assert result == [{"name": "web-1", "host_id": web.id, "outcome": "updated", "detail": "address, status"}]
    assert (web.address, web.status) == ("100.64.0.20", "unreachable")
    assert (web.roles, web.tags, web.log_source) == (
        ["app"],
        ["prod"],
        {"kind": "journald", "selector": "widgets.service"},
    )


async def test_import_skips_manual_and_removed_hosts() -> None:
    store = MemStore()
    manual = await store.create(name="web-1", address="web-1.example.com:22", transport="ssh", source="manual")
    removed = await store.create(name="db-1", address="", transport="tailnet", source="tailnet", status="removed")
    writes = store.writes
    result = {r["name"]: r for r in await import_tailnet(store, _devices()[:2])}
    assert result["web-1"]["outcome"] == "skipped" and "manual" in result["web-1"]["detail"]
    assert result["db-1"]["outcome"] == "skipped" and "removed" in result["db-1"]["detail"]
    assert store.writes == writes
    assert (manual.address, manual.transport, removed.status) == ("web-1.example.com:22", "ssh", "removed")
