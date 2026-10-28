"""The settings document as text and values: parse, check, read back, compare.

Pure functions, no database. A write goes ``parse_body`` -> ``check``; a
read goes ``read_sections``. Every problem a write has is collected before
anything is refused, each as a :class:`Problem` whose ``loc`` is the path
from the document's root (``("asks", "remind_hours")``), so a 422 names
every bad key at once. A rejected value is never echoed back: it may be
whatever someone pasted.

Every value is validated through :func:`embry0.settings.registry.validate_values`,
which turns anything a check raises into a refusal: a write gets a
:class:`Problem`, a read a :class:`Refusal` and the default, never an
exception that stops a tick or a boot.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

import structlog
import yaml

from embry0.settings.registry import (
    SCHEMA_VERSION,
    VERSION_KEY,
    Catalog,
    Section,
    SectionModel,
    SectionRefused,
)
from embry0.text_documents import NO_NEWLINE as NO_NEWLINE  # re-exported: the settings tests and API name it here
from embry0.text_documents import DuplicateKey, refuse_duplicate_keys, structure_problems
from embry0.text_documents import text_diff as shared_text_diff

logger = structlog.get_logger(__name__)

#: The largest document a write accepts, in bytes of text.
MAX_TEXT_BYTES: Final = 256 * 1024

Loc = tuple[str | int, ...]


@dataclass(frozen=True)
class Problem:
    """One reason a document cannot be stored."""

    loc: Loc
    type: str
    msg: str

    def to_dict(self) -> dict[str, Any]:
        return {"loc": list(self.loc), "type": self.type, "msg": self.msg}


class DocumentInvalid(ValueError):
    """The document has problems; ``problems`` lists every one."""

    def __init__(self, problems: list[Problem]) -> None:
        super().__init__("; ".join(f"{'.'.join(map(str, p.loc)) or 'document'}: {p.msg}" for p in problems))
        self.problems = problems


class DocumentTooLarge(ValueError):
    """The text is longer than :data:`MAX_TEXT_BYTES`."""


# ---- parsing ----------------------------------------------------------------


def _yaml_problem(exc: yaml.YAMLError) -> Problem:
    mark = getattr(exc, "problem_mark", None)
    where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark is not None else ""
    problem = getattr(exc, "problem", None) or str(exc)
    return Problem((), "yaml_syntax", f"The YAML cannot be read: {where}{problem}")


_TOO_DEEP: Final = Problem((), "too_deep", "The document is nested too deeply.")


def _compose(text: str) -> yaml.Node | None:
    try:
        node: yaml.Node | None = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise DocumentInvalid([_yaml_problem(exc)]) from None
    except RecursionError:
        raise DocumentInvalid([_TOO_DEEP]) from None
    return node


def _structure_problems(root: yaml.Node | None) -> list[Problem]:
    """What a settings document may not use: aliases, merge keys, non-text keys, a key written twice.

    The checks are shared with every document an operator writes
    (:mod:`embry0.text_documents`).
    """
    return [Problem(p.loc, p.type, p.msg) for p in structure_problems(root, what="a settings document")]


def load_yaml(text: str) -> dict[str, Any]:
    """The document ``text`` as a mapping; an empty or comment-only text is ``{}``.

    Raises :class:`DocumentInvalid` for a syntax error, an alias, a merge
    key, a key written twice, a document nested too deeply, or a top level
    that is not a mapping.
    """
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise DocumentTooLarge(f"the settings document is larger than {MAX_TEXT_BYTES} bytes")
    problems = _structure_problems(_compose(text))
    if problems:
        raise DocumentInvalid(problems)
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DocumentInvalid([_yaml_problem(exc)]) from None
    except RecursionError:
        raise DocumentInvalid([_TOO_DEEP]) from None
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise DocumentInvalid([Problem((), "not_a_mapping", "The document must be a mapping of sections.")])
    return loaded


def parse_body(body: bytes, content_type: str) -> tuple[str | None, dict[str, Any]]:
    """A request body as ``(text, values)``.

    YAML (any content type but JSON) keeps its text; a JSON object has no
    text yet (``None``): the store writes the YAML it dumps to.
    """
    if len(body) > MAX_TEXT_BYTES:
        raise DocumentTooLarge(f"the settings document is larger than {MAX_TEXT_BYTES} bytes")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise DocumentInvalid([Problem((), "encoding", "The document must be UTF-8 text.")]) from None
    if content_type.split(";", 1)[0].strip().lower() == "application/json":
        try:
            values = json.loads(text, object_pairs_hook=refuse_duplicate_keys) if text.strip() else {}
        except DuplicateKey as exc:
            raise DocumentInvalid([Problem((), "duplicate_key", str(exc))]) from None
        except json.JSONDecodeError as exc:
            raise DocumentInvalid(
                [Problem((), "json_syntax", f"The JSON cannot be read: line {exc.lineno}, column {exc.colno}")]
            ) from None
        except RecursionError:
            raise DocumentInvalid([_TOO_DEEP]) from None
        if not isinstance(values, dict):
            raise DocumentInvalid([Problem((), "not_a_mapping", "The document must be an object of sections.")])
        return None, values
    if "\x00" in text:
        raise DocumentInvalid([Problem((), "nul_character", "The document cannot hold the NUL character.")])
    return text, load_yaml(text)


# ---- checking a write -----------------------------------------------------------


@dataclass(frozen=True)
class Checked:
    """A document that can be stored: its sparse values and what it carried along."""

    data: dict[str, Any]
    schema_version: int
    #: Sections the running code does not know, kept because they are as stored.
    unknown_sections: tuple[str, ...] = ()
    #: ``section.key`` of keys a known section does not have, kept because they are as stored.
    unknown_keys: tuple[str, ...] = ()


def _close(word: str, options: Iterable[str]) -> str:
    match = difflib.get_close_matches(word, list(options), n=1)
    return f" Did you mean '{match[0]}'?" if match else ""


def _json_problems(value: Any, loc: Loc) -> list[Problem]:
    """Values YAML or JSON can hold and the stored document cannot: dates, binary, sets,
    non-text keys, infinities, and the NUL character, which Postgres refuses."""
    out: list[Problem] = []
    stack: list[tuple[Any, Loc]] = [(value, loc)]
    while stack:
        current, where = stack.pop()
        if isinstance(current, dict):
            for key, inner in current.items():
                if not isinstance(key, str):
                    out.append(Problem((*where, str(key)), "key_type", "A key must be text."))
                elif "\x00" in key:
                    out.append(Problem(where, "nul_character", "A key cannot hold the NUL character."))
                else:
                    stack.append((inner, (*where, key)))
        elif isinstance(current, list):
            stack.extend((inner, (*where, index)) for index, inner in enumerate(current))
        elif isinstance(current, _dt.date | _dt.datetime):
            out.append(
                Problem(where, "date_value", "A date is not a setting value here; put it in quotes if it is text.")
            )
        elif isinstance(current, float) and not math.isfinite(current):
            out.append(Problem(where, "finite_number", "Use a finite number."))
        elif isinstance(current, str) and "\x00" in current:
            out.append(Problem(where, "nul_character", "A value cannot hold the NUL character."))
        elif not (current is None or isinstance(current, str | bool | int | float)):
            out.append(Problem(where, "value_type", "Use text, a number, true or false, a list or a mapping."))
    return sorted(out, key=lambda p: [str(part) for part in p.loc])


#: Error types of a field that wanted text or one of a few words and got something else.
_WANTS_TEXT: Final = frozenset({"literal_error", "enum", "string_type"})


def _message(section: Section[Any], error: Mapping[str, Any]) -> str:
    loc = tuple(error.get("loc") or ())
    if error.get("type") == "extra_forbidden" and loc:
        key = str(loc[-1])
        if len(loc) == 1:
            return f"'{key}' is not a setting of '{section.name}'.{_close(key, section.fields)}"
        return f"'{key}' is not a setting here."
    msg = str(error.get("msg") or "invalid").removeprefix("Value error, ")
    if isinstance(error.get("input"), bool) and error.get("type") in _WANTS_TEXT:
        msg += ". YAML reads a bare on, off, yes or no as true or false; put the word in quotes"
    return msg


def validate_section(section: Section[Any], raw: Mapping[str, Any]) -> SectionModel:
    """``raw`` as the section's model; :class:`SectionRefused` naming each refused key.

    ``Section.validate`` under another name, kept for callers that have the
    section and the values apart.
    """
    model: SectionModel = section.validate(raw)
    return model


def mapping(value: Any) -> dict[str, Any]:
    """``value`` when it is a mapping, else an empty one: a section written as nothing, or as something else."""
    return value if isinstance(value, dict) else {}


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, dict) and not value)


def same(a: Any, b: Any) -> bool:
    """Equal and of the same type, all the way down: ``1`` is not ``true``, nor ``1.0``."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


