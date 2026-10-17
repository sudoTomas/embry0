"""HostsRepository against Postgres (ADR 0013, migration 100): hosts, repo_hosts and the import's writes."""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest

from embry0.services.hosts import device_from_api, import_tailnet
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.hosts import HostNameTakenError, HostsRepository, new_host_id

pytestmark = [pytest.mark.requires_postgres]

SEEN = datetime(2026, 10, 16, 11, 59, tzinfo=UTC)


def test_new_host_id_shape() -> None:
    hid = new_host_id()
    assert hid.startswith("host-") and len(hid) == 17


async def test_create_get_list_and_the_source_filter(db_with_migrations: DatabasePool) -> None:
    repo = HostsRepository(db_with_migrations)
    web = await repo.create(
        name="web-1",
        address="web-1.example.com:22",
        transport="ssh",
        roles=["app"],
        log_source={"kind": "journald", "selector": "widgets.service"},
        tags=["prod"],
    )
    ts = await repo.create(name="worker-1", address="100.64.0.12", transport="tailnet", source="tailnet")
    got = await repo.get(web.id)
    assert got is not None
    assert (got.name, got.transport, got.roles, got.tags, got.source, got.status) == (
        "web-1",
        "ssh",
        ["app"],
        ["prod"],
        "manual",
        "active",
    )
    assert got.log_source == {"kind": "journald", "selector": "widgets.service"}
    assert [h.id for h in await repo.list_all()] == [web.id, ts.id]
    assert [h.id for h in await repo.list_all(source="tailnet")] == [ts.id]
    assert [h.id for h in await repo.list_all(source="manual")] == [web.id]
    assert (await repo.get_by_name("WEB-1")) is not None
    assert await repo.get("host-000000000000") is None


async def test_names_are_unique_ignoring_case(db_with_migrations: DatabasePool) -> None:
    repo = HostsRepository(db_with_migrations)
    first = await repo.create(name="web-1", address="", transport="tailnet")
    with pytest.raises(HostNameTakenError):
        await repo.create(name="WEB-1", address="", transport="tailnet")
    other = await repo.create(name="web-2", address="", transport="tailnet")
    with pytest.raises(HostNameTakenError):
        await repo.update(other.id, {"name": "Web-1"})
    assert (await repo.get(first.id)) is not None


async def test_remove_keeps_the_row_drops_the_links_and_create_revives_it(db_with_migrations: DatabasePool) -> None:
    repo = HostsRepository(db_with_migrations)
    host = await repo.create(name="web-1", address="web-1.example.com", transport="http")
    await repo.replace_repos(host.id, [("acme/widgets", "app")])
    removed = await repo.remove(host.id)
    assert removed is not None and removed.status == "removed" and removed.repos == []
    assert await repo.list_all() == []
    assert [h.id for h in await repo.list_all(include_removed=True)] == [host.id]
    assert await repo.hosts_for_repo("acme/widgets") == []
    await repo.replace_repos(host.id, [("acme/widgets", "app")])
    assert await repo.hosts_for_repo("acme/widgets") == [], "a removed host still serves a repository"
    revived = await repo.create(name="WEB-1", address="web-1.example.com:22", transport="ssh")
    assert (revived.id, revived.name, revived.transport, revived.status) == (host.id, "WEB-1", "ssh", "active")


async def test_update_sets_only_what_it_is_given(db_with_migrations: DatabasePool) -> None:
    repo = HostsRepository(db_with_migrations)
    host = await repo.create(
        name="web-1",
        address="web-1.example.com",
        transport="http",
        log_source={"kind": "docker", "selector": "widgets-api"},
        tags=["prod"],
    )
    updated = await repo.update(host.id, {"roles": ["app", "db"], "log_source": None})
    assert updated is not None
    assert (updated.roles, updated.log_source, updated.tags, updated.address) == (
        ["app", "db"],
        None,
        ["prod"],
        "web-1.example.com",
    )
    with pytest.raises(ValueError, match="cannot update"):
        await repo.update(host.id, {"source": "tailnet"})
    assert await repo.update("host-000000000000", {"roles": []}) is None


