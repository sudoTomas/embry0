"""Skill names, deltas and the one rule that turns them into an effective list (spec 3.3).

Mirrored by frontend/src/lib/skills.ts; both are pinned to
tests/fixtures/skills/effective-skills.json.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


def _entry_name(entry: Any) -> str:
    """A delta entry's skill name: a string as is, or the ``name`` of a ``{"name": ...}`` object.

    The pipeline editor stores node skills as ``{"name", "mode"}`` objects; only the name means anything.
    """
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        name = entry.get("name")
        if isinstance(name, str) and name:
            return name
    raise ValueError('skills delta entries must be names or {"name": ...} objects')


def normalize_delta(value: Any) -> dict[str, Any]:
    """A delta dict with ``add`` (list) and ``remove`` (list or "*").

    A bare list is the legacy *replace* form: ``{"add": list, "remove": "*"}``.
    ``None`` is the identity delta. Entries are names or ``{"name": ...}`` objects.
    """
    if value is None:
        return {"add": [], "remove": []}
    if isinstance(value, list):
        return {"add": [_entry_name(v) for v in value], "remove": "*"}
    if isinstance(value, dict):
        add = value.get("add", [])
        remove = value.get("remove", [])
        if not isinstance(add, list) or not (remove == "*" or isinstance(remove, list)):
            raise ValueError('skills delta must be {"add": [...], "remove": [...] | "*"}')
        return {
            "add": [_entry_name(v) for v in add],
            "remove": "*" if remove == "*" else [_entry_name(v) for v in remove],
        }
    raise ValueError(f"skills delta must be a list or a dict, not {type(value).__name__}")


def effective_skills(base: Iterable[str], *deltas: Any) -> list[str]:
    """Apply each delta in order: remove, then add; deduplicated, base order then add order."""
    current = list(dict.fromkeys(str(s) for s in base))
    for raw in deltas:
        delta = normalize_delta(raw)
        if delta["remove"] == "*":
            current = []
        else:
            drop = set(delta["remove"])
            current = [s for s in current if s not in drop]
        for name in delta["add"]:
            if name not in current:
                current.append(name)
    return current


# The skills triage may add to an agent: the agent row's
# ``triage_may_add_skills``, which the operator sets. Each is a catalog name,
# <plugin>:<skill>, shaped as a skill source names its skills
# (embry0/skills/sync.py, _PLUGIN_NAME_RE and _SKILL_NAME_RE), held once each,
# and at most TRIAGE_MAY_ADD_MAX of them. Triage still adds one only from a
# source the agent's list already loads (narrowed_skills).
TRIAGE_MAY_ADD_FIELD = "triage_may_add_skills"
TRIAGE_MAY_ADD_MAX = 20
_CATALOG_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}:[a-z0-9][a-z0-9._-]{0,63}$")


def checked_triage_may_add(value: Any) -> list[str]:
    """``value`` as the ``triage_may_add_skills`` column holds it: catalog names, each once, in order.

    Raises ValueError saying what is wrong: not a list, an entry that is not
    a ``source:skill`` name, or more than ``TRIAGE_MAY_ADD_MAX`` names.
    """
    if not isinstance(value, list):
        raise ValueError(f"{TRIAGE_MAY_ADD_FIELD} must be a list of source:skill names")
    names: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not _CATALOG_SKILL_NAME_RE.fullmatch(entry):
            raise ValueError(
                f"{TRIAGE_MAY_ADD_FIELD}: {entry!r} is not a source:skill name "
                "(lowercase letters, digits, '_' and '-', a skill also '.', up to 64 each)"
            )
        if entry not in names:
            names.append(entry)
    if len(names) > TRIAGE_MAY_ADD_MAX:
        raise ValueError(f"{TRIAGE_MAY_ADD_FIELD} holds {len(names)} names; at most {TRIAGE_MAY_ADD_MAX}")
    return names


def triage_may_add(definition: Mapping[str, Any]) -> list[str]:
    """The skills an agent row lets triage add; none when the row holds none or a malformed value."""
    try:
        return checked_triage_may_add(definition.get(TRIAGE_MAY_ADD_FIELD) or [])
    except ValueError:
        return []


def triage_skill_choices(definition: Mapping[str, Any]) -> list[str]:
    """The skills triage may add to this agent that it would be granted: those from a source the row's skills load."""
    sources = {skill_source(str(s)) for s in definition.get("skills") or []} - {""}
    return [name for name in triage_may_add(definition) if skill_source(name) in sources]


