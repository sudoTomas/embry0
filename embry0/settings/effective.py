"""Every setting's effective value and where it came from (``GET /config/settings/effective``).

``source`` is ``default`` (nothing stored for the key), ``document``
(stored), or ``bootstrap`` (stored by the environment import and not changed
since). Who set a stored key and when come from the newest kept revision
that changed it; a key last changed before the kept history has neither.
A stored value the running code refuses reads as its default, and says why.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from embry0.settings.document import changes
from embry0.settings.env import env_name, is_set
from embry0.settings.registry import Catalog
from embry0.settings.store import Snapshot


def provenance(revisions: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    """``(section, key)`` to the newest revision that changed it, from revisions newest first."""
    found: dict[tuple[str, str], Mapping[str, Any]] = {}
    for revision in revisions:
        before = (revision.get("before") or {}).get("data") or {}
        after = (revision.get("after") or {}).get("data") or {}
        for change in changes(before, after):
            found.setdefault((change.section, change.key), revision)
    return found


def iso(value: Any) -> str | None:
    """A timestamp as ISO 8601 text; anything else, ``None`` included, as ``None``."""
    return value.isoformat() if isinstance(value, datetime) else None


def effective_view(
    catalog: Catalog, snapshot: Snapshot, revisions: Iterable[Mapping[str, Any]], config: Any
) -> dict[str, Any]:
    """The effective settings of every section, key by key."""
    changed_by = provenance(revisions)
    refused = {(r.section, r.key): r.reason for r in snapshot.sections.refused}
    sections: list[dict[str, Any]] = []
    for section in catalog:
        values = snapshot.get(section).model_dump(mode="json")
        defaults = section.defaults().model_dump(mode="json")
        stored = snapshot.data.get(section.name)
        stored = stored if isinstance(stored, dict) else {}
        imports = section.imports()
        keys: list[dict[str, Any]] = []
        for key in section.fields:
            why_refused = refused.get((section.name, key)) or refused.get((section.name, ""))
            in_force = key in stored and why_refused is None
            revision = changed_by.get((section.name, key)) if in_force else None
            if not in_force:
                source = "default"
            elif revision is not None and revision.get("source") == "bootstrap":
                source = "bootstrap"
            else:
                source = "document"
            imported = imports.get(key)
            overridable = [
                layer
                for layer, keys_of in (("organization", section.org_keys), ("repository", section.repo_keys))
                if key in keys_of
            ]
            keys.append(
                {
                    "key": key,
                    "value": values.get(key),
                    "default": defaults.get(key),
                    "source": source,
                    "set_by": revision.get("actor") if revision is not None else None,
                    "set_at": iso(revision.get("created_at")) if revision is not None else None,
                    "env": env_name(imported.attr) if imported is not None else None,
                    "env_still_set": imported is not None and is_set(config, imported.attr),
                    "overridable": overridable,
                    "refused": why_refused,
                    "description": section.model.model_fields[key].description,
                }
            )
        sections.append({"name": section.name, "summary": section.summary, "keys": keys})
    return {
        "generation": snapshot.generation,
        "sections": sections,
        "unknown_sections": list(snapshot.sections.unknown),
        "unknown_keys": list(snapshot.sections.unknown_keys),
    }
