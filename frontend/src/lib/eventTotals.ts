import type { LogEvent } from "@/lib/types";

/** Job totals as sums over runs (decision 5): a run's `cost_update` is its
 * delta and wins over its `turn_usage` partials; a run with no `cost_update`
 * yet contributes its partials (a `null` turn cost counts as 0). Turns are the
 * highest top-level `turn_start.turn_number` per run. Events without `run_id`
 * (pre-contract rows) form one pseudo-run. */
export interface Totals { costUsd: number; tokensIn: number; tokensOut: number; turns: number }

interface RunTotals {
  settled?: { cost: number; tokensIn: number; tokensOut: number };
  partialCost: number;
  partialIn: number;
  partialOut: number;
  maxTurn: number;
}

export interface TotalsState { runs: Map<string, RunTotals> }

export const emptyTotals = (): TotalsState => ({ runs: new Map() });

const runOf = (e: LogEvent) => (typeof e.run_id === "string" ? e.run_id : "");

const getRun = (state: TotalsState, id: string): RunTotals => {
  let r = state.runs.get(id);
  if (!r) { r = { partialCost: 0, partialIn: 0, partialOut: 0, maxTurn: 0 }; state.runs.set(id, r); }
  return r;
};

/** Mutates and returns `state`; callers that need immutability copy the Map first. */
export function applyTotals(state: TotalsState, event: LogEvent): TotalsState {
  switch (event.type) {
    case "cost_update": {
      const r = getRun(state, runOf(event));
      const prev = r.settled ?? { cost: 0, tokensIn: 0, tokensOut: 0 };
      r.settled = { cost: prev.cost + event.cost_usd, tokensIn: prev.tokensIn + event.tokens_in, tokensOut: prev.tokensOut + event.tokens_out };
      return state;
    }
    case "turn_usage": {
      const r = getRun(state, runOf(event));
      r.partialCost += event.cost_usd ?? 0;
      r.partialIn += event.input_tokens;
      r.partialOut += event.output_tokens;
      return state;
    }
    case "turn_start": {
      // The contract allows `parent_tool_use_id` on any event; the executor sets it
      // on no `turn_start`, so this is defence only.
      if (event.parent_tool_use_id) return state;
      const r = getRun(state, runOf(event));
      if (event.turn_number > r.maxTurn) r.maxTurn = event.turn_number;
      return state;
    }
    default:
      return state;
  }
}

export function totalsOf(state: TotalsState): Totals {
  const out: Totals = { costUsd: 0, tokensIn: 0, tokensOut: 0, turns: 0 };
  for (const r of state.runs.values()) {
    if (r.settled) { out.costUsd += r.settled.cost; out.tokensIn += r.settled.tokensIn; out.tokensOut += r.settled.tokensOut; }
    else if (r.partialIn || r.partialOut || r.partialCost) { out.costUsd += r.partialCost; out.tokensIn += r.partialIn; out.tokensOut += r.partialOut; }
    out.turns += r.maxTurn;
  }
  return out;
}

export function reduceTotals(events: readonly LogEvent[]): Totals {
  const state = emptyTotals();
  for (const e of events) applyTotals(state, e);
  return totalsOf(state);
}
