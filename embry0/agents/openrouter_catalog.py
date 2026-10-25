"""OpenRouter's live model list: every model it serves with tool calling, at its own prices.

The static ``OPENROUTER`` entry in providers.py names one model; OpenRouter
serves hundreds. ``embry0.services.openrouter_catalog`` reads
``GET /api/v1/models`` daily, keeps what an agent can run, stores it and
installs it here, and providers.py reads it for the model list, the prices
and the known-model checks.

Nothing here touches the network or the database, so the sandbox imports it
too. There no catalog is installed, and a run on a catalog model gets its
rates on its invocation instead (``AgentInvocation.pricing_usd_per_mtok``).

What is kept (``parse``):

- models whose ``supported_parameters`` include ``tools``: every agent but a
  bare chat one calls tools, and a model without them answers in prose and
  stops;
- at a price: ``pricing.prompt``/``pricing.completion`` are USD per token,
  kept per million tokens. The highest rate the entry names wins, its tiers
  (``overrides``: long prompts, hours of the day) and the separate rate for
  reasoning tokens included, so a job's budget never under-counts a run.

What is left out, by reason (``SKIP_REASONS``): no tool calling; the
``:batch`` variants (asynchronous, unusable for an interactive tool loop);
``:free`` variants (rate-limited to a few requests a minute, which an agent's
tool loop exceeds within a run); ``:online`` variants (a per-request search
fee the token price does not show); a negative price (OpenRouter's routers,
whose price is decided per request); a zero or missing price on a model
that is not free (it would run unmetered, #219).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

ROUTING_PREFIX: Final = "openrouter/"
"""embry0's routing prefix for OpenRouter; the upstream id is what follows it."""

EXCLUDED_VARIANTS: Final = {"batch": "batch", "free": "free", "online": "online"}
"""Variant suffix (after ``:``) -> skip reason."""

SKIP_REASONS: Final = ("no_tools", "batch", "free", "online", "variable_price", "unpriced", "malformed")


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """One model of the catalog, under embry0's routed id."""

    model: str
    name: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    context_length: int | None = None
    max_output_tokens: int | None = None

    @property
    def vendor(self) -> str:
        """The model's maker, the part of the upstream id before its slash (``anthropic``)."""
        upstream = self.model[len(ROUTING_PREFIX) :] if self.model.startswith(ROUTING_PREFIX) else self.model
        return upstream.split("/", 1)[0]

    def to_json(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "name": self.name,
            "input_usd_per_mtok": self.input_usd_per_mtok,
            "output_usd_per_mtok": self.output_usd_per_mtok,
            "context_length": self.context_length,
            "max_output_tokens": self.max_output_tokens,
        }

    @classmethod
    def from_json(cls, row: Mapping[str, Any]) -> CatalogModel | None:
        """A stored entry, or None when it is not one (a hand-edited or older row)."""
        try:
            model = str(row["model"])
            rate_in = float(row["input_usd_per_mtok"])
            rate_out = float(row["output_usd_per_mtok"])
        except (KeyError, TypeError, ValueError):
            return None
        if not model.startswith(ROUTING_PREFIX) or rate_in < 0 or rate_out < 0:
            return None
        return cls(
            model=model,
            name=str(row.get("name") or model),
            input_usd_per_mtok=rate_in,
            output_usd_per_mtok=rate_out,
            context_length=_positive_int(row.get("context_length")),
            max_output_tokens=_positive_int(row.get("max_output_tokens")),
        )


@dataclass(frozen=True, slots=True)
class Catalog:
    """The models in force, and when OpenRouter listed them."""

    models: Mapping[str, CatalogModel]
    fetched_at: datetime


_current: Catalog | None = None


def install(catalog: Catalog | None) -> None:
    """Put ``catalog`` in force for this process (None: back to the static entries)."""
    global _current
    _current = catalog


def current() -> Catalog | None:
    """The catalog in force, or None when none was ever loaded (the sandbox, a fresh deployment)."""
    return _current


def catalog_model(model: str) -> CatalogModel | None:
    catalog = _current
    return catalog.models.get(model) if catalog is not None else None


def catalog_rates(model: str) -> tuple[float, float] | None:
    """``model``'s (input, output) $/Mtok from the catalog, or None when it is not in it."""
    entry = catalog_model(model)
    return (entry.input_usd_per_mtok, entry.output_usd_per_mtok) if entry is not None else None


def catalog_model_ids() -> tuple[str, ...] | None:
    """The catalog's routed ids, sorted; None when no catalog is in force."""
    catalog = _current
    return tuple(sorted(catalog.models)) if catalog is not None else None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _rate(value: Any) -> float | None:
    """A USD-per-token string or number as $/Mtok; None when absent or not a number."""
    if value is None or value == "":
        return None
    try:
        return float(value) * 1_000_000
    except (TypeError, ValueError):
        return None


def _highest(rates: Iterable[float | None]) -> float | None:
    present = [r for r in rates if r is not None]
    return max(present) if present else None


def _rates_of(pricing: Mapping[str, Any]) -> tuple[float | None, float | None]:
    """The highest input and output $/Mtok the entry's pricing names, tiers included."""
    tiers: list[Mapping[str, Any]] = [pricing]
    overrides = pricing.get("overrides")
    if isinstance(overrides, list):
        tiers.extend(o for o in overrides if isinstance(o, Mapping))
    rate_in = _highest(_rate(t.get("prompt")) for t in tiers)
    # Reasoning tokens bill as output; where they have a rate of their own, the higher one counts.
    rate_out = _highest([*(_rate(t.get("completion")) for t in tiers), _rate(pricing.get("internal_reasoning"))])
    return rate_in, rate_out


def parse(payload: Any) -> tuple[list[CatalogModel], Counter[str]]:
    """The models an agent can run from OpenRouter's ``GET /models`` answer, and what was left out by reason."""
    skipped: Counter[str] = Counter()
    kept: dict[str, CatalogModel] = {}
    entries = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list):
        raise ValueError("OpenRouter's model list has no 'data' array")
    for entry in entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str) or "/" not in entry["id"]:
            skipped["malformed"] += 1
            continue
        upstream: str = entry["id"]
        if "tools" not in (entry.get("supported_parameters") or []):
            skipped["no_tools"] += 1
            continue
        variant = upstream.rsplit(":", 1)[1] if ":" in upstream else ""
        if variant in EXCLUDED_VARIANTS:
            skipped[EXCLUDED_VARIANTS[variant]] += 1
            continue
        pricing = entry.get("pricing")
        rate_in, rate_out = _rates_of(pricing) if isinstance(pricing, Mapping) else (None, None)
        if (rate_in is not None and rate_in < 0) or (rate_out is not None and rate_out < 0):
            skipped["variable_price"] += 1
            continue
        if not rate_in and not rate_out:
            skipped["unpriced"] += 1
            continue
        top_provider = entry.get("top_provider")
        top: Mapping[str, Any] = top_provider if isinstance(top_provider, Mapping) else {}
        model = f"{ROUTING_PREFIX}{upstream}"
        kept[model] = CatalogModel(
            model=model,
            name=str(entry.get("name") or upstream),
            input_usd_per_mtok=round(rate_in or 0.0, 6),
            output_usd_per_mtok=round(rate_out or 0.0, 6),
            context_length=_positive_int(entry.get("context_length")),
            max_output_tokens=_positive_int(top.get("max_completion_tokens")),
        )
    return [kept[m] for m in sorted(kept)], skipped
