"""Provider fallback: the model an agent runs on when its own model's provider has no usable account.

docs/superpowers/specs/2026-10-25-provider-fallback-design.md. The deployment's
fallback order (``provider_config.fallback_models``, ``[]`` is off) names
models. The account pre-check finds the job's paused providers and plans one
stand-in model for each (``embry0.services.credentials.fallback``); the
resolver applies the plan to every agent run, and the agent node records it.

Pure, so the pre-check, the agent node and the tests share one answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from embry0.safety.error_codes import ErrorCode

#: Codes that pause the provider a run failed on for the rest of its job:
#: the account is spent or rate-limited, so the next run would fail the same way.
ACCOUNT_PAUSE_REASONS: dict[str, str] = {
    ErrorCode.PROVIDER_QUOTA_EXHAUSTED.value: "quota_exhausted",
    ErrorCode.PROVIDER_RATE_LIMITED.value: "rate_limited",
}


@dataclass(frozen=True)
class Pause:
    """Why ``provider`` cannot serve the job now, and when it may again (None: not known)."""

    provider: str
    reason: str
    until: datetime | None = None


@dataclass(frozen=True)
class ModelFallback:
    """The model the agents of a paused provider run on instead, and why."""

    paused_provider: str
    model: str
    provider: str
    reason: str
    until: str | None = None

    def to_config(self) -> dict[str, Any]:
        """The JSON shape the graph's ``configurable["model_fallbacks"]`` carries per paused provider."""
        return {"model": self.model, "provider": self.provider, "reason": self.reason, "until": self.until}

    @classmethod
    def from_config(cls, paused_provider: str, raw: Any) -> ModelFallback | None:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("model"), str) or not raw["model"]:
            return None
        return cls(
            paused_provider=paused_provider,
            model=raw["model"],
            provider=str(raw.get("provider") or model_provider(raw["model"])),
            reason=str(raw.get("reason") or "paused"),
            until=str(raw["until"]) if raw.get("until") else None,
        )

    @classmethod
    def for_pause(cls, pause: Pause, model: str) -> ModelFallback:
        return cls(
            paused_provider=pause.provider,
            model=model,
            provider=model_provider(model),
            reason=pause.reason,
            until=pause.until.isoformat() if pause.until is not None else None,
        )

    def trace_record(self, instead_of: str) -> dict[str, Any]:
        """What ``traces.fallback`` (and ``AgentInvocation.fallback``) holds for a run on this model."""
        return {"instead_of": instead_of, "provider": self.paused_provider, "reason": self.reason, "until": self.until}


def describe(agent_type: str, model: str, record: Mapping[str, Any]) -> str:
    """A substituted run in words, for the job's events: which model ran, which it stood in for, and why.

    ``record`` is a :meth:`ModelFallback.trace_record`.
    """
    until = ""
    if record.get("until"):
        try:
            until = f" until {datetime.fromisoformat(str(record['until'])).strftime('%Y-%m-%d %H:%M %Z').strip()}"
        except ValueError:
            until = f" until {record['until']}"
    return (
        f"{agent_type} runs on {model} instead of {record.get('instead_of')}: "
        f"the {record.get('provider')} account is paused ({record.get('reason')}){until}."
    )


def model_provider(model: str) -> str:
    """The provider that serves ``model``: a routed provider's name, else anthropic."""
    from embry0.agents.providers import provider_for_model

    provider = provider_for_model(model)
    return provider.name if provider is not None else "anthropic"


def fallback_order(model_policy: Mapping[str, Any] | None) -> list[str]:
    """The policy's fallback order, cleaned: non-empty ids, first occurrence kept."""
    raw = (model_policy or {}).get("fallback_models")
    if not isinstance(raw, (list, tuple)):
        return []
    order: list[str] = []
    for model in raw:
        if isinstance(model, str) and model.strip() and model.strip() not in order:
            order.append(model.strip())
    return order


