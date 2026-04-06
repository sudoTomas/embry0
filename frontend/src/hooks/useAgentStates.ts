import { useMemo } from "react";
import type { LogEvent } from "@/lib/types";

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
  turnCount: number;
  toolCallCount: number;
  summary: string;
  retryLabel?: string;
  model?: string;
}

export function useAgentStates(events: LogEvent[]): {
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
      // LogEvent uses node_id; map to node for backward compat
      const node = (event as Record<string, unknown>).node_id as string
        ?? (event as Record<string, unknown>).node as string
        ?? "";

      if (event.type === "pipeline_stage_completed" && (event as Record<string, unknown>).pr_url) {
        prUrl = (event as Record<string, unknown>).pr_url as string;
      }

      // Detect interrupt / awaiting_input as interrupt data
      if (event.type === "awaiting_input" || (event as Record<string, unknown>).type === "interrupt") {
        const raw = event as Record<string, unknown>;
        interruptData = {
          reason: raw.reason as string | undefined,
          retry_count: raw.retry_count as number | undefined,
          latest_review: raw.latest_review as string | undefined,
          options: raw.options as string[] | undefined,
          paused_at: raw.paused_at as string | undefined,
          ttl_hours: raw.ttl_hours as number | undefined,
        };
      }

      if (event.type === "pipeline_stage_started" && node) {
        const startCount = (nodeStartCounts.get(node) || 0) + 1;
        nodeStartCounts.set(node, startCount);

        if (!agentMap.has(node)) {
          nodeOrder.push(node);
        }
        agentMap.set(node, {
          node,
          agent: (event as Record<string, unknown>).agent as string || node,
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

      // Also handle legacy node_started events
      if ((event as Record<string, unknown>).type === "node_started" && node) {
        const startCount = (nodeStartCounts.get(node) || 0) + 1;
        nodeStartCounts.set(node, startCount);

        if (!agentMap.has(node)) {
          nodeOrder.push(node);
        }
        agentMap.set(node, {
          node,
          agent: (event as Record<string, unknown>).agent as string || node,
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
      const raw = event as Record<string, unknown>;

      switch (event.type) {
        case "pipeline_stage_completed":
          agent.status = "completed";
          if (raw.action) agent.summary = raw.action as string;
          break;
        case "tool_start":
          agent.toolCalls.push({
            tool_name: event.tool || "",
            tool_id: event.tool_use_id || "",
            input: typeof event.input === "string" ? event.input : JSON.stringify(event.input ?? ""),
            timestamp: event.timestamp,
          });
          agent.toolCallCount++;
          break;
        case "tool_end":
          if (event.tool_use_id) {
            const tc = agent.toolCalls.find((t) => t.tool_id === event.tool_use_id);
            if (tc) {
              tc.result = event.output ?? event.content as string;
              tc.is_error = event.is_error;
            }
          }
          break;
        case "text":
          if (event.content && typeof event.content === "string") agent.textBlocks.push(event.content);
          break;
        case "turn_start":
          agent.turnCount++;
          if (event.model) agent.model = event.model;
          break;
        case "cost_update":
          if (event.cost_usd != null) agent.costUsd = event.cost_usd;
          if (event.duration_ms != null) agent.durationMs = event.duration_ms;
          if (event.turns != null) agent.turnCount = event.turns;
          break;
        case "progress":
          if (event.message) agent.summary = event.message;
          break;
        case "error":
          agent.status = "failed";
          if (event.message) agent.summary = event.message;
          break;
      }

      // Handle legacy node_completed
      if (raw.type === "node_completed") {
        agent.status = "completed";
        if (raw.action) agent.summary = raw.action as string;
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
