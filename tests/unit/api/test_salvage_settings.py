"""The salvage commit's subject template through the settings API (#182), on a real database.

A template that names a value the subject does not have, or holds a brace the
grammar does not take, is a 422 naming ``salvage.commit_subject``, never
repeating the text, and nothing is stored.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.unit.api.test_asks_settings import URL, _put, app, client  # noqa: F401 - app and client are fixtures

pytestmark = pytest.mark.requires_postgres

GOOD = "chore(wip): unfinished — {agent} stopped ({reason})"


async def test_an_unknown_placeholder_or_a_stray_brace_is_a_422_naming_the_key(
    app: Any,  # noqa: F811 - the imported fixture
    client: AsyncClient,  # noqa: F811 - the imported fixture
) -> None:
    good = await _put(client, f"salvage:\n  commit_subject: '{GOOD}'\n")
    assert good.status_code == 200, f"the settings document takes a salvage.commit_subject template: {good.text}"
    for template in ("wip: {job} stopped", "wip: {agent stopped", "wip: agent} stopped", "wip: {agent!r}"):
        resp = await _put(client, f"salvage:\n  commit_subject: '{template}'\n")
        assert resp.status_code == 422, f"{template!r} must be refused: {resp.text}"
        assert [p["loc"] for p in resp.json()["detail"]] == [["salvage", "commit_subject"]], (
            f"{template!r} is refused naming salvage.commit_subject: {resp.text}"
        )
        assert template not in resp.text, f"the 422 does not repeat {template!r}"
    stored = (await client.get(URL)).json()
    assert stored["data"] == {"salvage": {"commit_subject": GOOD}}, "a refused document stores nothing"
