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
  startedAt?: string;
  turnCount: number;
  toolCallCount: number;
  summary: string;
  retryLabel?: string;
  model?: string;
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
      // LogEvent uses node_id; map to node for backward compat
      const node = (event as unknown as Record<string, unknown>).node_id as string
        ?? (event as unknown as Record<string, unknown>).node as string
        ?? "";

      if (event.type === "pipeline_stage_completed" && (event as unknown as Record<string, unknown>).pr_url) {
        prUrl = (event as unknown as Record<string, unknown>).pr_url as string;
      }

      // Detect interrupt / awaiting_input as interrupt data
      if (event.type === "awaiting_input" || (event as unknown as Record<string, unknown>).type === "interrupt") {
        const raw = event as unknown as Record<string, unknown>;
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
          agent: (event as unknown as Record<string, unknown>).agent as string || node,
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
          retryLabel: startCount > 1 ? `Retry ${startCount - 1}` : undefined,
          model: event.model,
        });
      }

      // Also handle legacy node_started events
      if ((event as unknown as Record<string, unknown>).type === "node_started" && node) {
        const startCount = (nodeStartCounts.get(node) || 0) + 1;
        nodeStartCounts.set(node, startCount);

        if (!agentMap.has(node)) {
          nodeOrder.push(node);
        }
        agentMap.set(node, {
          node,
          agent: (event as unknown as Record<string, unknown>).agent as string || node,
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
          retryLabel: startCount > 1 ? `Retry ${startCount - 1}` : undefined,
          model: event.model,
        });
      }

      const agent = agentMap.get(node);
      if (!agent) continue;

      agent.events.push(event);
      const raw = event as unknown as Record<string, unknown>;

      switch (event.type) {
        case "pipeline_stage_completed":
          agent.status = "completed";
          if (raw.action) agent.summary = raw.action as string;
          // Compute duration if not set by cost_update
          if (agent.durationMs === 0 && agent.startedAt && event.timestamp) {
            agent.durationMs = new Date(event.timestamp).getTime() - new Date(agent.startedAt).getTime();
          }
          break;
        case "tool_call":
          agent.toolCalls.push({
            tool_name: (raw.tool_name as string) || event.tool || "",
            tool_id: (raw.tool_id as string) || event.tool_use_id || "",
            input: typeof raw.input === "string" ? (raw.input as string) : JSON.stringify(raw.input ?? ""),
            timestamp: event.timestamp,
          });
          agent.toolCallCount++;
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
        case "tool_result":
        case "tool_end": {
          const toolUseId = (raw.tool_use_id as string) || event.tool_use_id || "";
          if (toolUseId) {
            const tc = agent.toolCalls.find((t) => t.tool_id === toolUseId);
            if (tc) {
              tc.result =
                (raw.content as string) ??
                event.output ??
                (event.content as string);
              tc.is_error = (raw.is_error as boolean) ?? event.is_error;
            }
          }
          break;
        }
        case "thinking":
          if (raw.text && typeof raw.text === "string") {
            agent.thinkingBlocks.push(raw.text as string);
          }
          break;
        case "text":
          if (raw.text && typeof raw.text === "string") {
            agent.textBlocks.push(raw.text as string);
          } else if (event.content && typeof event.content === "string") {
            agent.textBlocks.push(event.content);
          }
          break;
        case "turn_start":
          agent.turnCount++;
          // Only set model from FIRST turn — Claude Code spawns subagents
          // (often Haiku) for tool calls. We want to show the parent model.
          if (!agent.model) {
            if (raw.model) agent.model = raw.model as string;
            else if (event.model) agent.model = event.model;
          }
          break;
        case "cost_update":
          if (raw.cost_usd != null) agent.costUsd = raw.cost_usd as number;
          else if (event.cost_usd != null) agent.costUsd = event.cost_usd;
          // NOTE: Do NOT set durationMs from cost_update — it contains API
          // duration (time spent in Claude API calls), not wall-clock time.
          // Wall-clock duration is computed from node_started → node_completed.
          if (raw.num_turns != null) agent.turnCount = raw.num_turns as number;
          else if (event.turns != null) agent.turnCount = event.turns;
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
        // Compute duration if not set by cost_update
        if (agent.durationMs === 0 && agent.startedAt && event.timestamp) {
          agent.durationMs = new Date(event.timestamp).getTime() - new Date(agent.startedAt).getTime();
        }
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
