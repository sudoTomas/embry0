/**
 * Discriminated union of all WebSocket log event types emitted by the backend.
 *
 * Each variant interface extends BaseLogEvent and carries only the fields it
 * uses — no optional junk. Narrowing on `event.type` gives full typed access
 * to variant-specific fields without any `as unknown as` casts.
 */

import type { PipelineGraph } from "./pipelines";

// ─── Base ─────────────────────────────────────────────────────────────────────

interface BaseLogEvent {
  type: string;
  timestamp: string;
}

// ─── Simple content variants ──────────────────────────────────────────────────

export interface TextEvent extends BaseLogEvent {
  type: "text";
  /** The text content. Backend field name is "text". */
  text: string;
}

export interface ThinkingEvent extends BaseLogEvent {
  type: "thinking";
  /** The thinking content. Backend field name is "text". */
  text: string;
}

// ─── Tool variants ────────────────────────────────────────────────────────────

export interface ToolCallEvent extends BaseLogEvent {
  type: "tool_call";
  /** Backend emits as tool_name. */
  tool_name: string;
  /** Backend emits as tool_id. */
  tool_id: string;
  input: Record<string, unknown>;
}

export interface ToolResultEvent extends BaseLogEvent {
  type: "tool_result";
  tool_use_id: string;
  /** Backend emits tool result content as "content" (not "result"). */
  content: string;
  is_error: boolean;
}

export interface ToolStartEvent extends BaseLogEvent {
  type: "tool_start";
  tool: string;
  tool_use_id?: string;
  input: Record<string, unknown>;
}

export interface ToolEndEvent extends BaseLogEvent {
  type: "tool_end";
  tool: string;
  tool_use_id?: string;
  duration_ms?: number;
  /** Output from the tool call (may be populated by some backends). */
  output?: string;
  /** Whether the tool call resulted in an error. */
  error?: boolean;
}

// ─── Cost / turn tracking ─────────────────────────────────────────────────────

export interface CostUpdateEvent extends BaseLogEvent {
  type: "cost_update";
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  /** Backend emits turn count as num_turns. */
  num_turns: number;
  /** Wall-clock duration in ms (API call time, not wall clock). */
  duration_ms?: number;
}

export interface CompleteEvent extends BaseLogEvent {
  type: "complete";
  cost_usd?: number;
  tokens_in?: number;
  tokens_out?: number;
  turns?: number;
  /** Whether the completion was due to an error condition. */
  is_error?: boolean;
}

export interface StreamEndEvent extends BaseLogEvent {
  type: "stream_end";
}

// ─── Pipeline graph ───────────────────────────────────────────────────────────

export interface PipelineGraphEventDU extends BaseLogEvent {
  type: "pipeline_graph";
  graph: PipelineGraph;
}

// ─── Node state (already a named interface in events.ts — mirrored here) ──────

export interface NodeStateEvent extends BaseLogEvent {
  type: "node_state";
  node_id: string;
  agent_type: string;
  state: "pending" | "ready" | "running" | "completed" | "failed";
  iteration: number;
  cost_usd: number;
  turns: number;
  duration_seconds: number;
}

export interface NodeStartedEvent extends BaseLogEvent {
  type: "node_started";
  node_id: string;
  agent_type?: string;
  agent?: string;
  model?: string;
}

export interface NodeCompletedEvent extends BaseLogEvent {
  type: "node_completed";
  node_id: string;
  cost_usd?: number;
  turns?: number;
  action?: string;
}

// ─── Feedback loop ────────────────────────────────────────────────────────────

export interface FeedbackTriggeredEvent extends BaseLogEvent {
  type: "feedback_triggered";
  edge_id: string;
  source_node: string;
  target_node: string;
  iteration: number;
  max_loops: number | null;
  reason: string;
}

export interface FeedbackResolvedEvent extends BaseLogEvent {
  type: "feedback_resolved";
  edge_id: string;
  reason: string;
  total_iterations: number;
  total_loop_cost_usd: number;
}

// ─── Pipeline stage ───────────────────────────────────────────────────────────

export interface PipelineStageStartedEvent extends BaseLogEvent {
  type: "pipeline_stage_started";
  node_id: string;
  agent_type?: string;
  agent?: string;
  model?: string;
}

export interface PipelineStageCompletedEvent extends BaseLogEvent {
  type: "pipeline_stage_completed";
  node_id: string;
  pr_url?: string;
  cost_usd?: number;
  action?: string;
}

