"""Hosts: the machines that run the code embry0 maintains (ADR 0013).

A repository says what the code is; a host is where it runs. The facts a
maintainer needs after a merge, where the logs are and whether the deploy
landed, live on machines, not in repositories, so a host is a record of its
own: a name, an address, how embry0 reaches it (``transport``), what it does
(``roles``), where its logs come from (``log_source``) and which repositories
it serves (``repo_hosts``).

This module holds the vocabulary, the reachability probe and the tailnet
import. It never logs or returns a key: a tailnet API key travels in
``TailnetAccess``, whose ``repr`` leaves it out, and every error is reported
by status code or exception type, never by the text of a request.
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx
import structlog

logger = structlog.get_logger(__name__)

TRANSPORTS = ("tailnet", "ssh", "http")
SOURCES = ("manual", "tailnet")
STATUSES = ("active", "unreachable", "removed")
LOG_SOURCE_KINDS = ("journald", "docker", "loki")
DEFAULT_PORTS = {"ssh": 22, "http": 80}

TAILNET_API_BASE = "https://api.tailscale.com/api/v2"
#: ``-`` asks the tailnet API for the key's own tailnet.
DEFAULT_TAILNET = "-"
PROBE_TIMEOUT_SECONDS = 5.0
#: Without ``connectedToControl`` a device counts as online when its
#: ``lastSeen`` is at most this old.
ONLINE_WINDOW = timedelta(minutes=5)


@dataclass
class Host:
    id: str
    name: str
    address: str
    transport: str
    roles: list[str] = field(default_factory=list)
    log_source: dict[str, Any] | None = None
    tags: list[str] = field(default_factory=list)
    source: str = "manual"
    last_seen_at: datetime | None = None
    status: str = "active"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    repos: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self, *, with_repos: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "transport": self.transport,
            "roles": list(self.roles),
            "log_source": dict(self.log_source) if self.log_source else None,
            "tags": list(self.tags),
            "source": self.source,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if with_repos:
            out["repos"] = [dict(link) for link in self.repos]
        return out


@dataclass(frozen=True)
class TailnetAccess:
    """A tailnet API key and the tailnet it reads. ``repr`` never shows the key."""

    api_key: str = field(repr=False)
    tailnet: str = DEFAULT_TAILNET
    credential_id: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class TailnetDevice:
    """One device from ``GET /tailnet/{tailnet}/devices``, reduced to what a host needs."""

    id: str
    name: str  # the short name: the first label of the MagicDNS name
    fqdn: str
    hostname: str
    addresses: tuple[str, ...]
    tags: tuple[str, ...]
    os: str
    last_seen: datetime | None
    online: bool | None

    @property
    def address(self) -> str:
        """The address a host row stores: the first IPv4 tailnet address, else the first, else the MagicDNS name."""
        for value in self.addresses:
            try:
                if isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address):
                    return value
            except ValueError:
                continue
        return self.addresses[0] if self.addresses else self.fqdn

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "fqdn": self.fqdn,
            "hostname": self.hostname,
            "addresses": list(self.addresses),
            "tags": list(self.tags),
            "os": self.os,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "online": self.online,
        }


@dataclass(frozen=True)
class ProbeResult:
    host_id: str
    transport: str
    reachable: bool
    detail: str
    checked_at: datetime
    last_seen_at: datetime | None = None
    device: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "transport": self.transport,
            "reachable": self.reachable,
            "detail": self.detail,
            "checked_at": self.checked_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "device": self.device,
        }


class TailnetAPIError(RuntimeError):
    """The tailnet API could not be read. ``reason`` is safe to show: a status or an exception type."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ProbeError(ValueError):
    """The probe cannot run for this host as configured (for example an address with no port to reach)."""


Connector = Callable[[str, int, float], Awaitable[None]]
DeviceFetcher = Callable[[TailnetAccess], Awaitable[list[TailnetDevice]]]


# ---- addresses ----


def parse_endpoint(address: str, transport: str) -> tuple[str, int]:
    """``(host, port)`` a TCP probe connects to, for an ``ssh`` or ``http`` host.

    Accepts ``host``, ``host:port``, ``[v6]:port`` and, for ``http``, a URL
    (``https://host:8443/path``; the scheme picks the default port).
    """
    if transport not in DEFAULT_PORTS:
        raise ProbeError(f"transport {transport!r} has no TCP endpoint")
    value = address.strip()
    if not value or any(c.isspace() for c in value):
        raise ProbeError("address must be a host, host:port or URL with no spaces")
    default = DEFAULT_PORTS[transport]
    if "://" in value:
        if transport != "http":
            raise ProbeError("only an http host takes a URL as its address")
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https"):
            raise ProbeError("an http host's URL must be http:// or https://")
        default = 443 if parts.scheme == "https" else 80
    else:
        parts = urlsplit("//" + value)
        if parts.path or parts.query or parts.fragment:
            raise ProbeError("address must be a host or host:port")
    host = parts.hostname or ""
    try:
        explicit = parts.port
    except ValueError:
        raise ProbeError("the port is not a number between 1 and 65535") from None
    port = default if explicit is None else explicit
    if not host:
        raise ProbeError("address has no host")
    if not 1 <= port <= 65535:
        raise ProbeError("the port is not a number between 1 and 65535")
    return host, port


