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

const EMPTY_COSTS: AgentCostsSummary = { total_usd: 0 };
const EMPTY_ROUTING: AgentRoutingStats = { by_model: {} };
const EMPTY_REVIEW: AgentReviewStats = { pass: 0, fail: 0 };
const EMPTY_HARDWARE: AgentHardware = { host: "unknown" };
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

  it("cost panel renders total, by_project rows, and top_tasks rows", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue({
      total_usd: 42.5,
      by_project: { "ravens-cargo": 30.25, embry0: 12.25 },
      top_tasks: [
        { id: "task-alpha", usd: 9.0 },
        { id: "task-beta", usd: 5.5 },
      ],
    });
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue(EMPTY_REVIEW);
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue(EMPTY_HARDWARE);
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);

    render(<InsightsPage />, { wrapper: wrapper() });

    expect(await screen.findByText("$42.50")).toBeInTheDocument();
    // Project breakdown rows
    expect(screen.getByTestId("cost-project-ravens-cargo")).toHaveTextContent(
      "ravens-cargo",
    );
    expect(screen.getByTestId("cost-project-ravens-cargo")).toHaveTextContent(
      "$30.25",
    );
    expect(screen.getByTestId("cost-project-embry0")).toHaveTextContent("$12.25");
    // Top tasks rows
    expect(screen.getByTestId("cost-task-task-alpha")).toHaveTextContent(
      "task-alpha",
    );
    expect(screen.getByTestId("cost-task-task-alpha")).toHaveTextContent("$9.00");
    expect(screen.getByTestId("cost-task-task-beta")).toHaveTextContent("$5.50");
  });

  it("routing-stats panel renders model -> count rows", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue(EMPTY_COSTS);
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue({
      by_model: { "claude-sonnet-4-6": 17, "claude-opus-4-7": 4 },
    });
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue(EMPTY_REVIEW);
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue(EMPTY_HARDWARE);
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);

    render(<InsightsPage />, { wrapper: wrapper() });
    expect(
      await screen.findByTestId("routing-row-claude-sonnet-4-6"),
    ).toHaveTextContent("claude-sonnet-4-6");
    expect(
      screen.getByTestId("routing-row-claude-sonnet-4-6"),
    ).toHaveTextContent("17");
    expect(screen.getByTestId("routing-row-claude-opus-4-7")).toHaveTextContent(
      "4",
    );
  });

  it("review-stats panel renders pass / fail / warn counts", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue(EMPTY_COSTS);
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue({
      pass: 21,
      fail: 3,
      warn: 5,
    });
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue(EMPTY_HARDWARE);
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);

    render(<InsightsPage />, { wrapper: wrapper() });
    expect(await screen.findByTestId("review-pass")).toHaveTextContent("21");
    expect(screen.getByTestId("review-fail")).toHaveTextContent("3");
    expect(screen.getByTestId("review-warn")).toHaveTextContent("5");
  });

  it("review-stats panel hides warn when not provided", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue(EMPTY_COSTS);
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue({ pass: 1, fail: 2 });
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue(EMPTY_HARDWARE);
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);

    render(<InsightsPage />, { wrapper: wrapper() });
    expect(await screen.findByTestId("review-pass")).toHaveTextContent("1");
    expect(screen.queryByTestId("review-warn")).toBeNull();
  });

  it("hardware panel renders host, cpu/mem, and gpus", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue(EMPTY_COSTS);
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue(EMPTY_REVIEW);
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue({
      host: "private-server",
      cpu_pct: 38,
      mem_pct: 71,
      gpus: [
        { name: "RTX 5070 Ti", mem_used_mb: 4096 },
        { name: "RTX 4090" },
      ],
    });
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue(EMPTY_MEMORIES);

    render(<InsightsPage />, { wrapper: wrapper() });
    expect(await screen.findByText("private-server")).toBeInTheDocument();
    const panel = screen.getByTestId("insights-hardware");
    expect(panel).toHaveTextContent("38%");
    expect(panel).toHaveTextContent("71%");
    expect(screen.getByTestId("hardware-gpu-0")).toHaveTextContent("RTX 5070 Ti");
    expect(screen.getByTestId("hardware-gpu-0")).toHaveTextContent("4096");
    expect(screen.getByTestId("hardware-gpu-1")).toHaveTextContent("RTX 4090");
  });

  it("memories panel renders one row per memory with scope + body", async () => {
    vi.spyOn(agentApi, "fetchCosts").mockResolvedValue(EMPTY_COSTS);
    vi.spyOn(agentApi, "fetchRoutingStats").mockResolvedValue(EMPTY_ROUTING);
    vi.spyOn(agentApi, "fetchReviewStats").mockResolvedValue(EMPTY_REVIEW);
    vi.spyOn(agentApi, "fetchHardware").mockResolvedValue(EMPTY_HARDWARE);
    vi.spyOn(agentApi, "fetchMemories").mockResolvedValue([
      { id: "m-1", scope: "global", body: "Always prefer ripgrep" },
      { id: "m-2", scope: "raven", body: "vendor-tms runs on private-server" },
    ]);

    render(<InsightsPage />, { wrapper: wrapper() });
    expect(await screen.findByTestId("memory-row-m-1")).toHaveTextContent("global");
    expect(screen.getByTestId("memory-row-m-1")).toHaveTextContent(
      "Always prefer ripgrep",
    );
    expect(screen.getByTestId("memory-row-m-2")).toHaveTextContent("raven");
  });

  it("cost panel renders an empty state when there is no spend", async () => {
    mockAllEmpty();
    render(<InsightsPage />, { wrapper: wrapper() });
    const panel = await screen.findByTestId("insights-cost");
    expect(panel).toHaveTextContent("$0.00");
    expect(screen.queryByTestId(/^cost-project-/)).toBeNull();
    expect(screen.queryByTestId(/^cost-task-/)).toBeNull();
  });
});
