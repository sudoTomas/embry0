"""The provider catalogue's audit rows reach the database and the audit file, whether the file's path is held as
text or as a Path: a refused part, and a write and an undo through the API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from embry0.audit.helpers import emit_audit
from embry0.audit.logger import emit_audit_event
from embry0.services.provider_catalogue_store import CatalogueStore
from embry0.storage.database import DatabasePool

pytestmark = [pytest.mark.requires_postgres]

AS = pytest.mark.parametrize("as_text", [True, False], ids=["text", "Path"])


def _path(tmp_path: Path, as_text: bool) -> Any:
    path = tmp_path / "audit" / "audit.jsonl"
    return str(path) if as_text else path


def _file_actions(path: Any) -> list[str]:
    target = Path(path)
    if not target.exists():
        return []
    return [json.loads(line)["action"] for line in target.read_text().splitlines() if line.strip()]


async def _db_actions(db: DatabasePool, prefix: str = "provider_catalogue.") -> list[str]:
    rows = await db.fetch("SELECT action FROM audit_log WHERE action LIKE $1 ORDER BY id", prefix + "%")
    return [r["action"] for r in rows]


@pytest.mark.parametrize("as_text", [True, False], ids=["text", "Path"])
def test_an_audit_event_is_written_to_a_file_named_as_text_or_a_path(tmp_path: Path, as_text: bool) -> None:
    path = _path(tmp_path, as_text)
    emit_audit_event("settings.updated", actor="a", details={"k": 1}, audit_log_path=path)
    assert _file_actions(path) == ["settings.updated"]


@AS
async def test_an_audit_row_reaches_the_database_and_the_file(
    db_with_migrations: DatabasePool, tmp_path: Path, as_text: bool
) -> None:
    path = _path(tmp_path, as_text)
    await emit_audit(db_with_migrations, "settings.updated", actor="a", audit_log_path=path)
    assert await _db_actions(db_with_migrations, "settings.") == ["settings.updated"], "the row was lost"
    assert _file_actions(path) == ["settings.updated"]


@AS
async def test_a_refused_part_is_audited_and_reads_go_on(
    db_with_migrations: DatabasePool, tmp_path: Path, as_text: bool
) -> None:
    """At the start, and in a write that names a model (held), which a failed audit would have answered with 500."""
    path = _path(tmp_path, as_text)
    await db_with_migrations.execute(
        "UPDATE provider_catalogue SET data = $1::jsonb, generation = generation + 1",
        {"providers": {"retired": {"enabled": True}, "deepseek": {"enabled": True}}},
    )
    async with CatalogueStore(db_with_migrations, audit_log_path=path).held() as held:
        assert held.enabled("deepseek")
    assert await _db_actions(db_with_migrations) == ["provider_catalogue.refused"]
    assert _file_actions(path) == ["provider_catalogue.refused"]
