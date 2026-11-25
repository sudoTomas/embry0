"""What a run that never ended is charged, and the fields that close its trace (#694).

A run's trace is written ``running`` before its exec and finished after it. A run whose process stopped first (an
orchestrator restart) or whose task was cancelled never reaches that finish; its trace is closed here instead, at
start-up for the first (``embry0.services.interrupted_runs``) and at the cancel for the second
(``embry0.orchestration.nodes.agent``). It is charged what is known of it, failing toward more:

- a run the metering proxies counted: from the tallies kept of it (``run_meter``), every proxy process summed and each
  call in flight at its ceiling. Its end was never read, so its tally is never whole, and it is charged the larger of
  that and the sandbox's figure (``embry0.agents.run_charge``);
- any other run: the sandbox's last figure, from the run's own events: its ``cost_update`` when one came, else its
  ``turn_usage`` events, each model call once;
- a run with neither: $0, and its trace says so (``metering.closed.charged_from = "nothing"``).

Pure: the callers read the events and the kept tallies, and write what this returns.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from embry0.agents.invocation import ProviderRecord
from embry0.agents.run_charge import BootTally, RunMetering, charge_run
from embry0.safety.error_codes import ErrorCode

__all__ = ["CAUSES", "CHARGED_FROM", "Closing", "SandboxFigure", "close_run", "metering_from_kept", "sandbox_figure"]

#: Why a run's trace is closed here: the orchestrator stopped while it ran, or its task was cancelled.
CAUSES = ("restart", "cancelled")
#: What a closed run was charged from.
CHARGED_FROM = ("proxy", "sandbox", "nothing")

Cause = Literal["restart", "cancelled"]


@dataclass(frozen=True)
class SandboxFigure:
    """What the run's sandbox reported of its spend, from its events."""

    cost_usd: float
    #: (input, output, cache read, cache creation) tokens.
    tokens: tuple[int, int, int, int]
    #: Model calls the figure counts.
    calls: int
    #: Calls whose cost neither their event nor the run's provider record could give: their tokens count, their cost
    #: does not.
    unpriced_calls: int
    #: What the run's ``cost_update`` already added to its job's total, when one came.
    reported_usd: float | None
    #: ``cost_update``, ``turn_usage`` or ``none``.
    source: str


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _cost(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    amount = float(value)
    return amount if math.isfinite(amount) and amount >= 0 else None


def sandbox_figure(events: Iterable[Mapping[str, Any]], record: ProviderRecord | None) -> SandboxFigure:
    """The sandbox's last figure of a run from its events, in the order they came.

    A ``cost_update`` is the run's own total and settles it. Without one, the ``turn_usage`` events are put together
    into model calls. One call's events are those of one parent (the run, or a subagent under its tool call) up to
    that parent's next ``tool_result``: a model call's answer comes before the result of the tool it asked for, and
    the next call after it, so a ``tool_result`` always lies between two calls of one parent. A sandbox sends one
    call's usage in one of two forms:

    - an image since #676 sends what each message adds to the response's usage, so the call's events sum to it and
      its later blocks add no input;
    - an older image sent each content block's message with the whole response's usage again: the same input side
      (model, input, cache read and cache creation tokens), at the most output and cost any of them carries.

    So within a call an event with the call's own input side (never all zero for a real call) is a repeat, folded
    at its largest; one adding no input is an addition, summed; one with another input side is the next call. A call
    whose events do not all carry a cost is priced at ``record``'s rates when it has some.
    """
    calls: list[dict[str, Any]] = []
    open_call: dict[str, dict[str, Any]] = {}
    for event in events:
        kind = event.get("type")
        if kind == "cost_update":
            reported = _cost(event.get("cost_usd"))
            return SandboxFigure(
                cost_usd=reported or 0.0,
                tokens=(
                    _count(event.get("tokens_in")),
                    _count(event.get("tokens_out")),
                    _count(event.get("cache_read_tokens")),
                    _count(event.get("cache_creation_tokens")),
                ),
                calls=_count(event.get("num_turns")),
                unpriced_calls=0,
                reported_usd=reported,
                source="cost_update",
            )
        parent = str(event.get("parent_tool_use_id") or "")
        if kind == "tool_result":
            open_call.pop(parent, None)  # that parent's call is answered; its next usage is its next call
            continue
        if kind != "turn_usage":
            continue
        model = str(event.get("model") or "")
        side = (
            _count(event.get("input_tokens")),
            _count(event.get("cache_read_tokens")),
            _count(event.get("cache_creation_tokens")),
        )
        output, cost = _count(event.get("output_tokens")), _cost(event.get("cost_usd"))
        call = open_call.get(parent)
        if call is not None and any(side) and (model, side) == (call["model"], call["side"]):
            call["output"] = max(call["output"], output)  # an older image's repeat of the whole response
            call["cost"] = None if cost is None or call["cost"] is None else max(call["cost"], cost)
            continue
        if call is not None and not any(side):
            call["output"] += output  # what one more block of the response adds
            call["cost"] = None if cost is None or call["cost"] is None else call["cost"] + cost
            continue
        call = {"model": model, "side": side, "output": output, "cost": cost}
        calls.append(call)
        open_call[parent] = call
    total = 0.0
    tokens = [0, 0, 0, 0]
    unpriced = 0
    for call in calls:
        input_tokens, cache_read, cache_creation = call["side"]
        for index, value in enumerate((input_tokens, call["output"], cache_read, cache_creation)):
            tokens[index] += value
        cost = call["cost"]
        if cost is None and record is not None:
            cost = record.price(
                input_tokens=input_tokens,
                output_tokens=call["output"],
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_creation,
            )
        if cost is None:
            unpriced += 1
        else:
            total += cost
    return SandboxFigure(
        cost_usd=total,
        tokens=(tokens[0], tokens[1], tokens[2], tokens[3]),
        calls=len(calls),
        unpriced_calls=unpriced,
        reported_usd=None,
        source="turn_usage" if calls else "none",
    )


def metering_from_kept(
    run_id: str,
    kept: Iterable[Mapping[str, Any]],
    *,
    targets: tuple[str, ...] = (),
    declared: Mapping[str, str] | None = None,
) -> RunMetering | None:
    """The metering proxies' account of a run from the tallies kept of it (``RunMeterRepository.for_run`` rows);
    None when none was kept. ``targets`` and ``declared`` are the run's when its caller still knows them, else the
    proxies the tallies came from. A proxy with no final tally of the run is unreached: the run's end was not read
    there, so its tally is never taken as whole."""
    tallies = tuple(
        BootTally(str(row["proxy"]), str(row["boot_id"]), row["snapshot"], final=bool(row.get("final")))
        for row in kept
        if isinstance(row.get("snapshot"), Mapping)
    )
    if not tallies:
        return None
    proxies = sorted({t.proxy for t in tallies})
    finals = {t.proxy for t in tallies if t.final}
    return RunMetering(
        run_id=run_id,
        targets=targets or tuple(proxies),
        declared=dict(declared or {}),
        tallies=tallies,
        unreached=tuple(p for p in sorted(set(proxies) | set(declared or {})) if p not in finals),
    )


@dataclass(frozen=True)
class Closing:
    """How a run that never ended is closed."""

    #: ``TracesRepository.finish`` fields, but ``tools_called`` and ``skill_actions``.
    fields: dict[str, Any]
    #: ``proxy``, ``sandbox`` or ``nothing``.
    charged_from: str
    #: What the close adds to the job's total: the charge, less what the run's ``cost_update`` already added.
    delta_usd: float
    #: Model calls the sandbox's figure counts.
    calls: int = 0

    def cost_update(self, *, run_id: str, node: str, agent: str) -> dict[str, Any]:
        """The run's ``cost_update`` as it would have sent one, carrying :attr:`delta_usd`: the one event that moves
        its job's total (``embry0.services.job_events``)."""
        from embry0.execution import events

        fields = self.fields
        return events.cost_update(
            cost_usd=self.delta_usd,
            duration_ms=int(fields["duration_ms"]),
            duration_api_ms=0,
            num_turns=self.calls,
            tokens_in=int(fields["input_tokens"]),
            tokens_out=int(fields["output_tokens"]),
            cache_read_tokens=int(fields["cache_read_tokens"]),
            cache_creation_tokens=int(fields["cache_creation_tokens"]),
            run_id=run_id,
            node=node,
            agent=agent,
        )


_REASONS = {
    "proxy": "charged the metering proxies' count of its calls, each call in flight at its ceiling",
    "cost_update": "charged the sandbox's figure, from the run's cost_update",
    "turn_usage": "charged the sandbox's last figure, from the run's turn_usage events, each model call once",
    "nothing": "no tally of its calls and no usage event: closed at $0, what it spent is not known",
}
_SUMMARIES = {
    "restart": "The orchestrator stopped before the run ended; the run was closed at start-up.",
    "cancelled": "The run was cancelled before it ended.",
}


def close_run(
    *,
    cause: Cause,
    record: ProviderRecord | None,
    metering: RunMetering | None,
    figure: SandboxFigure,
    duration_ms: int,
) -> Closing:
    """The charge of a run that never ended, and the fields that close its trace."""
    charge = charge_run(metering, record, sandbox_cost_usd=figure.cost_usd, sandbox_tokens=figure.tokens)
    if charge.source.startswith("proxy"):
        charged_from = "proxy"
    elif figure.source != "none":
        charged_from = "sandbox"
    else:
        charged_from = "nothing"
    reason = _REASONS["proxy" if charged_from == "proxy" else figure.source if charged_from == "sandbox" else "nothing"]
    closed = {
        "cause": cause,
        "charged_from": charged_from,
        "sandbox_figure_from": figure.source,
        "calls": figure.calls,
        "unpriced_calls": figure.unpriced_calls,
        "reason": reason,
    }
    fields = {
        "result": "error",
        "error_code": ErrorCode.RUN_INTERRUPTED.value,
        "cost_usd": charge.cost_usd,
        "input_tokens": charge.input_tokens,
        "output_tokens": charge.output_tokens,
        "cache_read_tokens": charge.cache_read_tokens,
        "cache_creation_tokens": charge.cache_creation_tokens,
        "metering": {**charge.metering, "closed": closed},
        "sandbox_cost_usd": charge.sandbox_cost_usd,
        "duration_ms": max(0, int(duration_ms)),
        "result_summary": f"{_SUMMARIES[cause]} It was {reason}.",
        "stderr_tail": None,
    }
    return Closing(
        fields=fields,
        charged_from=charged_from,
        delta_usd=max(0.0, charge.cost_usd - (figure.reported_usd or 0.0)),
        calls=figure.calls,
    )
