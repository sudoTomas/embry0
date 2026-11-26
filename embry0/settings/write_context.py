"""What a write of the settings document is checked against beyond the values themselves.

A section whose values name something the running deployment offers (a
model of its provider catalogue, a pipeline template) checks them through
pydantic's validation context, which the settings store builds here for
every write it stores: ``PUT`` and ``PATCH /config/settings``, their dry
runs, and an undo. A stored value read back, the environment import at
startup and the offline ``embry0 settings check`` validate without it, so
what the deployment offers moving (OpenRouter's live list is read after
startup; a provider is turned off) never turns a stored value into its
default. Such a value is caught where it is used: the feature logs that its
model cannot be called, or that its template is gone.

What lives in the database is read on the write's own connection, inside
its transaction, and only for a write that may change a section that checks
against it (:func:`deployment_reads`); the rest is built in memory
(:func:`deployment_context`).

The check lives here, in the orchestrator, and not in
``embry0.agents.providers``, which the sandbox images carry: it reads that
module's catalogue and changes nothing in it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from functools import partial
from typing import TYPE_CHECKING, Any

from embry0.settings.sections.background_models import BACKGROUND_MODELS, CATALOGUE
from embry0.settings.sections.leads import LEADS
from embry0.settings.sections.maps import ANALYSIS_TEMPLATES, MAPS, TRIGGER_LABELS, fold_label
from embry0.settings.sections.slop_policy import ROUTE_TEMPLATES, SLOP_POLICY

if TYPE_CHECKING:
    from embry0.agents.catalogue import Catalogue

#: Builds the context of one write; the store's default is :func:`deployment_context`.
WriteContext = Callable[[], Mapping[str, Any]]
#: ``(conn, sections) -> context``: reads, on one write's connection, the part of its context that lives in the
#: database, for a write that may change ``sections``; the store's default is :func:`deployment_reads`.
WriteReads = Callable[[Any, frozenset[str]], Awaitable[Mapping[str, Any]]]


def model_use_problem(model: str, needs: Iterable[str], catalogue: Catalogue | None = None) -> str | None:
    """Why this deployment cannot run ``model`` for a use that ``needs`` some things of it, or None.

    ``catalogue`` is the provider catalogue to check against, the installed
    one by default. ``model`` must be one the catalogue offers (``known_models``, or a
    ``custom/<model>`` of the operator's own server, with the custom provider
    enabled) and priced, so what it costs is counted. Each need:

    - ``tools``: its agents call tools, so not a model its provider serves
      without tool calling;
    - ``chat``: it is asked directly over chat completions, which only an
      OpenAI-compatible provider (``custom`` included) serves.

    The words never repeat ``model``: the API answers them in a 422.
    """
    from embry0.agents.providers import (
        chat_only_models,
        is_custom_model,
        known_models,
        provider_for_model,
        unpriced_models,
    )

    if model not in known_models(catalogue) and not is_custom_model(model, catalogue):
        return (
            "not a model this deployment offers: GET /api/v1/config/models lists them, and a model of your own "
            "server is custom/<model>, with the custom provider turned on in the provider catalogue"
        )
    if unpriced_models([model], catalogue):
        return "this model has no price, so what it spends could not be counted against the budget caps"
    wanted = set(needs)
    if "tools" in wanted and chat_only_models([model], catalogue):
        return "its provider serves it without tool calling, and the agents of this feature call tools"
    provider = provider_for_model(model, catalogue)
    if "chat" in wanted and (provider is None or provider.runtime != "chat_completions"):
        return "this is asked over chat completions, which only custom/<model> or an OpenAI-compatible provider serves"
    return None


def deployment_context() -> dict[str, Any]:
    """The context every write the orchestrator stores is checked with: the provider catalogue as it is now."""
    return {CATALOGUE: model_use_problem}


def no_context() -> dict[str, Any]:
    """No deployment to check against: each value meets only its section's own rules."""
    return {}


