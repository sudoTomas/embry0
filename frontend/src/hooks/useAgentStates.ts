import { useMemo } from "react";
import type { LogEvent, PipelineGraph } from "@/lib/types";
import { eventNode } from "@/lib/wire";
import { isTerminalStatus } from "@/lib/jobStatus";
import { deriveNodeStates } from "@/lib/pipeline-live";

export type AgentStatus = "pending" | "running" | "completed" | "failed";

export interface ToolCallEntry {
  tool_name: string;
  tool_id: string;
  input: string;
  result?: string;
  is_error?: boolean;
  timestamp?: string;
  /** Calls a subagent made on behalf of this one (their `parent_tool_use_id` is this `tool_id`). */
  children: ToolCallEntry[];
  /** Set by `subagent_start`/`subagent_end`, whose `tool_id` is this (Task) call's id. */
  subagent?: { name: string; is_error?: boolean };
}

/** Built from the contract's `interrupt.reason`, `questions` and `summary.*`. */
export interface InterruptData {
  reason?: string;
  message?: string;
  retry_count?: number;
  latest_review?: string;
  options?: string[];
  questions?: Record<string, unknown>[];
}

/** One agent run (`run_id`); a retried node is a second state, not a replacement. */
export interface AgentState {
  runId: string;
  node: string;
  agent: string;
  attempt: number;
  status: AgentStatus;
  events: LogEvent[];
  toolCalls: ToolCallEntry[];
  thinkingBlocks: string[];
  textBlocks: string[];
  costUsd: number;
  durationMs: number;
  startedAt?: string;
  turnCount: number;
  toolCallCount: number;
  summary: string;
  retryLabel?: string;
  model?: string;
  errorCode?: string;
}

function makeAgentState(runId: string, node: string, agent: string, attempt: number, status: AgentStatus): AgentState {
  return {
    runId,
    node,
    agent,
    attempt,
    status,
    events: [],
    toolCalls: [],
    thinkingBlocks: [],
    textBlocks: [],
    costUsd: 0,
    durationMs: 0,
    turnCount: 0,
    toolCallCount: 0,
    summary: "",
    retryLabel: attempt > 1 ? `attempt ${attempt}` : undefined,
  };
}

function findCall(calls: ToolCallEntry[], toolId: string): ToolCallEntry | undefined {
  for (const c of calls) {
    if (c.tool_id === toolId) return c;
    const nested = findCall(c.children, toolId);
    if (nested) return nested;
  }
  return undefined;
}

