"""A repository's areas as a document embry0 stores (spec 2026-11-10): parse, check, read back, compare.

Pure: no database and no web framework. A write goes :func:`parse_body`
then :func:`check`, which collects every problem before refusing anything,
each a :class:`Problem` whose ``loc`` is the path from the document's root
(``("areas", 0, "paths", 1)``), so a 422 names every bad key at once. A
refused value is never echoed back: it may be whatever someone pasted.

The document::

    version: 1
    areas:
      - name: Billing
        paths: ["services/billing/**", "web/apps/billing/"]
        logs: ["billing-api"]
        owners: ["octo"]

``check`` returns the values every reader reads (``data``): each area with
its name stripped, its globs with a trailing ``/`` written as ``/**``, its
owners without a leading ``@`` and each once. Globs are qa.yaml's
(:func:`embry0.workflows.qa._glob_match.compile_glob`), so a glob means the
same in both documents. Owners are checked as ``repo_preferences.owners``
are, because every consumer matches them against GitHub logins.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import yaml

from embry0.forge_logins import GITHUB_LOGIN_RE
from embry0.services.areas import Area, slug_of
from embry0.text_documents import DuplicateKey, load_json, structure_problems
from embry0.text_documents import text_diff as shared_text_diff
from embry0.workflows.qa._glob_match import GlobPatternError, compile_glob

#: The schema version this code writes and reads.
VERSION: Final = 1
#: The largest document a write accepts, in bytes of text.
MAX_TEXT_BYTES: Final = 256 * 1024
MAX_AREAS: Final = 200
MAX_PATHS: Final = 200
MAX_LOGS: Final = 50
MAX_OWNERS: Final = 20
MAX_NAME: Final = 100
MAX_LOG: Final = 200
MAX_GLOB: Final = 300

_TOP_KEYS: Final = ("version", "areas")
_AREA_KEYS: Final = ("name", "paths", "logs", "owners")

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


TOO_LARGE: Final = f"The areas document is larger than {MAX_TEXT_BYTES} bytes."


def problem_list(exc: DocumentInvalid | DocumentTooLarge) -> list[dict[str, Any]]:
    """Why a document cannot be stored, as every 422 about one says it: a list of ``{loc, type, msg}``."""
    if isinstance(exc, DocumentTooLarge):
        return [{"loc": [], "type": "too_large", "msg": str(exc)}]
    return [p.to_dict() for p in exc.problems]


# ---- parsing ----------------------------------------------------------------


def _yaml_problem(exc: yaml.YAMLError) -> Problem:
    mark = getattr(exc, "problem_mark", None)
    where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark is not None else ""
    problem = getattr(exc, "problem", None) or "it is not YAML"
    return Problem((), "yaml_syntax", f"The YAML cannot be read: {where}{problem}")


_TOO_DEEP: Final = Problem((), "too_deep", "The document is nested too deeply.")


def load_yaml(text: str) -> Any:
    """``text`` as YAML values; an empty or comment-only text is None.

    Raises :class:`DocumentInvalid` for a syntax error, an alias, a merge
    key, a key written twice, a number YAML cannot read, the NUL character
    or a document nested too deeply; :class:`DocumentTooLarge` past
    :data:`MAX_TEXT_BYTES`.
    """
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise DocumentTooLarge(TOO_LARGE)
    if "\x00" in text:
        raise DocumentInvalid([Problem((), "nul_character", "The document cannot hold the NUL character.")])
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise DocumentInvalid([_yaml_problem(exc)]) from None
    except RecursionError:
        raise DocumentInvalid([_TOO_DEEP]) from None
    problems = [Problem(p.loc, p.type, p.msg) for p in structure_problems(root, what="an areas document")]
    if problems:
        raise DocumentInvalid(problems)
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DocumentInvalid([_yaml_problem(exc)]) from None
    except RecursionError:
        raise DocumentInvalid([_TOO_DEEP]) from None


def _json(text: str) -> Any:
    try:
        values, too_long = load_json(text)
    except DuplicateKey as exc:
        raise DocumentInvalid([Problem((), "duplicate_key", str(exc))]) from None
    except json.JSONDecodeError as exc:
        raise DocumentInvalid(
            [Problem((), "json_syntax", f"The JSON cannot be read: line {exc.lineno}, column {exc.colno}")]
        ) from None
    except RecursionError:
        raise DocumentInvalid([_TOO_DEEP]) from None
    if too_long:
        raise DocumentInvalid([Problem(p.loc, p.type, p.msg) for p in too_long])
    return values


def parse_body(body: bytes, content_type: str) -> tuple[str | None, Any]:
    """A request body as ``(text, values)``.

    YAML (any content type but JSON) keeps its text; a JSON body has no text
    (``None``): the store writes the YAML it dumps to. Empty is the empty
    document.
    """
    if len(body) > MAX_TEXT_BYTES:
        raise DocumentTooLarge(TOO_LARGE)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise DocumentInvalid([Problem((), "encoding", "The document must be UTF-8 text.")]) from None
    if content_type.split(";", 1)[0].strip().lower() == "application/json":
        return None, _json(text) if text.strip() else None
    return text, load_yaml(text)


# ---- checking a write ---------------------------------------------------------


def _near(word: str, options: Iterable[str]) -> str:
    close = difflib.get_close_matches(word, list(options), n=1)
    return f" Did you mean '{close[0]}'?" if close else ""


def _unknown(loc: Loc, key: Any, options: Sequence[str], what: str) -> Problem:
    if not isinstance(key, str):
        return Problem(loc, "extra_forbidden", f"A key of {what} must be text.")
    return Problem(
        (*loc, key), "extra_forbidden", f"'{key}' is not a key of {what}: {', '.join(options)}.{_near(key, options)}"
    )


def glob_problem(pattern: str) -> str | None:
    """Why ``pattern`` is not a glob areas take, or None. Never repeats the pattern.

    qa.yaml's grammar (:mod:`embry0.workflows.qa._glob_match`), with the
    trailing ``/`` already expanded by the caller.
    """
    if len(pattern) > MAX_GLOB:
        return f"A glob is at most {MAX_GLOB} characters."
    if pattern.startswith("/"):
        return "A glob starts at the repository's root: no leading '/'."
    segments = pattern.split("/")
    if any(s == "" for s in segments):
        return "A glob has no empty part: no doubled '/'."
    if any(s == ".." for s in segments):
        return "A glob names no '..' directory."
    if any("**" in s and s != "**" for s in segments):
        return "'**' stands alone between slashes, as in services/**/api or web/**."
    if pattern.startswith("!"):
        return "A glob cannot start with '!': negation is not supported, so list only the paths the area has."
    try:
        compile_glob(pattern)
    except GlobPatternError:
        return "A character class ([...]) must close, and cannot hold '/'. A literal '[' is written [[]."
    return None


def expand(pattern: str) -> str:
    """``pattern`` with the one shorthand the document keeps: a trailing ``/`` is everything below it."""
    return pattern + "**" if pattern.endswith("/") else pattern


def _text(value: Any, loc: Loc, problems: list[Problem], *, limit: int, what: str) -> str | None:
    if not isinstance(value, str):
        problems.append(Problem(loc, "string_type", f"{what} must be text."))
        return None
    stripped = value.strip()
    if not stripped:
        problems.append(Problem(loc, "string_too_short", f"{what} cannot be empty."))
        return None
    if len(stripped) > limit:
        problems.append(Problem(loc, "string_too_long", f"{what} is at most {limit} characters."))
        return None
    if "\n" in stripped or "\r" in stripped:
        problems.append(Problem(loc, "line_break", f"{what} is one line."))
        return None
    return stripped


def _list(value: Any, loc: Loc, problems: list[Problem], *, limit: int, what: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append(Problem(loc, "list_type", f"{what} must be a list."))
        return []
    if len(value) > limit:
        problems.append(Problem(loc, "too_long", f"{what} has at most {limit} entries."))
        return []
    return value


def check_area(raw: Any, loc: Loc) -> tuple[dict[str, Any] | None, list[Problem]]:
    """One area's checked values, and every problem it has. The values are None when there is a problem."""
    problems: list[Problem] = []
    if not isinstance(raw, Mapping):
        return None, [Problem(loc, "dict_type", "An area is a mapping with a name, paths, logs and owners.")]
    for key in raw:
        if key not in _AREA_KEYS:
            problems.append(_unknown(loc, key, _AREA_KEYS, "an area"))
    name: str | None = None
    if "name" not in raw:
        problems.append(Problem((*loc, "name"), "missing", "An area needs a name."))
    else:
        name = _text(raw["name"], (*loc, "name"), problems, limit=MAX_NAME, what="A name")
        if name is not None and not slug_of(name):
            problems.append(Problem((*loc, "name"), "value_error", "A name needs a letter or a digit."))
            name = None
    paths: list[str] = []
    for i, item in enumerate(_list(raw.get("paths"), (*loc, "paths"), problems, limit=MAX_PATHS, what="paths")):
        glob = _text(item, (*loc, "paths", i), problems, limit=MAX_GLOB, what="A glob")
        if glob is None:
            continue
        # qa.yaml refuses a padded glob rather than reading it as another: so do areas, so a glob means one thing.
        why = "A glob has no space at its start or end." if glob != item else glob_problem(expand(glob))
        if why is not None:
            problems.append(Problem((*loc, "paths", i), "invalid_glob", why))
        elif expand(glob) not in paths:
            paths.append(expand(glob))
    logs: list[str] = []
    for i, item in enumerate(_list(raw.get("logs"), (*loc, "logs"), problems, limit=MAX_LOGS, what="logs")):
        text = _text(item, (*loc, "logs", i), problems, limit=MAX_LOG, what="A log string")
        if text is not None and text not in logs:
            logs.append(text)
    owners: list[str] = []
    for i, item in enumerate(_list(raw.get("owners"), (*loc, "owners"), problems, limit=MAX_OWNERS, what="owners")):
        text = _text(item, (*loc, "owners", i), problems, limit=40, what="An owner")
        if text is None:
            continue
        login = text.removeprefix("@")
        if not GITHUB_LOGIN_RE.fullmatch(login):
            problems.append(
                Problem(
                    (*loc, "owners", i),
                    "invalid_login",
                    "An owner is a login: letters, digits and single hyphens, up to 39, with or without a leading @.",
                )
            )
        elif login.lower() not in (o.lower() for o in owners):
            owners.append(login)
    if problems or name is None:
        return None, problems
    return {"name": name, "paths": paths, "logs": logs, "owners": owners}, []


