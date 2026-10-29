"""Sections of the settings document: what a feature declares, checked when it is declared.

A feature describes its policy as a :class:`SectionModel` and wraps it in a
:class:`Section`. The model is the only home of the section's defaults and
bounds: nothing else in the code repeats them. Every field takes a default,
since an operator writes only what differs, and every field has a
description, which the settings reference in ``docs/configuration.md`` is
generated from.

This module imports only pydantic, the standard library and
``embry0.config_redaction`` (a leaf), so a section module that imports it
can never take part in an import cycle.
"""

from __future__ import annotations

import json
import re
import typing
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, ValidationError, create_model, field_validator

from embry0.config_redaction import SECRET_NAME_PATTERN

#: The top-level key that holds the document's schema version, never a section.
VERSION_KEY: Final = "version"
#: The schema version this code reads and writes. Bumped only by a change to
#: a section that is not additive; see the spec, "Schema changes are additive".
SCHEMA_VERSION: Final = 1
#: The top-level key that says where the document is kept, never a section.
MANAGED_BY_KEY: Final = "managed_by"
#: What ``managed_by`` may say: the document is kept in a file, in the
#: operator's own repository, and written with ``embry0 settings apply``; the
#: dashboard only shows it.
MANAGED_BY_FILE: Final = "file"
#: The document's own top-level keys: everything else at the top is a section.
DOCUMENT_KEYS: Final = frozenset({VERSION_KEY, MANAGED_BY_KEY})

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
#: Words YAML 1.1 reads as true, false or null: a key spelled so is not text.
YAML_WORDS: Final = frozenset({"yes", "no", "on", "off", "true", "false", "null", "y", "n"})
#: Section names that are taken: the document's own keys, and the first ``loc``
#: segment a FastAPI error parser drops (``frontend/src/api/errorDetail.ts``).
RESERVED_NAMES: Final = DOCUMENT_KEYS | frozenset({"body", "query", "path", "header", "cookie"})


class SectionError(ValueError):
    """A section declaration that cannot be registered."""


class SectionRefused(ValueError):
    """Values a section refuses. ``errors`` are pydantic-shaped: ``loc`` within
    the section, ``type`` and ``msg`` (and, for pydantic's own, ``input``)."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(f"{'.'.join(map(str, e.get('loc') or ()))}: {e.get('msg')}" for e in errors))
        self.errors = errors


def _failed(loc: tuple[str | int, ...], exc: BaseException) -> dict[str, Any]:
    return {"loc": loc, "type": "check_failed", "msg": f"The check of this value failed ({type(exc).__name__})"}


def validate_values[M: BaseModel](model: type[M], raw: Mapping[str, Any]) -> M:
    """``raw`` as ``model``, validated as stored values are read; :class:`SectionRefused` when it is not one.

    Through JSON, strictly: the same rules for a value parsed from YAML,
    sent as JSON, or read back from the database, where a list stands for a
    tuple and a string for an enum member. Any exception a check raises is a
    refusal, never an escape: a validator that raises ``KeyError`` (as
    ``zoneinfo`` does for an unknown zone) is pinned to the key it checked.
    """
    try:
        text = json.dumps(dict(raw), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise SectionRefused([_failed((), exc)]) from None
    try:
        return model.model_validate_json(text, strict=True)
    except ValidationError as exc:
        raise SectionRefused([dict(error) for error in exc.errors()]) from None
    except Exception as exc:  # noqa: BLE001 - any check that raised is a refusal
        errors: list[dict[str, Any]] = []
        for key, value in raw.items():
            try:
                model.model_validate_json(json.dumps({key: value}, allow_nan=False), strict=True)
            except ValidationError as inner:
                errors.extend(dict(error) for error in inner.errors())
            except Exception as inner:  # noqa: BLE001 - the key whose check raised
                errors.append(_failed((key,), inner))
        raise SectionRefused(errors or [_failed((), exc)]) from None


class SectionModel(BaseModel):
    """The base of every section's model.

    Unknown keys are refused (``extra="forbid"``): a typo must not become a
    setting nothing reads. Strict: ``"5"`` is not a number and ``"true"`` is
    not a boolean, because the stored value is what every loop reads.
    Frozen, so a snapshot handed to a loop cannot be changed under it.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


#: Each (model, field) as a model of that field alone, built once: :func:`field_alone`.
_ALONE: dict[tuple[type[BaseModel], str], type[BaseModel]] = {}


