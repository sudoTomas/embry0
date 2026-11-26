"""The overlay the dashboard's form writes reads back as the overlay it meant (#173, the Providers page).

The form writes the catalogue overlay as YAML from its values (``frontend/src/lib/catalogueOverlay.ts``). Each case of
``frontend/src/lib/__tests__/fixtures/catalogue_overlay_yaml.json`` is what it writes for an overlay, pinned there by
the frontend's own test (the file is inside the frontend, whose image builds with only that directory); here the
catalogue reads each back to that overlay, and takes it.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from embry0.agents import catalogue
from embry0.services.provider_catalogue_store import load_text

SHARED = Path(__file__).resolve().parents[3] / "frontend/src/lib/__tests__/fixtures/catalogue_overlay_yaml.json"
CASES = json.loads(SHARED.read_text())["cases"]


def _json_form(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_form(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_form(item) for item in value]
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_what_the_form_writes_reads_back_as_the_overlay_it_meant(case: dict[str, Any]) -> None:
    loaded = load_text(case["yaml"]) if case["yaml"] else {}
    assert _json_form(loaded) == case["overlay"], f"the catalogue reads {case['name']!r} as another overlay"
    if case.get("valid", True):
        catalogue.merge(catalogue.seed().document, loaded or None)


@dataclass
class _Typed:
    """The overlay's fields the form must write by name, found in its schema: whole numbers, days, free-form JSON;
    and those whose whole numbers or days are a map's values, which no name can reach."""

    whole: set[str] = field(default_factory=set)
    days: set[str] = field(default_factory=set)
    json: set[str] = field(default_factory=set)
    whole_in_map: set[str] = field(default_factory=set)
    days_in_map: set[str] = field(default_factory=set)


def _walk(node: Any, name: str, defs: dict[str, Any], found: _Typed, *, in_map: bool = False) -> None:
    """Each field under ``node`` (a JSON schema) the form's writer must know by name: one whose value may be an
    integer, a day (``format: date``) or any JSON (untyped, ``JsonValue``). ``in_map``: ``node`` is a map's values,
    which the writer reaches by the map's keys, not by ``name``."""
    if not isinstance(node, dict):
        return
    if node == {} or node.get("$ref", "").endswith("/JsonValue"):
        if name:
            found.json.add(name)
        return
    if "$ref" in node:
        _walk(defs[node["$ref"].rsplit("/", 1)[-1]], name, defs, found, in_map=in_map)
        return
    if node.get("type") == "integer" and name:
        (found.whole_in_map if in_map else found.whole).add(name)
    if node.get("format") == "date" and name:
        (found.days_in_map if in_map else found.days).add(name)
    for alternative in (*node.get("anyOf", ()), *node.get("oneOf", ()), *node.get("allOf", ())):
        _walk(alternative, name, defs, found, in_map=in_map)
    for key, value in node.get("properties", {}).items():
        _walk(value, key, defs, found)
    for value in (*node.get("patternProperties", {}).values(), node.get("additionalProperties")):
        _walk(value, name, defs, found, in_map=True)
    _walk(node.get("items"), name, defs, found, in_map=in_map)


def _typed() -> _Typed:
    schema = catalogue.CatalogueOverlay.model_json_schema()
    found = _Typed()
    _walk(schema, "", schema.get("$defs", {}), found)
    return found


def _listed(key: str) -> set[str]:
    listed = json.loads(SHARED.read_text()).get(key)
    assert listed is not None, f"the shared fixture names no {key} for the form to write by name"
    return set(listed)


_WRITER = "frontend/src/lib/catalogueOverlay.ts"


def test_the_form_knows_every_whole_number_field_of_the_overlay() -> None:
    """The form writes every number of the overlay as a float but those it lists as whole (``WHOLE_NUMBER_KEYS``,
    pinned to the fixture's ``whole_number_keys`` by the frontend's test), and the catalogue refuses a float where
    its schema says integer: ``context_window: 32768.0`` was refused on any save from the form (#174). So a whole-
    number field the schema gains must be on that list before anything can write it."""
    found, listed = _typed(), _listed("whole_number_keys")
    assert not found.whole_in_map, (
        f"{sorted(found.whole_in_map)} map keys to whole numbers: the writer decides by the key a value is written "
        f"under, a map's own key here, so naming the field does not help: change the writer ({_WRITER}) for them"
    )
    assert found.whole == listed, (
        f"the overlay's whole-number fields are {sorted(found.whole)}; the form writes {sorted(listed)} as "
        f"integers: add each to whole_number_keys in the fixture and to WHOLE_NUMBER_KEYS in {_WRITER}"
    )


def test_the_form_knows_every_day_field_of_the_overlay() -> None:
    """A day is written bare, as YAML reads a date, only under the keys the writer names (``DATE_KEYS``); under any
    other it is a quoted string, which the catalogue refuses where its schema says date."""
    found, listed = _typed(), _listed("date_keys")
    assert not found.days_in_map, (
        f"{sorted(found.days_in_map)} map keys to days: the writer decides by the key a value is written under, a "
        f"map's own key here, so naming the field does not help: change the writer ({_WRITER}) for them"
    )
    assert found.days == listed, (
        f"the overlay's day fields are {sorted(found.days)}; the form writes {sorted(listed)} as days: add each to "
        f"date_keys in the fixture and to DATE_KEYS in {_WRITER}"
    )


def test_the_form_knows_every_free_form_json_field_of_the_overlay() -> None:
    """A field of any JSON is written as JSON, its numbers as they are, only under the keys the writer names
    (``EXTRAS_KEYS``); under any other its numbers become floats (``max_tokens: 256.0``)."""
    found, listed = _typed(), _listed("json_keys")
    assert found.json == listed, (
        f"the overlay's free-form JSON fields are {sorted(found.json)}; the form writes {sorted(listed)} as JSON: "
        f"add each to json_keys in the fixture and to EXTRAS_KEYS in {_WRITER}"
    )


def _keys_with_int(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, int) and not isinstance(item, bool):
                out.add(key)
            _keys_with_int(item, out)
    elif isinstance(value, list):
        for item in value:
            _keys_with_int(item, out)


def test_each_whole_number_field_has_a_case_the_form_writes() -> None:
    """A case per whole-number field pins on both sides how the form writes it: as an integer the catalogue takes."""
    listed = set(json.loads(SHARED.read_text()).get("whole_number_keys") or ())
    written: set[str] = set()
    for case in CASES:
        if case.get("valid", True):
            _keys_with_int(case["overlay"], written)
    assert listed <= written, f"no valid case writes {sorted(listed - written)} with a whole number"