def check(values: Any) -> dict[str, Any]:
    """The values every reader reads, ``{"areas": [...]}``; :class:`DocumentInvalid` naming every problem.

    ``values`` is what :func:`parse_body` read: None (an empty document) or a
    mapping.
    """
    if values is None:
        return {"areas": []}
    if not isinstance(values, Mapping):
        raise DocumentInvalid(
            [Problem((), "dict_type", "The areas document is a mapping: an optional version and a list of areas.")]
        )
    problems: list[Problem] = []
    for key in values:
        if key not in _TOP_KEYS:
            problems.append(_unknown((), key, _TOP_KEYS, "the areas document"))
    version = values.get("version", VERSION)
    if isinstance(version, bool) or not isinstance(version, int):
        problems.append(Problem(("version",), "int_type", f"version is a whole number: {VERSION}."))
    elif version > VERSION:
        problems.append(
            Problem(("version",), "version", f"This embry0 reads areas documents of version {VERSION}, not newer.")
        )
    elif version < VERSION:
        problems.append(Problem(("version",), "version", f"version is {VERSION}."))
    entries = _list(values.get("areas"), ("areas",), problems, limit=MAX_AREAS, what="areas")
    areas: list[dict[str, Any]] = []
    first_by_slug: dict[str, int] = {}
    for index, raw in enumerate(entries):
        area, found = check_area(raw, ("areas", index))
        problems += found
        if area is None:
            continue
        slug = slug_of(area["name"])
        if slug in first_by_slug:
            problems.append(
                Problem(
                    ("areas", index, "name"),
                    "duplicate_slug",
                    f"This name labels work as area 'area:{slug}', as area {first_by_slug[slug]}'s does: "
                    "each area needs a name of its own.",
                )
            )
            continue
        first_by_slug[slug] = index
        areas.append(area)
    if problems:
        raise DocumentInvalid(problems)
    return {"areas": areas}


