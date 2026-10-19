"""Skill names, deltas and the one rule that turns them into an effective list (spec 3.3).

Mirrored by frontend/src/lib/skills.ts; both are pinned to
tests/fixtures/skills/effective-skills.json.
"""

from __future__ import annotations

import difflib
from collections.abc import Collection, Iterable
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
