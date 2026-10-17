import { useMemo } from "react";
import type { LogEvent, NodeStartedContract } from "@/lib/types";
import { eventNode } from "@/lib/wire";
import { isTerminalStatus } from "@/lib/jobStatus";

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

/** Build an initial AgentState for a node. */
function makeAgentState(
  node: string,
  event: NodeStartedContract,
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
    // Only pr_created carries a PR URL; nothing reads it here yet.
    const prUrl: string | null = null;
    let interruptData: InterruptData | null = null;
    const nodeOrder: string[] = [];

    // Track retry counts per node
    const nodeStartCounts = new Map<string, number>();

    for (const event of events) {
      const node = eventNode(event);

      if (event.type === "interrupt") {
        interruptData = {
          reason: event.reason,
          retry_count: event.summary?.retry_count,
          latest_review: event.summary?.latest_review,
          options: event.summary?.options,
        };
      }

      if (event.type === "node_started" && node) {
        const startCount = (nodeStartCounts.get(node) || 0) + 1;
        nodeStartCounts.set(node, startCount);

        if (!agentMap.has(node)) {
          nodeOrder.push(node);
        }
        agentMap.set(node, makeAgentState(node, event, startCount));
      }

      const agent = agentMap.get(node);
      if (!agent) continue;

      agent.events.push(event);

      switch (event.type) {
        case "node_completed": {
          agent.status = "completed";
          if (event.action) agent.summary = event.action;
          // Compute duration if not set by cost_update
          if (agent.durationMs === 0 && agent.startedAt && event.timestamp) {
            agent.durationMs = new Date(event.timestamp).getTime() - new Date(agent.startedAt).getTime();
          }
          break;
        }
        case "tool_call":
          agent.toolCalls.push({
            tool_name: event.tool_name,
            tool_id: event.tool_id,
            input: event.summary,
            timestamp: event.timestamp,
          });
          agent.toolCallCount++;
          break;
        case "tool_result": {
          if (event.tool_id) {
            const tc = agent.toolCalls.find((t) => t.tool_id === event.tool_id);
            if (tc) {
              tc.result = event.content;
              tc.is_error = event.is_error;
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