async def tcp_connect(host: str, port: int, timeout: float) -> None:
    """Open a TCP connection and close it at once. Sends nothing."""
    _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass


# ---- the tailnet API ----


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _short_name(fqdn: str, hostname: str) -> str:
    if fqdn:
        return fqdn.rstrip(".").split(".", 1)[0].lower()
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in hostname.strip().lower()).strip("-")


def device_from_api(raw: dict[str, Any], *, now: datetime | None = None) -> TailnetDevice | None:
    """A ``TailnetDevice`` from one entry of the API's ``devices`` list, or None when it has no name."""
    fqdn = str(raw.get("name") or "").rstrip(".").lower()
    hostname = str(raw.get("hostname") or "")
    name = _short_name(fqdn, hostname)
    if not name:
        return None
    last_seen = _timestamp(raw.get("lastSeen"))
    connected = raw.get("connectedToControl")
    online: bool | None
    if isinstance(connected, bool):
        online = connected
    elif last_seen is not None:
        online = (now or datetime.now(UTC)) - last_seen <= ONLINE_WINDOW
    else:
        online = None
    addresses = raw.get("addresses")
    tags = raw.get("tags")
    return TailnetDevice(
        id=str(raw.get("nodeId") or raw.get("id") or ""),
        name=name,
        fqdn=fqdn,
        hostname=hostname,
        addresses=tuple(str(a) for a in addresses) if isinstance(addresses, list) else (),
        tags=tuple(str(t) for t in tags) if isinstance(tags, list) else (),
        os=str(raw.get("os") or ""),
        last_seen=last_seen,
        online=online,
    )


