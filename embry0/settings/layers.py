"""Resolving a section through its layers (spec 2026-10-29): pure functions, no database.

A key takes the value of the most specific layer that sets it: the
deployment's document, then an organization's layer, then a repository's.
A layer may set only the keys its section declares for its level
(``Section.settable``), and replaces the inherited value of such a key
whole, except a key of ``Section.merged_keys``, a mapping it merges entry by
entry over the inherited one (:func:`merged`), as the deployment's document
merges it over the section's default (:func:`over_default`). Every other key
it holds is ignored.

A section can check its fields against each other, so a value accepted when
it was written can be refused later, together with what it inherits, once
the layer below changes. The values that combine are kept; the keys a
refusal names are dropped from the layer that set them, which then reads the
inherited value instead. A read never raises because of it.

A loop or a request that works per repository takes one section of one
snapshot as a :class:`PerRepository`, so the service never imports the store.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from embry0.settings.registry import Section, SectionModel, SectionRefused


@runtime_checkable
class PerRepository[M: SectionModel](Protocol):
    """One section of one settings snapshot, read per repository.

    ``deployment()`` is the deployment's values: what a loop decides once for
    every repository. ``repo(repo)`` is what one repository reads: the
    deployment's values, then those of its organization's layer and its own
    where the section lets them set a key; ``None`` reads the deployment's.
    Each answer is worked out in memory. ``scoped_reader`` hands one out per
    call (:class:`embry0.settings.store.Scoped`), so a loop built with it
    takes this type and never imports the store.
    """

    def deployment(self) -> M: ...

    def repo(self, repo: str | None) -> M: ...


@runtime_checkable
class SnapshotSection[M: SectionModel](PerRepository[M], Protocol):
    """A :class:`PerRepository` that also names the snapshot it was read from.

    What a loop takes when something else it reads in the same pass must come
    from the same snapshot: the leads loop reads its repositories' enrollment
    (``background.leads``) from the snapshot its ``leads`` section came from.
    :class:`embry0.settings.store.Scoped` is one.
    """

    @property
    def snapshot(self) -> Any: ...


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


def merged(section: Section[Any], inherited: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """A document's ``values`` as they apply over ``inherited``: each key of ``section.merged_keys`` it sets, a
    mapping, holds the section's default entries, then the inherited ones, under its own; every other key as the
    document wrote it."""
    out = dict(values)
    defaults: dict[str, Any] | None = None
    for key in section.merged_keys & set(values):
        own = values[key]
        if not isinstance(own, Mapping):
            continue
        defaults = defaults if defaults is not None else section.defaults().model_dump(mode="json")
        below: dict[str, Any] = {}
        for layer in (defaults.get(key), inherited.get(key)):
            if isinstance(layer, Mapping):
                below.update(layer)
        out[key] = {**below, **own}
    return out


def over_default(section: Section[Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """The deployment's stored ``values`` as its readers read them: each merged key over the section's default
    entries, so writing one entry never drops the others' defaults."""
    return merged(section, {}, values)


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
        accepted = merged(section, current, values)
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