def route_template_checker(rows: Iterable[Mapping[str, Any]], coders: frozenset[str]) -> Callable[[str], str | None]:
    """``name -> problem``: why a slop policy cannot route a code job to the template ``name``, of ``rows``.

    A template the rows do not hold is none; one they hold must take a code
    job (``row_behaviour.code_job_problem``: its jobs recorded as code jobs,
    and a coding step in its route, a step of one of ``coders``).
    """
    from embry0.workflows.row_behaviour import code_job_problem

    templates = {str(row["name"]): row for row in rows}

    def problem(name: str) -> str | None:
        row = templates.get(name)
        if row is None:
            return f"no pipeline template named {name!r}: GET /api/v1/pipelines/templates lists them"
        return code_job_problem(row, coders)

    return problem


def analysis_template_checker(rows: Iterable[Mapping[str, Any]]) -> Callable[[str], str | None]:
    """``name -> problem``: why a map's part cannot be analysed with the template ``name``, of ``rows``.

    A template the rows do not hold is none; one they hold must record its jobs as analysis jobs
    (``row_behaviour.analysis_job_problem``).
    """
    from embry0.workflows.row_behaviour import analysis_job_problem

    templates = {str(row["name"]): row for row in rows}

    def problem(name: str) -> str | None:
        row = templates.get(name)
        if row is None:
            return f"no pipeline template named {name!r}: GET /api/v1/pipelines/templates lists them"
        return analysis_job_problem(row)

    return problem


async def trigger_labels_on(conn: Any) -> frozenset[str]:
    """The deployment's trigger labels, folded as the trigger compares them, read on ``conn``: the stored ones,
    else ``TRIGGER_LABELS``, else the default (``IntegrationConfigRepository.resolve_trigger_labels``)."""
    from embry0.storage.repositories.integration_config import resolved_trigger_labels

    stored = await conn.fetchval("SELECT trigger_labels FROM integration_config WHERE id = 'default'")
    return frozenset(fold_label(label) for label in resolved_trigger_labels(stored).labels if label.strip())


async def deployment_reads(conn: Any, sections: frozenset[str]) -> dict[str, Any]:
    """The part of a write's context the database holds: the pipeline templates, for a write that may change
    ``slop_policy`` or ``maps``, the deployment's trigger labels, for one that may change ``maps`` or ``leads``,
    and the stored provider catalogue, for one that may change ``background_models``; nothing for any other.

    ``conn`` is the write's connection, inside its transaction, and the rows
    are read ``FOR SHARE``: a template a policy is checked against cannot be
    deleted or renamed before the write commits, nor the catalogue a model is
    checked against changed. A change of a template takes its row ``FOR
    UPDATE`` before it reads the settings documents
    (``embry0.services.slop_routing``), and so does a change of the catalogue
    (``embry0.services.provider_catalogue_store``), so of a write naming a
    template or a model and a change of what it names, one waits for the other
    and sees what it wrote. A write of another section takes no lock. A dry
    run reads on the pool, where the lock ends with the statement.
    """
    found: dict[str, Any] = {}
    if SLOP_POLICY.name in sections or MAPS.name in sections:
        rows = [
            dict(row)
            for row in await conn.fetch(
                "SELECT name, default_for_kind, job_kind, graph_definition FROM pipeline_templates FOR SHARE"
            )
        ]
        if SLOP_POLICY.name in sections:
            from embry0.storage.repositories.agent_definitions import coders_on

            found[ROUTE_TEMPLATES] = route_template_checker(rows, await coders_on(conn, share=True))
        if MAPS.name in sections:
            found[ANALYSIS_TEMPLATES] = analysis_template_checker(rows)
    if MAPS.name in sections or LEADS.name in sections:
        # #180: a label embry0 puts on its own issues may not be one that starts a job.
        found[TRIGGER_LABELS] = await trigger_labels_on(conn)
    if BACKGROUND_MODELS.name in sections:
        from embry0.services.provider_catalogue_store import held_catalogue

        found[CATALOGUE] = partial(_model_use_problem_in, await held_catalogue(conn))
    return found


def _model_use_problem_in(catalogue: Catalogue, model: str, needs: Iterable[str]) -> str | None:
    return model_use_problem(model, needs, catalogue)