# ---- reading and writing back -------------------------------------------------


def areas_of(data: Mapping[str, Any] | None) -> list[Area]:
    """The areas stored ``data`` names, in order.

    Lenient, as a reader must be: an entry without a usable name is skipped,
    a key or an owner form this release does not know is left out, so a
    document a newer release wrote still reads.
    """
    out: list[Area] = []
    entries = data.get("areas") if isinstance(data, Mapping) else None
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
            continue

        def strings(key: str, entry: Mapping[str, Any] = entry) -> tuple[str, ...]:
            value = entry.get(key)
            return tuple(s for s in value if isinstance(s, str) and s) if isinstance(value, list) else ()

        owners = tuple(o for o in strings("owners") if GITHUB_LOGIN_RE.fullmatch(o))
        out.append(Area(entry["name"].strip(), strings("paths"), strings("logs"), owners))
    return out


class _Dumper(yaml.SafeDumper):
    pass


def dump(data: Mapping[str, Any], *, header: str = "") -> str:
    """``data`` as the YAML text a JSON body or a rewritten import is stored as, under ``header`` comment lines."""
    areas = [
        {k: v for k, v in area.items() if k == "name" or v}
        for area in data.get("areas") or []
        if isinstance(area, dict)
    ]
    body = yaml.dump({"areas": areas}, Dumper=_Dumper, sort_keys=False, allow_unicode=True, default_flow_style=False)
    lines = "".join(f"# {line}".rstrip() + "\n" for line in header.splitlines()) if header else ""
    return lines + body