_ABSENT: Final = object()


def _as_stored(value: Any, *stored: Any) -> bool:
    """``value`` is one of the stored forms: the stored text's, or the stored values' (a newer
    release may have normalised it, and a JSON client sends back what GET's ``data`` holds)."""
    return any(candidate is not _ABSENT and same(value, candidate) for candidate in stored)


def _stored_text_values(stored_text: str) -> dict[str, Any]:
    """The stored text's own values, which a section this code does not know is compared with."""
    try:
        return load_yaml(stored_text)
    except (DocumentInvalid, DocumentTooLarge):
        return {}


def check(catalog: Catalog, raw: Mapping[str, Any], *, stored: Mapping[str, Any], stored_text: str = "") -> Checked:
    """``raw`` as it would be stored, or :class:`DocumentInvalid` listing every problem.

    ``stored`` is the stored document's values and ``stored_text`` its text.
    A section this code does not know, or a key a known section does not
    have (both written by a newer release), passes only when it is exactly
    as stored, type included: as the stored text has it, or as the stored
    values do (what a JSON client sends back from GET's ``data``). A rollback
    so never makes the document unwritable. For a section, nothing written,
    ``null`` and ``{}`` are alike. It keeps the stored value.
    """
    problems: list[Problem] = []
    data: dict[str, Any] = {}
    unknown: list[str] = []
    unknown_keys: list[str] = []
    as_stored = _stored_text_values(stored_text)
    version = raw.get(VERSION_KEY, SCHEMA_VERSION)
    if type(version) is not int:
        problems.append(Problem((VERSION_KEY,), "int_type", "'version' must be a whole number."))
    elif version < 1:
        problems.append(Problem((VERSION_KEY,), "greater_than_equal", "'version' starts at 1."))
    elif version > SCHEMA_VERSION:
        problems.append(
            Problem(
                (VERSION_KEY,),
                "version_newer",
                f"This document is written for schema version {version}; this embry0 reads version "
                f"{SCHEMA_VERSION}. Upgrade embry0, or write version {SCHEMA_VERSION}.",
            )
        )
    for key, value in raw.items():
        if key == VERSION_KEY:
            continue
        if not isinstance(key, str):
            problems.append(Problem((str(key),), "key_type", "A section name must be text."))
            continue
        section = catalog.get(key)
        if section is None:
            before = as_stored.get(key)
            if _as_stored(value, as_stored.get(key, _ABSENT), stored.get(key, _ABSENT)) or (
                _empty(value) and _empty(before) and _empty(stored.get(key))
            ):
                unknown.append(key)
                if key in stored:
                    data[key] = stored[key]
            elif key in as_stored or key in stored:
                problems.append(
                    Problem(
                        (key,),
                        "unknown_section",
                        f"'{key}' is not a section this embry0 knows, so it can only be kept as stored.",
                    )
                )
            else:
                problems.append(
                    Problem(
                        (key,), "unknown_section", f"'{key}' is not a settings section.{_close(key, catalog.names)}"
                    )
                )
            continue
        if value is None:
            continue
        if not isinstance(value, dict):
            problems.append(Problem((key,), "section_type", f"'{key}' must be a mapping of its settings."))
            continue
        not_json = _json_problems(value, (key,))
        if not_json:
            problems.extend(not_json)
            continue
        stored_section = mapping(as_stored.get(key))
        kept_section = mapping(stored.get(key))
        known = {k: v for k, v in value.items() if k in section.model.model_fields}
        kept: dict[str, Any] = {}
        for extra, extra_value in value.items():
            if extra in known:
                continue
            if _as_stored(extra_value, stored_section.get(extra, _ABSENT), kept_section.get(extra, _ABSENT)):
                unknown_keys.append(f"{key}.{extra}")
                if extra in kept_section:
                    kept[extra] = kept_section[extra]
            else:
                problems.append(
                    Problem(
                        (key, extra),
                        "extra_forbidden",
                        f"'{extra}' is not a setting of '{key}'.{_close(extra, section.fields)}",
                    )
                )
        try:
            model = section.validate(known)
        except SectionRefused as exc:
            for error in exc.errors:
                problems.append(
                    Problem((key, *tuple(error.get("loc") or ())), str(error.get("type")), _message(section, error))
                )
            continue
        clean = {**model.model_dump(mode="json", exclude_unset=True), **kept}
        if clean:
            data[key] = clean
    if problems:
        raise DocumentInvalid(problems)
    return Checked(
        data=data,
        schema_version=int(version),
        unknown_sections=tuple(sorted(unknown)),
        unknown_keys=tuple(sorted(unknown_keys)),
    )


