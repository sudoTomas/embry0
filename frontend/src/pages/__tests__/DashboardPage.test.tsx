import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";

// React-query result shape used by both useStats and useAgentStats.
type FakeQuery<T> = {
  data: T | undefined;
  isPending: boolean;
  isError: boolean;
};

const mockUseStats = vi.fn();
const mockUseAgentStats = vi.fn();

vi.mock("@/hooks/useStats", () => ({
  useStats: () => mockUseStats(),
}));

vi.mock("@/hooks/useAgentStats", () => ({
  useAgentStats: () => mockUseAgentStats(),
}));

import { DashboardPage } from "../DashboardPage";

function ready<T>(data: T): FakeQuery<T> {
  return { data, isPending: false, isError: false };
}
function loading<T>(): FakeQuery<T> {
  return { data: undefined, isPending: true, isError: false };
}
function errored<T>(): FakeQuery<T> {
  return { data: undefined, isPending: false, isError: true };
}

const ORCH_OK = ready({
  total_issues: 10,
  total_jobs: 20,
  completed: 18,
  failed: 2,
  success_rate: 0.9,
  total_cost_usd: 12.5,
  cost_by_tier: {},
  failure_categories: {},
  success_rate_by_tier: {},
  avg_attempts_by_tier: {},
  avg_cost_per_tier: {},
  daily_cost_usd: 3.45,
  monthly_cost_usd: 47.5,
  queue_depth: 0,
  recent_issues: [],
});

// Real /stats shape: { counts:[{status,count}], running, recent:[...] }.
// Tiles derive queued/done/failed by status lookup; running is direct.
const AGENT_OK = ready({
  running: 4,
  counts: [
    { status: "queued", count: 7 },
    { status: "done", count: 100 },
    { status: "failed", count: 1 },
  ],
  recent: [
    {
      id: 13,
      title: "Wire activity band",
      project: "companion",
      cost_usd: 1.01,
      finished_at: "2026-05-06 14:42:08",
    },
  ],
});

beforeEach(() => {
  mockUseStats.mockReset();
  mockUseAgentStats.mockReset();
});

describe("DashboardPage", () => {
  it("renders all six vital-signs tiles plus a heartbeat strip when both backends are healthy", () => {
    mockUseStats.mockReturnValue(ORCH_OK);
    mockUseAgentStats.mockReturnValue(AGENT_OK);

    render(<DashboardPage />);

    // Agent-sourced live counts.
    expect(within(screen.getByTestId("tile-Running")).getByText("4")).toBeInTheDocument();
    expect(within(screen.getByTestId("tile-Queued")).getByText("7")).toBeInTheDocument();
    expect(within(screen.getByTestId("tile-Done")).getByText("100")).toBeInTheDocument();
    expect(within(screen.getByTestId("tile-Failed")).getByText("1")).toBeInTheDocument();

    // Orchestrator-sourced derived metrics.
    expect(
      within(screen.getByTestId("tile-QA Pass Rate")).getByText("90%"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("tile-Cost Today")).getByText("$3.45"),
    ).toBeInTheDocument();

    // Heartbeat strip — one per backend.
    const strip = screen.getByTestId("heartbeat-strip");
    expect(within(strip).getByLabelText(/orchestrator/i)).toBeInTheDocument();
    expect(within(strip).getByLabelText(/agent/i)).toBeInTheDocument();
  });

  // Guards the assay finding: "Cost Today" must come from the orchestrator's
  // daily_cost_usd, NOT the agent's /costs total_usd (which is all-time spend).
  it("sources Cost Today from orchestrator daily_cost_usd, not the agent backend", () => {
    mockUseStats.mockReturnValue(ORCH_OK); // daily_cost_usd = 3.45
    mockUseAgentStats.mockReturnValue(AGENT_OK);

    render(<DashboardPage />);
    const tile = screen.getByTestId("tile-Cost Today");
    expect(within(tile).getByText("$3.45")).toBeInTheDocument();
    // The orchestrator monthly figure (47.5) must never leak into Today.
    expect(within(tile).queryByText("$47.50")).toBeNull();
  });

  // Guards the assay finding: pass rate must use the orchestrator's existing
  // success_rate field, not be re-derived from completed/failed (which ignores
  // queued/running and diverges from the backend's reported rate).
  it("sources QA Pass Rate directly from orchestrator success_rate (no client-side derivation)", () => {
    mockUseStats.mockReturnValue(
      ready({
        ...ORCH_OK.data!,
        // Mismatched bookkeeping — completed/failed would imply 50% if derived,
        // but the orchestrator's authoritative success_rate is 0.9.
        completed: 5,
        failed: 5,
        total_jobs: 20,
        success_rate: 0.9,
      }),
    );
    mockUseAgentStats.mockReturnValue(AGENT_OK);

    render(<DashboardPage />);
    expect(
      within(screen.getByTestId("tile-QA Pass Rate")).getByText("90%"),
    ).toBeInTheDocument();
  });

  it("renders every tile in loading state when both backends are pending", () => {
    mockUseStats.mockReturnValue(loading());
    mockUseAgentStats.mockReturnValue(loading());

    render(<DashboardPage />);
    for (const label of [
      "Running",
      "Queued",
      "Done",
      "Failed",
      "QA Pass Rate",
      "Cost Today",
    ]) {
      expect(screen.getByTestId(`tile-${label}`)).toHaveAttribute(
        "data-state",
        "loading",
      );
    }
  });

  it("only orchestrator-sourced tiles show error when the orchestrator backend fails", () => {
    mockUseStats.mockReturnValue(errored());
    mockUseAgentStats.mockReturnValue(AGENT_OK);

    render(<DashboardPage />);
    for (const label of ["QA Pass Rate", "Cost Today"]) {
      expect(screen.getByTestId(`tile-${label}`)).toHaveAttribute(
        "data-state",
        "error",
      );
    }
    for (const label of ["Running", "Queued", "Done", "Failed"]) {
      expect(screen.getByTestId(`tile-${label}`)).toHaveAttribute(
        "data-state",
        "ready",
      );
    }
  });

  it("only agent-sourced tiles show error when the agent backend fails", () => {
    mockUseStats.mockReturnValue(ORCH_OK);
    mockUseAgentStats.mockReturnValue(errored());

    render(<DashboardPage />);
    for (const label of ["Running", "Queued", "Done", "Failed"]) {
      expect(screen.getByTestId(`tile-${label}`)).toHaveAttribute(
        "data-state",
        "error",
      );
    }
    for (const label of ["QA Pass Rate", "Cost Today"]) {
      expect(screen.getByTestId(`tile-${label}`)).toHaveAttribute(
        "data-state",
        "ready",
      );
    }
  });
});
