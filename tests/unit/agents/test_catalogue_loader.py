"""The provider catalogue's YAML loader reads a number in exponent form as a number (#756).

YAML 1.1, which PyYAML follows, reads ``1e-7`` (an exponent, no decimal point) as a string, so a hand-typed
``temperature: 1e-7`` reached the provider as ``"1e-7"``; YAML 1.2 and JSON read it as a float. The catalogue's loader
reads it so, for the shipped catalogue and every stored overlay, and nothing else changes: no field of the catalogue
takes a string the wider rule would read as a number.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from embry0.agents import catalogue
from embry0.services.provider_catalogue_store import load_text

#: The YAML 1.2 forms YAML 1.1 reads as text: an exponent with no decimal point, or one without a sign.
EXPONENT_FORMS = ["1e-7", "5E-1", "1e+21", "1e21", "-2e-3", "1.0e5", ".5e3"]


def _read(text: str) -> Any:
    return load_text(f"x: {text}")["x"]


@pytest.mark.parametrize("text", EXPONENT_FORMS)
def test_a_number_in_exponent_form_is_a_number(text: str) -> None:
    value = _read(text)
    assert type(value) is float and value == float(text), f"{text} reads as {value!r}"


@pytest.mark.parametrize(
    ("text", "read"),
    [
        ("0.0000001", 1e-07),
        ("1.0e-7", 1e-07),
        ("1000", 1000),
        ("2026-12-01", datetime.date(2026, 12, 1)),
        ('"1e-7"', "1e-7"),
        ("e5", "e5"),
        ("1e", "1e"),
        ("1e5x", "1e5x"),
        ("gpt-1e5", "gpt-1e5"),
    ],
)
def test_everything_else_reads_as_it_did(text: str, read: Any) -> None:
    value = _read(text)
    assert type(value) is type(read) and value == read, f"{text} reads as {value!r}, not {read!r}"


def test_the_safe_loader_every_other_reader_uses_is_left_as_it_was() -> None:
    assert yaml.safe_load("x: 1e-7")["x"] == "1e-7", "the wider rule leaked into yaml.SafeLoader"


def test_the_shipped_catalogue_is_read_with_the_same_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = catalogue.SEED_FILE.read_text(encoding="utf-8")
    assert "claude-opus-5: {prices: [{in: 5.0, out: 25.0}]}" in text, "the probe line moved in providers.yaml"
    seeded = tmp_path / "providers.yaml"
    seeded.write_text(text.replace("{in: 5.0, out: 25.0}", "{in: 5e0, out: 2.5e1}"), encoding="utf-8")
    monkeypatch.setattr(catalogue, "SEED_FILE", seeded)
    built = catalogue.seed.__wrapped__()
    assert built.rates("claude-opus-5") == (5.0, 25.0), "a price in exponent form in the shipped file"


def _matches_a_number(pattern: str | None) -> bool:
    """Whether a string field's pattern lets in a scalar the wider rule reads as a number."""
    return pattern is None or any(re.fullmatch(pattern, text) for text in EXPONENT_FORMS)


def test_no_key_or_string_of_the_catalogue_can_be_read_as_a_number() -> None:
    """A scalar the wider rule now reads as a number starts with a digit, a sign or a point. A provider's name starts
    with a letter, a model id with its provider's prefix (each a letter first), an extras key is one of the names
    REQUEST_EXTRAS allows, and the overlay's string values are an enum and a day: none can be such a scalar, so no
    catalogue that read before reads otherwise now but for numbers."""
    schema = catalogue.CatalogueOverlay.model_json_schema()
    defs = schema["$defs"]
    providers = schema["properties"]["providers"]["patternProperties"]
    assert not any(_matches_a_number(pattern) for pattern in providers), (
        f"a provider's name could be a number: {providers}"
    )
    prefixes = [p for entry in catalogue.seed().document.providers.values() for p in entry.model_prefixes]
    assert prefixes and all(p[:1].isalpha() for p in prefixes), f"a model id could start as a number: {prefixes}"
    assert all(key[:1].isalpha() for key in catalogue.REQUEST_EXTRAS), (
        f"an extras key could be a number: {catalogue.REQUEST_EXTRAS}"
    )
    strings = {
        name: field
        for model in ("ModelOverlay", "ProviderOverlay", "FactorsOverlay", "Price")
        for name, field in defs[model]["properties"].items()
        for option in (field.get("anyOf") or [field])
        if option.get("type") == "string"
    }
    assert set(strings) == {"structured_output", "from"}, f"a string field of the overlay to check: {sorted(strings)}"
