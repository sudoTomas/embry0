import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

vi.mock("@/api/agents", () => ({
  fetchAgents: vi.fn(),
}));

import { fetchAgents } from "@/api/agents";
import { RESULT_COLORS } from "@/lib/constants";
import type { AgentDefinition } from "@/lib/types/agents";
import type { TraceResponse } from "@/lib/types";
import { TracesTable } from "../TracesTable";

function makeAgent(type: string): AgentDefinition {
  return {
    type,
    description: `${type} agent`,
    model: "model-fixture",
    tools: [],
    skills: [],
    system_prompt: "",
    is_builtin: true,
  };
}

function makeTrace(overrides: Partial<TraceResponse>): TraceResponse {
  return {
    trace_id: "run-fixture-0",
    job_id: "job-fixture-1",
    agent_type: "developer",
    model: "model-fixture",
    result: "success",
    cost_usd: 0.42,
    duration_ms: 12_300,
    tools_called: { Read: 2 },
    result_summary: "",
    created_at: "2026-10-16T12:00:00Z",
    input_tokens: 1200,
    output_tokens: 340,
    cache_read_tokens: 0,
    cache_creation_tokens: 0,
    ...overrides,
  };
}

const TRACES: TraceResponse[] = [
  makeTrace({ trace_id: "run-fixture-1", agent_type: "triage", result: "success" }),
  makeTrace({ trace_id: "run-fixture-2", agent_type: "developer", result: "error" }),
  makeTrace({ trace_id: "run-fixture-3", agent_type: "reviewer", result: "running" }),
];

/** The component's own truncation rule for the Trace cell. */
function shownId(id: string): string {
  return id.length > 20 ? `${id.slice(0, 20)}...` : id;
}

function renderTable() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <TracesTable
          traces={TRACES}
          total={TRACES.length}
          offset={0}
          limit={50}
          filters={{}}
          onFilterChange={vi.fn()}
          onPageChange={vi.fn()}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function optionLabels(select: HTMLElement): string[] {
  return within(select)
    .getAllByRole("option")
    .map((o) => o.textContent ?? "");
}

beforeEach(() => {
  vi.mocked(fetchAgents)
    .mockReset()
    .mockResolvedValue([makeAgent("developer"), makeAgent("reviewer")]);
});

describe("TracesTable", () => {
  it("renders every trace id the way the Trace cell truncates it", () => {
    renderTable();
    for (const t of TRACES) {
      expect(screen.getByText(shownId(t.trace_id))).toBeInTheDocument();
    }
  });

  it("colors each result cell with the storage result value's class", () => {
    // Literal classes, not only RESULT_COLORS: a lookup on both sides would
    // pass whatever the constant held.
    const expected: Record<string, string> = {
      success: "text-success",
      error: "text-destructive",
      running: "text-warning",
    };
    renderTable();
    for (const t of TRACES) {
      const row = screen.getByRole("button", { name: `View trace ${t.trace_id}` });
      const cell = within(row).getByText(t.result);
      expect(cell.tagName).toBe("TD");
      expect(cell).toHaveClass(RESULT_COLORS[t.result]);
      expect(cell).toHaveClass(expected[t.result]);
    }
  });

  it("offers exactly the storage result values in the result filter", () => {
    renderTable();
    expect(optionLabels(screen.getByRole("combobox", { name: "Result" }))).toEqual([
      "All Result",
      "success",
      "error",
      "running",
    ]);
  });

  it("lists the agents from GET /agents in the agent filter", async () => {
    renderTable();
    await screen.findByRole("option", { name: "reviewer" });
    expect(fetchAgents).toHaveBeenCalled();
    expect(optionLabels(screen.getByRole("combobox", { name: "Agent" }))).toEqual([
      "All Agent",
      "developer",
      "reviewer",
    ]);
  });
});