// ─── User input / ask-user flow ───────────────────────────────────────────────

export interface AwaitingInputEvent extends BaseLogEvent {
  type: "awaiting_input";
  input_id: string;
  node_id: string;
  question: string;
  category: string;
  options: string[] | null;
}

export interface AutoAnsweredEvent extends BaseLogEvent {
  type: "auto_answered";
  input_id: string;
  node_id: string;
  question: string;
  answer: string;
  category: string;
}

export interface InputResumedEvent extends BaseLogEvent {
  type: "input_resumed";
  input_id: string;
  answered_by: string;
}

// ─── Conversation capture ─────────────────────────────────────────────────────

export interface ConversationTextBlock {
  type: "text";
  text: string;
}

export interface ConversationThinkingBlock {
  type: "thinking";
  thinking: string;
}

export interface ConversationToolUseBlock {
  type: "tool_use";
  id: string;
  name: string;
  input: Record<string, unknown>;
}

export interface ConversationToolResultBlock {
  type: "tool_result";
  tool_use_id: string;
  content: string | Record<string, unknown>[] | null;
  is_error: boolean | null;
}

export type ConversationContentBlock =
  | ConversationTextBlock
  | ConversationThinkingBlock
  | ConversationToolUseBlock
  | ConversationToolResultBlock;

export interface ConversationMessageEvent extends BaseLogEvent {
  type: "conversation_message";
  node_id?: string;
  role: "assistant" | "user" | "system";
  content: ConversationContentBlock[];
  turn_number: number;
  model?: string;
}

// ─── Interrupt / PR / agent lifecycle ────────────────────────────────────────

export interface InterruptEvent extends BaseLogEvent {
  type: "interrupt";
  reason?: string;
  retry_count?: number;
  latest_review?: string;
  options?: string[];
  paused_at?: string;
  ttl_hours?: number;
}

export interface PrCreatedEvent extends BaseLogEvent {
  type: "pr_created";
  pr_url: string;
}

export interface AgentStartedEvent extends BaseLogEvent {
  type: "agent_started";
  node_id?: string;
  agent_type?: string;
}

export interface AgentCompletedEvent extends BaseLogEvent {
  type: "agent_completed";
  node_id?: string;
  cost_usd?: number;
}

// ─── Turn / subagent tracking ─────────────────────────────────────────────────

export interface TurnStartEvent extends BaseLogEvent {
  type: "turn_start";
  turn_number?: number;
  model?: string;
}

export interface SubagentStartEvent extends BaseLogEvent {
  type: "subagent_start";
  name?: string;
  step?: string;
}

export interface SubagentEndEvent extends BaseLogEvent {
  type: "subagent_end";
  name?: string;
}

// ─── Progress / errors / system / finding ─────────────────────────────────────

export interface ProgressEvent extends BaseLogEvent {
  type: "progress";
  message: string;
  step?: string;
  status?: string;
  detail?: string;
}

export interface ErrorEvent extends BaseLogEvent {
  type: "error";
  message: string;
  detail?: string;
}

export interface SystemEvent extends BaseLogEvent {
  type: "system";
  message: string;
}

export interface FindingPublishedEvent extends BaseLogEvent {
  type: "finding_published";
  message: string;
  category?: string;
}

// ─── Discriminated union ──────────────────────────────────────────────────────

/** Discriminated union of all event types emitted by the backend WS stream. */
export type LogEventDU =
  | TextEvent
  | ThinkingEvent
  | ToolCallEvent
  | ToolResultEvent
  | ToolStartEvent
  | ToolEndEvent
  | CostUpdateEvent
  | CompleteEvent
  | StreamEndEvent
  | PipelineGraphEventDU
  | NodeStateEvent
  | NodeStartedEvent
  | NodeCompletedEvent
  | FeedbackTriggeredEvent
  | FeedbackResolvedEvent
  | PipelineStageStartedEvent
  | PipelineStageCompletedEvent
  | AwaitingInputEvent
  | AutoAnsweredEvent
  | InputResumedEvent
  | ConversationMessageEvent
  | InterruptEvent
  | PrCreatedEvent
  | AgentStartedEvent
  | AgentCompletedEvent
  | TurnStartEvent
  | SubagentStartEvent
  | SubagentEndEvent
  | ProgressEvent
  | ErrorEvent
  | SystemEvent
  | FindingPublishedEvent;
