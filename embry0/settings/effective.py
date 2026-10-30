"""Every setting's effective value and where it came from (``GET /config/settings/effective``).

``source`` is ``default`` (nothing stored for the key), ``document``
(stored in the deployment's document), ``bootstrap`` (stored there by the
environment import and not changed since), ``not_imported`` (a key moved from
older storage that the import has not imported yet, read meanwhile as the
release before read it; ``not_imported`` says from where), or, for a view of
an organization or a repository (spec 2026-10-29), ``organization`` or
``repository`` when that layer supplies it. Who set a stored key and when come from the newest
kept revision of the supplying document that changed it; a key last changed
before the kept history has neither. A stored value the running code refuses
reads as its default, or as the inherited value in a layer, and says why.

A view of a layer also answers ``inherited``, the value the key would have
without the layer the view is of, and ``layers``: one entry per document in
the view, most general first, each saying whether it sets the key and with
what.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from embry0.settings.document import changes, mapping
from embry0.settings.env import env_name, is_set
from embry0.settings.layers import Resolved
from embry0.settings.registry import Catalog, Section
from embry0.settings.scope import LAYER_NOUNS, Scope, owner_of
from embry0.settings.store import Snapshot


@dataclass(frozen=True)
class NotImported:
    """A key the import has not imported into a document yet, as its reader reads it meanwhile.

    ``read_from`` is where from, as a code (``row``: a record kept in the
    database before the document; ``variable``; ``default``; ``organization``:
    the organization's own settings), and ``label`` names it for a person.
    """

    value: Any
    read_from: str
    label: str

    def view(self) -> dict[str, str]:
        return {"read_from": self.read_from, "label": self.label}


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


def _layer_entry(
    section: Section[Any],
    key: str,
    scope: Scope,
    stored: Mapping[str, Any],
    resolved: Resolved,
    changed_by: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    """One organization's or repository's say on one key."""
    name = str(scope)
    applied = resolved.applied.get(name) or {}
    refusal = next((r for r in resolved.refused if r.scope == name and r.key == key), None)
    held = key in stored
    revision = changed_by.get((section.name, key)) if key in applied else None
    why: str | None = None
    if held and key not in section.settable(scope.layer):
        why = f"{LAYER_NOUNS[scope.layer]} cannot set this key; the stored value is ignored."
    elif refusal is not None:
        why = refusal.reason
    return {
        **scope.view(),
        "set": key in applied,
        "value": stored.get(key),
        "set_by": revision.get("actor") if revision is not None else None,
        "set_at": iso(revision.get("created_at")) if revision is not None else None,
        "settable": key in section.settable(scope.layer),
        "refused": why,
    }


def effective_view(
    catalog: Catalog,
    snapshot: Snapshot,
    revisions: Mapping[str, Iterable[Mapping[str, Any]]] | Iterable[Mapping[str, Any]],
    config: Any,
    *,
    owner: str | None = None,
    repo: str | None = None,
    not_imported: Mapping[str, Mapping[tuple[str, str], NotImported]] | None = None,
) -> dict[str, Any]:
    """The effective settings of every section, key by key, as the deployment, ``owner`` or ``repo`` reads them.

    ``revisions`` maps each document's scope string to its revisions, newest
    first; a plain list is the deployment's. ``not_imported`` maps a
    document's scope string (``deployment``, an organization's) to each
    ``(section, key)`` its readers read the old way because the import has not
    imported it yet: where that document neither stores the key nor has a
    layer under the view that sets it, the view shows that value, with source
    ``not_imported``, so it shows what is enforced and not a default.
    """
    pending = not_imported or {}
    by_scope: Mapping[str, Iterable[Mapping[str, Any]]] = (
        revisions if isinstance(revisions, Mapping) else {"deployment": revisions}
    )
    repo_scope = Scope.repository(repo) if repo else None
    org_name = owner_of(repo_scope.name) if repo_scope is not None else owner
    org_scope = Scope.organization(org_name) if org_name else None
    view = repo_scope or org_scope or Scope.deployment()
    changed_by = {name: provenance(rows) for name, rows in by_scope.items()}
    deployment_changes = changed_by.get("deployment", {})
    refused = {(r.section, r.key): r.reason for r in snapshot.sections.refused}
    sections: list[dict[str, Any]] = []
    for section in catalog:
        deployment_values = snapshot.get(section).model_dump(mode="json")
        defaults = section.defaults().model_dump(mode="json")
        org = snapshot.resolve(section, owner=org_scope.name) if org_scope else None
        full = snapshot.resolve(section, repo=repo_scope.name) if repo_scope else org
        values = full.model.model_dump(mode="json") if full else deployment_values
        if view.layer == "repository" and org is not None:
            inherited = org.model.model_dump(mode="json")
        elif view.layer == "organization":
            inherited = deployment_values
        else:
            inherited = defaults
        stored = mapping(snapshot.data.get(section.name))
        imports = section.imports()
        keys: list[dict[str, Any]] = []
        for key in section.fields:
            why_refused = refused.get((section.name, key)) or refused.get((section.name, ""))
            in_force = key in stored and why_refused is None
            revision = deployment_changes.get((section.name, key)) if in_force else None
            if not in_force:
                source = "default"
            elif revision is not None and revision.get("source") == "bootstrap":
                source = "bootstrap"
            else:
                source = "document"
            layers: list[dict[str, Any]] = [
                {
                    **Scope.deployment().view(),
                    "set": in_force,
                    "value": stored.get(key),
                    "set_by": revision.get("actor") if revision is not None else None,
                    "set_at": iso(revision.get("created_at")) if revision is not None else None,
                    "settable": True,
                    "refused": why_refused,
                }
            ]
            for scope in (org_scope, repo_scope):
                if scope is None or full is None:
                    continue
                entry = _layer_entry(
                    section,
                    key,
                    scope,
                    mapping(snapshot.layer_data(scope).get(section.name)),
                    full,
                    changed_by.get(str(scope), {}),
                )
                layers.append(entry)
                if entry["set"]:
                    source = scope.layer
                    revision = changed_by.get(str(scope), {}).get((section.name, key))
            value = values.get(key)
            inherited_value = inherited.get(key)
            waiting: NotImported | None = None
            sets = {entry["layer"] for entry in layers[1:] if entry["set"]}
            org_waiting = pending.get(str(org_scope), {}).get((section.name, key)) if org_scope else None
            dep_waiting = None if in_force else pending.get("deployment", {}).get((section.name, key))
            if org_waiting is not None and not sets:
                # The organization's own settings, until its layer imported them: what its readers enforce.
                waiting = org_waiting
            elif dep_waiting is not None and not sets:
                waiting = dep_waiting
            if waiting is not None:
                value, source, revision = waiting.value, "not_imported", None
            # What a layer view's override overrides: the deployment's value as it is enforced, or the
            # organization's own setting for a repository of an organization not imported yet.
            if view.layer == "organization" and dep_waiting is not None:
                inherited_value = dep_waiting.value
            elif view.layer == "repository" and "organization" not in sets:
                if org_waiting is not None:
                    inherited_value = org_waiting.value
                elif dep_waiting is not None:
                    inherited_value = dep_waiting.value
            imported = imports.get(key)
            overridable = [
                layer
                for layer, keys_of in (("organization", section.org_keys), ("repository", section.repo_keys))
                if key in keys_of
            ]
            entry_out: dict[str, Any] = {
                "key": key,
                "value": value,
                "default": defaults.get(key),
                "source": source,
                "not_imported": waiting.view() if waiting is not None else None,
                "set_by": revision.get("actor") if revision is not None else None,
                "set_at": iso(revision.get("created_at")) if revision is not None else None,
                "env": env_name(imported.attr) if imported is not None else None,
                "env_still_set": imported is not None and is_set(config, imported.attr),
                "overridable": overridable,
                "refused": why_refused,
                "description": section.model.model_fields[key].description,
            }
            if view.layer != "deployment":
                entry_out["inherited"] = inherited_value
                entry_out["layers"] = layers
            keys.append(entry_out)
        sections.append({"name": section.name, "summary": section.summary, "keys": keys})
    out: dict[str, Any] = {
        "generation": snapshot.generation,
        "sections": sections,
        "unknown_sections": list(snapshot.sections.unknown),
        "unknown_keys": list(snapshot.sections.unknown_keys),
    }
    if view.layer != "deployment":
        out["scope"] = view.view()
    return out
