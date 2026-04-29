/**
 * Event type definitions for the backend WebSocket log stream.
 *
 * The per-variant interfaces live in events-discriminated.ts. This file
 * re-exports everything for back-compat so existing import sites do not need
 * to change.
 */

// Re-export all discriminated union variants and the union itself.
export type {
  LogEventDU,
  TextEvent,
  ThinkingEvent,
  ToolCallEvent,
  ToolResultEvent,
  ToolStartEvent,
  ToolEndEvent,
  CostUpdateEvent,
  CompleteEvent,
  StreamEndEvent,
  PipelineGraphEventDU,
  NodeStateEvent,
  NodeStartedEvent,
  NodeCompletedEvent,
  FeedbackTriggeredEvent,
  FeedbackResolvedEvent,
  PipelineStageStartedEvent,
  PipelineStageCompletedEvent,
  AwaitingInputEvent,
  AutoAnsweredEvent,
  InputResumedEvent,
  ConversationContentBlock,
  ConversationTextBlock,
  ConversationThinkingBlock,
  ConversationToolUseBlock,
  ConversationToolResultBlock,
  ConversationMessageEvent,
  InterruptEvent,
  PrCreatedEvent,
  AgentStartedEvent,
  AgentCompletedEvent,
  TurnStartEvent,
  SubagentStartEvent,
  SubagentEndEvent,
  ProgressEvent,
  ErrorEvent,
  SystemEvent,
  FindingPublishedEvent,
} from "./events-discriminated";

export type LogEventType =
  | "text"
  | "tool_start"
  | "tool_end"
  | "tool_call"
  | "tool_result"
  | "thinking"
  | "cost_update"
  | "turn_start"
  | "subagent_start"
  | "subagent_end"
  | "progress"
  | "error"
  | "complete"
  | "system"
  | "stream_end"
  | "pipeline_graph"
  | "node_state"
  | "node_started"
  | "node_completed"
  | "feedback_triggered"
  | "feedback_resolved"
  | "awaiting_input"
  | "auto_answered"
  | "input_resumed"
  | "conversation_message"
  | "agent_started"
  | "agent_completed"
  | "pipeline_stage_started"
  | "pipeline_stage_completed"
  | "pr_created"
  | "interrupt"
  | "finding_published";

/** Back-compat alias: PipelineGraphEvent → PipelineGraphEventDU */
export type { PipelineGraphEventDU as PipelineGraphEvent } from "./events-discriminated";

import type { LogEventDU } from "./events-discriminated";

/** Discriminated union of all event types emitted by the backend WS stream. */
export type LogEvent = LogEventDU;
