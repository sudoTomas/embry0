// The logs page's graph arrives on the event stream a second or so after the
// page paints. The note standing in for it must take the graph's height, or
// the transcript under it jumps down when the graph lands.
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import type { PipelineGraph } from "@/lib/types";
import { PipelineGraphView } from "@/components/pipeline/PipelineGraphView";

vi.mock("@/hooks/useJobs", () => ({
  useJob: () => ({ data: { job_id: "job-1", status: "running" }, error: null }),
}));
vi.mock("@/hooks/useInputs", () => ({ useJobInputs: () => ({ data: [] }) }));
vi.mock("@/hooks/useJobLogs", () => ({
  useJobLogs: () => ({
    events: [],
    isConnected: true,
    isComplete: false,
    latestCost: 0,
    latestTokensIn: 0,
    latestTokensOut: 0,
    latestTurns: 0,
    pipelineGraph: null,
  }),
}));

import { JobLogsPage } from "../JobLogsPage";

const GRAPH = {
  graph_id: "g-1",
  name: "quick-fix",
  nodes: [{ node_id: "triage", agent_type: "triage", physical_node: "triage", position: { x: 0, y: 0 } }],
  edges: [],
  metadata: { max_total_budget_usd: 5, max_total_loops: 3, created_by: "auto" },
} as unknown as PipelineGraph;

/** The height utility a box carries, e.g. "h-[40vh]". */
function heightClass(el: Element): string | undefined {
  return [...el.classList].find((c) => /^h-/.test(c));
}

describe("the logs page's graph card", () => {
  it("holds the graph's height while the graph is on its way", () => {
    render(
      <MemoryRouter initialEntries={["/jobs/job-1/logs"]}>
        <Routes>
          <Route path="/jobs/:jobId/logs" element={<JobLogsPage />} />
        </Routes>
      </MemoryRouter>,
    );
    const pending = screen.getByTestId("pipeline-graph-pending");

    const { container } = render(<PipelineGraphView graph={GRAPH} nodeStates={{}} />);
    const graphBox = container.firstElementChild as Element;

    expect(heightClass(graphBox)).toBeDefined();
    expect(heightClass(pending)).toBe(heightClass(graphBox));
  });
});
