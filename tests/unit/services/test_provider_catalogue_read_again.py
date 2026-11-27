"""A stored overlay's text is read again by this release's loader when it is installed (#756 review).

The row's values were read when its text was written, by that release's loader: an overlay stored before the
catalogue read ``1e-7`` as a number holds the string, and a save of the same text records nothing. Installing reads
the text again: its values are in force when a string that now reads as the number it spells is all that differs.
The stored ones stand when the text does not read, reads to anything else, or reads to a part this release refuses,
so a deployment never loses its catalogue.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from structlog.testing import capture_logs

from embry0.agents import catalogue
from embry0.services import provider_catalogue_store as store_module
from embry0.services.provider_catalogue_store import MAX_TEXT_BYTES, CatalogueStore, read_again
from embry0.storage.database import DatabasePool

pytestmark = [pytest.mark.requires_postgres]

_MODEL = "providers:\n  custom:\n    models:\n      custom/local:\n        free: true\n"
_TEXT = _MODEL + "        request_extras: {{temperature: {value}}}\n"


def _installed_temperature() -> Any:
    dumped = catalogue.current().document.model_dump(mode="python")
    return dumped["providers"]["custom"]["models"]["custom/local"]["request_extras"]["temperature"]


async def _stored_by_an_older_release(db: DatabasePool, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """``text`` saved as a release before the wider float rule saved it: read by YAML 1.1's rules."""
    with monkeypatch.context() as patch:
        patch.setattr(store_module, "CatalogueLoader", yaml.SafeLoader)
        await CatalogueStore(db).save(text, actor="ops@example.test")


async def _text_changed_by_hand(db: DatabasePool, text: str) -> None:
    """A hand at the database: the text changes, the values stored with it do not."""
    await db.execute(
        "UPDATE provider_catalogue SET yaml_text = $1, generation = generation + 1 WHERE scope = 'deployment'", text
    )


def _events(logs: list[dict[str, Any]]) -> list[str]:
    return [e["event"] for e in logs if e["event"].startswith("provider_catalogue_")]


async def _stored_values_in_force(store: CatalogueStore) -> bool:
    return catalogue.current().document == store_module._built(await store.document())[0].document


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
    assert read_again(await upgraded.document()).kept() is None
    result = await upgraded.save(text, actor="ops@example.test")  # the same text again: nothing to record
    assert not result.changed
    assert _installed_temperature() == 1e-07, "a save of the same text put the string back"


