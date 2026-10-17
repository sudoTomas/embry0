"""AuditRepository.latest_entry against Postgres: the newest matching row, per details key."""

import pytest

from embry0.audit.db_logger import emit_audit_event_db
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.audit import AuditRepository

pytestmark = pytest.mark.requires_postgres

ACTION = "provider_connection_tested"


@pytest.mark.asyncio
async def test_latest_entry_returns_the_newest_row_for_the_key(db_with_migrations: DatabasePool):
    for provider, status in (("xai", "unauthorized"), ("anthropic", "ok"), ("xai", "ok")):
        await emit_audit_event_db(db_with_migrations, ACTION, details={"provider": provider, "status": status})
    await emit_audit_event_db(db_with_migrations, "credential.verified", details={"provider": "xai"})
    repo = AuditRepository(db_with_migrations)

    xai = await repo.latest_entry(ACTION, detail_key="provider", detail_value="xai")
    assert xai is not None
    assert xai["details"] == {"provider": "xai", "status": "ok"}
    assert xai["created_at"] is not None

    anthropic = await repo.latest_entry(ACTION, detail_key="provider", detail_value="anthropic")
    assert anthropic is not None
    assert anthropic["details"]["status"] == "ok"

    assert await repo.latest_entry(ACTION, detail_key="provider", detail_value="other") is None
    assert await repo.latest_entry("never.written", detail_key="provider", detail_value="xai") is None
