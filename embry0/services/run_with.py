"""Run with: the model and the key a person chose for one job (spec 2026-10-25).

Every dashboard action that starts agent work takes an optional ``run_with``:
a model, which every agent of the job then runs, and a stored LLM key, which
then serves that key's provider for the job instead of the one its bindings
would pick. Leaving both out is the usual run.

The choice rides on the job's ``pipeline_config``:

- ``run_with``: what was chosen, ``{model, credential_id, provider,
  credential_label}``, for the job page to show;
- ``agent_models_override["*"]``: the model, below any agent the job names
  itself (``embry0.agents.resolver``);
- the pin, read back through :func:`pins_of` by the credential resolver,
  which serves the chosen key for its provider and fails the job when that
  key cannot run, rather than serving another one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

#: The ``agent_models_override`` key that names the model for every agent of a job.
ALL_AGENTS = "*"
#: The credential kinds a key for an LLM provider has.
LLM_KINDS = ("llm_api_key", "llm_oauth")


@dataclass(frozen=True)
class RunWith:
    """A checked choice: ``provider`` is the key's, ``credential_label`` its name on the Credentials page."""

    model: str | None = None
    credential_id: str | None = None
    provider: str | None = None
    credential_label: str | None = None

    def is_default(self) -> bool:
        return not self.model and not self.credential_id

    def to_config(self) -> dict[str, str]:
        return {k: v for k, v in self.__dict__.items() if v}


def model_provider(model: str) -> str:
    """The provider that serves ``model``: a routed provider's name, else anthropic."""
    from embry0.agents.providers import provider_for_model

    provider = provider_for_model(model)
    return provider.name if provider is not None else "anthropic"


def apply_run_with(config: Mapping[str, Any] | None, run_with: RunWith | None) -> dict[str, Any] | None:
    """``config`` carrying ``run_with``; unchanged (None stays None) for the usual run.

    The chosen model fills ``agent_models_override["*"]``: an agent the
    request names itself keeps its own model.
    """
    if run_with is None or run_with.is_default():
        return dict(config) if config is not None else None
    out = dict(config or {})
    out["run_with"] = run_with.to_config()
    if run_with.model:
        out["agent_models_override"] = {ALL_AGENTS: run_with.model, **(out.get("agent_models_override") or {})}
    return out


def models_with(agent_models: Mapping[str, str] | None, run_with: RunWith | None) -> dict[str, str] | None:
    """``agent_models`` with the chosen model for every agent the request does not name itself."""
    if run_with is None or not run_with.model:
        return dict(agent_models) if agent_models else None
    return {ALL_AGENTS: run_with.model, **(agent_models or {})}


def run_with_of(config: Mapping[str, Any] | None) -> RunWith | None:
    """The choice a job's ``pipeline_config`` carries, or None."""
    raw = (config or {}).get("run_with") if isinstance(config, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    fields = {k: str(raw[k]) for k in ("model", "credential_id", "provider", "credential_label") if raw.get(k)}
    chosen = RunWith(**fields)
    return None if chosen.is_default() else chosen


def pins_of(config: Mapping[str, Any] | None) -> dict[str, str]:
    """The keys a job's ``pipeline_config`` pins: provider to credential id."""
    chosen = run_with_of(config)
    if chosen is None or not chosen.credential_id or not chosen.provider:
        return {}
    return {chosen.provider: chosen.credential_id}


#: Reads a job's pins by its id (the credential resolver's view of the jobs table).
JobPins = Callable[[str], Awaitable[Mapping[str, str]]]


def job_pins_reader(jobs: Any) -> JobPins:
    """:data:`JobPins` over the jobs repository: a job that does not exist pins nothing."""

    async def read(job_id: str) -> Mapping[str, str]:
        job = await jobs.get(job_id)
        return pins_of((job or {}).get("pipeline_config"))

    return read


def key_scopes(repo: str | None) -> list[tuple[str, str]]:
    """The binding scopes whose LLM keys a job on ``repo`` may use: the repo, its owner, and the global pool."""
    scopes: list[tuple[str, str]] = []
    if repo and "/" in repo:
        owner = repo.split("/", 1)[0].lower()
        scopes += [("repo", repo.lower()), ("owner", f"github:{owner}"), ("owner", f"gitlab:{owner}")]
    scopes.append(("global", ""))
    return scopes
