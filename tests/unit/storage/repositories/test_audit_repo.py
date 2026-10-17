"""AuditRepository unit tests (RAV-1056 Phase C) — mocked DB, bound-args
assertions (the house pattern for storage code whose requires_postgres
round-trips skip in the local gate)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.storage.repositories.audit import AuditRepository, _where

SINCE = datetime(2026, 1, 1, tzinfo=UTC)
UNTIL = datetime(2026, 2, 1, tzinfo=UTC)


def test_where_empty():
    where, args = _where(None, None, None, None)
    assert where == ""
    assert args == []


def test_where_all_filters_number_sequentially():
    where, args = _where(["a@x.com", "key:svc"], "user.login", SINCE, UNTIL)
    assert where == " WHERE actor = ANY($1) AND action = $2 AND created_at >= $3 AND created_at < $4"
    assert args == [["a@x.com", "key:svc"], "user.login", SINCE, UNTIL]


def test_where_partial_filters_renumber():
    where, args = _where(None, "user.login", None, UNTIL)
    assert where == " WHERE action = $1 AND created_at < $2"
    assert args == ["user.login", UNTIL]


@pytest.mark.asyncio
async def test_list_entries_binds_limit_offset_and_orders():
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[])
    repo = AuditRepository(db)
    await repo.list_entries(actors=["a@x.com"], limit=25, offset=50)
    sql = db.fetch.call_args.args[0]
    assert "ORDER BY created_at DESC, id DESC" in sql
    assert "SELECT id, action, actor, details, issue_id, created_at" in sql
    assert "trace_id" not in sql
    assert "LIMIT $2 OFFSET $3" in sql
    assert db.fetch.call_args.args[1:] == (["a@x.com"], 25, 50)


@pytest.mark.asyncio
async def test_list_entries_unfiltered_binds_only_paging():
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[{"id": 1}])
    repo = AuditRepository(db)
    rows = await repo.list_entries()
    sql = db.fetch.call_args.args[0]
    assert "WHERE" not in sql
    assert "LIMIT $1 OFFSET $2" in sql
    assert db.fetch.call_args.args[1:] == (50, 0)
    assert rows == [{"id": 1}]


@pytest.mark.asyncio
async def test_count_entries_shares_where_clauses():
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value={"n": 7})
    repo = AuditRepository(db)
    n = await repo.count_entries(action="apikey.created", since=SINCE)
    sql = db.fetchrow.call_args.args[0]
    assert sql.endswith(" WHERE action = $1 AND created_at >= $2")
    assert db.fetchrow.call_args.args[1:] == ("apikey.created", SINCE)
    assert n == 7


@pytest.mark.asyncio
async def test_delete_older_than_parses_command_tag():
    db = MagicMock()
    db.execute = AsyncMock(return_value="DELETE 7")
    repo = AuditRepository(db)
    assert await repo.delete_older_than(SINCE) == 7
    assert db.execute.call_args.args == ("DELETE FROM audit_log WHERE created_at < $1", SINCE)


@pytest.mark.asyncio
async def test_delete_older_than_malformed_tag_returns_zero():
    db = MagicMock()
    db.execute = AsyncMock(return_value="")
    repo = AuditRepository(db)
    assert await repo.delete_older_than(SINCE) == 0


@pytest.mark.asyncio
async def test_latest_entry_filters_on_one_details_key_newest_first():
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value={"id": 3, "details": {"provider": "xai", "status": "ok"}})
    repo = AuditRepository(db)
    row = await repo.latest_entry("provider_connection_tested", detail_key="provider", detail_value="xai")
    sql = db.fetchrow.call_args.args[0]
    assert "WHERE action = $1 AND details ->> $2::text = $3" in sql
    assert "ORDER BY created_at DESC, id DESC" in sql
    assert "LIMIT 1" in sql
    assert "trace_id" not in sql
    assert db.fetchrow.call_args.args[1:] == ("provider_connection_tested", "provider", "xai")
    assert row == {"id": 3, "details": {"provider": "xai", "status": "ok"}}


@pytest.mark.asyncio
async def test_latest_entry_none_when_no_row_matches():
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value=None)
    assert await AuditRepository(db).latest_entry("a", detail_key="k", detail_value="v") is None
