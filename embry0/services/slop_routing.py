"""A pipeline template a slop policy routes to stays one it can route to (#169).

A settings document's ``slop_policy.route_template`` names the template a
code job judged slop runs. A write of the key is checked against the
templates (``embry0.settings.write_context``: it must name one that takes a
code job). This is the other side: while any document routes to a template,
a change that would leave it naming one that is gone, or one that no longer
takes a code job, is refused (``DELETE`` and ``PUT /pipelines/templates``
answer 409 with the documents that name it), so an operator points those
documents elsewhere first, and that write is recorded and undoable like any
other.

**A map's analysis template too** (#180). ``maps.analysis_template`` names
the template a map's part sent for analysis runs, and is held the same way:
while a document names it, a delete, a rename, or a change that stops it
recording analysis jobs is refused, naming the documents.

**Refused, not carried along.** A rename does not rewrite the documents
(as it carries an agent's ``quest_template``, a column): a document is the
operator's text, possibly kept in a file that ``embry0 settings apply``
writes back, and an automatic write to it would be undone, or refused, by
the next apply.

**Counted with what each document reads.** A document routes to a template
when the policy it resolves to names it: the deployment's (its default
``slop-cleanup`` included, while it sets no other), and each layer that sets
``route_template`` itself. ``enabled`` is not consulted: turning a policy
back on is a write that does not name the template again, so it is never
checked again; a document that names a template keeps it.

**No race.** The change runs in the template's transaction after its row is
locked ``FOR UPDATE``, and reads the documents there; a settings write
reads the templates ``FOR SHARE`` in its own transaction. Whichever comes
second waits for the first and sees what it committed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import asyncpg
import structlog

from embry0.settings.registry import Catalog, Section
from embry0.settings.scope import DEPLOYMENT, Scope, ScopeError
from embry0.settings.sections.maps import MAPS
from embry0.settings.sections.slop_policy import SLOP_POLICY
from embry0.storage.repositories.pipeline_templates import Guard
from embry0.storage.repositories.settings_documents import SettingsDocumentsRepository
from embry0.workflows.row_behaviour import analysis_job_problem, code_job_problem

logger = structlog.get_logger(__name__)


class TemplateInUse(Exception):
    """A template change refused: a settings document routes slop jobs to the template. The message names
    the documents and what to do; it is the API's 409."""


async def documents_routing_to(
    conn: asyncpg.Connection, documents: SettingsDocumentsRepository, catalog: Catalog, name: str
) -> list[str]:
    """The settings documents whose ``slop_policy`` routes a slop job to the template ``name``, read on ``conn``.

    Each as a person names it: ``the deployment's document`` (``its default``
    when it sets no ``route_template``), ``organization acme``, ``repository
    acme/widgets``. Empty when the catalog has no ``slop_policy``.
    """
    return await _documents_naming(conn, documents, catalog, SLOP_POLICY, "route_template", name)


async def documents_analysing_with(
    conn: asyncpg.Connection, documents: SettingsDocumentsRepository, catalog: Catalog, name: str
) -> list[str]:
    """The settings documents whose ``maps`` sends a map's part for analysis to the template ``name`` (#180), named
    as :func:`documents_routing_to` names them. Empty when the catalog has no ``maps``."""
    return await _documents_naming(conn, documents, catalog, MAPS, "analysis_template", name)


async def _documents_naming(
    conn: asyncpg.Connection,
    documents: SettingsDocumentsRepository,
    catalog: Catalog,
    section: Section[Any],
    key: str,
    name: str,
) -> list[str]:
    """The settings documents whose ``section`` resolves ``key`` to the template ``name``: the deployment's (its
    default included, while it sets no other), and each layer that sets ``key`` itself."""
    if section.name not in catalog:
        return []
    from embry0.settings.store import build_snapshot

    snapshot = build_snapshot(catalog, await documents.all(conn), token=(), log=False)
    holders: list[str] = []
    if getattr(snapshot.get(section), key) == name:
        own = key in (snapshot.data.get(section.name) or {})
        holders.append("the deployment's document" + ("" if own else " (its default)"))
    for stored, data in sorted(snapshot.layers.items()):
        if key not in (data.get(section.name) or {}):
            continue
        try:
            scope = Scope.parse(stored)
        except ScopeError:
            continue
        if scope.layer == DEPLOYMENT:
            continue
        model = (
            snapshot.get(section, owner=scope.name)
            if scope.layer == "organization"
            else snapshot.get(section, repo=scope.name)
        )
        if getattr(model, key) == name:
            holders.append(f"{scope.layer} {scope.name}")
    return holders


def slop_route_guard(documents: SettingsDocumentsRepository, catalog: Catalog) -> Guard:
    """The guard a template's change runs: refuse a delete, a rename, or a change that makes the template stop
    taking a code job (``row_behaviour.code_job_problem``), while a document routes slop jobs to it; and the same
    while a document sends a map's parts for analysis to it, a change that makes it stop recording analysis jobs
    (``row_behaviour.analysis_job_problem``, ``maps.analysis_template``, #180)."""

    async def guard(conn: asyncpg.Connection, before: Mapping[str, Any], after: Mapping[str, Any] | None) -> None:
        name = str(before.get("name") or "")
        if after is None:
            change = "delete it"
        elif str(after.get("name") or "") != name:
            change = f"rename it to {str(after.get('name') or '')!r}"
        elif code_job_problem(before) is None and (problem := code_job_problem(after)) is not None:
            change = f"change it so that it no longer takes a code job ({problem})"
        else:
            return
        holders = await documents_routing_to(conn, documents, catalog, name)
        if not holders:
            return
        logger.info("pipeline_template_change_refused", template=name, change=change, held_by=holders)
        raise TemplateInUse(
            f"slop_policy.route_template sends code jobs judged slop to {name!r} in {', '.join(holders)}, so "
            f"you cannot {change}. Set slop_policy.route_template there to another template first "
            "(PATCH /api/v1/config/settings/slop_policy, with ?owner= or ?repo= for a layer)."
        )

    async def analysis_guard(
        conn: asyncpg.Connection, before: Mapping[str, Any], after: Mapping[str, Any] | None
    ) -> None:
        name = str(before.get("name") or "")
        if after is None:
            change = "delete it"
        elif str(after.get("name") or "") != name:
            change = f"rename it to {str(after.get('name') or '')!r}"
        elif analysis_job_problem(before) is None and (problem := analysis_job_problem(after)) is not None:
            change = f"change it so that it no longer records analysis jobs ({problem})"
        else:
            return
        holders = await documents_analysing_with(conn, documents, catalog, name)
        if not holders:
            return
        logger.info("pipeline_template_change_refused", template=name, change=change, held_by=holders)
        raise TemplateInUse(
            f"maps.analysis_template sends a map's parts for analysis to {name!r} in {', '.join(holders)}, so "
            f"you cannot {change}. Set maps.analysis_template there to another template first "
            "(PATCH /api/v1/config/settings/maps, with ?owner= or ?repo= for a layer)."
        )

    async def both(conn: asyncpg.Connection, before: Mapping[str, Any], after: Mapping[str, Any] | None) -> None:
        await guard(conn, before, after)
        await analysis_guard(conn, before, after)

    return both
