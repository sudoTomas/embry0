"""A stored overlay's text is read again by this release's loader when it is installed (#756 review).

The row's values were read when its text was written, by that release's loader: an overlay stored before the
catalogue read ``1e-7`` as a number holds the string, and a save of the same text records nothing. Installing reads
the text again: its values are in force when they differ, and the stored ones stand when the text does not read or
reads to an overlay this release takes less of, so a deployment never loses its catalogue.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from structlog.testing import capture_logs

from embry0.agents import catalogue
from embry0.services import provider_catalogue_store as store_module
from embry0.services.provider_catalogue_store import CatalogueStore
from embry0.storage.database import DatabasePool

pytestmark = [pytest.mark.requires_postgres]

_TEXT = "providers:\n  custom:\n    models:\n      custom/local:\n        free: true\n        request_extras: {{temperature: {value}}}\n"


def _installed_temperature() -> Any:
    dumped = catalogue.current().document.model_dump(mode="python")
    return dumped["providers"]["custom"]["models"]["custom/local"]["request_extras"]["temperature"]


async def _stored_by_an_older_release(db: DatabasePool, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """``text`` saved as a release before the wider float rule saved it: read by YAML 1.1's rules."""
    monkeypatch.setattr(store_module, "CatalogueLoader", yaml.SafeLoader)
    await CatalogueStore(db).save(text, actor="ops@example.test")
    monkeypatch.undo()


def _events(logs: list[dict[str, Any]]) -> list[str]:
    return [e["event"] for e in logs if e["event"].startswith("provider_catalogue_")]


async def test_an_overlay_stored_before_the_wider_rule_is_in_force_as_its_text_reads_now(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    text = _TEXT.format(value="1e-7")
    await _stored_by_an_older_release(db, monkeypatch, text)
    stored = (await CatalogueStore(db).document()).data
    assert stored["providers"]["custom"]["models"]["custom/local"]["request_extras"]["temperature"] == "1e-7"

    upgraded = CatalogueStore(db)  # a process of this release, at its start
    with capture_logs() as logs:
        await upgraded.refresh()
        await upgraded.refresh()
    assert _installed_temperature() == 1e-07, f"the provider is still sent {_installed_temperature()!r}"
    assert _events(logs) == ["provider_catalogue_read_again"], f"said once a generation: {_events(logs)}"
    result = await upgraded.save(text, actor="ops@example.test")  # the same text again: nothing to record
    assert not result.changed
    assert _installed_temperature() == 1e-07, "a save of the same text put the string back"


async def test_a_text_that_now_reads_to_less_keeps_the_stored_values(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1e400 was the string "1e400"; read now it is infinite, which the catalogue refuses: the stored values stand."""
    db = db_with_migrations
    await _stored_by_an_older_release(db, monkeypatch, _TEXT.format(value="1e400"))
    with capture_logs() as logs:
        await CatalogueStore(db).refresh()
    assert _installed_temperature() == "1e400", "a new reading took a part of the catalogue out"
    assert _events(logs) == ["provider_catalogue_text_not_read_again"], _events(logs)


@pytest.mark.parametrize("text", ["providers: {", ""], ids=["unreadable", "empty"])
async def test_a_text_that_does_not_read_keeps_the_stored_values(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    db = db_with_migrations
    await CatalogueStore(db).save(_TEXT.format(value="0.5"), actor="ops@example.test")
    await db.execute(
        "UPDATE provider_catalogue SET yaml_text = $1, generation = generation + 1 WHERE scope = 'deployment'", text
    )
    await CatalogueStore(db).refresh()
    assert _installed_temperature() == 0.5, "a text that does not read lost the deployment its catalogue"
