import type { PipelineGraph } from "./pipelines";

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

export interface LogEvent {
  type: LogEventType;
  timestamp: string;
  tool?: string;
  input?: Record<string, unknown>;
  tool_use_id?: string;
  output?: string;
  error?: boolean;
  duration_ms?: number;
  cost_usd?: number;
  tokens_in?: number;
  tokens_out?: number;
  turns?: number;
  is_error?: boolean;
  result?: string;
  content?: string | ConversationContentBlock[];
  message?: string;
  name?: string;
  step?: string;
  status?: string;
  detail?: string;
  node_id?: string;
  graph?: PipelineGraph;
  input_id?: string;
  question?: string;
  category?: string;
  options?: string[] | null;
  answer?: string;
  answered_by?: string;
  // Conversation message fields
  role?: "assistant" | "user" | "system";
  turn_number?: number;
  model?: string;
  prompt?: string;
}

export interface NodeStateEvent {
  type: "node_state";
  node_id: string;
  agent_type: string;
  state: "pending" | "ready" | "running" | "completed" | "failed";
  iteration: number;
  cost_usd: number;
  turns: number;
  duration_seconds: number;
}

export interface FeedbackTriggeredEvent {
  type: "feedback_triggered";
  edge_id: string;
  source_node: string;
  target_node: string;
  iteration: number;
  max_loops: number | null;
  reason: string;
}

export interface FeedbackResolvedEvent {
  type: "feedback_resolved";
  edge_id: string;
  reason: string;
  total_iterations: number;
  total_loop_cost_usd: number;
}

export interface PipelineGraphEvent {
  type: "pipeline_graph";
  graph: PipelineGraph;
}

export interface AwaitingInputEvent {
  type: "awaiting_input";
  input_id: string;
  node_id: string;
  question: string;
  category: string;
  options: string[] | null;
}

export interface AutoAnsweredEvent {
  type: "auto_answered";
  input_id: string;
  node_id: string;
  question: string;
  answer: string;
  category: string;
}

export interface InputResumedEvent {
  type: "input_resumed";
  input_id: string;
  answered_by: string;
}

// --- Conversation message types (full LLM conversation capture) ---

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

export interface ConversationMessageEvent {
  type: "conversation_message";
  timestamp: string;
  node_id?: string;
  role: "assistant" | "user" | "system";
  content: ConversationContentBlock[];
  turn_number: number;
  model?: string;
}
