import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import * as agentApi from "@/api/agent";
import type {
  AgentCostsSummary,
  AgentRoutingStats,
  AgentReviewStats,
  AgentHardware,
  AgentMemory,
} from "@/api/agent";
import { InsightsPage } from "../InsightsPage";

function wrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

// Empty fixtures use the REAL response shapes (per the live agent), so the
// empty/loading branches are exercised against the actual contract.
const EMPTY_COSTS: AgentCostsSummary = {
  grok: { real_cost_usd: 0, tokens_in: 0, tokens_out: 0 },
  claude: { notional_cost_usd: 0, tokens_in: 0, tokens_out: 0 },
  reviews: { total: 0, pass: 0, warn: 0, fail: 0, needs_review: 0 },
  daily_usage: [],
};
const EMPTY_ROUTING: AgentRoutingStats = { by_model: [], by_phase: [] };
const EMPTY_REVIEW: AgentReviewStats = {
  by_type: [],
  agreement_rate: "N/A",
  total_dual_reviews: 0,
  agreed: 0,
};
const EMPTY_HARDWARE: AgentHardware = {
  id: 1,
  hostname: "unknown",
  total_memory_gb: 0,
  available_memory_gb: 0,
  gpu_info: "",
  ollama_models: "[]",
};
const EMPTY_MEMORIES: AgentMemory[] = [];