def text_diff(before: str, after: str) -> str:
    return shared_text_diff(before, after, before_name="stored", after_name="proposed")


def changes(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """What going from ``before`` to ``after`` changes, area by area (matched by slug), owners first.

    Each entry is ``{"area", "change", "keys", "owners_added", "owners_removed"}``: ``change`` is ``added``,
    ``removed`` or ``changed``, and ``keys`` the keys that differ (``order`` when an area moved, which can
    change which area a file falls in).
    """
    old = {a.slug: (i, a) for i, a in enumerate(areas_of(before))}
    new = {a.slug: (i, a) for i, a in enumerate(areas_of(after))}
    out: list[dict[str, Any]] = []
    for slug, (_, area) in new.items():
        if slug not in old:
            out.append(_entry(area.name, "added", ["name", "paths", "logs", "owners"], (), area.owners))
    for slug, (_, area) in old.items():
        if slug not in new:
            out.append(_entry(area.name, "removed", [], area.owners, ()))
    kept_old = [s for s in old if s in new]
    kept_new = [s for s in new if s in old]
    for slug in kept_new:
        (_, was), (_, now) = old[slug], new[slug]
        keys = [k for k in ("name", "paths", "logs", "owners") if getattr(was, k) != getattr(now, k)]
        if kept_old.index(slug) != kept_new.index(slug):
            keys.append("order")
        if keys:
            gone = tuple(o for o in was.owners if o.lower() not in {n.lower() for n in now.owners})
            came = tuple(o for o in now.owners if o.lower() not in {w.lower() for w in was.owners})
            out.append(_entry(now.name, "changed", keys, gone, came))
    out.sort(key=lambda c: (not c["owners_added"] and not c["owners_removed"], c["change"] != "added"))
    return out


def _entry(name: str, change: str, keys: list[str], gone: Iterable[str], came: Iterable[str]) -> dict[str, Any]:
    return {
        "area": name,
        "change": change,
        "keys": keys,
        "owners_added": list(came),
        "owners_removed": list(gone),
    }
