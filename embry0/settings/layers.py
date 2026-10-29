"""Resolving a section through its layers (spec 2026-10-29): pure functions, no database.

A key takes the value of the most specific layer that sets it: the
deployment's document, then an organization's layer, then a repository's.
A layer may set only the keys its section declares for its level
(``Section.settable``), and replaces the inherited value of such a key
whole. Every other key it holds is ignored.

A section can check its fields against each other, so a value accepted when
it was written can be refused later, together with what it inherits, once
the layer below changes. The values that combine are kept; the keys a
refusal names are dropped from the layer that set them, which then reads the
inherited value instead. A read never raises because of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from embry0.settings.registry import Section, SectionModel, SectionRefused


@dataclass(frozen=True)
class LayerRefusal:
    """A layer's value that is read as the inherited one, and why."""

    scope: str
    section: str
    key: str
    reason: str


@dataclass(frozen=True)
class Resolved:
    """A section as one organization or repository reads it."""

    model: SectionModel
    #: Scope string to the values of that layer that apply, most general first.
    applied: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    refused: tuple[LayerRefusal, ...] = ()


def settable_values(section: Section[Any], layer: str, data: Mapping[str, Any] | None) -> dict[str, Any]:
    """The values a layer's stored ``data`` holds for ``section`` that the layer may set."""
    raw = (data or {}).get(section.name)
    if not isinstance(raw, dict):
        return {}
    allowed = section.settable(layer)
    return {key: value for key, value in raw.items() if key in allowed and key in section.model.model_fields}


def _reason(errors: Sequence[Mapping[str, Any]], key: str | None) -> str:
    for error in errors:
        loc = tuple(error.get("loc") or ())
        if key is None or (loc and str(loc[0]) == key):
            return str(error.get("msg") or "refused").removeprefix("Value error, ")
    return str((errors[0] if errors else {}).get("msg") or "refused").removeprefix("Value error, ")


def resolve(
    section: Section[Any],
    base: Mapping[str, Any],
    layers: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    base_model: SectionModel | None = None,
) -> Resolved:
    """``section`` with ``layers`` (scope string, settable values), most general first, over ``base``.

    ``base`` is the inherited values, already accepted (the deployment's
    stored values, without defaults); ``base_model`` is them as the model,
    when the caller has it. Never raises for a stored value: whatever a
    check raises is a refusal.
    """
    current = dict(base)
    applied: dict[str, dict[str, Any]] = {}
    refused: list[LayerRefusal] = []
    for scope, values in layers:
        accepted = dict(values)
        while accepted:
            try:
                section.validate({**current, **accepted})
                break
            except SectionRefused as exc:
                named = {
                    str(error["loc"][0])
                    for error in exc.errors
                    if error.get("loc") and str(error["loc"][0]) in accepted
                }
                blamed = named or set(accepted)
                for key in sorted(blamed):
                    reason = _reason(exc.errors, key if named else None)
                    refused.append(LayerRefusal(scope, section.name, key, reason))
                accepted = {key: value for key, value in accepted.items() if key not in blamed}
        if accepted:
            current = {**current, **accepted}
            applied[scope] = accepted
    if not applied and base_model is not None:
        return Resolved(base_model, {}, tuple(refused))
    try:
        model = section.validate(current)
    except SectionRefused:  # pragma: no cover - every step above was validated
        model = base_model if base_model is not None else section.defaults()
        applied = {}
    return Resolved(model, applied, tuple(refused))
