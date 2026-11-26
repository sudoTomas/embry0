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
