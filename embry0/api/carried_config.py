"""What running a job again carries of its stored ``pipeline_config``, for the caller who runs it again.

``POST /jobs/{id}/run-again`` starts a new job from an old one's launch
record and stored ``pipeline_config``. The old config was stored by whoever
started that job, and a job stored before ``POST /jobs`` refused embry0's own
keys (``embry0.api.schemas.INTERNAL_PIPELINE_KEYS``) may hold any of them as a
request sent them. So the new job carries each key by a rule of its own
(:func:`carried_config`):

- ``run_with``: checked again against the caller, as a run with chosen now
  is (``checked_run_with``); one the caller may not use is dropped, and the
  new job runs with the deployment's choice.
- ``agent_models_override``: checked again as ``POST /jobs`` checks
  ``agent_models``; dropped when any model is no longer usable.
- ``max_budget_usd``: a real number above 0, at most the field's bound
  (``MAX_BUDGET_USD_LE``); a larger one is held to the bound, any other value
  is dropped.
- ``execution_mode_override``, ``auth_mode_override``: carried, as
  ``POST /jobs`` writes them from its own fields.
- The keys a runner reads from the job's config: a QA job's ``pipeline``,
  ``branch`` and ``qa``; an onboarding job's ``pipeline``, ``branch``,
  ``skip_smoke`` and ``proposal_id``; a pull request review's frozen config
  (:data:`~embry0.api.schemas.STARTED_JOB_KEYS` and ``head_sha``). Its launch
  record names the runner, and embry0 wrote them from the request's own
  fields.
- The keys of a job one of embry0's own flows started, by the actor it ran
  under (a request cannot run as an embry0 actor): a context audit's
  ``open_pr``, ``context_fingerprint``, ``head_sha``, ``actor``,
  ``budget_overrun_mode`` and ``local_first_model``; a scout job's ``actor``
  and ``budget_overrun_mode``; a watcher analysis' ``local_first_model``. An
  area map, which a person starts, carries ``open_pr``,
  carries ``budget_overrun_mode`` only with the value that flow writes (a
  ``hard`` budget), and ``areas_proposal_id`` (running a map again starts a
  new map: ``embry0.api.v1.job_review``).
- Any other of embry0's own keys is dropped: it could only have come from a
  request.

Every other key is carried as stored. Each drop is said in the new job's
first event.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request

from embry0.api.schemas import INTERNAL_PIPELINE_KEYS, MAX_BUDGET_USD_LE, STARTED_JOB_KEYS, RunWithRequest
from embry0.orchestration import dispatch_actors
from embry0.services.run_with import ALL_AGENTS

#: The keys each runner reads from a job's config, written by embry0 from the request's own fields.
_RUNNER_KEYS: dict[str, frozenset[str]] = {
    "qa": frozenset({"pipeline", "branch", "qa"}),
    "onboard": frozenset({"pipeline", "branch", "skip_smoke", "proposal_id"}),
    "pr-review": frozenset({*STARTED_JOB_KEYS, "head_sha"}),
}
#: The keys of a job one of embry0's own flows started, by the actor it ran under.
_FLOW_KEYS: dict[str, frozenset[str]] = {
    dispatch_actors.CONTEXT_AUDIT: frozenset(
        {"open_pr", "context_fingerprint", "head_sha", "actor", "budget_overrun_mode", "local_first_model"}
    ),
    dispatch_actors.SCOUT: frozenset({"actor", "budget_overrun_mode"}),
    dispatch_actors.WATCHER: frozenset({"local_first_model"}),
}
#: An area map's keys, with the values its flow writes (``embry0.services.area_map``), by its template.
_AREA_MAP_VALUES: dict[str, Any] = {"budget_overrun_mode": "hard"}
_AREA_MAP_KEYS = frozenset({*_AREA_MAP_VALUES, "areas_proposal_id"})


def _budget(value: Any) -> float | None:
    """A stored ``max_budget_usd`` held to the field's bound: a real number above 0, at most the bound; None for any
    other value (a string, a boolean, a number at or below 0)."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
        return None
    return float(min(value, MAX_BUDGET_USD_LE))


def _own_keys(job: Mapping[str, Any], runner: str | None) -> tuple[frozenset[str], dict[str, Any]]:
    """embry0's own keys the job's own run needs, by its runner and the flow that started it, and the value a key must
    hold to be carried when only its flow's template vouches for it."""
    from embry0.workflows.issue_to_pr.areas_report import TEMPLATE as AREA_MAP_TEMPLATE

    keys = set(_RUNNER_KEYS.get(str(runner or ""), frozenset()))
    keys |= _FLOW_KEYS.get(str(job.get("dispatched_by") or ""), frozenset())
    required: dict[str, Any] = {}
    if job.get("pipeline_template") == AREA_MAP_TEMPLATE:
        # A person starts an area map, and any request may name its template: its values vouch for it.
        required = {key: value for key, value in _AREA_MAP_VALUES.items() if key not in keys}
        keys |= _AREA_MAP_KEYS
    return frozenset(keys), required


async def carried_config(
    request: Request, stored: Mapping[str, Any] | None, *, job: Mapping[str, Any], runner: str | None, repo: str | None
) -> tuple[dict[str, Any] | None, list[str]]:
    """``stored``, the old job's ``pipeline_config``, as the new job carries it (the module docstring), and a sentence
    for each key dropped or changed, for the new job's first event."""
    from embry0.api.v1.jobs import check_agent_models
    from embry0.api.v1.run_with import checked_run_with

    if stored is None:
        return None, []
    kept, required = _own_keys(job, runner)
    out: dict[str, Any] = {}
    notes: list[str] = []
    for key, value in stored.items():
        if key in ("run_with", "agent_models_override", "max_budget_usd"):
            continue
        if key in INTERNAL_PIPELINE_KEYS and key not in kept:
            notes.append(f"{key} is not carried: embry0 sets it for the work it starts.")
        elif key in required and value != required[key]:
            notes.append(f"{key} is not carried: the area map writes {required[key]!r}.")
        else:
            out[key] = value
    if "max_budget_usd" in stored:
        budget = _budget(stored["max_budget_usd"])
        if budget is None:
            notes.append(f"max_budget_usd is not carried: it is not a number above 0 and at most {MAX_BUDGET_USD_LE}.")
        else:
            out["max_budget_usd"] = budget
            if budget != stored["max_budget_usd"]:
                notes.append(f"max_budget_usd is held to {MAX_BUDGET_USD_LE}.")
    chosen = stored.get("run_with")
    if isinstance(chosen, Mapping) and (chosen.get("model") or chosen.get("credential_id")):
        try:
            body = RunWithRequest(model=chosen.get("model"), credential_id=chosen.get("credential_id"))
            checked = await checked_run_with(request, body, repo)
        except (HTTPException, ValueError):
            checked = None
        if checked is None:
            notes.append("The model or key it ran with is not one this run may use: it runs with the default.")
        else:
            out["run_with"] = checked.to_config()
    models = stored.get("agent_models_override")
    if isinstance(models, Mapping) and models:
        if "run_with" not in out:
            # The model of a run with dropped above is no longer the chosen one for every agent.
            models = {agent: model for agent, model in models.items() if agent != ALL_AGENTS}
        try:
            if models:
                await check_agent_models(request, dict(models))
        except HTTPException:
            notes.append("Its agents' models are not all usable now: it runs with the default models.")
        else:
            if models:
                out["agent_models_override"] = dict(models)
    return out, notes