def field_alone(model: type[BaseModel], key: str) -> type[BaseModel]:
    """``model`` with only the field ``key``, its type, bounds and own field validators.

    What a key is checked against when nothing else is known, as offline,
    where a layer's file is checked without the values it inherits: a field
    validator of ``key`` sees no other field's value (``info.data`` is empty,
    so a check that compares it with another key, written to read that key
    with ``info.data.get``, has nothing to compare), and the model's own
    validators, which see every field, are left out.
    """
    found = _ALONE.get((model, key))
    if found is not None:
        return found
    field = model.model_fields[key]
    validators: dict[str, Any] = {}
    for name, decorator in model.__pydantic_decorators__.field_validators.items():
        if key in decorator.info.fields or "*" in decorator.info.fields:
            function = getattr(decorator.func, "__func__", decorator.func)
            validators[name] = field_validator(key, mode=decorator.info.mode)(classmethod(function))
    fields: dict[str, Any] = {key: (field.annotation, field)}
    alone: type[BaseModel] = create_model(
        f"{model.__name__}_{key}", __base__=SectionModel, __validators__=validators, **fields
    )
    _ALONE[(model, key)] = alone
    return alone


@dataclass(frozen=True)
class EnvImport:
    """Where a section field's value came from before the document existed.

    ``attr`` is the ``Embry0Config`` attribute (its variable is the
    attribute's name in capitals, or its alias). ``convert`` turns the
    config value into the field's form, for a field whose shape differs, such
    as a comma-separated string that became a list.
    """

    attr: str
    convert: Callable[[Any], Any] | None = None