def runnable_fallback_models(order: Iterable[str], disabled: Iterable[str]) -> list[str]:
    """The models of ``order`` an agent may run on: catalogue, priced, with tool calling, not disabled.

    ``PUT /config/provider`` refuses the rest, so this only drops what changed
    after the order was saved (a model disabled or no longer priced, a
    provider no longer enabled).
    """
    from embry0.agents.providers import chat_only_models, known_models, unpriced_models

    off = set(disabled)
    models = [m for m in order if m in known_models() and m not in off]
    unrunnable = set(unpriced_models(models)) | set(chat_only_models(models))
    return [m for m in models if m not in unrunnable]


def candidates(paused_provider: str, order: Sequence[str], skip: Iterable[str] = ()) -> Iterator[tuple[str, str]]:
    """``(model, provider)`` for each model of ``order``, in order, served by neither
    ``paused_provider`` nor a provider in ``skip``."""
    skipped = {paused_provider, *skip}
    for model in order:
        provider = model_provider(model)
        if provider not in skipped:
            yield model, provider


def substitute(model: str, fallbacks: Mapping[str, ModelFallback] | None) -> ModelFallback | None:
    """The stand-in for ``model`` when its provider is paused, else None."""
    if not fallbacks:
        return None
    fallback = fallbacks.get(model_provider(model))
    if fallback is None or fallback.model == model:
        return None
    return fallback


def fallbacks_from_config(raw: Any) -> dict[str, ModelFallback]:
    """``configurable["model_fallbacks"]`` read back; anything malformed is dropped."""
    if not isinstance(raw, Mapping):
        return {}
    plan: dict[str, ModelFallback] = {}
    for provider, entry in raw.items():
        fallback = ModelFallback.from_config(str(provider), entry)
        if fallback is not None:
            plan[str(provider)] = fallback
    return plan


def paused_by_outputs(outputs: Iterable[Mapping[str, Any]]) -> dict[str, Pause]:
    """The providers whose latest run in ``outputs`` ended on a spent or rate-limited account.

    ``outputs`` are the job's ``agent_outputs`` in order; each carries the
    ``provider`` that served it. A later run on the same provider that did not
    fail that way clears it.
    """
    from embry0.execution.agent_runner import aware_datetime

    paused: dict[str, Pause] = {}
    for output in outputs:
        provider = output.get("provider") if isinstance(output, Mapping) else None
        if not isinstance(provider, str) or not provider:
            continue
        reason = ACCOUNT_PAUSE_REASONS.get(str(output.get("error_code") or ""))
        if reason is None:
            paused.pop(provider, None)
        else:
            paused[provider] = Pause(provider, reason, aware_datetime(output.get("provider_resets_at")))
    return paused


def replan(
    plan: Mapping[str, ModelFallback],
    paused_now: Mapping[str, Pause],
    order: Sequence[str],
    ready: Iterable[str],
) -> dict[str, ModelFallback]:
    """The plan for the next agent run: the pre-check's, plus each provider a run of the job failed on since.

    A stand-in is only ever a provider in ``ready``, the providers whose keys
    the sandbox holds: a key cannot reach a container after it exists (a
    resume into a sandbox created before the pause keeps its old keys). A
    planned stand-in that is not there, or that has itself failed since,
    moves to the next ready model of the order.
    """
    available = set(ready)
    paused = set(plan) | set(paused_now)
    out: dict[str, ModelFallback] = {}
    for provider in sorted(paused):
        kept = plan.get(provider)
        if kept is not None and kept.provider in available and kept.provider not in paused_now:
            out[provider] = kept
            continue
        for model, other in candidates(provider, order, skip=paused):
            if other not in available:
                continue
            if kept is not None:
                out[provider] = replace(kept, model=model, provider=other)
            else:
                out[provider] = ModelFallback.for_pause(paused_now[provider], model)
            break
    return out
