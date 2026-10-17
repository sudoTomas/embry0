import { describe, it, expect } from "vitest";
import { reduceTotals } from "../eventTotals";
import type { LogEvent } from "@/lib/types";
import { loadFixture } from "./fixtures";

const t = "2026-01-01T00:00:00Z";
const usage = (run: string, cost: number | null, tin: number, tout: number) =>
  ({ type: "turn_usage", run_id: run, cost_usd: cost, input_tokens: tin, output_tokens: tout, cache_read_tokens: 0, cache_creation_tokens: 0, model: "m", timestamp: t }) as LogEvent;
const cost = (run: string, c: number, tin: number, tout: number, turns: number) =>
  ({ type: "cost_update", run_id: run, cost_usd: c, tokens_in: tin, tokens_out: tout, num_turns: turns, duration_ms: 1, duration_api_ms: 1, cache_read_tokens: 0, cache_creation_tokens: 0, timestamp: t }) as LogEvent;
const turn = (run: string, n: number) => ({ type: "turn_start", run_id: run, turn_number: n, model: "m", timestamp: t }) as LogEvent;

describe("reduceTotals", () => {
  it("sums cost_update across runs instead of keeping the last one", () => {
    const r = reduceTotals([cost("r1", 0.004, 100, 10, 1), cost("r2", 0.048, 1000, 100, 2)]);
    expect(r.costUsd).toBeCloseTo(0.052, 6);
    expect(r.tokensIn).toBe(1100);
    expect(r.tokensOut).toBe(110);
  });

  it("adds an in-flight run's turn_usage until its cost_update arrives, then uses the cost_update", () => {
    const partial = reduceTotals([cost("r1", 0.004, 100, 10, 1), usage("r2", 0.02, 500, 50), usage("r2", 0.01, 300, 30)]);
    expect(partial.costUsd).toBeCloseTo(0.034, 6);
    expect(partial.tokensIn).toBe(900);
    expect(partial.runsInFlight).toBe(1);
    const settled = reduceTotals([cost("r1", 0.004, 100, 10, 1), usage("r2", 0.02, 500, 50), usage("r2", 0.01, 300, 30), cost("r2", 0.048, 1000, 100, 2)]);
    expect(settled.costUsd).toBeCloseTo(0.052, 6);
    expect(settled.tokensIn).toBe(1100);
    expect(settled.runsInFlight).toBe(0);
  });

  it("treats a null turn_usage cost as 0 and still counts its tokens", () => {
    const r = reduceTotals([usage("r1", null, 700, 60)]);
    expect(r.costUsd).toBe(0);
    expect(r.tokensIn).toBe(700);
    expect(r.tokensOut).toBe(60);
  });

  it("counts turns as the highest turn_number per run, summed over runs", () => {
    const r = reduceTotals([turn("r1", 1), turn("r1", 2), turn("r2", 1), turn("r1", 2)]);
    expect(r.turns).toBe(3);
  });

  it("puts legacy events without run_id in one pseudo-run", () => {
    const legacy = (c: number) => ({ type: "cost_update", cost_usd: c, tokens_in: 1, tokens_out: 1, num_turns: 1, duration_ms: 0, duration_api_ms: 0, cache_read_tokens: 0, cache_creation_tokens: 0, timestamp: t }) as LogEvent;
    const r = reduceTotals([legacy(0.01), legacy(0.02), usage("", 5, 1, 1)]);
    expect(r.costUsd).toBeCloseTo(0.03, 6);
  });

  it("sums cost and turns over two runs of one retried node", () => {
    const dev = (e: LogEvent) => ({ ...e, node: "developer" }) as LogEvent;
    const r = reduceTotals([
      dev(turn("run-a", 1)), dev(turn("run-a", 2)), dev(cost("run-a", 0.01, 100, 10, 2)),
      dev(turn("run-b", 1)), dev(turn("run-b", 2)), dev(turn("run-b", 3)), dev(cost("run-b", 0.02, 200, 20, 3)),
    ]);
    expect(r.costUsd).toBeCloseTo(0.03, 6);
    expect(r.tokensIn).toBe(300);
    expect(r.tokensOut).toBe(30);
    expect(r.turns).toBe(5);
  });

  it("does not count a subagent's turn_start towards its run's turns", () => {
    const sub = { ...turn("r1", 4), parent_tool_use_id: "toolu-parent" } as LogEvent;
    const r = reduceTotals([turn("r1", 1), turn("r1", 2), sub]);
    expect(r.turns).toBe(2);
  });

  it("matches job_finished.total_cost_usd over the issue-to-pr fixture", () => {
    const events = loadFixture("issue-to-pr");
    const finished = events.find((e) => e.type === "job_finished");
    expect(finished?.type === "job_finished" && finished.total_cost_usd).toBeCloseTo(0.062, 6);
    const r = reduceTotals(events);
    expect(r.costUsd).toBeCloseTo(0.062, 6);
    expect(r.tokensIn).toBe(1200 + 12000 + 2000 + 900);
    expect(r.tokensOut).toBe(80 + 1000 + 150 + 60);
    expect(r.turns).toBe(5);
    expect(r.runsInFlight).toBe(0);
  });
});
