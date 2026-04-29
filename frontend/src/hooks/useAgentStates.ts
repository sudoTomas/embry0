import { useMemo } from "react";
import type {
  LogEvent,
  NodeStartedEvent,
  PipelineStageStartedEvent,
  PipelineStageCompletedEvent,
  NodeCompletedEvent,
  InterruptEvent,
  AwaitingInputEvent,
} from "@/lib/types";

export type AgentStatus = "pending" | "running" | "completed" | "failed";

export interface ToolCallEntry {
  tool_name: string;
  tool_id: string;
  input: string;
  result?: string;
  is_error?: boolean;
  timestamp?: string;
}

export interface InterruptData {
  reason?: string;
  retry_count?: number;
  latest_review?: string;
  options?: string[];
  paused_at?: string;
  ttl_hours?: number;
}

export interface AgentState {
  node: string;
  agent: string;
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
}

/** Extract the node_id from any event variant that carries it. */
function getNodeId(event: LogEvent): string {
  if ("node_id" in event && typeof event.node_id === "string") {
    return event.node_id;
  }
  return "";
}

/** Build an initial AgentState for a node. */
function makeAgentState(
  node: string,
  event: NodeStartedEvent | PipelineStageStartedEvent,
  retryCount: number,
): AgentState {
  return {
    node,
    agent: event.agent ?? node,
    status: "running",
    events: [],
    toolCalls: [],
    thinkingBlocks: [],
    textBlocks: [],
    costUsd: 0,
    durationMs: 0,
    startedAt: event.timestamp,
    turnCount: 0,
    toolCallCount: 0,
    summary: "",
    retryLabel: retryCount > 1 ? `Retry ${retryCount - 1}` : undefined,
    model: event.model,
  };
}