async def test_record_probe_writes_status_and_last_seen_but_never_a_removed_host(
    db_with_migrations: DatabasePool,
) -> None:
    repo = HostsRepository(db_with_migrations)
    host = await repo.create(name="web-1", address="web-1.example.com:22", transport="ssh")
    down = await repo.record_probe(host.id, reachable=False, last_seen_at=None)
    assert down is not None and (down.status, down.last_seen_at) == ("unreachable", None)
    up = await repo.record_probe(host.id, reachable=True, last_seen_at=SEEN)
    assert up is not None and (up.status, up.last_seen_at) == ("active", SEEN)
    down_again = await repo.record_probe(host.id, reachable=False, last_seen_at=None)
    assert down_again is not None and down_again.last_seen_at == SEEN, "a failed probe erased the last sighting"
    await repo.remove(host.id)
    assert await repo.record_probe(host.id, reachable=True, last_seen_at=SEEN) is None
    still = await repo.get(host.id)
    assert still is not None and still.status == "removed"


async def test_repo_links_replace_lowercase_and_show_on_both_sides(db_with_migrations: DatabasePool) -> None:
    repo = HostsRepository(db_with_migrations)
    web = await repo.create(name="web-1", address="", transport="tailnet")
    db = await repo.create(name="db-1", address="", transport="tailnet")
    links = await repo.replace_repos(web.id, [("Acme/Widgets", "app"), ("acme/platform/api", "app")])
    assert links == [{"repo": "acme/platform/api", "role": "app"}, {"repo": "acme/widgets", "role": "app"}]
    await repo.replace_repos(db.id, [("acme/widgets", "db")])
    serving = await repo.hosts_for_repo("ACME/widgets")
    assert [(h["name"], h["role"]) for h in serving] == [("db-1", "db"), ("web-1", "app")]
    assert "repos" not in serving[0]
    assert await repo.replace_repos(web.id, []) == []
    assert [h["name"] for h in await repo.hosts_for_repo("acme/widgets")] == ["db-1"]
    listed = {h.name: h.repos for h in await repo.list_all()}
    assert listed == {"db-1": [{"repo": "acme/widgets", "role": "db"}], "web-1": []}


async def test_the_schema_refuses_what_the_vocabulary_does_not_name(db_with_migrations: DatabasePool) -> None:
    async def insert(transport: str, log_source: object = None, status: str = "active") -> None:
        await db_with_migrations.execute(
            "INSERT INTO hosts (id, name, transport, log_source, status) VALUES ($1, $2, $3, $4::jsonb, $5)",
            new_host_id(),
            new_host_id(),
            transport,
            log_source,
            status,
        )

    await insert("ssh", {"kind": "loki", "selector": '{app="widgets"}'})
    for bad in (
        lambda: insert("telnet"),
        lambda: insert("ssh", {"kind": "syslog", "selector": "x"}),
        lambda: insert("ssh", ["journald"]),
        lambda: insert("ssh", status="gone"),
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            await bad()


async def test_the_import_against_postgres_is_idempotent(db_with_migrations: DatabasePool) -> None:
    repo = HostsRepository(db_with_migrations)
    now = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    raw = [
        {
            "name": "web-1.example.ts.net",
            "addresses": ["100.64.0.10"],
            "tags": ["tag:server"],
            "lastSeen": "2026-10-16T11:59:00Z",
            "connectedToControl": True,
        },
        {"name": "db-1.example.ts.net", "addresses": ["100.64.0.11"], "connectedToControl": False},
    ]
    devices = [d for d in (device_from_api(x, now=now) for x in raw) if d]
    first = await import_tailnet(repo, devices)
    assert sorted(r["outcome"] for r in first) == ["created", "created"]
    web = await repo.get_by_name("web-1")
    assert web is not None
    assert (web.source, web.transport, web.address, web.status, web.tags, web.last_seen_at) == (
        "tailnet",
        "tailnet",
        "100.64.0.10",
        "active",
        ["tag:server"],
        SEEN,
    )
    second = await import_tailnet(repo, devices)
    assert [r["outcome"] for r in second] == ["unchanged", "unchanged"]
    assert len(await repo.list_all()) == 2