export function useAgentStates(
  events: LogEvent[],
  jobStatus?: string,
  graph?: PipelineGraph | null,
): { agents: AgentState[]; interruptData: InterruptData | null; prUrl: string | null } {
  return useMemo(() => {
    const runs = new Map<string, AgentState>();
    const latestRunByNode = new Map<string, string>();
    const attemptByNode = new Map<string, number>();
    let lastStartedNode = "";
    let prUrl: string | null = null;
    let interruptData: InterruptData | null = null;

    events.forEach((event, index) => {
      const node = eventNode(event);

      // Job-level events: they belong to no run.
      if (event.type === "pr_created") {
        prUrl = event.pr_url;
        return;
      }
      if (event.type === "interrupt") {
        interruptData = {
          reason: event.reason ?? event.summary?.reason,
          message: event.summary?.message,
          retry_count: event.summary?.retry_count,
          options: event.summary?.options,
          latest_review: event.summary?.latest_review ?? event.summary?.review_error,
          questions: event.questions,
        };
        return;
      }
      if (event.type === "node_started") {
        // A card is a run, not a node: only the attempt is recorded here.
        attemptByNode.set(node, event.attempt ?? (attemptByNode.get(node) ?? 0) + 1);
        lastStartedNode = node;
        return;
      }
      if (event.type === "agent_started") {
        // A pre-contract agent_started carries neither run_id nor node; it opens
        // a card for the node that last started. The old executor stamped that
        // run's later rows with `node` = agent type (`reviewer` under `review`),
        // so the legacy run is also found under its agent.
        const runNode = node || lastStartedNode;
        const runId = event.run_id ?? `legacy:${index}`;
        const state = makeAgentState(runId, runNode, event.agent ?? runNode, attemptByNode.get(runNode) ?? 1, "running");
        state.model = event.model;
        state.startedAt = event.timestamp;
        runs.set(runId, state);
        latestRunByNode.set(runNode, runId);
        if (!event.run_id && event.agent) latestRunByNode.set(event.agent, runId);
        state.events.push(event);
        return;
      }

      const key = event.run_id ?? latestRunByNode.get(node);
      const agent = key ? runs.get(key) : undefined;
      if (!agent) return;
      agent.events.push(event);

      switch (event.type) {
        case "node_completed":
          if (agent.status === "running") agent.status = "completed";
          if (event.action) agent.summary = event.action;
          if (agent.durationMs === 0 && agent.startedAt && event.timestamp) {
            agent.durationMs = new Date(event.timestamp).getTime() - new Date(agent.startedAt).getTime();
          }
          break;
        case "tool_call": {
          const entry: ToolCallEntry = {
            tool_name: event.tool_name,
            tool_id: event.tool_id,
            input: event.summary,
            timestamp: event.timestamp,
            children: [],
          };
          // A child whose parent is unknown (lost to a bus overflow) stays at the top level.
          const parent = event.parent_tool_use_id ? findCall(agent.toolCalls, event.parent_tool_use_id) : undefined;
          (parent ? parent.children : agent.toolCalls).push(entry);
          agent.toolCallCount++;
          break;
        }
        case "tool_result": {
          const tc = findCall(agent.toolCalls, event.tool_id);
          if (tc) {
            tc.result = event.content;
            tc.is_error = event.is_error;
          }
          break;
        }
        case "subagent_start": {
          const tc = findCall(agent.toolCalls, event.tool_id);
          if (tc) tc.subagent = { name: event.name };
          break;
        }
        case "subagent_end": {
          const tc = findCall(agent.toolCalls, event.tool_id);
          if (tc?.subagent) tc.subagent.is_error = event.is_error;
          break;
        }
        case "thinking":
          if (event.text) agent.thinkingBlocks.push(event.text);
          break;
        case "text":
          if (event.text) agent.textBlocks.push(event.text);
          break;
        case "context_compacted":
          agent.textBlocks.push("[context compacted]");
          break;
        case "turn_start":
          agent.turnCount = Math.max(agent.turnCount, event.turn_number);
          agent.model ??= event.model;
          break;
        case "cost_update":
          agent.costUsd += event.cost_usd;
          agent.durationMs = event.duration_ms;
          break;
        case "agent_completed":
          agent.status = event.is_error ? "failed" : "completed";
          agent.summary = event.is_error ? event.error_message : event.output;
          agent.errorCode = event.error_code ?? undefined;
          agent.costUsd = event.cost_usd;
          agent.durationMs = event.duration_ms;
          break;
        case "error":
          // An error naming a tool is a recoverable denial: the run goes on.
          if (event.tool_name) break;
          agent.status = "failed";
          agent.summary = event.message;
          agent.errorCode = event.code;
          break;
        case "progress":
          agent.summary = event.message;
          break;
      }
    });

    const agents = [...runs.values()];

    // If the job is in a terminal state, mark any "running" agents as failed
    // (they were orphaned by an orchestrator restart or crash and never completed).
    if (isTerminalStatus(jobStatus)) {
      for (const agent of agents) {
        if (agent.status === "running") {
          const ok = jobStatus === "completed" || jobStatus === "pr_merged" || jobStatus === "skipped";
          agent.status = ok ? "completed" : "failed";
          if (!agent.summary) {
            agent.summary = ok ? "" : "Agent did not complete (orphaned)";
          }
        }
      }
    }

    // Graph nodes without a run card of their own get one card in their derived
    // state: pending for the nodes that have not started (deriveNodeStates only
    // reports nodes some event touched, so an absent entry is pending too), and
    // running/completed/failed for a node no agent runs on (QA, onboarding
    // steps), which would otherwise vanish from the Pipeline tab once it starts.
    if (graph) {
      const states = deriveNodeStates(graph, events, jobStatus);
      const withRun = new Set(agents.map((a) => a.node));
      const terminal = isTerminalStatus(jobStatus);
      for (const n of graph.nodes) {
        const st = states[n.node_id];
        const physical = (n as { physical_node?: string }).physical_node ?? n.node_id;
        const agentType = st?.agent_type ?? n.agent_type;
        if (!st || st.state === "pending" || st.state === "ready") {
          if (!terminal) agents.push(makeAgentState(`pending:${n.node_id}`, physical, agentType, 0, "pending"));
          continue;
        }
        if (withRun.has(physical)) continue;
        const card = makeAgentState(`node:${n.node_id}`, physical, agentType, st.iteration, st.state);
        card.costUsd = st.cost_usd;
        card.durationMs = st.duration_seconds * 1000;
        agents.push(card);
      }
    }

    return { agents, interruptData, prUrl };
  }, [events, jobStatus, graph]);
}