export function useAgentStates(
  events: LogEvent[],
  jobStatus?: string,
): {
  agents: AgentState[];
  activeAgents: AgentState[];
  completedAgents: AgentState[];
  pendingAgents: AgentState[];
  prUrl: string | null;
  interruptData: InterruptData | null;
} {
  return useMemo(() => {
    const agentMap = new Map<string, AgentState>();
    let prUrl: string | null = null;
    let interruptData: InterruptData | null = null;
    const nodeOrder: string[] = [];

    // Track retry counts per node
    const nodeStartCounts = new Map<string, number>();

    for (const event of events) {
      const node = getNodeId(event);

      if (event.type === "pipeline_stage_completed") {
        const ev = event as PipelineStageCompletedEvent;
        if (ev.pr_url) prUrl = ev.pr_url;
      }

      // Detect interrupt / awaiting_input as interrupt data
      if (event.type === "awaiting_input") {
        const ev = event as AwaitingInputEvent;
        interruptData = {
          reason: ev.question,
          options: ev.options ?? undefined,
        };
      }
      if (event.type === "interrupt") {
        const ev = event as InterruptEvent;
        interruptData = {
          reason: ev.reason,
          retry_count: ev.retry_count,
          latest_review: ev.latest_review,
          options: ev.options,
          paused_at: ev.paused_at,
          ttl_hours: ev.ttl_hours,
        };
      }

      if (event.type === "pipeline_stage_started" && node) {
        const startCount = (nodeStartCounts.get(node) || 0) + 1;
        nodeStartCounts.set(node, startCount);

        if (!agentMap.has(node)) {
          nodeOrder.push(node);
        }
        agentMap.set(node, makeAgentState(node, event as PipelineStageStartedEvent, startCount));
      }

      // Also handle legacy node_started events
      if (event.type === "node_started" && node) {
        const startCount = (nodeStartCounts.get(node) || 0) + 1;
        nodeStartCounts.set(node, startCount);

        if (!agentMap.has(node)) {
          nodeOrder.push(node);
        }
        agentMap.set(node, makeAgentState(node, event as NodeStartedEvent, startCount));
      }

      const agent = agentMap.get(node);
      if (!agent) continue;

      agent.events.push(event);

      switch (event.type) {
        case "pipeline_stage_completed": {
          const ev = event as PipelineStageCompletedEvent;
          agent.status = "completed";
          if (ev.action) agent.summary = ev.action;
          // Compute duration if not set by cost_update
          if (agent.durationMs === 0 && agent.startedAt && ev.timestamp) {
            agent.durationMs = new Date(ev.timestamp).getTime() - new Date(agent.startedAt).getTime();
          }
          break;
        }
        case "node_completed": {
          const ev = event as NodeCompletedEvent;
          agent.status = "completed";
          if (ev.action) agent.summary = ev.action;
          // Compute duration if not set by cost_update
          if (agent.durationMs === 0 && agent.startedAt && ev.timestamp) {
            agent.durationMs = new Date(ev.timestamp).getTime() - new Date(agent.startedAt).getTime();
          }
          break;
        }
        case "tool_call":
          agent.toolCalls.push({
            tool_name: event.tool_name,
            tool_id: event.tool_id,
            input: typeof event.input === "string"
              ? event.input
              : JSON.stringify(event.input ?? ""),
            timestamp: event.timestamp,
          });
          agent.toolCallCount++;
          break;
        case "tool_start":
          agent.toolCalls.push({
            tool_name: event.tool,
            tool_id: event.tool_use_id ?? "",
            input: typeof event.input === "string"
              ? event.input
              : JSON.stringify(event.input ?? ""),
            timestamp: event.timestamp,
          });
          agent.toolCallCount++;
          break;
        case "tool_result": {
          if (event.tool_use_id) {
            const tc = agent.toolCalls.find((t) => t.tool_id === event.tool_use_id);
            if (tc) {
              tc.result = event.content;
              tc.is_error = event.is_error;
            }
          }
          break;
        }
        case "tool_end": {
          if (event.tool_use_id) {
            const tc = agent.toolCalls.find((t) => t.tool_id === event.tool_use_id);
            if (tc) {
              // tool_end carries duration but not result content — leave result as-is
              tc.is_error = false;
            }
          }
          break;
        }
        case "thinking":
          // ThinkingEvent.text is the backend field name (not content)
          if (event.text) {
            agent.thinkingBlocks.push(event.text);
          }
          break;
        case "text":
          // TextEvent.text is the backend field name (not content)
          if (event.text) {
            agent.textBlocks.push(event.text);
          }
          break;
        case "turn_start":
          agent.turnCount++;
          // Only set model from FIRST turn — Claude Code spawns subagents
          // (often Haiku) for tool calls. We want to show the parent model.
          if (!agent.model && event.model) {
            agent.model = event.model;
          }
          break;
        case "cost_update":
          agent.costUsd = event.cost_usd;
          // NOTE: Do NOT set durationMs from cost_update — it contains API
          // duration (time spent in Claude API calls), not wall-clock time.
          // Wall-clock duration is computed from node_started → node_completed.
          agent.turnCount = event.num_turns;
          break;
        case "progress":
          agent.summary = event.message;
          break;
        case "error":
          agent.status = "failed";
          agent.summary = event.message;
          break;
      }
    }

    const agents = nodeOrder
      .filter((n) => n !== "init")
      .map((n) => agentMap.get(n)!)
      .filter(Boolean);

    // If the job is in a terminal state, mark any "running" agents as failed
    // (they were orphaned by an orchestrator restart or crash and never completed).
    const terminalStatuses = new Set(["failed", "cancelled", "expired", "completed", "pr_merged", "pr_closed"]);
    if (jobStatus && terminalStatuses.has(jobStatus)) {
      for (const agent of agents) {
        if (agent.status === "running") {
          agent.status = jobStatus === "completed" || jobStatus === "pr_merged" ? "completed" : "failed";
          if (!agent.summary) {
            agent.summary = jobStatus === "completed" ? "" : "Agent did not complete (orphaned)";
          }
        }
      }
    }

    const activeAgents = agents.filter((a) => a.status === "running");
    const completedAgents = agents.filter((a) => a.status === "completed" || a.status === "failed");
    const pendingAgents: AgentState[] = [];

    // Infer pending agents from pipeline structure
    const knownNodes = new Set(nodeOrder);
    const pipelineNodes = ["triage", "developer", "review"];
    for (const pn of pipelineNodes) {
      if (!knownNodes.has(pn)) {
        pendingAgents.push({
          node: pn,
          agent: pn,
          status: "pending",
          events: [],
          toolCalls: [],
          thinkingBlocks: [],
          textBlocks: [],
          costUsd: 0,
          durationMs: 0,
          turnCount: 0,
          toolCallCount: 0,
          summary: "",
        });
      }
    }

    return { agents, activeAgents, completedAgents, pendingAgents, prUrl, interruptData };
  }, [events, jobStatus]);
}
