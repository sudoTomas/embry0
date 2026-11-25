"""What an agent run is charged, from the metering proxies' tallies and the sandbox's own figure.

A run whose provider's calls all pass a metering proxy (an xAI run through the xai-proxy, a local model's through
the local-llm-proxy) is charged what that proxy counted, at the rates its provider record carried from the run's
start; the sandbox's figure is kept beside it, and compared. Any other run is charged what its sandbox reported, as
before. Counting fails toward more, never less:

- a call in flight when its tally was last read (at the run's end, or before its proxy process went away) is
  charged its ceiling, as the proxy reported it;
- a run whose proxy's tally is not whole (the proxy process changed during the run, the run's end could not be
  read, or the run was never declared on it) is charged the larger of the proxy's count and the sandbox's figure.

Pure: :class:`RunMetering` is what ``embry0.execution.run_meter`` gathered; nothing here reads or writes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from embry0.agents.invocation import ProviderRecord

__all__ = [
    "DISAGREEMENT_FLOOR_USD",
    "DISAGREEMENT_SHARE",
    "PROXY_PROVIDERS",
    "BootTally",
    "Charge",
    "RunMetering",
    "charge_run",
]

#: The provider each metering proxy carries the calls of (a provider record's ``name``).
PROXY_PROVIDERS: dict[str, str] = {"xai-proxy": "xai", "local-llm-proxy": "custom"}
#: The two figures disagree when they differ by more than this share of the larger one, and by more than
#: :data:`DISAGREEMENT_FLOOR_USD`.
DISAGREEMENT_SHARE = 0.10
DISAGREEMENT_FLOOR_USD = 0.05

_TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
_COUNTS = (*_TOKENS, "cache_creation_1h_tokens", "web_search_requests", "web_fetch_requests")


@dataclass(frozen=True)
class BootTally:
    """One proxy process's tally of a run: the latest read of it, or its final word at the run's end."""

    proxy: str
    boot_id: str
    snapshot: Mapping[str, Any]
    final: bool


@dataclass(frozen=True)
class RunMetering:
    """What the metering proxies said of one run."""

    run_id: str
    #: The metering proxies the run's sandbox was enrolled with when the run began.
    targets: tuple[str, ...] = ()
    #: The proxies the run was declared on, each with the boot id it was declared under.
    declared: Mapping[str, str] = field(default_factory=dict)
    tallies: tuple[BootTally, ...] = ()
    #: Declared proxies whose final tally could not be read at the run's end.
    unreached: tuple[str, ...] = ()


@dataclass(frozen=True)
class Charge:
    """A run's charge and how it was reached."""

    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    sandbox_cost_usd: float
    #: For the trace's ``metering`` column.
    metering: dict[str, Any]
    #: ``sandbox_higher`` or ``proxy_higher`` when the two figures disagree, else None.
    disagreement: str | None = None

    @property
    def source(self) -> str:
        return str(self.metering["source"])


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _account(proxy: str, tallies: list[BootTally], metering: RunMetering) -> dict[str, Any]:
    """One proxy's account of the run, every process of it summed, with each call left in flight at its ceiling."""
    models: dict[str, dict[str, int]] = {}
    requests = estimated = errors = open_calls = 0
    for tally in tallies:
        snap = tally.snapshot
        raw_models = snap.get("models")
        for model, entry in raw_models.items() if isinstance(raw_models, Mapping) else ():
            if not isinstance(entry, Mapping):
                continue
            into = models.setdefault(str(model), dict.fromkeys(_COUNTS, 0))
            for name in _COUNTS:
                into[name] += _count(entry.get(name))
            requests += _count(entry.get("requests"))
            estimated += _count(entry.get("estimated"))
        errors += _count(snap.get("errors"))
        raw_open = snap.get("open")
        for call in raw_open if isinstance(raw_open, list) else ():
            if not isinstance(call, Mapping):
                continue
            into = models.setdefault(str(call.get("model") or "unknown"), dict.fromkeys(_COUNTS, 0))
            for name in _COUNTS:
                into[name] += _count(call.get(name))
            open_calls += 1
    boots = sorted({t.boot_id for t in tallies})
    finals = [t for t in tallies if t.final]
    # Whole: declared, its end read, and one process all along: the one the run was declared under (a process that
    # went away before any read leaves no tally, only a final from another boot).
    whole = (
        proxy in metering.declared
        and proxy not in metering.unreached
        and len(finals) == 1
        and boots == [finals[0].boot_id]
        and metering.declared[proxy] == finals[0].boot_id
    )
    return {
        "boots": boots,
        "whole": whole,
        "requests": requests,
        "estimated": estimated,
        "errors": errors,
        "open_at_ceiling": open_calls,
        "models": models,
    }


def _totals(account: Mapping[str, Any]) -> dict[str, int]:
    totals = dict.fromkeys(_TOKENS, 0)
    for entry in account["models"].values():
        for name in _TOKENS:
            totals[name] += entry[name]
    return totals


def charge_run(
    metering: RunMetering | None,
    record: ProviderRecord | None,
    *,
    sandbox_cost_usd: float,
    sandbox_tokens: tuple[int, int, int, int],
) -> Charge:
    """The run's charge: the proxy's count when the run's provider's calls pass a metering proxy, the sandbox's
    figure otherwise, the larger of the two when the proxy's tally is not whole."""
    sandbox_cost = float(sandbox_cost_usd or 0.0)
    targets = metering.targets if metering is not None else ()
    proxies: dict[str, dict[str, Any]] = {}
    if metering is not None:
        for proxy in sorted(set(targets) | {t.proxy for t in metering.tallies} | set(metering.declared)):
            proxies[proxy] = _account(proxy, [t for t in metering.tallies if t.proxy == proxy], metering)
    serving = [p for p in targets if record is not None and PROXY_PROVIDERS.get(p) == record.name]
    report: dict[str, Any] = {
        "source": "sandbox",
        "expected": bool(serving),
        "gap": False,
        "proxy_cost_usd": None,
        "sandbox_cost_usd": sandbox_cost,
        "disagreement": None,
        "proxies": proxies,
    }

    def _sandbox(source: str) -> Charge:
        report["source"] = source
        return Charge(
            sandbox_cost,
            *sandbox_tokens,
            sandbox_cost_usd=sandbox_cost,
            metering=report,
            disagreement=report["disagreement"],
        )

    if not serving or record is None:
        return _sandbox("sandbox")
    account = proxies[serving[0]]
    totals = _totals(account)
    proxy_cost = record.price(
        input_tokens=totals["input_tokens"],
        output_tokens=totals["output_tokens"],
        cache_read_tokens=totals["cache_read_tokens"],
        cache_creation_tokens=totals["cache_creation_tokens"],
    )
    if proxy_cost is None:
        return _sandbox("sandbox_unpriced")
    gap = not account["whole"]
    report["proxy_cost_usd"] = proxy_cost
    report["gap"] = gap
    difference = sandbox_cost - proxy_cost
    if abs(difference) > max(DISAGREEMENT_FLOOR_USD, DISAGREEMENT_SHARE * max(sandbox_cost, proxy_cost)):
        report["disagreement"] = "sandbox_higher" if difference > 0 else "proxy_higher"
    if gap and sandbox_cost > proxy_cost:
        return _sandbox("sandbox_gap")
    report["source"] = "proxy_gap" if gap else "proxy"
    return Charge(
        proxy_cost,
        totals["input_tokens"],
        totals["output_tokens"],
        totals["cache_read_tokens"],
        totals["cache_creation_tokens"],
        sandbox_cost_usd=sandbox_cost,
        metering=report,
        disagreement=report["disagreement"],
    )