@dataclass(frozen=True)
class Section[M: SectionModel]:
    """One feature's part of the settings document.

    ``org_keys`` and ``repo_keys`` name the fields an organization or a
    repository may override in its own layer (spec 2026-10-29): a layer
    replaces the inherited value of such a field whole, and may set nothing
    else.
    ``env`` maps a field to the ``Embry0Config`` attribute its value is
    imported from, once (``embry0.settings.bootstrap``): a string is the
    attribute, an :class:`EnvImport` adds a converter.
    """

    name: str
    model: type[M]
    summary: str
    org_keys: frozenset[str] = frozenset()
    repo_keys: frozenset[str] = frozenset()
    env: Mapping[str, str | EnvImport] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "org_keys", frozenset(self.org_keys))
        object.__setattr__(self, "repo_keys", frozenset(self.repo_keys))
        object.__setattr__(self, "env", dict(self.env))
        problems = _section_problems(self)
        if problems:
            raise SectionError(f"section {self.name!r} cannot be registered: " + "; ".join(problems))

    @property
    def fields(self) -> tuple[str, ...]:
        """The section's keys, in the order the model declares them."""
        return tuple(self.model.model_fields)

    def imports(self) -> dict[str, EnvImport]:
        """``env`` with every entry as an :class:`EnvImport`."""
        return {key: value if isinstance(value, EnvImport) else EnvImport(value) for key, value in self.env.items()}

    def defaults(self) -> M:
        """The section with nothing written: every field at its default."""
        return self.model()

    def validate(self, raw: Mapping[str, Any]) -> M:
        """``raw`` as this section's model; :class:`SectionRefused` naming each refused key."""
        return validate_values(self.model, raw)

    def validate_alone(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """Each key of ``raw`` on its own (:func:`field_alone`), as the values a reader of it would get.

        For a layer's file checked offline, where what it inherits is not
        known: each key meets its type, bounds and own validators, and a
        check that compares it with another key is left to the online check.
        :class:`SectionRefused` naming each refused key.
        """
        errors: list[dict[str, Any]] = []
        out: dict[str, Any] = {}
        for key, value in raw.items():
            try:
                alone = validate_values(field_alone(self.model, key), {key: value})
            except SectionRefused as exc:
                errors.extend(exc.errors)
                continue
            out[key] = alone.model_dump(mode="json")[key]
        if errors:
            raise SectionRefused(errors)
        return out

    def settable(self, layer: str) -> frozenset[str]:
        """The keys a document at ``layer`` may set: every field for the deployment's,
        ``org_keys`` for an organization's, ``repo_keys`` for a repository's."""
        if layer == "organization":
            return self.org_keys
        if layer == "repository":
            return self.repo_keys
        return frozenset(self.fields)


def _nested_models(annotation: Any) -> Iterator[type[BaseModel]]:
    """The models an annotation holds: ``Model``, ``list[Model]``, ``Model | None``, ..."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for arg in typing.get_args(annotation):
        yield from _nested_models(arg)


def _model_problems(model: type[BaseModel], where: str) -> list[str]:
    """What a section's model, or a model nested in it, may not be or hold."""
    if not issubclass(model, SectionModel):
        return [f"{where} must be a SectionModel"]
    problems: list[str] = []
    config = model.model_config
    if config.get("extra") != "forbid" or config.get("strict") is not True:
        problems.append(f"{where} must keep SectionModel's extra='forbid' and strict=True")
    for name, info in model.model_fields.items():
        if SECRET_NAME_PATTERN.search(name):
            problems.append(f"field {name!r} looks like a secret; secrets never go in the settings document")
        if name.lower() in YAML_WORDS:
            problems.append(f"field {name!r} is a word YAML reads as true, false or null, not as a name")
        if any(getattr(meta, "strict", None) is False for meta in info.metadata):
            problems.append(f"field {name!r} turns strict off; a stored value must be exactly its type")
        if info.alias or info.validation_alias or info.serialization_alias:
            problems.append(f"field {name!r} has an alias; a key is written as the field's own name")
    return problems


def _section_problems(section: Section[Any]) -> list[str]:
    problems: list[str] = []
    if not _NAME.match(section.name):
        problems.append("its name must be lowercase letters, digits and '_', starting with a letter")
    if section.name in RESERVED_NAMES or section.name in YAML_WORDS:
        problems.append(f"{section.name!r} is a reserved name, not a section")
    if SECRET_NAME_PATTERN.search(section.name):
        problems.append("its name looks like a secret; secrets never go in the settings document")
    if not section.summary.strip():
        problems.append("it needs a one-line summary for the settings reference")
    model = section.model
    if not (isinstance(model, type) and issubclass(model, SectionModel)):
        problems.append("its model must be a SectionModel")
        return problems
    seen: set[type[BaseModel]] = set()
    queue: list[tuple[type[BaseModel], str]] = [(model, "its model")]
    while queue:
        current, where = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        problems.extend(_model_problems(current, where))
        for name, info in current.model_fields.items():
            queue.extend((nested, f"the model of field {name!r}") for nested in _nested_models(info.annotation))
    names = set(model.model_fields)
    for name, info in model.model_fields.items():
        if info.is_required():
            problems.append(f"field {name!r} needs a default: a key nobody wrote takes its default")
        if not (info.description or "").strip():
            problems.append(f"field {name!r} needs a description: the settings reference is generated from it")
    for label, keys in (("org_keys", section.org_keys), ("repo_keys", section.repo_keys), ("env", section.env)):
        unknown = sorted(set(keys) - names)
        if unknown:
            problems.append(f"{label} names no field of the model: {unknown}")
    if not problems:
        problems.extend(_default_problems(section))
    return problems


def _default_problems(section: Section[Any]) -> list[str]:
    """A default the section's own checks refuse would be the value of every key nobody wrote."""
    try:
        defaults = section.model().model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 - any failure to build the defaults
        return [f"its defaults cannot be built ({type(exc).__name__})"]
    try:
        section.validate(defaults)
    except SectionRefused as exc:
        return [f"its defaults are refused by its own checks: {exc}"]
    return []


class Catalog:
    """The sections of one settings document, in order.

    The application's catalog is :data:`embry0.settings.sections.CATALOG`;
    tests build their own.
    """

    def __init__(self, sections: Iterable[Section[Any]] = ()) -> None:
        self._sections: dict[str, Section[Any]] = {}
        for section in sections:
            if section.name in self._sections:
                raise SectionError(f"section {section.name!r} is registered twice")
            self._sections[section.name] = section

    def __iter__(self) -> Iterator[Section[Any]]:
        return iter(self._sections.values())

    def __len__(self) -> int:
        return len(self._sections)

    def __contains__(self, name: object) -> bool:
        return name in self._sections

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._sections)

    def get(self, name: str) -> Section[Any] | None:
        return self._sections.get(name)

    def json_schema(self) -> dict[str, Any]:
        """The JSON Schema of the whole document.

        Each section is an optional property holding its model; ``version``
        is the optional schema version, and ``managed_by`` the optional word
        that the document is kept in a file. ``x-embry0-sections`` adds what
        JSON Schema has no word for: each section's summary and the keys an
        organization or a repository may override.
        """
        fields: dict[str, Any] = {VERSION_KEY: (int | None, None), MANAGED_BY_KEY: (str | None, None)}
        for section in self:
            fields[section.name] = (section.model | None, None)
        document = create_model(
            "SettingsDocument",
            __config__=ConfigDict(extra="forbid", title="embry0 settings document"),
            **fields,
        )
        schema: dict[str, Any] = document.model_json_schema()
        version = schema.get("properties", {}).get(VERSION_KEY)
        if isinstance(version, dict):
            version.clear()
            version.update(
                {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": SCHEMA_VERSION,
                    "description": "The schema version the document is written for; absent means the current one.",
                }
            )
        managed = schema.get("properties", {}).get(MANAGED_BY_KEY)
        if isinstance(managed, dict):
            managed.clear()
            managed.update(
                {
                    "enum": [MANAGED_BY_FILE, None],
                    "description": (
                        "file: the document is kept in a file and written with `embry0 settings apply`, so the "
                        "dashboard only shows it. Absent: it is edited on the dashboard too."
                    ),
                }
            )
        schema["x-embry0-sections"] = {
            section.name: {
                "summary": section.summary,
                "org_keys": sorted(section.org_keys),
                "repo_keys": sorted(section.repo_keys),
            }
            for section in self
        }
        return schema
