"""A layer's document is checked against what its level may set and what it inherits (spec 2026-10-29, #161).

Pure: :func:`embry0.settings.document.check` with a layer, and
:func:`embry0.settings.layers.resolve`, with no database.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.settings.document import DocumentInvalid, check, load_yaml
from embry0.settings.layers import resolve, settable_values
from tests.fixtures.settings_sections import LAYERED_CATALOG, SAMPLE, WINDOW


def _problems(
    text: str, *, layer: str, inherited: dict[str, Any] | None = None, stored: dict[str, Any] | None = None
) -> list[Any]:
    with pytest.raises(DocumentInvalid) as caught:
        check(LAYERED_CATALOG, load_yaml(text), stored=stored or {}, layer=layer, inherited=inherited or {})
    return [(list(p.loc), p.type, p.msg) for p in caught.value.problems]


def test_an_organization_sets_only_its_keys_and_each_other_is_named() -> None:
    problems = _problems(
        "sample:\n  enabled: true\n  per_tick: 4\n  mode: 'on'\nother:\n  limit: 1\n", layer="organization"
    )
    assert problems == [
        (
            ["sample", "enabled"],
            "not_overridable",
            "An organization cannot set 'enabled'. In 'sample' it can set per_tick, remind_hours.",
        ),
        (
            ["sample", "mode"],
            "not_overridable",
            "An organization cannot set 'mode'. In 'sample' it can set per_tick, remind_hours.",
        ),
        (["other", "limit"], "not_overridable", "An organization cannot set anything in 'other'."),
    ]


def test_a_repository_sets_only_its_keys() -> None:
    assert _problems("sample:\n  per_tick: 4\n", layer="repository") == [
        (["sample", "per_tick"], "not_overridable", "A repository cannot set 'per_tick'. In 'sample' it can set mode.")
    ]
    checked = check(LAYERED_CATALOG, {"sample": {"mode": "on"}}, stored={}, layer="repository", inherited={})
    assert checked.data == {"sample": {"mode": "on"}}


def test_the_deployment_sets_every_key() -> None:
    checked = check(LAYERED_CATALOG, {"sample": {"enabled": True, "mode": "on"}, "other": {"limit": 1}}, stored={})
    assert checked.data == {"sample": {"enabled": True, "mode": "on"}, "other": {"limit": 1}}


def test_a_layer_stores_only_what_it_wrote_not_what_it_inherits() -> None:
    inherited = {"window": {"high": 80}}
    checked = check(LAYERED_CATALOG, {"window": {"low": 70}}, stored={}, layer="organization", inherited=inherited)
    assert checked.data == {"window": {"low": 70}}


def test_a_value_that_conflicts_with_an_inherited_key_is_refused_at_the_section() -> None:
    """The organization set ``low``; the check names ``high``, which the deployment set."""
    problems = _problems("window:\n  low: 70\n", layer="organization", inherited={"window": {"high": 50}})
    assert problems == [
        (
            ["window"],
            "conflicts_with_inherited",
            "high must not be below low (together with the values this layer inherits)",
        )
    ]


def test_a_value_the_check_names_is_refused_at_its_own_key() -> None:
    problems = _problems("window:\n  high: 40\n", layer="repository", inherited={"window": {"low": 45, "high": 90}})
    assert problems == [(["window", "high"], "value_error", "high must not be below low")]


def test_a_stored_key_the_layer_may_no_longer_set_is_kept_while_unchanged() -> None:
    """A release that stops letting an organization set a key must not make its layer unwritable."""
    stored = {"sample": {"enabled": True, "per_tick": 4}}
    text = "sample:\n  enabled: true\n  per_tick: 5\n"
    checked = check(
        LAYERED_CATALOG, load_yaml(text), stored=stored, stored_text=text, layer="organization", inherited={}
    )
    assert checked.data == {"sample": {"per_tick": 5, "enabled": True}}
    assert checked.unknown_keys == ("sample.enabled",)
    assert _problems("sample:\n  enabled: false\n", layer="organization", stored=stored)[0][1] == "not_overridable"


# ---- resolving -------------------------------------------------------------------------


def test_the_most_specific_layer_that_sets_a_key_wins() -> None:
    result = resolve(
        SAMPLE,
        {"per_tick": 4, "mode": "off"},
        [("organization:acme", {"per_tick": 6}), ("repository:acme/widgets", {"mode": "on"})],
    )
    assert (result.model.per_tick, result.model.mode, result.model.remind_hours) == (6, "on", 24.0)
    assert result.applied == {"organization:acme": {"per_tick": 6}, "repository:acme/widgets": {"mode": "on"}}
    assert result.refused == ()


def test_a_value_that_no_longer_combines_reads_as_inherited() -> None:
    """The organization's ``low`` was fine until the deployment lowered ``high``."""
    result = resolve(WINDOW, {"high": 30}, [("organization:acme", {"low": 40, "high": 60})])
    assert (result.model.low, result.model.high) == (40, 60), "the organization's own high keeps its low valid"
    narrowed = resolve(WINDOW, {"high": 30}, [("organization:acme", {"low": 40})])
    assert (narrowed.model.low, narrowed.model.high) == (10, 30), "the low that no longer combines reads as inherited"
    assert [(r.scope, r.key) for r in narrowed.refused] == [("organization:acme", "low")]
    assert narrowed.refused[0].reason == "high must not be below low"


def test_only_the_keys_a_refusal_names_are_dropped() -> None:
    result = resolve(WINDOW, {"low": 45, "high": 90}, [("repository:acme/widgets", {"high": 40})])
    assert (result.model.low, result.model.high) == (45, 90)
    assert [(r.scope, r.key) for r in result.refused] == [("repository:acme/widgets", "high")]


def test_a_layer_reads_only_the_keys_its_level_may_set() -> None:
    stored = {"sample": {"enabled": True, "per_tick": 4, "nonsense": 1}, "unknown": {"x": 1}}
    assert settable_values(SAMPLE, "organization", stored) == {"per_tick": 4}
    assert settable_values(SAMPLE, "repository", stored) == {}
    assert settable_values(SAMPLE, "organization", {"sample": "not a mapping"}) == {}