async def test_a_write_is_checked_against_the_catalogue_in_force(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What a write naming a model is checked against (``held_catalogue``, and a catalogue write's ``before``) is
    the stored catalogue as installing reads it, not the values an earlier release stored."""
    db = db_with_migrations
    await _stored_by_an_older_release(db, monkeypatch, _TEXT.format(value="1e-7"))
    await CatalogueStore(db).refresh()
    async with db.transaction() as conn:
        held = await store_module.held_catalogue(conn)
    assert held.document == catalogue.current().document, "a write is checked against another catalogue"


_TWO_MODELS = (
    "providers:\n  custom:\n    models:\n      custom/a:\n        free: true\n      custom/b:\n        free: true\n"
)
_KWARGS = _MODEL + "        request_extras: {{temperature: 1e-7, chat_template_kwargs: {{weights: {weights}}}}}\n"
_FLAG = _MODEL + "        request_extras: {{chat_template_kwargs: {{thinking: {flag}}}}}\n"
_TOKENS = _MODEL + "        request_extras: {{max_tokens: {tokens}}}\n"


@pytest.mark.parametrize(
    ("stored", "text"),
    [
        (_TWO_MODELS, "providers:\n  custom:\n    models:\n      custom/a:\n        free: true\n"),
        (_TEXT.format(value="0.5"), _TEXT.format(value="0.7")),
        (_TEXT.format(value="1e-7"), _TEXT.format(value="2e-7")),
        (_TEXT.format(value="'0.5'"), _TEXT.format(value="0.5")),
        (
            _MODEL + "        request_extras: {temperature: 1e-7, max_tokens: 4096}\n",
            _MODEL + "        request_extras: {temperature: 1e-7, max_tokens: 4096.0}\n",
        ),
        (_KWARGS.format(weights="[1, 2]"), _KWARGS.format(weights="[1]")),
        (_FLAG.format(flag="true"), _FLAG.format(flag="1.0")),
        (_TOKENS.format(tokens="4096"), _TOKENS.format(tokens="'default'")),
        (_TOKENS.format(tokens="1.0e+23"), _TOKENS.format(tokens="2.0e+23")),
    ],
    ids=[
        "a-model-dropped",
        "a-value-changed",
        "another-number",
        "a-number-not-in-exponent-form",
        "a-whole-number-made-a-float",
        "a-list-item-dropped",
        "a-flag-made-a-number",
        "a-number-made-text",
        "another-large-number",
    ],
)
async def test_a_text_that_reads_to_other_values_keeps_the_stored_ones(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, stored: str, text: str
) -> None:
    """Only a string that now reads as the number it spells is a new reading: a text changed beside its values reads
    to something else, and taking it would drop or change what the deployment stored (#757 review)."""
    db = db_with_migrations
    await _stored_by_an_older_release(db, monkeypatch, stored)
    await _text_changed_by_hand(db, text)
    store = CatalogueStore(db)
    with capture_logs() as logs:
        await store.refresh()
    assert await _stored_values_in_force(store), "a text that reads to other values was taken over the stored ones"
    kept = [e for e in logs if e["event"] == "provider_catalogue_text_not_read_again"]
    assert [e.get("reason") for e in kept] == ["differs"], _events(logs)
    assert read_again(await store.document()).kept() == {"reason": "differs", "problems": []}


@pytest.mark.parametrize("typed", ["1e23", "1.0e+23", "12345678901234567890.5", "1e16", "4096"])
async def test_a_number_jsonb_writes_back_as_a_whole_one_reads_as_stored(
    db_with_migrations: DatabasePool, typed: str
) -> None:
    """The values are stored as jsonb, which writes a large float back as an integer (1e23 as
    100000000000000000000000, which the float is not exactly): that is no difference, so nothing is said at each
    start, and the overlay is not left unread (#757 review)."""
    db = db_with_migrations
    await CatalogueStore(db).save(_TOKENS.format(tokens=typed), actor="ops@example.test")
    store = CatalogueStore(db)
    with capture_logs() as logs:
        await store.refresh()
    reading = read_again(await store.document())
    assert not [e for e in logs if "read_again" in e["event"]], f"{typed}: {_events(logs)}"
    assert (reading.how, reading.kept()) == ("same", None), f"{typed}: {reading.how} {reading.why}"
    assert await _stored_values_in_force(store)


async def test_a_number_jsonb_wrote_back_beside_one_in_exponent_form_is_read_again(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1.0e+23 was a float to the earlier release too, stored as a whole number; 1e-7 was a string. The text reads to
    both as floats, and only the string is a new reading."""
    db = db_with_migrations
    text = _MODEL + "        request_extras: {temperature: 1e-7, max_tokens: 1.0e+23}\n"
    await _stored_by_an_older_release(db, monkeypatch, text)
    store = CatalogueStore(db)
    with capture_logs() as logs:
        await store.refresh()
    assert _installed_temperature() == 1e-07, f"kept: {_events(logs)}"
    assert _events(logs) == ["provider_catalogue_read_again"], _events(logs)


async def test_a_text_that_now_reads_to_a_refused_part_keeps_the_stored_values(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1e400 was the string "1e400"; read now it is infinite, which the catalogue refuses: the stored values stand."""
    db = db_with_migrations
    await _stored_by_an_older_release(db, monkeypatch, _TEXT.format(value="1e400"))
    store = CatalogueStore(db)
    with capture_logs() as logs:
        await store.refresh()
    assert _installed_temperature() == "1e400", "a new reading took a part of the catalogue out"
    kept = [e for e in logs if e["event"] == "provider_catalogue_text_not_read_again"]
    assert [e.get("reason") for e in kept] == ["refused"], _events(logs)
    said = read_again(await store.document()).kept()
    assert said is not None and said["reason"] == "refused", said
    # The extra is refused, which leaves the model unpriced: both are parts the new reading refuses.
    expected = [("temperature", "finite_number"), ("custom/local", "withheld")]
    assert [(p["loc"][-1], p["type"]) for p in said["problems"]] == expected, said


async def test_a_part_refused_before_is_no_reason_to_keep_the_stored_values(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider a release took out was refused before the text was read again, and still is: only a part the new
    reading refuses keeps the stored values."""
    db = db_with_migrations
    await _stored_by_an_older_release(db, monkeypatch, _TEXT.format(value="1e-7"))
    stored = await CatalogueStore(db).document()
    retired = {"providers": {**stored.data["providers"], "retired": {"enabled": True}}}
    await db.execute(
        "UPDATE provider_catalogue SET yaml_text = $1, data = $2::jsonb, generation = generation + 1 "
        "WHERE scope = 'deployment'",
        stored.yaml_text + "  retired:\n    enabled: true\n",
        retired,
    )
    store = CatalogueStore(db)
    await store.refresh()
    assert _installed_temperature() == 1e-07, "a part refused before kept the stored string in force"
    assert [p.loc for p in store.refused] == [("providers", "retired")]


@pytest.mark.parametrize(
    ("text", "problem"),
    [("providers: {", "yaml_syntax"), ("#" * (MAX_TEXT_BYTES + 1), "too_large")],
    ids=["unreadable", "too-large"],
)
async def test_a_text_that_does_not_read_keeps_the_stored_values(
    db_with_migrations: DatabasePool, text: str, problem: str
) -> None:
    db = db_with_migrations
    await CatalogueStore(db).save(_TEXT.format(value="0.5"), actor="ops@example.test")
    await _text_changed_by_hand(db, text)
    store = CatalogueStore(db)
    with capture_logs() as logs:
        await store.refresh()
    assert _installed_temperature() == 0.5, "a text that does not read lost the deployment its catalogue"
    kept = [e for e in logs if e["event"] == "provider_catalogue_text_not_read_again"]
    assert [e.get("reason") for e in kept] == ["unreadable"], _events(logs)
    said = read_again(await store.document()).kept()
    assert said is not None and said["reason"] == "unreadable", said
    assert [p["type"] for p in said["problems"]] == [problem], said


async def test_an_empty_text_keeps_the_stored_values_and_says_nothing(db_with_migrations: DatabasePool) -> None:
    """A row written with values and no text (an earlier release's, or a hand's): nothing to read again."""
    db = db_with_migrations
    await CatalogueStore(db).save(_TEXT.format(value="0.5"), actor="ops@example.test")
    await _text_changed_by_hand(db, "")
    store = CatalogueStore(db)
    with capture_logs() as logs:
        await store.refresh()
    assert _installed_temperature() == 0.5, "an empty text lost the deployment its catalogue"
    assert not [e for e in logs if "read_again" in e["event"]], _events(logs)
    assert read_again(await store.document()).kept() is None
