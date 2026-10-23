import type { LogEvent, PipelineGraph } from "@/lib/types";
import { eventNode } from "@/lib/wire";
import { loadFixture } from "./fixtures";

// The event stream of a completed standalone QA job (#90), named by event for
// the pipeline-live tests. It is the generated fixture
// tests/fixtures/job-events/qa.json (#102), read through the loader like every
// other fixture, not a copy of it: the graph's three nodes have no agent of
// their own, and the QA agent run nested inside `orchestrate_qa` stamps
// `node: "qa"` on its own events, a name the graph does not know.

export const STANDALONE_QA_EVENTS: LogEvent[] = loadFixture("qa");

function one(label: string, match: (e: LogEvent) => boolean): LogEvent {
  const hits = STANDALONE_QA_EVENTS.filter(match);
  if (hits.length !== 1) throw new Error(`qa.json holds ${hits.length} events for ${label}, expected one`);
  return hits[0];
}

function ofNode(type: LogEvent["type"], node: string): LogEvent {
  return one(`${type} ${node}`, (e) => e.type === type && eventNode(e) === node);
}

function subtaskStatus(status: string): LogEvent {
  return one(`qa_subtask_status ${status}`, (e) => e.type === "qa_subtask_status" && e.status === status);
}

export const QA_PIPELINE_GRAPH = one("pipeline_graph", (e) => e.type === "pipeline_graph");
export const STANDALONE_QA_GRAPH: PipelineGraph =
  QA_PIPELINE_GRAPH.type === "pipeline_graph" ? QA_PIPELINE_GRAPH.graph : (() => { throw new Error("no graph"); })();

export const QA_INIT_STARTED = ofNode("node_started", "init_orchestrator");
export const QA_INIT_COMPLETED = ofNode("node_completed", "init_orchestrator");
export const QA_ORCHESTRATE_STARTED = ofNode("node_started", "orchestrate_qa");
export const QA_APPS_RESOLVED = one("qa_apps_resolved", (e) => e.type === "qa_apps_resolved");
export const QA_AGENT_STARTED = one("agent_started", (e) => e.type === "agent_started");
export const QA_COST_UPDATE = one("cost_update", (e) => e.type === "cost_update");
export const QA_SUBTASK_PASSED = subtaskStatus("passed");
export const QA_SUBTASK_FINISHED = one("qa_subtask_finished", (e) => e.type === "qa_subtask_finished");
export const QA_ORCHESTRATE_COMPLETED = ofNode("node_completed", "orchestrate_qa");
export const QA_REPORT_STARTED = ofNode("node_started", "qa_report");
export const QA_REPORT_COMPLETED = ofNode("node_completed", "qa_report");
export const QA_JOB_FINISHED = one("job_finished", (e) => e.type === "job_finished");

/** The qa_* rows: the orchestrator's, stamped with the nested agent's node "qa" and no run_id. */
export const STANDALONE_QA_QA_EVENTS = STANDALONE_QA_EVENTS.filter((e) => e.type.startsWith("qa_"));