# ---- reading stored values ----------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    """A stored value the running code refuses, read as its default instead."""

    section: str
    key: str
    reason: str


@dataclass(frozen=True)
class Sections:
    """Every section of a catalog as its model, from stored values."""

    models: dict[str, SectionModel]
    refused: tuple[Refusal, ...] = ()
    #: Stored sections the catalog does not hold, ignored.
    unknown: tuple[str, ...] = ()
    #: ``section.key`` of stored keys a known section does not have, ignored.
    unknown_keys: tuple[str, ...] = ()


def unknown_parts(catalog: Catalog, data: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The stored sections the catalog does not hold, and the stored keys its sections do not have."""
    sections = tuple(sorted(k for k in data if k not in catalog))
    keys: list[str] = []
    for section in catalog:
        raw = data.get(section.name)
        if isinstance(raw, dict):
            keys.extend(f"{section.name}.{k}" for k in raw if k not in section.model.model_fields)
    return sections, tuple(sorted(keys))


def read_sections(catalog: Catalog, data: Mapping[str, Any]) -> Sections:
    """Each section's model from ``data``, keeping every value the code still accepts.

    A value the code refuses (stored under an older, looser schema) is left
    out, key by key, so its siblings keep theirs; the key reads as its
    default and is listed in ``refused``. A key the section does not have (a
    newer release wrote it) is ignored. Never raises: whatever a check
    raises is a refusal.
    """
    models: dict[str, SectionModel] = {}
    refused: list[Refusal] = []
    for section in catalog:
        raw = data.get(section.name) or {}
        if not isinstance(raw, dict):
            refused.append(Refusal(section.name, "", "the stored section is not a mapping"))
            models[section.name] = section.defaults()
            continue
        known = {k: v for k, v in raw.items() if k in section.model.model_fields}
        try:
            models[section.name] = section.validate(known)
            continue
        except SectionRefused as exc:
            bad: dict[str, str] = {}
            for error in exc.errors:
                loc = tuple(error.get("loc") or ())
                bad.setdefault(str(loc[0]) if loc else "", _message(section, error))
        kept = {k: v for k, v in known.items() if k not in bad}
        refused.extend(Refusal(section.name, key, reason) for key, reason in bad.items())
        try:
            models[section.name] = section.validate(kept) if "" not in bad else section.defaults()
        except SectionRefused:
            refused.extend(Refusal(section.name, key, "refused with another value") for key in kept)
            models[section.name] = section.defaults()
    unknown, unknown_keys = unknown_parts(catalog, data)
    return Sections(models=models, refused=tuple(refused), unknown=unknown, unknown_keys=unknown_keys)


# ---- text -----------------------------------------------------------------------------


def dump_yaml(values: Mapping[str, Any]) -> str:
    """``values`` as block-style YAML, keys in their given order; ``""`` for nothing."""
    if not values:
        return ""
    return yaml.safe_dump(dict(values), sort_keys=False, default_flow_style=False, allow_unicode=True)


def _comment(comment: str, indent: str = "", newline: str = "\n") -> str:
    return "".join(f"{indent}# {line}{newline}" for line in comment.splitlines())


def _lines(text: str, indent: str, newline: str) -> str:
    return "".join(f"{indent}{line}{newline}" for line in text.splitlines())


def _last_scalar_end(node: yaml.Node) -> int:
    """Where a node's text ends: the end of its last scalar, which a block collection's own end
    mark is not (that one sits at the next key, past any comment between)."""
    while isinstance(node, yaml.MappingNode | yaml.SequenceNode) and node.value:
        last = node.value[-1]
        node = last[1] if isinstance(node, yaml.MappingNode) else last
    return int(node.end_mark.index)


def _after_line(text: str, index: int) -> int:
    """The position just past the line break that ends the line holding ``index``."""
    end = text.find("\n", index)
    return len(text) if end < 0 else end + 1


def _insert_point(text: str, after: int, indent: int) -> int:
    """Where keys added to a section go: just past the line its last value ends on.

    A block scalar (``|``, ``>``) ends at the start of a later line, past the
    blank and comment lines that follow it; those belong to whatever comes
    next, so the point moves back above them. A comment indented deeper than
    the section's keys is the scalar's own text and is not passed over.
    """
    if not (after > 0 and text[after - 1] == "\n"):
        return _after_line(text, after)
    cut = after
    while cut > 0:
        start = text.rfind("\n", 0, cut - 1) + 1
        line = text[start:cut]
        stripped = line.strip()
        depth = len(line) - len(line.lstrip(" "))
        if stripped and not (stripped.startswith("#") and depth <= indent):
            break
        cut = start
    return cut


def _is_empty_value(node: yaml.Node) -> bool:
    """A section written as nothing, ``~``, ``null`` or ``{}``."""
    if isinstance(node, yaml.ScalarNode):
        return node.tag == "tag:yaml.org,2002:null"
    return isinstance(node, yaml.MappingNode) and not node.value


def _insert_into_section(text: str, name: str, add: Mapping[str, Any], comment: str) -> str | None:
    """``text`` with ``add`` written at the end of section ``name``'s block, or None when it cannot be.

    Written into: a block-style section with at least one key, and a section
    written as nothing, ``~``, ``null`` or ``{}`` (which becomes a block). A
    flow-style section with keys (``name: {a: 1}``) returns None, and so the
    caller writes the whole document again. The document's own line ending
    is kept. The caller checks the result parses to what it meant.
    """
    try:
        root = _compose(text)
    except DocumentInvalid:
        return None
    if not isinstance(root, yaml.MappingNode):
        return None
    newline = "\r\n" if "\r\n" in text else "\n"
    for key, value in root.value:
        if not (isinstance(key, yaml.ScalarNode) and key.value == name):
            continue
        if isinstance(value, yaml.MappingNode) and not value.flow_style and value.value:
            column = int(value.value[0][0].start_mark.column)
            cut = _insert_point(text, _last_scalar_end(value), column)
        elif _is_empty_value(value):
            column = int(key.start_mark.column) + 2
            # Drop the value's own text (``~``, ``null``, ``{}``), so the keys below make a block.
            start, end = int(value.start_mark.index), int(value.end_mark.index)
            text = text[:start] + text[end:]
            line_end = text.find("\n", start)
            if not text[start : len(text) if line_end < 0 else line_end].strip():
                # Nothing but spaces left after the key: no trailing space either.
                text = text[:start].rstrip(" ") + text[start:].lstrip(" ")
            cut = _after_line(text, int(key.end_mark.index))
        else:
            return None
        head, rest = text[:cut], text[cut:]
        if head and not head.endswith("\n"):
            head += newline
        indent = " " * column
        return head + _comment(comment, indent, newline) + _lines(dump_yaml(dict(add)), indent, newline) + rest
    return None


def with_section(text: str, values: Mapping[str, Any], name: str, add: Mapping[str, Any], comment: str) -> str:
    """``text`` with ``add`` written into section ``name``, comments kept where it can.

    ``values`` is the stored document's values. A section the text does not
    hold is appended under ``comment``; keys for a section it holds are
    written at the end of that section's block (see
    :func:`_insert_into_section`). Either way every comment stays, and the
    document's line ending (``\\n`` or ``\\r\\n``) is kept. Else, as for a
    flow-style section with keys or a text that would not parse back to the
    same values, the whole document is written again from the merged values,
    which loses its comments: the first line says so, and a warning is logged.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    try:
        parsed: dict[str, Any] | None = load_yaml(text)
    except (DocumentInvalid, DocumentTooLarge):
        parsed = None
    if parsed is not None:
        before = mapping(parsed.get(name))
        expected = {**parsed, name: {**before, **add}}
        if name not in parsed:
            lead = text.rstrip("\r\n") + newline + newline if text.strip() else ""
            candidate: str | None = (
                lead + _comment(comment, "", newline) + _lines(dump_yaml({name: dict(add)}), "", newline)
            )
        else:
            candidate = _insert_into_section(text, name, add, comment)
        if candidate is not None:
            try:
                if load_yaml(candidate) == expected:
                    return candidate
            except (DocumentInvalid, DocumentTooLarge):
                pass
    if text.strip():
        logger.warning(
            "settings_text_rewritten",
            section=name,
            hint="the document was written again from its values, and its comments were lost",
        )
    merged = {**values, name: {**(values.get(name) or {}), **add}}
    header = "# Written again by embry0, which could not keep this document's comments."
    return _lines(header, "", newline) + _comment(comment, "", newline) + _lines(dump_yaml(merged), "", newline)


