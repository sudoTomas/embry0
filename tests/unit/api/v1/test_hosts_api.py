"""Hosts API (ADR 0013) against a real Postgres: CRUD, repository links, the probe and the tailnet import.

The tailnet API and the TCP connect are replaced on ``embry0.services.hosts``;
everything else, the credential registry included, is the real thing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from embry0.safety.redaction import known_secret_values, register_known_secrets
from embry0.services import hosts as hosts_service
from embry0.services.hosts import TailnetAccess, TailnetAPIError, TailnetDevice, device_from_api

pytestmark = [pytest.mark.requires_postgres]

SECRET = "tskey-api-unit-test-secret-value"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
DEVICES = [
    {
        "name": "web-1.example.ts.net",
        "hostname": "web-1",
        "addresses": ["100.64.0.10"],
        "tags": ["tag:server"],
        "lastSeen": "2026-10-16T11:59:00Z",
        "connectedToControl": True,
    },
    {"name": "db-1.example.ts.net", "hostname": "db-1", "addresses": ["100.64.0.11"], "connectedToControl": False},
]


@pytest.fixture(autouse=True)
def _restore_scrub_set() -> Iterator[None]:
    """POST /credentials registers its secret process-wide; put the set back for the modules after this one."""
    before = known_secret_values()
    yield
    register_known_secrets(before, reset=True)


@pytest.fixture
async def client(api_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    db = api_client.app.state.db  # type: ignore[attr-defined]
    await db.execute("TRUNCATE hosts, repo_hosts, credential_bindings, credential_uses, credentials CASCADE")
    yield api_client
    await db.execute("TRUNCATE hosts, repo_hosts, credential_bindings, credential_uses, credentials CASCADE")


@pytest.fixture
def tailnet(monkeypatch: pytest.MonkeyPatch) -> list[TailnetAccess]:
    """Serve the canned device list for every tailnet read, and record who asked."""
    calls: list[TailnetAccess] = []

    async def fake(access: TailnetAccess, **_: Any) -> list[TailnetDevice]:
        calls.append(access)
        return [d for d in (device_from_api(raw, now=NOW) for raw in DEVICES) if d]

    monkeypatch.setattr(hosts_service, "list_tailnet_devices", fake)
    return calls


async def _bind_tailnet_key(client: AsyncClient, tailnet: str = "example.com") -> str:
    created = await client.post(
        "/api/v1/credentials",
        json={
            "kind": "tailnet_api_key",
            "provider": "tailscale",
            "label": "tailnet",
            "secret": SECRET,
            "metadata": {"tailnet": tailnet},
        },
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    bound = await client.put(
        "/api/v1/credentials/bindings",
        json={"scope_kind": "global", "scope_value": "", "bindings": [{"credential_id": cid, "role": "tailnet"}]},
    )
    assert bound.status_code == 200, bound.text
    return cid


async def _create(client: AsyncClient, **body: Any) -> dict[str, Any]:
    resp = await client.post("/api/v1/hosts", json={"name": "web-1", "transport": "ssh", "address": "web-1:22", **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---- CRUD ----


async def test_create_list_get(client: AsyncClient) -> None:
    host = await _create(
        client,
        roles=["app"],
        tags=["prod"],
        log_source={"kind": "journald", "selector": "widgets.service"},
        repos=[{"repo": "acme/widgets", "role": "app"}],
    )
    assert host["id"].startswith("host-")
    assert (host["source"], host["status"], host["last_seen_at"]) == ("manual", "active", None)
    assert host["repos"] == [{"repo": "acme/widgets", "role": "app"}]
    listing = (await client.get("/api/v1/hosts")).json()
    assert [h["id"] for h in listing["hosts"]] == [host["id"]]
    assert listing["transports"] == ["tailnet", "ssh", "http"]
    assert listing["log_source_kinds"] == ["journald", "docker", "loki"]
    assert (await client.get("/api/v1/hosts", params={"source": "tailnet"})).json()["hosts"] == []
    assert (await client.get("/api/v1/hosts", params={"source": "nowhere"})).status_code == 422
    one = await client.get(f"/api/v1/hosts/{host['id']}")
    assert one.status_code == 200 and one.json() == host
    assert (await client.get("/api/v1/hosts/host-000000000000")).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"name": "web-1", "transport": "ssh", "address": ""},
        {"name": "web-1", "transport": "ssh", "address": "web-1:0"},
        {"name": "web-1", "transport": "http", "address": "ftp://web-1"},
        {"name": "web-1", "transport": "telnet", "address": "web-1"},
        {"name": "web 1", "transport": "tailnet"},
        {"name": "web-1", "transport": "tailnet", "log_source": {"kind": "syslog", "selector": "x"}},
        {"name": "web-1", "transport": "tailnet", "roles": ["App Server"]},
        {"name": "web-1", "transport": "tailnet", "repos": [{"repo": "widgets"}]},
        {"name": "web-1", "transport": "tailnet", "owner": "acme"},
    ],
)
async def test_create_refuses_a_bad_body(client: AsyncClient, body: dict[str, Any]) -> None:
    resp = await client.post("/api/v1/hosts", json=body)
    assert resp.status_code == 422, resp.text


async def test_a_second_host_with_the_same_name_is_a_conflict(client: AsyncClient) -> None:
    await _create(client)
    resp = await client.post("/api/v1/hosts", json={"name": "WEB-1", "transport": "tailnet"})
    assert resp.status_code == 409


async def test_patch_changes_what_is_sent_and_replaces_links(client: AsyncClient) -> None:
    host = await _create(client, tags=["prod"], log_source={"kind": "docker", "selector": "widgets-api"})
    resp = await client.patch(
        f"/api/v1/hosts/{host['id']}",
        json={"roles": ["app", "db"], "log_source": None, "repos": [{"repo": "Acme/Platform/API", "role": "db"}]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["roles"], body["log_source"], body["tags"], body["address"]) == (
        ["app", "db"],
        None,
        ["prod"],
        "web-1:22",
    )
    assert body["repos"] == [{"repo": "acme/platform/api", "role": "db"}]
    assert (await client.patch(f"/api/v1/hosts/{host['id']}", json={"name": None})).status_code == 422
    assert (await client.patch(f"/api/v1/hosts/{host['id']}", json={"address": "web 1"})).status_code == 422
    assert (await client.patch(f"/api/v1/hosts/{host['id']}", json={"transport": "http"})).status_code == 200
    assert (await client.patch("/api/v1/hosts/host-000000000000", json={"roles": []})).status_code == 404


async def test_repo_detail_includes_its_hosts(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/repos", json={"repo": "acme/widgets", "forge": "github"})).status_code == 201
    assert (await client.get("/api/v1/repos/acme/widgets")).json()["hosts"] == []
    host = await _create(client, repos=[{"repo": "acme/widgets", "role": "app"}])
    detail = (await client.get("/api/v1/repos/acme/widgets")).json()
    assert [(h["id"], h["name"], h["role"], h["transport"]) for h in detail["hosts"]] == [
        (host["id"], "web-1", "app", "ssh")
    ]


async def test_delete_removes_the_host_and_its_links(client: AsyncClient) -> None:
    host = await _create(client, repos=[{"repo": "acme/widgets"}])
    assert (await client.delete(f"/api/v1/hosts/{host['id']}")).status_code == 204
    assert (await client.get("/api/v1/hosts")).json()["hosts"] == []
    gone = (await client.get(f"/api/v1/hosts/{host['id']}")).json()
    assert (gone["status"], gone["repos"]) == ("removed", [])
    shown = (await client.get("/api/v1/hosts", params={"include_removed": "true"})).json()["hosts"]
    assert [h["id"] for h in shown] == [host["id"]]
    relink = await client.patch(f"/api/v1/hosts/{host['id']}", json={"repos": [{"repo": "acme/widgets"}]})
    assert relink.status_code == 409
    assert (await client.post(f"/api/v1/hosts/{host['id']}/probe")).status_code == 409
    assert (await client.delete("/api/v1/hosts/host-000000000000")).status_code == 404
    back = await client.patch(f"/api/v1/hosts/{host['id']}", json={"status": "active"})
    assert back.status_code == 200 and back.json()["status"] == "active"


async def test_mutations_keep_the_csrf_guard(client: AsyncClient) -> None:
    bare = {"X-Requested-With": ""}
    assert (
        await client.post("/api/v1/hosts", json={"name": "web-1", "transport": "tailnet"}, headers=bare)
    ).status_code == 403
    assert (await client.post("/api/v1/hosts/import/tailnet", json={"confirm": True}, headers=bare)).status_code == 403


# ---- the probe ----


async def test_probe_an_ssh_host(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    dialed: list[tuple[str, int]] = []
    answer: list[BaseException | None] = [None]

    async def connect(host: str, port: int, timeout: float) -> None:
        dialed.append((host, port))
        if answer[0] is not None:
            raise answer[0]

    monkeypatch.setattr(hosts_service, "tcp_connect", connect)
    host = await _create(client, address="web-1.example.com:2222")
    up = await client.post(f"/api/v1/hosts/{host['id']}/probe")
    assert up.status_code == 200, up.text
    assert up.json()["probe"]["reachable"] is True
    assert up.json()["host"]["status"] == "active" and up.json()["host"]["last_seen_at"]
    answer[0] = ConnectionRefusedError()
    down = (await client.post(f"/api/v1/hosts/{host['id']}/probe")).json()
    assert down["probe"]["reachable"] is False and "ConnectionRefusedError" in down["probe"]["detail"]
    assert down["host"]["status"] == "unreachable"
    assert dialed == [("web-1.example.com", 2222), ("web-1.example.com", 2222)]


async def test_probe_a_tailnet_host_needs_a_bound_key(client: AsyncClient, tailnet: list[TailnetAccess]) -> None:
    host = await _create(client, transport="tailnet", address="")
    missing = await client.post(f"/api/v1/hosts/{host['id']}/probe")
    assert missing.status_code == 422 and "tailnet_api_key" in missing.json()["detail"]
    assert tailnet == []
    cid = await _bind_tailnet_key(client)
    resp = await client.post(f"/api/v1/hosts/{host['id']}/probe")
    assert resp.status_code == 200, resp.text
    probe = resp.json()["probe"]
    assert probe["reachable"] is True and probe["device"]["name"] == "web-1"
    assert resp.json()["host"]["last_seen_at"] == "2026-10-16T11:59:00+00:00"
    assert [(a.api_key, a.tailnet, a.credential_id) for a in tailnet] == [(SECRET, "example.com", cid)]
    assert SECRET not in resp.text


async def test_probe_reports_a_tailnet_failure_without_touching_the_host(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refused(access: TailnetAccess, **_: Any) -> list[TailnetDevice]:
        raise TailnetAPIError("the key was refused (HTTP 401)")

    monkeypatch.setattr(hosts_service, "list_tailnet_devices", refused)
    await _bind_tailnet_key(client)
    host = await _create(client, transport="tailnet", address="")
    resp = await client.post(f"/api/v1/hosts/{host['id']}/probe")
    assert resp.status_code == 502 and resp.json()["detail"] == "tailnet API: the key was refused (HTTP 401)"
    assert (await client.get(f"/api/v1/hosts/{host['id']}")).json()["status"] == "active"


async def test_an_unusable_tailnet_key_is_a_409_when_named_and_skipped_when_bound(
    client: AsyncClient, tailnet: list[TailnetAccess]
) -> None:
    db = client.app.state.db  # type: ignore[attr-defined]
    cid = await _bind_tailnet_key(client)
    host = await _create(client, transport="tailnet", address="")
    probe = f"/api/v1/hosts/{host['id']}/probe"
    await db.execute("UPDATE credentials SET status = 'disabled' WHERE id = $1", cid)
    named = await client.post(probe, json={"credential_id": cid})
    assert named.status_code == 409 and "is disabled" in named.json()["detail"], named.text
    imported = await client.post("/api/v1/hosts/import/tailnet", json={"confirm": True, "credential_id": cid})
    assert imported.status_code == 409 and "is disabled" in imported.json()["detail"], imported.text
    bound = await client.post(probe)
    assert bound.status_code == 422 and "skipped (disabled)" in bound.json()["detail"], bound.text
    await db.execute("UPDATE credentials SET status = 'ok', secret_ciphertext = 'not-a-token' WHERE id = $1", cid)
    undecryptable = await client.post(probe, json={"credential_id": cid})
    assert undecryptable.status_code == 409 and "cannot be decrypted" in undecryptable.json()["detail"]
    row = await db.fetchrow("SELECT status, status_reason FROM credentials WHERE id = $1", cid)
    assert (row["status"], row["status_reason"]) == ("disabled", "decryption_failed")
    assert tailnet == [], "an unusable key reached the tailnet API"
    assert (await client.get(f"/api/v1/hosts/{host['id']}")).json()["status"] == "active"


# ---- the import ----


async def test_import_needs_confirm_then_is_idempotent(client: AsyncClient, tailnet: list[TailnetAccess]) -> None:
    refused = await client.post("/api/v1/hosts/import/tailnet", json={})
    assert refused.status_code == 422 and "confirm=true" in refused.json()["detail"]
    assert tailnet == [], "an unconfirmed import read the tailnet"
    cid = await _bind_tailnet_key(client)
    first = await client.post("/api/v1/hosts/import/tailnet", json={"confirm": True})
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["tailnet"], body["credential_id"]) == ("example.com", cid)
    assert body["counts"] == {"created": 2, "updated": 0, "unchanged": 0, "skipped": 0}
    hosts = {h["name"]: h for h in (await client.get("/api/v1/hosts", params={"source": "tailnet"})).json()["hosts"]}
    assert set(hosts) == {"db-1", "web-1"}
    assert (hosts["web-1"]["status"], hosts["db-1"]["status"]) == ("active", "unreachable")
    second = (await client.post("/api/v1/hosts/import/tailnet", json={"confirm": True})).json()
    assert second["counts"] == {"created": 0, "updated": 0, "unchanged": 2, "skipped": 0}
    assert SECRET not in first.text + str(second)


async def test_import_with_an_explicit_credential(client: AsyncClient, tailnet: list[TailnetAccess]) -> None:
    created = await client.post(
        "/api/v1/credentials",
        json={"kind": "tailnet_api_key", "provider": "tailscale", "label": "second", "secret": SECRET},
    )
    cid = created.json()["id"]
    assert created.json()["metadata"] == {"tailnet": "-"}
    resp = await client.post("/api/v1/hosts/import/tailnet", json={"confirm": True, "credential_id": cid})
    assert resp.status_code == 200, resp.text
    assert tailnet[0].tailnet == "-" and tailnet[0].credential_id == cid
    unbound = await client.post("/api/v1/hosts/import/tailnet", json={"confirm": True})
    assert unbound.status_code == 422, "an import with no credential named and none bound must say so"
