import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

vi.mock("@/api/agents", () => ({
  fetchAgents: vi.fn(),
}));

import { fetchAgents } from "@/api/agents";
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
  makeTrace({ trace_id: "run-fixture-long-trace-id-3", agent_type: "reviewer", result: "running" }),
];

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
  it("renders short trace ids whole and truncates one over 20 characters", () => {
    renderTable();
    expect(screen.getByText("run-fixture-1")).toBeInTheDocument();
    expect(screen.getByText("run-fixture-2")).toBeInTheDocument();
    expect(screen.getByText("run-fixture-long-tra...")).toBeInTheDocument();
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
      expect(cell).toHaveClass(expected[t.result]);
    }
  });

  it("offers exactly the storage result values in the result filter", () => {
    renderTable();
    expect(optionLabels(screen.getByRole("combobox", { name: "Results" }))).toEqual([
      "All results",
      "success",
      "error",
      "running",
    ]);
  });

  it("lists the agents from GET /agents in the agent filter", async () => {
    renderTable();
    await screen.findByRole("option", { name: "reviewer" });
    expect(fetchAgents).toHaveBeenCalled();
    expect(optionLabels(screen.getByRole("combobox", { name: "Agents" }))).toEqual([
      "All agents",
      "developer",
      "reviewer",
    ]);
  });

  it("spans an open trace's detail across every column of the table", () => {
    renderTable();
    const row = screen.getByRole("button", { name: "View trace run-fixture-1" });
    fireEvent.click(row);
    const columns = row.closest("table")!.tHead!.rows[0].cells.length;
    expect((row as HTMLTableRowElement).cells.length, "a trace row and the head disagree").toBe(columns);
    const detail = within(row.nextElementSibling as HTMLElement).getByRole("button", { name: "Close detail panel" });
    expect((detail.closest("td") as HTMLTableCellElement).colSpan).toBe(columns);
  });

  it("spans the empty state across every column of the table", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          <TracesTable traces={[]} total={0} offset={0} limit={50} filters={{}} onFilterChange={vi.fn()} onPageChange={vi.fn()} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    const cell = screen.getByText("No traces found") as HTMLTableCellElement;
    expect(cell.colSpan).toBe(cell.closest("table")!.tHead!.rows[0].cells.length);
  });

  it("marks the open trace's row on the overlay, not a gold wash: a selection is not gold", () => {
    renderTable();
    const row = screen.getByRole("button", { name: "View trace run-fixture-1" });
    fireEvent.click(row);
    expect(row).toHaveClass("bg-overlay");
    expect(row.getAttribute("class")).not.toMatch(/(^|\s)bg-primary/);
  });

  it("opens a trace without a gold border: gold marks only what needs a person", () => {
    renderTable();
    const row = screen.getByRole("button", { name: "View trace run-fixture-1" });
    fireEvent.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
    const panel = row.nextElementSibling;
    expect(panel?.innerHTML).toContain("border-t");
    for (const el of [row, ...(panel?.querySelectorAll("*") ?? [])]) {
      expect(el.getAttribute("class") ?? "").not.toMatch(/(^|\s)border(-[trblxy])?-primary/);
    }
  });
});
