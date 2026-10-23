// The party strip's members arrive in turn on the first paint, and a run that
// joins later arrives at once rather than in a turn it no longer has. A member
// that is already on the strip is the same element across a poll, so it never
// arrives twice; its sprite changes state in place.
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { JobResponse, LogEvent, PipelineGraph } from "@/lib/types";
import { STANDALONE_QA_EVENTS, STANDALONE_QA_GRAPH } from "@/lib/__tests__/standalone-qa-stream";

const job = vi.hoisted(() => ({ current: null as unknown }));
const logs = vi.hoisted(() => ({ events: [] as unknown[], graph: null as unknown }));
vi.mock("@/hooks/useJobs", () => ({ useJob: () => ({ data: job.current, isLoading: false, isError: false, refetch: vi.fn() }) }));
vi.mock("@/hooks/useInputs", () => ({ useJobInputs: () => ({ data: [], refetch: vi.fn() }) }));
vi.mock("@/hooks/useTraces", () => ({ useTraces: () => ({ data: { traces: [], total: 0 } }) }));
vi.mock("@/hooks/useRepos", () => ({ useRegisteredRepos: () => ({ data: [] }) }));
vi.mock("@/hooks/useQaResults", () => ({ useQaAttempts: () => ({ data: [] }), useQaServices: () => ({ data: [] }) }));
vi.mock("@/hooks/useJobVerification", () => ({ useJobVerification: () => ({ data: null, refetch: vi.fn() }) }));
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [] }) }));
vi.mock("@/hooks/useJobLogs", () => ({
  useJobLogs: () => ({
    events: logs.events, isConnected: true, isComplete: false, latestCost: 0, latestTokensIn: 0, latestTokensOut: 0, latestTurns: 0, pipelineGraph: logs.graph,
  }),
}));
vi.mock("@/components/pipeline/PipelineGraphView", () => ({ PipelineGraphView: () => <div data-testid="party-map" /> }));

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

/** The strip's member for a run: the button that arrives through .motion-enter. */
function memberOf(runId: string): HTMLElement {
  const member = within(screen.getByTestId("party-strip")).getAllByRole("button").find((b) => b.getAttribute("data-run-id") === runId);
  expect(member, `the strip lists ${runId}`).toBeDefined();
  expect(member!.classList.contains("motion-enter"), `the ${runId} member arrives through .motion-enter`).toBe(true);
  return member!;
}
const turnOf = (runId: string) => memberOf(runId).style.getPropertyValue("--i");

describe("the Job page's party strip", () => {
  it("members arrive in turn on the first paint, and a run that joins later arrives at once", () => {
    logs.graph = graph;
    logs.events = [...started("triage"), ...completed("triage"), ...started("developer")] as LogEvent[];
    const { rerender } = render(page());
    const members = within(screen.getByTestId("party-strip")).getAllByRole("button").map((b) => b.getAttribute("data-run-id")!);
    expect(members.map(turnOf), "each member takes the turn after the one before it").toEqual(members.map((_, i) => String(i)));
    const triage = memberOf("run-triage");
    const developer = memberOf("run-developer");

    // The review step starts: its pending node becomes a run.
    logs.events = [...logs.events, ...completed("developer"), ...started("review")] as LogEvent[];
    rerender(page());
    expect(memberOf("run-triage"), "a member already on the strip does not arrive again").toBe(triage);
    expect(memberOf("run-developer"), "a run whose state changes stays put; its sprite changes").toBe(developer);
    expect(memberOf("run-developer").getAttribute("data-state")).toBe("done");
    expect(turnOf("run-review"), "a run joining after the first paint does not wait for a turn").toBe("0");
  });

  it("the sprite is the member's one animation: the entrance is the button's", () => {
    logs.graph = graph;
    logs.events = [...started("triage")] as LogEvent[];
    render(page());
    const member = memberOf("run-triage");
    const sprite = member.querySelector("svg.sprite");
    expect(sprite, "one sprite per member").not.toBeNull();
    expect(sprite!.classList.contains("motion-enter")).toBe(false);
    expect(member.querySelectorAll("svg.sprite").length).toBe(1);
  });

  it("a standalone QA job's one run is one member, and the HUD counts its cost once (#104)", () => {
    logs.graph = STANDALONE_QA_GRAPH;
    logs.events = STANDALONE_QA_EVENTS;
    render(page());
    const members = within(screen.getByTestId("party-strip")).getAllByRole("button").map((b) => b.getAttribute("data-run-id"));
    expect(members, "the orchestrate_qa node's run is its only card").toEqual(["run-0001", "node:init_orchestrator", "node:qa_report"]);
    expect(screen.getByTestId("job-hud").querySelector('[data-hud="cost"]')!.textContent, "the run's one cost_update, once").toBe("$0.42");
  });
});