async def list_tailnet_devices(
    access: TailnetAccess,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    api_base: str = TAILNET_API_BASE,
    now: datetime | None = None,
) -> list[TailnetDevice]:
    """Every device of the tailnet, by ``GET {api_base}/tailnet/{tailnet}/devices``.

    The only call embry0 makes to the tailnet API: one read, so the key needs
    read access to devices and nothing more.
    """
    url = f"{api_base}/tailnet/{quote(access.tailnet or DEFAULT_TAILNET, safe='')}/devices"
    headers = {"Authorization": f"Bearer {access.api_key}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        # Never str(exc): a transport error can quote the request.
        raise TailnetAPIError(f"unreachable ({type(exc).__name__})") from None
    if response.status_code in (401, 403):
        raise TailnetAPIError(f"the key was refused (HTTP {response.status_code})")
    if response.status_code == 404:
        raise TailnetAPIError(f"tailnet {access.tailnet!r} not found (HTTP 404)")
    if response.status_code != 200:
        raise TailnetAPIError(f"HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        raise TailnetAPIError("the answer was not JSON") from None
    raw_devices = body.get("devices") if isinstance(body, dict) else None
    if not isinstance(raw_devices, list):
        raise TailnetAPIError("the answer has no devices list")
    devices = [device_from_api(d, now=now) for d in raw_devices if isinstance(d, dict)]
    return [d for d in devices if d is not None]


def match_device(host: Host, devices: Iterable[TailnetDevice]) -> TailnetDevice | None:
    """The device that is this host: by address first, then by name."""
    address = host.address.strip().rstrip(".").lower()
    name = host.name.strip().lower()
    candidates = list(devices)
    if address:
        for device in candidates:
            if address in (a.lower() for a in device.addresses) or address in (device.fqdn, device.name):
                return device
    for device in candidates:
        if name in (device.name, device.fqdn, device.hostname.lower()):
            return device
    return None


# ---- the probe ----


async def probe_host(
    host: Host,
    *,
    tailnet: TailnetAccess | None = None,
    connect: Connector | None = None,
    fetch_devices: DeviceFetcher | None = None,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> ProbeResult:
    """Is the host reachable over its transport?

    ``tailnet``: list the tailnet's devices and look for this host by address
    or name; reachable means the tailnet reports it connected. ``ssh`` and
    ``http``: a TCP connect to the port, closed at once, nothing sent.

    Raises ``ProbeError`` when the probe cannot run as configured and
    ``TailnetAPIError`` when the tailnet cannot be read; neither says
    anything about the host, so neither may change its status.
    """
    checked = now or datetime.now(UTC)
    if host.transport == "tailnet":
        if tailnet is None:
            raise ProbeError("a tailnet host needs a tailnet_api_key credential to probe")
        fetch = fetch_devices or list_tailnet_devices
        devices = await fetch(tailnet)
        device = match_device(host, devices)
        if device is None:
            result = ProbeResult(
                host_id=host.id,
                transport=host.transport,
                reachable=False,
                detail=f"no device in tailnet {tailnet.tailnet!r} has this host's address or name",
                checked_at=checked,
            )
        else:
            reachable = device.online is True
            last_seen = device.last_seen or (checked if reachable else None)
            if reachable:
                detail = f"device {device.name} is connected to the tailnet"
            elif device.online is None:
                detail = f"device {device.name} is in the tailnet, which reports no connection state for it"
            else:
                seen = device.last_seen.isoformat() if device.last_seen else "never"
                detail = f"device {device.name} is offline (last seen {seen})"
            result = ProbeResult(
                host_id=host.id,
                transport=host.transport,
                reachable=reachable,
                detail=detail,
                checked_at=checked,
                last_seen_at=last_seen,
                device=device.to_dict(),
            )
    else:
        target, port = parse_endpoint(host.address, host.transport)
        dial = connect or tcp_connect
        try:
            await dial(target, port, timeout)
        except TimeoutError:
            result = ProbeResult(
                host_id=host.id,
                transport=host.transport,
                reachable=False,
                detail=f"TCP connect to {target}:{port} timed out after {timeout:g}s",
                checked_at=checked,
            )
        except OSError as exc:
            result = ProbeResult(
                host_id=host.id,
                transport=host.transport,
                reachable=False,
                detail=f"TCP connect to {target}:{port} failed ({type(exc).__name__})",
                checked_at=checked,
            )
        else:
            result = ProbeResult(
                host_id=host.id,
                transport=host.transport,
                reachable=True,
                detail=f"TCP connect to {target}:{port} succeeded",
                checked_at=checked,
                last_seen_at=checked,
            )
    logger.info("host_probed", host_id=host.id, transport=host.transport, reachable=result.reachable)
    return result


# ---- the import ----


class HostStore(Protocol):
    async def get_by_name(self, name: str) -> Host | None: ...

    async def create(
        self,
        *,
        name: str,
        address: str,
        transport: str,
        roles: list[str] | None = None,
        log_source: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        source: str = "manual",
        last_seen_at: datetime | None = None,
        status: str = "active",
    ) -> Host: ...

    async def update(self, host_id: str, fields: dict[str, Any]) -> Host | None: ...


def _device_status(device: TailnetDevice) -> str:
    return "active" if device.online is True else "unreachable"


async def import_tailnet(store: HostStore, devices: Iterable[TailnetDevice]) -> list[dict[str, Any]]:
    """Create or update one host row per device. Idempotent.

    A new device becomes a ``tailnet`` host named after its MagicDNS short
    name, with its tailnet address and tags. A host the tailnet created
    before gets only its address, status and last-seen time refreshed:
    roles, tags, log source and repositories belong to whoever set them. A
    manual host with the same name, or a host a person removed, is left
    alone and reported as skipped.
    """
    results: list[dict[str, Any]] = []
    for device in sorted(devices, key=lambda d: d.name):
        existing = await store.get_by_name(device.name)
        status = _device_status(device)
        if existing is None:
            host = await store.create(
                name=device.name,
                address=device.address,
                transport="tailnet",
                tags=list(device.tags),
                source="tailnet",
                last_seen_at=device.last_seen,
                status=status,
            )
            results.append({"name": device.name, "host_id": host.id, "outcome": "created", "detail": ""})
            continue
        if existing.status == "removed":
            results.append(
                {
                    "name": device.name,
                    "host_id": existing.id,
                    "outcome": "skipped",
                    "detail": "a person removed this host; set its status to active to take it back",
                }
            )
            continue
        if existing.source != "tailnet":
            results.append(
                {
                    "name": device.name,
                    "host_id": existing.id,
                    "outcome": "skipped",
                    "detail": "a manual host has this name",
                }
            )
            continue
        changes: dict[str, Any] = {}
        if existing.address != device.address:
            changes["address"] = device.address
        if existing.status != status:
            changes["status"] = status
        if device.last_seen is not None and existing.last_seen_at != device.last_seen:
            changes["last_seen_at"] = device.last_seen
        if changes:
            await store.update(existing.id, changes)
        results.append(
            {
                "name": device.name,
                "host_id": existing.id,
                "outcome": "updated" if changes else "unchanged",
                "detail": ", ".join(sorted(changes)),
            }
        )
    return results
