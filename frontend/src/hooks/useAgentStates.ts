import { useMemo } from "react";
import type { JobEvent } from "@/lib/types/jobs";

export type AgentStatus = "pending" | "running" | "completed" | "failed";

export interface ToolCallEntry {
  tool_name: string;
  tool_id: string;
  input: string;
  result?: string;
  is_error?: boolean;
  timestamp?: string;
}

export interface AgentState {
  node: string;
  agent: string;
  status: AgentStatus;
  events: JobEvent[];
  toolCalls: ToolCallEntry[];
  thinkingBlocks: string[];
  textBlocks: string[];
  costUsd: number;
  durationMs: number;
  turnCount: number;
  toolCallCount: number;
  summary: string;
  retryLabel?: string;
  model?: string;
}

export function useAgentStates(events: JobEvent[]): {
  agents: AgentState[];
  activeAgents: AgentState[];
  completedAgents: AgentState[];
  pendingAgents: AgentState[];
  prUrl: string | null;
  interruptData: JobEvent | null;
} {
  return useMemo(() => {
    const agentMap = new Map<string, AgentState>();
    let prUrl: string | null = null;
    let interruptData: JobEvent | null = null;
    const nodeOrder: string[] = [];

    // Track retry counts per node
    const nodeStartCounts = new Map<string, number>();

    for (const event of events) {
      const node = event.node || "";

      if (event.type === "pr_created" && event.pr_url) {
        prUrl = event.pr_url;
      }

      if (event.type === "interrupt") {
        interruptData = event;
      }

      if (event.type === "node_started" && node) {
        const startCount = (nodeStartCounts.get(node) || 0) + 1;
        nodeStartCounts.set(node, startCount);

        if (!agentMap.has(node)) {
          nodeOrder.push(node);
        }
        agentMap.set(node, {
          node,
          agent: event.agent || node,
          status: "running",
          events: [],
          toolCalls: [],
          thinkingBlocks: [],
          textBlocks: [],
          costUsd: 0,
          durationMs: 0,
          turnCount: 0,
          toolCallCount: 0,
          summary: "",
          retryLabel: startCount > 1 ? `Retry ${startCount - 1}` : undefined,
          model: event.model,
        });
      }

      const agent = agentMap.get(node);
      if (!agent) continue;

      agent.events.push(event);

      switch (event.type) {
        case "node_completed":
          agent.status = "completed";
          if (event.action) agent.summary = event.action;
          break;
        case "tool_call":
          agent.toolCalls.push({
            tool_name: event.tool_name || event.tool || "",
            tool_id: event.tool_id || "",
            input: event.input || "",
            timestamp: event.timestamp,
          });
          agent.toolCallCount++;
          break;
        case "tool_result":
          if (event.tool_use_id) {
            const tc = agent.toolCalls.find((t) => t.tool_id === event.tool_use_id);
            if (tc) {
              tc.result = event.content;
              tc.is_error = event.is_error;
            }
          }
          break;
        case "thinking":
          if (event.text) agent.thinkingBlocks.push(event.text);
          break;
        case "text":
          if (event.text) agent.textBlocks.push(event.text);
          break;
        case "turn_start":
          agent.turnCount++;
          if (event.model) agent.model = event.model;
          break;
        case "cost_update":
          if (event.cost_usd != null) agent.costUsd = event.cost_usd;
          if (event.duration_ms != null) agent.durationMs = event.duration_ms;
          if (event.num_turns != null) agent.turnCount = event.num_turns;
          break;
        case "progress":
          if (event.message) agent.summary = event.message;
          break;
        case "error":
          agent.status = "failed";
          if (event.message) agent.summary = event.message;
          break;
      }
    }

    const agents = nodeOrder
      .filter((n) => n !== "init")
      .map((n) => agentMap.get(n)!)
      .filter(Boolean);

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
  }, [events]);
}
