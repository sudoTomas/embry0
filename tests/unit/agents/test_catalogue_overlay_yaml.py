"""The overlay the dashboard's form writes reads back as the overlay it meant (#173, the Providers page).

The form writes the catalogue overlay as YAML from its values (``frontend/src/lib/catalogueOverlay.ts``). Each case of
``frontend/src/lib/__tests__/fixtures/catalogue_overlay_yaml.json`` is what it writes for an overlay, pinned there by
the frontend's own test (the file is inside the frontend, whose image builds with only that directory); here the
catalogue reads each back to that overlay, and takes it.
"""

from __future__ import annotations

import datetime
import json
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


def _whole_number_fields(node: Any, name: str, defs: dict[str, Any], found: set[str]) -> None:
    """Every field name under ``node`` (a JSON schema) whose value may be an integer. A request extra's value is any
    JSON, which the schema leaves untyped, and the form writes as JSON."""
    if not isinstance(node, dict):
        return
    if "$ref" in node:
        _whole_number_fields(defs[node["$ref"].rsplit("/", 1)[-1]], name, defs, found)
        return
    if node.get("type") == "integer" and name:
        found.add(name)
    for alternative in (*node.get("anyOf", ()), *node.get("oneOf", ()), *node.get("allOf", ())):
        _whole_number_fields(alternative, name, defs, found)
    for key, value in node.get("properties", {}).items():
        _whole_number_fields(value, key, defs, found)
    for value in (*node.get("patternProperties", {}).values(), node.get("additionalProperties"), node.get("items")):
        _whole_number_fields(value, name, defs, found)


def test_the_form_knows_every_whole_number_field_of_the_overlay() -> None:
    """The form writes every number of the overlay as a float but those it lists as whole (``WHOLE_NUMBER_KEYS``,
    pinned to the fixture's ``whole_number_keys`` by the frontend's test), and the catalogue refuses a float where
    its schema says integer: ``context_window: 32768.0`` was refused on any save from the form (#174). So a whole-
    number field the schema gains must be on that list before anything can write it."""
    listed = json.loads(SHARED.read_text()).get("whole_number_keys")
    assert listed is not None, "the shared fixture names no whole_number_keys for the form to write as integers"
    schema = catalogue.CatalogueOverlay.model_json_schema()
    found: set[str] = set()
    _whole_number_fields(schema, "", schema.get("$defs", {}), found)
    assert found == set(listed), (
        f"the overlay's whole-number fields are {sorted(found)}; the form writes {sorted(listed)} as integers: add "
        "each to whole_number_keys in the fixture and to WHOLE_NUMBER_KEYS in frontend/src/lib/catalogueOverlay.ts"
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
