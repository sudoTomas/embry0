"""ASK_USER_ROUNDS_CAP and USER_RETRY_ROUNDS_CAP are imported into the agent_questions section once (#179).

The first start of the release that added the section writes a value an operator set into the settings document,
once; a second start imports nothing, and a value the section cannot take is skipped, never stopping the start.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest

from embry0.config import Embry0Config
from embry0.settings.bootstrap import BOOTSTRAP_ACTOR, import_env_once
from embry0.settings.registry import Catalog
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool

pytestmark = pytest.mark.requires_postgres

DAY = date(2026, 11, 23)


def _section() -> Any:
    try:
        from embry0.settings.sections.agent_questions import AGENT_QUESTIONS
    except ImportError:
        pytest.fail("the settings document has no agent_questions section: the caps are still variables")
    return AGENT_QUESTIONS


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


async def test_a_cap_an_operator_set_is_imported_once(db_with_migrations: DatabasePool) -> None:
    section = _section()
    catalog = Catalog([section])
    store = SettingsStore(db_with_migrations, catalog)
    config = _config(ASK_USER_ROUNDS_CAP="12", USER_RETRY_ROUNDS_CAP="3")

    result = await import_env_once(store, config, today=DAY)

    assert result.imported == {"agent_questions": {"rounds_cap": 12, "retry_rounds_cap": 3}}, result.imported
    row = await store.document()
    assert row["data"] == {"agent_questions": {"rounds_cap": 12, "retry_rounds_cap": 3}}, row["data"]
    (revision,) = await store.revisions()
    assert (revision["actor"], revision["source"]) == (BOOTSTRAP_ACTOR, "bootstrap")
    assert (await store.snapshot()).get(section).rounds_cap == 12

    again = await import_env_once(
        SettingsStore(db_with_migrations, catalog), _config(ASK_USER_ROUNDS_CAP="20"), today=DAY
    )
    assert (again.imported, again.revision_id) == ({}, None), "a second start imported the variable again"
    assert (await store.snapshot()).get(section).rounds_cap == 12


@pytest.mark.parametrize("value", ["ten", "-1", "", "10"])
async def test_a_value_the_section_cannot_take_or_its_default_is_not_imported(
    db_with_migrations: DatabasePool, value: str
) -> None:
    """Not a whole number at or above 0, empty, or equal to the default: the key keeps its default, as the old
    reader fell back to it, and the start goes on."""
    section = _section()
    store = SettingsStore(db_with_migrations, Catalog([section]))
    result = await import_env_once(store, _config(ASK_USER_ROUNDS_CAP=value), today=DAY)
    assert result.imported == {}, f"ASK_USER_ROUNDS_CAP={value!r} was imported: {result.imported}"
    assert (await store.snapshot()).get(section).rounds_cap == 10