function mockAllEmpty() {
  vi.spyOn(agentApi, "fetchCosts").mockResolvedValue(EMPTY_COSTS);
  vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
  vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue(EMPTY_REVIEW);
  vi.spyOn(agentApi, "fetchHardware").mockResolvedValue(EMPTY_HARDWARE);
  vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("InsightsPage", () => {
  it("renders the page title", async () => {
    mockAllEmpty();
    render(<InsightsPage />, { wrapper: wrapper() });
    expect(
      await screen.findByRole("heading", { name: /insights/i, level: 1 }),
    ).toBeInTheDocument();
  });

  it("renders all five panels", async () => {
    mockAllEmpty();
    render(<InsightsPage />, { wrapper: wrapper() });
    await waitFor(() => {
      expect(screen.getByTestId("insights-cost")).toBeInTheDocument();
      expect(screen.getByTestId("insights-routing-stats")).toBeInTheDocument();
      expect(screen.getByTestId("insights-review-stats")).toBeInTheDocument();
      expect(screen.getByTestId("insights-hardware")).toBeInTheDocument();
      expect(screen.getByTestId("insights-memories")).toBeInTheDocument();
    });
  });

  it("cost panel renders total, per-provider rows, and daily usage", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue({
      grok: { real_cost_usd: 0, tokens_in: 0, tokens_out: 0 },
      claude: {
        notional_cost_usd: 4.54,
        tokens_in: 415,
        tokens_out: 54231,
        subscription: "max",
      },
      reviews: { total: 0, pass: 0, warn: 0, fail: 0, needs_review: 0 },
      daily_usage: [
        { day: "2026-05-06", tokens_in: 384, tokens_out: 35601, tasks_completed: 6 },
      ],
    });
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue(EMPTY_REVIEW);
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue(EMPTY_HARDWARE);
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);

    render(<InsightsPage />, { wrapper: wrapper() });

    // Total = grok (0) + claude (4.54). The value appears both in the
    // Total-Spend StatCard and the claude provider row, so assert via testid.
    const claudeRow = await screen.findByTestId("cost-provider-claude");
    expect(screen.getByTestId("insights-cost")).toHaveTextContent("$4.54");
    expect(claudeRow).toHaveTextContent("claude");
    expect(claudeRow).toHaveTextContent("max");
    expect(claudeRow).toHaveTextContent("415");
    expect(screen.getByTestId("cost-provider-grok")).toHaveTextContent("grok");
    // Daily usage row.
    expect(screen.getByTestId("cost-day-2026-05-06")).toHaveTextContent(
      "6 tasks",
    );
  });

  it("routing-stats panel renders by_model array rows (model + count + success rate)", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue(EMPTY_COSTS);
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue({
      by_model: [
        { routed_model: "sonnet", count: 10, success_rate: 0.5 },
        { routed_model: "opus", count: 4, success_rate: 1 },
      ],
      by_phase: [{ phase: "implement", count: 8, success_rate: 0.75 }],
    });
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue(EMPTY_REVIEW);
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue(EMPTY_HARDWARE);
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);

    render(<InsightsPage />, { wrapper: wrapper() });
    const sonnet = await screen.findByTestId("routing-row-sonnet");
    expect(sonnet).toHaveTextContent("sonnet");
    expect(sonnet).toHaveTextContent("10");
    expect(sonnet).toHaveTextContent("50%");
    expect(screen.getByTestId("routing-row-opus")).toHaveTextContent("4");
    // by_phase array also renders, never as a raw object.
    expect(screen.getByTestId("routing-phase-implement")).toHaveTextContent(
      "implement",
    );
    expect(screen.queryByText("[object Object]")).toBeNull();
  });

  it("routing-stats panel shows an empty state when arrays are empty", async () => {
    mockAllEmpty();
    render(<InsightsPage />, { wrapper: wrapper() });
    // Wait for the resolved empty state, not the loading placeholder.
    expect(await screen.findByText(/no routing data/i)).toBeInTheDocument();
    expect(screen.queryByTestId(/^routing-row-/)).toBeNull();
  });

  it("review-stats panel renders agreement_rate, dual reviews, agreed, and by_type", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue(EMPTY_COSTS);
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue({
      by_type: [{ type: "consensus", count: 4 }],
      agreement_rate: 0.8,
      total_dual_reviews: 5,
      agreed: 4,
    });
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue(EMPTY_HARDWARE);
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);

    render(<InsightsPage />, { wrapper: wrapper() });
    // Numeric agreement_rate renders as a percentage.
    expect(await screen.findByTestId("review-agreement")).toHaveTextContent(
      "80%",
    );
    expect(screen.getByTestId("review-dual")).toHaveTextContent("5");
    expect(screen.getByTestId("review-agreed")).toHaveTextContent("4");
    expect(screen.getByTestId("review-type-consensus")).toHaveTextContent("4");
  });

  it("review-stats panel renders a string agreement_rate ('N/A') verbatim", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue(EMPTY_COSTS);
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue(EMPTY_REVIEW);
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue(EMPTY_HARDWARE);
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);

    render(<InsightsPage />, { wrapper: wrapper() });
    expect(await screen.findByTestId("review-agreement")).toHaveTextContent(
      "N/A",
    );
  });

  it("hardware panel renders hostname, memory, gpu_info, and parsed ollama models", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue(EMPTY_COSTS);
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue(EMPTY_REVIEW);
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue({
      id: 1,
      hostname: "demo-server",
      total_memory_gb: 251.1,
      available_memory_gb: 239,
      gpu_info: "RTX 5070 Ti",
      // ollama_models is a JSON-ENCODED STRING and must be parsed.
      ollama_models: JSON.stringify([
        { name: "hermes3:70b" },
        { name: "qwen2.5:32b" },
      ]),
    });
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);

    render(<InsightsPage />, { wrapper: wrapper() });
    expect(await screen.findByText("demo-server")).toBeInTheDocument();
    const panel = screen.getByTestId("insights-hardware");
    expect(panel).toHaveTextContent("251.1 GB");
    expect(panel).toHaveTextContent("239 GB");
    expect(panel).toHaveTextContent("RTX 5070 Ti");
    expect(screen.getByTestId("hardware-model-0")).toHaveTextContent(
      "hermes3:70b",
    );
    expect(screen.getByTestId("hardware-model-1")).toHaveTextContent(
      "qwen2.5:32b",
    );
  });

  it("hardware panel does not crash when ollama_models is malformed JSON", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue(EMPTY_COSTS);
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue(EMPTY_REVIEW);
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue({
      id: 1,
      hostname: "demo-server",
      ollama_models: "{ not valid json",
    });
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);

    render(<InsightsPage />, { wrapper: wrapper() });
    // Host still renders; no model rows; no crash.
    expect(await screen.findByText("demo-server")).toBeInTheDocument();
    expect(screen.queryByTestId(/^hardware-model-/)).toBeNull();
  });

  it("memories panel renders one row per memory with scope + body", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue(EMPTY_COSTS);
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue(EMPTY_REVIEW);
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue(EMPTY_HARDWARE);
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue([
      { id: "m-1", scope: "global", body: "Always prefer ripgrep" },
      { id: "m-2", scope: "acme-org", body: "The staging DB runs on demo-server" },
    ]);

    render(<InsightsPage />, { wrapper: wrapper() });
    expect(await screen.findByTestId("memory-row-m-1")).toHaveTextContent("global");
    expect(screen.getByTestId("memory-row-m-1")).toHaveTextContent(
      "Always prefer ripgrep",
    );
    expect(screen.getByTestId("memory-row-m-2")).toHaveTextContent("acme-org");
  });

  it("memories panel renders an empty state for a top-level empty array", async () => {
    mockAllEmpty();
    render(<InsightsPage />, { wrapper: wrapper() });
    expect(await screen.findByText(/no memories/i)).toBeInTheDocument();
    expect(screen.queryByTestId(/^memory-row-/)).toBeNull();
  });

  it("cost panel renders an empty state when there is no provider spend", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue({ daily_usage: [] });
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue(EMPTY_REVIEW);
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue(EMPTY_HARDWARE);
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);

    render(<InsightsPage />, { wrapper: wrapper() });
    // Wait for the resolved empty state, not the loading placeholder.
    expect(await screen.findByText(/no provider spend/i)).toBeInTheDocument();
    expect(screen.getByTestId("insights-cost")).toHaveTextContent("$0.00");
    expect(screen.queryByTestId(/^cost-provider-/)).toBeNull();
  });
});
