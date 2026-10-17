import { describe, it, expect } from "vitest";
import { filterEventsForNode } from "@/lib/pipeline-live";
import type { LogEvent, PipelineGraph } from "@/lib/types";

const GRAPH: PipelineGraph = {
  graph_id: "route-job-fixture-1",
  name: "filter-test",
  nodes: [
    { node_id: "triage", agent_type: "triage", physical_node: "triage", position: { x: 0, y: 0 } },
    { node_id: "reviewer-1", agent_type: "reviewer", physical_node: "review", position: { x: 230, y: 0 } },
    { node_id: "research-a", agent_type: "research", physical_node: "generic_agent", position: { x: 460, y: 0 } },
    { node_id: "research-b", agent_type: "analysis", physical_node: "generic_agent", position: { x: 690, y: 0 } },
    { node_id: "qa", agent_type: "qa", physical_node: "qa", position: { x: 920, y: 0 } },
  ],
  edges: [],
  metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "triage" },
};

function ev(partial: Record<string, unknown>): LogEvent {
  return { timestamp: "2026-10-16T12:00:00Z", ...partial } as unknown as LogEvent;
}

const JOB_LEVEL = [
  ev({ type: "pipeline_graph", graph: GRAPH }),
  ev({ type: "job_finished", status: "completed", error_code: null, error_message: null, pr_url: null, total_cost_usd: 0 }),
  ev({ type: "error", message: "job-level failure" }),
  ev({ type: "system", message: "[raw] undecodable row" }),
];
const REVIEW_TEXT = ev({ type: "text", node: "review", text: "review says hi" });
const DEV_TEXT = ev({ type: "text", node: "developer", text: "developer says hi" });
const TRIAGE_TEXT = ev({ type: "text", node: "triage", text: "triage says hi" });
const GENERIC_1 = ev({ type: "node_started", node: "generic_agent", attempt: 1 });
const GENERIC_2 = ev({ type: "text", node: "generic_agent", text: "research output" });
const QA_STATUS = ev({ type: "qa_subtask_status", node: "orchestrate_qa", app: "backend", status: "running" });
const QA_RETRY = ev({ type: "qa_agent_transient_retry", node: "orchestrate_qa", app: "backend", reason: "overloaded" });
const QA_NODE_TEXT = ev({ type: "text", node: "qa", text: "qa node text" });

const ALL = [...JOB_LEVEL, REVIEW_TEXT, DEV_TEXT, TRIAGE_TEXT, GENERIC_1, GENERIC_2, QA_STATUS, QA_RETRY, QA_NODE_TEXT];

describe("filterEventsForNode", () => {
  it("no selection returns everything", () => {
    expect(filterEventsForNode(GRAPH, ALL, null), "null selection must not filter").toEqual(ALL);
  });

  it("no graph and no selection returns everything", () => {
    expect(filterEventsForNode(null, ALL, null), "no graph, no selection").toEqual(ALL);
  });

  it("selecting reviewer-1 keeps events whose node is review", () => {
    const out = filterEventsForNode(GRAPH, ALL, "reviewer-1");
    expect(out, "the review text belongs to reviewer-1").toContain(REVIEW_TEXT);
    expect(out, "a developer text is not reviewer-1's").not.toContain(DEV_TEXT);
    expect(out, "a triage text is not reviewer-1's").not.toContain(TRIAGE_TEXT);
  });

  it("job-level events are always kept", () => {
    for (const id of ["triage", "reviewer-1", "research-a", "qa", "no-such-node"]) {
      const out = filterEventsForNode(GRAPH, ALL, id);
      for (const e of JOB_LEVEL) {
        expect(out, `job-level ${e.type} dropped when selecting ${id}`).toContain(e);
      }
    }
  });

  it("two graph nodes on one physical show the same events", () => {
    const a = filterEventsForNode(GRAPH, ALL, "research-a");
    const b = filterEventsForNode(GRAPH, ALL, "research-b");
    expect(a, "research-a shows the generic_agent events").toEqual([...JOB_LEVEL, GENERIC_1, GENERIC_2]);
    expect(b, "research-b shows the same events as research-a").toEqual(a);
  });

  it("the qa node collects qa_* events", () => {
    const out = filterEventsForNode(GRAPH, ALL, "qa");
    expect(out, "qa_subtask_status from orchestrate_qa belongs to the qa node").toContain(QA_STATUS);
    expect(out, "qa_agent_transient_retry is a qa_* event too").toContain(QA_RETRY);
    expect(out, "an event whose node is qa belongs to the qa node").toContain(QA_NODE_TEXT);
    expect(out, "the qa node does not collect review events").not.toContain(REVIEW_TEXT);
  });

  it("qa_* events are not collected by a non-qa node", () => {
    const out = filterEventsForNode(GRAPH, ALL, "triage");
    expect(out, "triage must not show qa_subtask_status").not.toContain(QA_STATUS);
  });

  it("an unknown selected id matches nothing but job-level events", () => {
    expect(filterEventsForNode(GRAPH, ALL, "no-such-node"), "unknown id keeps only job-level").toEqual(JOB_LEVEL);
  });

  it("a selection with no graph keeps only job-level events", () => {
    expect(filterEventsForNode(null, ALL, "reviewer-1"), "no graph to resolve the id against").toEqual(JOB_LEVEL);
  });
});
