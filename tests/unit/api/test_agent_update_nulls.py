"""``PUT /agents/{type}`` takes a null only where it means something (#417).

An explicit ``null`` clears ``execution_mode`` and ``auth_mode`` (the agent
inherits its mode again; the form's "inherit", the MCP tool's
``clear_execution_mode`` and ``clear_auth_mode``) and ``quest_template`` (its
task leaves the guild). Anywhere else it has no meaning: ``description``,
``model`` and ``mcp_servers`` are NOT NULL, so the write raised and answered
500; ``tools``, ``skills`` and ``system_prompt`` stored SQL NULL, and a NULL
``tools`` reads as the type's default tools. Each is now a 422 naming the
field, and the row is left as it was. Run on the real app over a real
database (:mod:`tests.fixtures.repository_writes`).
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import MASTER, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

AGENT = "developer"
URL = f"/api/v1/agents/{AGENT}"
#: Every field PUT takes that has no meaning as null, as the update request names it.
NOT_NULLABLE = (
    "description",
    "model",
    "tools",
    "skills",
    "system_prompt",
    "mcp_servers",
    "display_name",
    "avatar",
    "color",
    "tagline",
    "triage_may_add_skills",
    "station",
)
COLUMNS = (*NOT_NULLABLE, "execution_mode", "auth_mode", "quest_template")


async def _row(db: DatabasePool) -> dict[str, Any]:
    row = await db.fetchrow(f"SELECT {', '.join(COLUMNS)} FROM agent_definitions WHERE type = $1", AGENT)
    assert row is not None, f"no {AGENT} row"
    return dict(row)


async def test_a_null_where_it_means_nothing_is_a_422_naming_the_field(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _values):
        before = await _row(db_with_migrations)
        answers = {}
        for field in NOT_NULLABLE:
            resp = await call(app, "PUT", URL, MASTER, {field: None})
            answers[field] = (resp.status_code, resp.json().get("detail") if resp.status_code != 500 else resp.text)
        # The request model lists tools before skills: the answer names them in order, not as the model has them.
        both = await call(app, "PUT", URL, MASTER, {"tools": None, "skills": None})
        after = await _row(db_with_migrations)
    assert answers == {field: (422, f"{field} cannot be null") for field in NOT_NULLABLE}, answers
    assert (both.status_code, both.json()["detail"]) == (422, "skills, tools cannot be null"), both.text
    assert after == before, "a refused null changed the row"


async def test_a_null_still_clears_the_modes_and_the_task(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _values):
        set_ = await call(app, "PUT", URL, MASTER, {"execution_mode": "cli", "auth_mode": "api_key"})
        assert set_.status_code == 200, set_.text
        cleared = await call(
            app, "PUT", URL, MASTER, {"execution_mode": None, "auth_mode": None, "quest_template": None}
        )
        after = await _row(db_with_migrations)
    assert cleared.status_code == 200, cleared.text
    assert (after["execution_mode"], after["auth_mode"], after["quest_template"]) == (None, None, None), after