def text_diff(before: str, after: str) -> str:
    """A unified diff of two texts, ``stored`` to ``proposed``, one line per line, as git writes it.

    Shared with every document an operator writes (:func:`embry0.text_documents.text_diff`).
    """
    return shared_text_diff(before, after, before_name="stored", after_name="proposed")


_MISSING: Final = object()


@dataclass(frozen=True)
class Change:
    """One key whose stored value differs between two documents."""

    section: str
    key: str
    before: Any = None
    after: Any = None
    set_before: bool = False
    set_after: bool = False

    @property
    def path(self) -> str:
        return f"{self.section}.{self.key}"


def changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[Change]:
    """Every stored key that differs between two documents' values, in section then key order.

    A key written in one and absent in the other counts, even when its
    value equals the default: writing a default down is a change.
    """
    out: list[Change] = []
    for name in dict.fromkeys([*before, *after]):
        old = before.get(name)
        new = after.get(name)
        old_map = old if isinstance(old, dict) else {}
        new_map = new if isinstance(new, dict) else {}
        for key in dict.fromkeys([*old_map, *new_map]):
            a = old_map.get(key, _MISSING)
            b = new_map.get(key, _MISSING)
            if a is _MISSING and b is _MISSING:
                continue
            if a is not _MISSING and b is not _MISSING and a == b:
                continue
            out.append(
                Change(
                    section=name,
                    key=key,
                    before=None if a is _MISSING else a,
                    after=None if b is _MISSING else b,
                    set_before=a is not _MISSING,
                    set_after=b is not _MISSING,
                )
            )
    return out
