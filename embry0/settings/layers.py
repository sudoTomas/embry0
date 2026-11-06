"""Resolving a section through its layers (spec 2026-10-29): pure functions, no database.

A key takes the value of the most specific layer that sets it: the
deployment's document, then an organization's layer, then a repository's.
A layer may set only the keys its section declares for its level
(``Section.settable``), and replaces the inherited value of such a key
whole, except a key of ``Section.merged_keys``, a mapping it merges entry by
entry over the inherited one (:func:`merged`), as the deployment's document
merges it over the section's default (:func:`over_default`), and a key of
``Section.added_keys``, a list it adds its items to the inherited one with.
Every other key it holds is ignored.

A section can check its fields against each other, so a value accepted when
it was written can be refused later, together with what it inherits, once
the layer below changes. The values that combine are kept; the keys a
refusal names are dropped from the layer that set them, which then reads the
inherited value instead. A read never raises because of it.

A loop or a request that works per repository takes one section of one
snapshot as a :class:`PerRepository`, so the service never imports the store.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import structlog

from embry0.settings.registry import Section, SectionModel, SectionRefused

logger = structlog.get_logger(__name__)


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


def _same(item: Any) -> Any:
    """What two items of an added list are compared by: text without regard to case, as a list of bots is
    matched; anything else as JSON writes it."""
    return item.lower() if isinstance(item, str) else json.dumps(item, sort_keys=True)


def cap_of(section: Section[Any], key: str) -> int | None:
    """The most entries ``key`` of ``section`` holds, as its model's ``max_length`` says; None for no bound."""
    info = section.model.model_fields.get(key)
    for meta in getattr(info, "metadata", ()) or ():
        bound = getattr(meta, "max_length", None)
        if isinstance(bound, int):
            return bound
    return None


def _trimmed(section: Section[Any], key: str, below: int, kept: int) -> None:
    logger.warning(
        "settings_layer_entries_left_out",
        section=section.name,
        key=key,
        inherited=below,
        kept=kept,
        hint="the layer's own entries and the ones it inherits are more than the key holds: the most specific are "
        "kept, the layer's own, then those of the documents below it, nearest first, then the default's",
    )


def _name(entry: Any) -> Any:
    """What two entries of a merged mapping are compared by: their name."""
    return entry


def _entries(value: Any, mapping_key: bool) -> list[Any]:
    """The entries of a merged key's value (a mapping's names) or an added key's (a list's items); none for any
    other value."""
    if mapping_key:
        return list(value) if isinstance(value, Mapping) else []
    return list(value) if isinstance(value, list | tuple) else []


def beyond_default(section: Section[Any], key: str, value: Any) -> Any:
    """The part of the deployment's merged ``value`` of ``key`` that is its own: what it adds to the section's
    default, or changes in it."""
    default = section.defaults().model_dump(mode="json").get(key)
    if isinstance(value, Mapping):
        base = default if isinstance(default, Mapping) else {}
        return {name: entry for name, entry in value.items() if name not in base or base[name] != entry}
    if isinstance(value, list | tuple):
        known = {_same(item) for item in default or ()}
        return [item for item in value if _same(item) not in known]
    return value


def merged(
    section: Section[Any],
    inherited: Mapping[str, Any],
    values: Mapping[str, Any],
    *,
    fit: bool = True,
    tiers: Mapping[str, Sequence[Any]] | None = None,
) -> dict[str, Any]:
    """A document's ``values`` as they apply over ``inherited``.

    Each key of ``section.merged_keys`` it sets, a mapping, holds the section's
    default entries, then the inherited ones, under its own. Each key of
    ``section.added_keys`` it sets, a list, holds the section's default items,
    then the inherited ones, then its own, each once (text without regard to
    case: the first spelling stays). Every other key is as the document wrote
    it.

    ``fit`` is how a read takes a key that, so merged, holds more entries than
    its ``max_length``, as when a document below it grew after the layer was
    written. The entries kept are the most specific first: every entry the
    document names itself, then those of the documents below it, nearest
    first, then the default's, each in its order, until the bound; the rest
    are left out with a warning (``settings_layer_entries_left_out``).
    ``tiers`` names, per key, the values of the documents below, nearest
    first, ending with the default (:func:`resolve` knows them); without it,
    the inherited value counts as one document. A write checks with ``fit``
    false, so the same count is refused there, naming the key.
    """
    out = dict(values)
    defaults: dict[str, Any] | None = None
    for key in (section.merged_keys | section.added_keys) & set(values):
        mapping_key = key in section.merged_keys
        own = values[key]
        if not isinstance(own, Mapping if mapping_key else list | tuple):
            continue
        defaults = defaults if defaults is not None else section.defaults().model_dump(mode="json")
        below = (defaults.get(key), inherited.get(key))
        union: Any
        if mapping_key:
            union = {}
            for layer in below:
                if isinstance(layer, Mapping):
                    union.update(layer)
            union.update(own)
        else:
            union, seen = [], set()
            for layer in (*below, own):
                for item in _entries(layer, False):
                    if _same(item) not in seen:
                        seen.add(_same(item))
                        union.append(item)
        cap = cap_of(section, key)
        if fit and cap is not None and len(union) > cap:
            identity: Callable[[Any], Any] = _name if mapping_key else _same
            order = (own, *(tiers.get(key, ()) if tiers is not None else (inherited.get(key), defaults.get(key))))
            keep: set[Any] = {identity(entry) for entry in _entries(own, mapping_key)}
            for layer in order[1:]:
                for entry in _entries(layer, mapping_key):
                    if len(keep) >= cap:
                        break
                    keep.add(identity(entry))
            _trimmed(section, key, len(union) - len(_entries(own, mapping_key)), len(keep))
            if mapping_key:
                union = {name: entry for name, entry in union.items() if name in keep}
            else:
                union = [item for item in union if _same(item) in keep]
        out[key] = union
    return out


def over_default(section: Section[Any], values: Mapping[str, Any], *, fit: bool = True) -> dict[str, Any]:
    """The deployment's stored ``values`` as its readers read them: each merged key over the section's default
    entries, so writing one entry never drops the others' defaults. ``fit`` as :func:`merged` takes it."""
    return merged(section, {}, values, fit=fit)


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
    # Per key merged or added to, the documents below the next layer, nearest first, then the default: what a read
    # past the key's bound keeps, the most specific first (:func:`merged`).
    spread = (section.merged_keys | section.added_keys) & set(section.model.model_fields)
    defaults = section.defaults().model_dump(mode="json") if spread else {}
    tiers: dict[str, list[Any]] = {
        key: [beyond_default(section, key, base.get(key)), defaults.get(key)] for key in spread
    }
    for scope, values in layers:
        accepted = merged(section, current, values, tiers=tiers)
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
            for key in spread & set(accepted):
                tiers[key] = [values[key], *tiers[key]]
    if not applied and base_model is not None:
        return Resolved(base_model, {}, tuple(refused))
    try:
        model = section.validate(current)
    except SectionRefused:  # pragma: no cover - every step above was validated
        model = base_model if base_model is not None else section.defaults()
        applied = {}
    return Resolved(model, applied, tuple(refused))
