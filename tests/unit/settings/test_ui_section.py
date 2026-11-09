"""The ``ui`` section of the settings document (#177): the game layer and the page the dashboard opens on.

Nothing written keeps the dashboard as it was before the section, the game
on and the guild its home. An organization's layer may set both keys, read
by the keys whose allowlist covers its repositories (ADR 0029); a
repository's may set neither. It is read through the catalog, so each test
fails with its own message on a tree that has no ui section.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.settings.document import DocumentInvalid, check, load_yaml, read_sections
from embry0.settings.sections import CATALOG


def _stored(document: dict[str, Any], layer: str = "deployment") -> Any:
    """What a write of ``document`` stores, or each of its problems as ``(loc, type)``."""
    try:
        return check(CATALOG, document, stored={}, layer=layer, inherited={}).data
    except DocumentInvalid as exc:
        return [(problem.loc, problem.type) for problem in exc.problems]


def _read(data: dict[str, Any]) -> dict[str, Any] | None:
    """The ui section as the dashboard's read answers it from stored ``data``; None with no such section."""
    model = read_sections(CATALOG, data).models.get("ui")
    return None if model is None else model.model_dump(mode="json")


def test_nothing_written_keeps_the_game_on_with_the_guild_as_home() -> None:
    assert _read({}) == {"game": True, "home": "guild"}, (
        "an existing deployment that writes nothing keeps the game layer and opens on the guild"
    )


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("ui:\n  game: false\n", {"game": False}),
        ("ui:\n  game: off\n", {"game": False}),
        ("ui:\n  game: on\n  home: overview\n", {"game": True, "home": "overview"}),
    ],
    ids=["false", "off", "on-overview"],
)
def test_the_section_reads_as_an_operator_writes_it(text: str, values: dict[str, Any]) -> None:
    data = _stored(load_yaml(text))
    assert data == {"ui": values}, f"{text!r} is stored as {values}: {data}"
    assert _read(data) == {"game": True, "home": "guild", **values}, "and read back with the defaults for the rest"


@pytest.mark.parametrize(
    ("key", "value"),
    [("game", "false"), ("game", 0), ("game", None), ("home", "map"), ("home", "Guild"), ("home", False)],
)
def test_a_value_it_cannot_read_is_refused_naming_the_key(key: str, value: Any) -> None:
    found = _stored({"ui": {key: value}})
    assert isinstance(found, list) and [loc for loc, _ in found] == [("ui", key)], (
        f"ui.{key}: {value!r} must be refused as ui.{key}, never read as a guess: {found}"
    )


def test_an_unknown_key_is_refused() -> None:
    found = _stored({"ui": {"theme": "dark"}})
    assert isinstance(found, list) and [loc for loc, _ in found] == [("ui", "theme")], found


def test_an_organization_may_set_the_game_and_the_home() -> None:
    """Read by the keys whose allowlist covers its repositories (ADR 0029)."""
    section = CATALOG.get("ui")
    assert section is not None, "ui is a section of the settings document"
    found = _stored({"ui": {"game": False, "home": "overview"}}, layer="organization")
    assert found == {"ui": {"game": False, "home": "overview"}}, (
        f"an organization's layer sets ui.game and ui.home for the people who see only its work: {found}"
    )
    assert section.org_keys == frozenset({"game", "home"}), sorted(section.org_keys)


@pytest.mark.parametrize("key", ["game", "home"])
def test_a_repository_may_set_neither(key: str) -> None:
    """No caller is one repository's, so a repository's layer would be read by no one."""
    value: Any = {"game": False, "home": "overview"}[key]
    found = _stored({"ui": {key: value}}, layer="repository")
    assert found == [(("ui", key), "not_overridable")], f"a repository's layer may not set ui.{key}: {found}"


def test_no_variable_is_imported_into_it() -> None:
    section = CATALOG.get("ui")
    assert section is not None and section.imports() == {}, "no environment variable ever configured the game layer"
