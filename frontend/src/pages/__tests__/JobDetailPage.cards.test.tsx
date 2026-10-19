// The Pipeline tab's agent cards arrive in turn on the first paint, and a
// card that changes group (waiting, running, done) arrives again in its new
// place, at once rather than in a turn it no longer has.
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { JobResponse, LogEvent, PipelineGraph } from "@/lib/types";

const job = vi.hoisted(() => ({ current: null as unknown }));
const logs = vi.hoisted(() => ({ events: [] as unknown[], graph: null as unknown }));
vi.mock("@/hooks/useJobs", () => ({ useJob: () => ({ data: job.current, isLoading: false, isError: false, refetch: vi.fn() }) }));
vi.mock("@/hooks/useInputs", () => ({ useJobInputs: () => ({ data: [] }) }));
vi.mock("@/hooks/useTraces", () => ({ useTraces: () => ({ data: { traces: [], total: 0 } }) }));
vi.mock("@/hooks/useJobLogs", () => ({
  useJobLogs: () => ({ events: logs.events, isConnected: true, latestCost: 0, pipelineGraph: logs.graph }),
}));

import { JobDetailPage } from "../JobDetailPage";

const t = "2026-10-19T00:00:00Z";
const graph: PipelineGraph = {
  graph_id: "g",
  name: "g",
  nodes: ["triage", "developer", "review"].map((node_id, i) => ({ node_id, agent_type: node_id, physical_node: node_id, position: { x: i, y: 0 } })),
  edges: [],
  metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "triage" },
};

const started = (node: string) => [
  { type: "node_started", node, attempt: 1, timestamp: t },
  { type: "agent_started", node, agent: node, run_id: `run-${node}`, model: "m", max_turns: 10, timestamp: t },
];
const completed = (node: string) => [
  { type: "agent_completed", node, agent: node, run_id: `run-${node}`, is_error: false, output: "", error_message: "", cost_usd: 0, duration_ms: 1, timestamp: t },
  { type: "node_completed", node, attempt: 1, timestamp: t },
];

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function page() {
  job.current = {
    job_id: "job-1234abcd5678", status: "running", repo: "acme/app", task: "Fix the thing", issue_number: null,
    issue_id: null, pr_url: null, error_message: null, total_cost_usd: 0, created_at: t, started_at: t, finished_at: null,
  } satisfies Partial<JobResponse>;
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/jobs/job-1234abcd5678"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/** The arrival wrapper around an agent's card on the Pipeline tab. */
function cardOf(agent: string): HTMLElement {
  const card = within(screen.getByRole("tabpanel")).getByText(agent).closest<HTMLElement>(".motion-enter");
  expect(card, `the ${agent} card arrives through .motion-enter`).not.toBeNull();
  return card!;
}
const turnOf = (agent: string) => cardOf(agent).style.getPropertyValue("--i");

describe("the Job page's agent cards", () => {
  it("arrive in turn on the first paint, and again at once in a new group", () => {
    logs.graph = graph;
    logs.events = [...started("triage"), ...completed("triage"), ...started("developer")] as LogEvent[];
    const { rerender } = render(page());
    expect(["triage", "developer", "review"].map(turnOf), "done, running, then waiting, in turn").toEqual(["0", "1", "2"]);
    const triage = cardOf("triage");
    const developerRunning = cardOf("developer");

    logs.events = [...logs.events, ...completed("developer"), ...started("review")] as LogEvent[];
    rerender(page());
    expect(cardOf("triage"), "a card that stays in its group does not arrive again").toBe(triage);
    expect(cardOf("developer"), "a card that changes group arrives in its new place").not.toBe(developerRunning);
    expect(turnOf("developer"), "a card arriving after the first paint does not wait for a turn").toBe("0");
    expect(turnOf("review")).toBe("0");
  });
});