@dataclass(frozen=True)
class NarrowedSkills:
    """What a run-time request leaves of an operator's skill list.

    ``hidden`` is no skills at all, the repository's own included. With
    ``hidden`` false an empty ``skills`` is no filter, as for an operator's
    empty list. ``not_granted`` names what the request asked for and did not
    get.
    """

    skills: list[str]
    hidden: bool = False
    not_granted: list[str] = field(default_factory=list)


def skill_source(name: str) -> str:
    """The source a catalog-shaped name comes from (``source:skill``); "" for a repository skill."""
    return name.split(":", 1)[0] if ":" in name else ""


def narrowed_skills(operator: list[str], request: Any, *, may_add: Collection[str] = ()) -> NarrowedSkills:
    """``operator`` narrowed by a run-time ``request`` (a bare list or a delta): never a skill or a source it lacks.

    ``operator`` is the effective list the definition and the template step
    give, where empty means no filter: the repository's own skills, and no
    source's. The request's removes apply; its adds are granted only when
    ``operator`` already allows the name (an allowlist that names it, or no
    filter and a repository skill), or the name is in ``may_add`` and its
    source is one ``operator`` already loads. A request that replaces the list
    (a bare list, ``"remove": "*"``) and keeps nothing is ``hidden``; so is a
    malformed one.
    """
    try:
        delta = normalize_delta(request)
    except ValueError:
        return NarrowedSkills([], hidden=True)
    sources = {skill_source(s) for s in operator} - {""}

    def addable(name: str) -> bool:
        if name in operator:
            return True
        if not operator and is_repository_skill(name):
            return True
        return name in may_add and skill_source(name) in sources

    replaces = delta["remove"] == "*"
    if not operator and not replaces:
        # No filter stays no filter: a repository skill already loads, a source's never does.
        return NarrowedSkills([], not_granted=[n for n in delta["add"] if not addable(n)])
    drop = set() if replaces else set(delta["remove"])
    current = [] if replaces else [s for s in operator if s not in drop]
    refused: list[str] = []
    for name in delta["add"]:
        if name in current:
            continue
        if addable(name):
            current.append(name)
        else:
            refused.append(name)
    return NarrowedSkills(current, hidden=not current, not_granted=refused)


def is_repository_skill(name: str) -> bool:
    """A name without a colon is the target repository's own ``.claude/skills`` entry.

    The catalog cannot list those, so they are never checked against it, and
    they pass through to the SDK allowlist as written.
    """
    return ":" not in name


def closest_names(name: str, known: Iterable[str], n: int = 3) -> list[str]:
    """Up to ``n`` known names, most similar first."""
    candidates = sorted(set(known))
    return difflib.get_close_matches(name, candidates, n=n, cutoff=0.0)[:n]


class UnknownSkillError(ValueError):
    """Skill names the catalog does not know, each with its closest known names.

    Repository skills (no colon, ``is_repository_skill``) are never unknown.
    """

    def __init__(self, unknown: list[str], suggestions: dict[str, list[str]]) -> None:
        self.unknown = unknown
        self.suggestions = suggestions
        parts = [f"{u} (did you mean: {', '.join(suggestions[u]) or 'nothing close'})" for u in unknown]
        super().__init__("unknown skill name(s): " + "; ".join(parts))


def check_skill_names(names: Iterable[str], catalog_names: Collection[str]) -> None:
    """Raise UnknownSkillError when any catalog-shaped name is missing from the catalog."""
    unknown = [n for n in dict.fromkeys(names) if not is_repository_skill(n) and n not in catalog_names]
    if unknown:
        raise UnknownSkillError(unknown, {u: closest_names(u, catalog_names) for u in unknown})
