"""Which skill names a write may add (spec 3.3).

Validation runs on writes, against the names the write adds. Each name is:

- ``available``: in the catalog, from a source that is ready and enabled;
- ``unavailable``: known but not loadable right now: in the catalog from a
  source that is not ready or not enabled, or prefixed by a source that exists
  but is not ready or not enabled (a first sync still pending, a failed clone);
- ``repository``: no colon, so the target repository's own skill;
- ``unknown``: none of the above.

An unknown name is refused; an unavailable one is accepted with a warning.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from embry0.agents.skills import check_skill_names, is_repository_skill

if TYPE_CHECKING:
    from embry0.storage.repositories.skills import SkillSourcesRepository

BUCKETS = ("available", "unavailable", "repository", "unknown")


def _not_ready_prefixes(source_rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Each prefix of a source that is not ready and enabled, mapped to why."""
    out: dict[str, str] = {}
    for src in source_rows:
        if src.get("status") == "ready" and src.get("enabled"):
            continue
        why = str(src.get("status") or "") if src.get("enabled") else "disabled"
        for prefix in (src.get("plugin_name"), src.get("id")):
            if prefix:
                out[str(prefix)] = f"source {src.get('id')!r} is {why}"
    return out


def classify_skill_names(
    names: Iterable[str],
    catalog_rows: Sequence[Mapping[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Each distinct name in exactly one of ``BUCKETS``, in input order."""
    catalog = {str(c["name"]): bool(c.get("available")) for c in catalog_rows}
    not_ready = _not_ready_prefixes(source_rows)
    out: dict[str, list[str]] = {b: [] for b in BUCKETS}
    for name in dict.fromkeys(names):
        if is_repository_skill(name):
            bucket = "repository"
        elif name in catalog:
            bucket = "available" if catalog[name] else "unavailable"
        elif name.split(":", 1)[0] in not_ready:
            bucket = "unavailable"
        else:
            bucket = "unknown"
        out[bucket].append(name)
    return out


def check_skill_write(
    names: Iterable[str],
    catalog_rows: Sequence[Mapping[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    """The warnings for the names a write adds; raise UnknownSkillError for unknown ones."""
    buckets = classify_skill_names(names, catalog_rows, source_rows)
    # What is left once the unavailable names are set aside is checked like any catalog name.
    check_skill_names(buckets["unknown"], {str(c["name"]) for c in catalog_rows})
    catalog = {str(c["name"]): c for c in catalog_rows}
    not_ready = _not_ready_prefixes(source_rows)
    warnings = []
    for name in buckets["unavailable"]:
        row = catalog.get(name)
        why = f"source {row.get('source_id')!r} is {row.get('status')}" if row else not_ready[name.split(":", 1)[0]]
        warnings.append(f"{name} is not available yet ({why}); it is saved and loads once its source is ready")
    return warnings


async def validate_skill_write(repo: SkillSourcesRepository | None, names: Iterable[str]) -> list[str]:
    """``check_skill_write`` against a ``SkillSourcesRepository``; no repo (minimal apps) checks nothing."""
    names = list(names)
    if repo is None or not names:
        return []
    return check_skill_write(names, await repo.catalog(), await repo.list_all())
