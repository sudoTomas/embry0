import type { LogEvent, PipelineGraph } from "@/lib/types";

// The event stream of a completed standalone QA job (#90), ids neutralised,
// order and fields as the backend emits them. The graph's three nodes have no
// `physical_node`, so each physical is its own id; the QA agent run nested
// inside `orchestrate_qa` stamps `node: "qa"` on its own events, a name the
// graph does not know.

export const STANDALONE_QA_GRAPH: PipelineGraph = {
  graph_id: "qa-job-0001",
  name: "qa",
  nodes: [
    { node_id: "init_orchestrator", agent_type: "qa", position: { x: 0, y: 0 } },
    { node_id: "orchestrate_qa", agent_type: "qa", position: { x: 230, y: 0 } },
    { node_id: "qa_report", agent_type: "qa", position: { x: 460, y: 0 } },
  ],
  edges: [
    { edge_id: "init_orchestrator->orchestrate_qa", source: "init_orchestrator", target: "orchestrate_qa", edge_type: "flow" },
    { edge_id: "orchestrate_qa->qa_report", source: "orchestrate_qa", target: "qa_report", edge_type: "flow" },
  ],
  metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "auto" },
};

function at(seconds: number, partial: Record<string, unknown>): LogEvent {
  const timestamp = new Date(Date.UTC(2026, 9, 22, 12, 0, seconds)).toISOString();
  return { timestamp, ...partial } as unknown as LogEvent;
}

export const QA_INIT_STARTED = at(0, { type: "node_started", node: "init_orchestrator", attempt: 1 });
export const QA_PIPELINE_GRAPH = at(1, { type: "pipeline_graph", node: "init_orchestrator", graph: STANDALONE_QA_GRAPH });
export const QA_INIT_COMPLETED = at(2, { type: "node_completed", node: "init_orchestrator" });
export const QA_ORCHESTRATE_STARTED = at(3, { type: "node_started", node: "orchestrate_qa", attempt: 1 });
export const QA_APPS_RESOLVED = at(4, { type: "qa_apps_resolved", node: "qa", apps: ["web"] });
export const QA_SUBTASK_STARTED = at(5, { type: "qa_subtask_started", node: "qa", app: "web", target: "managed" });
export const QA_SUBTASK_RUNNING = at(6, { type: "qa_subtask_status", node: "qa", app: "web", status: "running" });
export const QA_COST_UPDATE = at(167, {
  type: "cost_update",
  node: "qa",
  run_id: "run-0001",
  cost_usd: 1.602263,
  num_turns: 57,
  duration_ms: 160769,
  duration_api_ms: 150000,
  tokens_in: 108,
  tokens_out: 13785,
  cache_creation_tokens: 0,
  cache_read_tokens: 0,
});
export const QA_SUBTASK_PASSED = at(184, {
  type: "qa_subtask_status",
  node: "qa",
  app: "web",
  status: "passed",
  duration_ms: 178384,
});
export const QA_SUBTASK_FINISHED = at(184, {
  type: "qa_subtask_finished",
  node: "qa",
  app: "web",
  status: "passed",
  duration_ms: 178384,
});
export const QA_ORCHESTRATE_COMPLETED = at(185, { type: "node_completed", node: "orchestrate_qa", action: "passed" });
export const QA_REPORT_STARTED = at(186, { type: "node_started", node: "qa_report" });
export const QA_REPORT_COMPLETED = at(187, { type: "node_completed", node: "qa_report" });
export const QA_JOB_FINISHED = at(188, {
  type: "job_finished",
  status: "completed",
  error_code: null,
  error_message: null,
  pr_url: null,
  total_cost_usd: 1.602263,
});

export const STANDALONE_QA_QA_EVENTS = [
  QA_APPS_RESOLVED,
  QA_SUBTASK_STARTED,
  QA_SUBTASK_RUNNING,
  QA_SUBTASK_PASSED,
  QA_SUBTASK_FINISHED,
];

export const STANDALONE_QA_EVENTS: LogEvent[] = [
  QA_INIT_STARTED,
  QA_PIPELINE_GRAPH,
  QA_INIT_COMPLETED,
  QA_ORCHESTRATE_STARTED,
  QA_APPS_RESOLVED,
  QA_SUBTASK_STARTED,
  QA_SUBTASK_RUNNING,
  QA_COST_UPDATE,
  QA_SUBTASK_PASSED,
  QA_SUBTASK_FINISHED,
  QA_ORCHESTRATE_COMPLETED,
  QA_REPORT_STARTED,
  QA_REPORT_COMPLETED,
  QA_JOB_FINISHED,
];
