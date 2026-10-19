/**
 * One union, mirrored from docs/contracts/events.json (embry0/execution/events.py);
 * see spec 3.1. `EVENT_FIELDS` is the same contract as data;
 * src/lib/types/__tests__/events.contract.test.ts compares it with the JSON, and
 * the guards at the end make `tsc -b` fail when an interface and its row disagree.
 */

import type { PipelineGraph } from "./pipelines";

export interface ContractBase {
  type: string;
  timestamp: string;
  job_id?: string;
  event_seq?: number;
  /** The job's previous persisted event_seq; event_seq is job_logs.id, shared by every job. */
  prev_seq?: number;
  run_id?: string;
  node?: string;
  agent?: string;
  parent_tool_use_id?: string;
}

interface Truncatable {
  truncated?: boolean;
  original_length?: number;
}

export interface AgentStartedContract extends ContractBase { type: "agent_started"; model: string; max_turns: number }
export interface TurnStartContract extends ContractBase { type: "turn_start"; turn_number: number; model: string }
export interface TurnUsageContract extends ContractBase {
  type: "turn_usage"; input_tokens: number; output_tokens: number; cache_read_tokens: number;
  cache_creation_tokens: number; cost_usd: number | null; model: string;
}
export interface TextContract extends ContractBase, Truncatable { type: "text"; text: string }
export interface ThinkingContract extends ContractBase, Truncatable { type: "thinking"; text: string }
/** Set on a stored event whose code the job log retention sweep replaced (ADR 0017). */
interface Redactable {
  redacted?: Record<string, unknown>;
}

export interface ToolCallContract extends ContractBase, Redactable {
  type: "tool_call"; tool_id: string; tool_name: string; summary: string; tool_input: Record<string, unknown>;
}
export interface ToolResultContract extends ContractBase, Truncatable, Redactable { type: "tool_result"; tool_id: string; content: string; is_error: boolean }
export interface SubagentStartContract extends ContractBase { type: "subagent_start"; tool_id: string; name: string }
export interface SubagentEndContract extends ContractBase { type: "subagent_end"; tool_id: string; is_error: boolean }
export interface ContextCompactedContract extends ContractBase { type: "context_compacted"; pre_tokens?: number }
export interface AgentAskUserContract extends ContractBase {
  type: "agent_ask_user"; question: string; category: string; options: string[]; importance: string; suggested_answer?: string;
}
export interface CostUpdateContract extends ContractBase {
  type: "cost_update"; cost_usd: number; duration_ms: number; duration_api_ms: number; num_turns: number;
  tokens_in: number; tokens_out: number; cache_read_tokens: number; cache_creation_tokens: number;
}
export interface AgentCompletedContract extends ContractBase, Truncatable {
  type: "agent_completed"; is_error: boolean; error_code: string | null; error_message: string; output: string;
  cost_usd: number; duration_ms: number; tools_called: Record<string, number>; policy_denials: Record<string, number>;
  input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_creation_tokens: number;
}
export interface ErrorContract extends ContractBase { type: "error"; message: string; code?: string; tool_name?: string }
export interface NodeStartedContract extends ContractBase { type: "node_started"; attempt: number; round?: number }
export interface NodeCompletedContract extends ContractBase {
  type: "node_completed"; attempt: number; action?: string; job_kind?: string; template?: string; deliverables?: number; ok?: boolean; round?: number; error?: string;
}
export interface PipelineGraphContract extends ContractBase { type: "pipeline_graph"; graph: PipelineGraph }
export interface ProgressContract extends ContractBase { type: "progress"; message: string; app?: string }
/** What the issue-to-pr interrupts put in `interrupt.summary` (embry0/workflows/issue_to_pr/nodes/_interrupts.py). */
export interface InterruptSummary {
  message?: string;
  reason?: string;
  pr_url?: string;
  retry_count?: number;
  options?: string[];
  latest_review?: string;
  review_error?: string;
  [key: string]: unknown;
}
export interface InterruptContract extends ContractBase { type: "interrupt"; reason?: string; questions?: Record<string, unknown>[]; summary?: InterruptSummary }
export interface AgentPausedForInputContract extends ContractBase { type: "agent_paused_for_input"; count: number }
export interface ReviewDecisionContract extends ContractBase { type: "review_decision"; decision: string | null; summary: string | null; duplicates_open_pr?: Record<string, unknown> }
export interface ValidationResultContract extends ContractBase { type: "validation_result"; validation: Record<string, unknown> }
export interface VerificationResultContract extends ContractBase {
  type: "verification_result"; tier: string; gate_verdict: string; blocking: boolean;
  browser_qa?: Record<string, unknown>; human_merge_only?: boolean; mode?: string;
}
export interface PrCreatedContract extends ContractBase { type: "pr_created"; pr_url: string; branch: string }
export interface QaAppsResolvedContract extends ContractBase { type: "qa_apps_resolved"; apps: string[] }
export interface QaSubtaskStartedContract extends ContractBase { type: "qa_subtask_started"; app: string; target: string }
export interface QaSubtaskFinishedContract extends ContractBase { type: "qa_subtask_finished"; app: string; status: string; duration_ms: number; failure_summary?: string }
export interface QaSubtaskStatusContract extends ContractBase {
  type: "qa_subtask_status"; app: string; status: string; duration_ms?: number; failure_summary?: string; boot_phase?: Record<string, unknown>;
}
export interface QaAgentTransientRetryContract extends ContractBase { type: "qa_agent_transient_retry"; app: string; reason: string }
export interface SystemContract extends ContractBase { type: "system"; message: string }
export interface JobFailedContract extends ContractBase { type: "job_failed"; kind: string; message: string }
export interface JobFinishedContract extends ContractBase {
  type: "job_finished"; status: string; error_code: string | null; error_message: string | null; pr_url: string | null; total_cost_usd: number;
}

export type ContractEvent =
  | AgentStartedContract | TurnStartContract | TurnUsageContract | TextContract | ThinkingContract
  | ToolCallContract | ToolResultContract | SubagentStartContract | SubagentEndContract | ContextCompactedContract
  | AgentAskUserContract | CostUpdateContract | AgentCompletedContract | ErrorContract
  | NodeStartedContract | NodeCompletedContract | PipelineGraphContract | ProgressContract | InterruptContract
  | AgentPausedForInputContract | ReviewDecisionContract | ValidationResultContract | VerificationResultContract
  | PrCreatedContract | QaAppsResolvedContract | QaSubtaskStartedContract | QaSubtaskFinishedContract
  | QaSubtaskStatusContract | QaAgentTransientRetryContract | SystemContract | JobFailedContract | JobFinishedContract;

export type ContractEventType = ContractEvent["type"];

/** The exported name of the one union; twenty files import it. */
export type LogEvent = ContractEvent;

type Fields = { required: readonly string[]; optional: readonly string[] };

const BASE_OPTIONAL = ["agent", "event_seq", "job_id", "node", "parent_tool_use_id", "prev_seq", "run_id"] as const;
const TRUNC = ["original_length", "truncated"] as const;
/** A sorted copy that keeps the members literal. `sorted([...BASE_OPTIONAL, "x"])` widens the
 *  fresh literal to string, which makes RowKeys<T> string and both guards below vacuous. */
const sorted = <const T extends readonly string[]>(fields: T): readonly T[number][] => [...fields].sort();

/** Same contract as data. Keys and field lists must equal docs/contracts/events.json (sorted). */
export const EVENT_FIELDS = {
  agent_started: { required: ["max_turns", "model", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  turn_start: { required: ["model", "timestamp", "turn_number", "type"], optional: [...BASE_OPTIONAL] },
  turn_usage: { required: ["cache_creation_tokens", "cache_read_tokens", "cost_usd", "input_tokens", "model", "output_tokens", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  text: { required: ["text", "timestamp", "type"], optional: sorted([...BASE_OPTIONAL, ...TRUNC]) },
  thinking: { required: ["text", "timestamp", "type"], optional: sorted([...BASE_OPTIONAL, ...TRUNC]) },
  tool_call: { required: ["summary", "timestamp", "tool_id", "tool_input", "tool_name", "type"], optional: sorted([...BASE_OPTIONAL, "redacted"]) },
  tool_result: { required: ["content", "is_error", "timestamp", "tool_id", "type"], optional: sorted([...BASE_OPTIONAL, ...TRUNC, "redacted"]) },
  subagent_start: { required: ["name", "timestamp", "tool_id", "type"], optional: [...BASE_OPTIONAL] },
  subagent_end: { required: ["is_error", "timestamp", "tool_id", "type"], optional: [...BASE_OPTIONAL] },
  context_compacted: { required: ["timestamp", "type"], optional: sorted([...BASE_OPTIONAL, "pre_tokens"]) },
  agent_ask_user: { required: ["category", "importance", "options", "question", "timestamp", "type"], optional: sorted([...BASE_OPTIONAL, "suggested_answer"]) },
  cost_update: { required: ["cache_creation_tokens", "cache_read_tokens", "cost_usd", "duration_api_ms", "duration_ms", "num_turns", "timestamp", "tokens_in", "tokens_out", "type"], optional: [...BASE_OPTIONAL] },
  agent_completed: { required: ["cache_creation_tokens", "cache_read_tokens", "cost_usd", "duration_ms", "error_code", "error_message", "input_tokens", "is_error", "output", "output_tokens", "policy_denials", "timestamp", "tools_called", "type"], optional: sorted([...BASE_OPTIONAL, ...TRUNC]) },
  error: { required: ["message", "timestamp", "type"], optional: sorted([...BASE_OPTIONAL, "code", "tool_name"]) },
  node_started: { required: ["attempt", "timestamp", "type"], optional: sorted([...BASE_OPTIONAL, "round"]) },
  node_completed: { required: ["attempt", "timestamp", "type"], optional: sorted([...BASE_OPTIONAL, "action", "deliverables", "error", "job_kind", "ok", "round", "template"]) },
  pipeline_graph: { required: ["graph", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  progress: { required: ["message", "timestamp", "type"], optional: sorted([...BASE_OPTIONAL, "app"]) },
  interrupt: { required: ["timestamp", "type"], optional: sorted([...BASE_OPTIONAL, "questions", "reason", "summary"]) },
  agent_paused_for_input: { required: ["count", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  review_decision: { required: ["decision", "summary", "timestamp", "type"], optional: sorted([...BASE_OPTIONAL, "duplicates_open_pr"]) },
  validation_result: { required: ["timestamp", "type", "validation"], optional: [...BASE_OPTIONAL] },
  verification_result: { required: ["blocking", "gate_verdict", "tier", "timestamp", "type"], optional: sorted([...BASE_OPTIONAL, "browser_qa", "human_merge_only", "mode"]) },
  pr_created: { required: ["branch", "pr_url", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  qa_apps_resolved: { required: ["apps", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  qa_subtask_started: { required: ["app", "target", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  qa_subtask_finished: { required: ["app", "duration_ms", "status", "timestamp", "type"], optional: sorted([...BASE_OPTIONAL, "failure_summary"]) },
  qa_subtask_status: { required: ["app", "status", "timestamp", "type"], optional: sorted([...BASE_OPTIONAL, "boot_phase", "duration_ms", "failure_summary"]) },
  qa_agent_transient_retry: { required: ["app", "reason", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  system: { required: ["message", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  job_failed: { required: ["kind", "message", "timestamp", "type"], optional: [...BASE_OPTIONAL] },
  job_finished: { required: ["error_code", "error_message", "pr_url", "status", "timestamp", "total_cost_usd", "type"], optional: [...BASE_OPTIONAL] },
} as const satisfies Record<ContractEventType, Fields>;

// Compile-time guards, both directions: every key of each contract interface
// appears in its row (required or optional), and every row field appears in
// its interface. Without the second, "backend adds a field, JSON regenerated,
// row updated for the vitest, interface forgotten" compiles silently.
type RowKeys<T extends ContractEventType> = (typeof EVENT_FIELDS)[T]["required"][number] | (typeof EVENT_FIELDS)[T]["optional"][number];
type Missing = { [T in ContractEventType]: Exclude<keyof Extract<ContractEvent, { type: T }>, RowKeys<T>> }[ContractEventType];
const _everyInterfaceKeyIsInItsRow: Missing extends never ? true : never = true;
void _everyInterfaceKeyIsInItsRow;
type Extra = { [T in ContractEventType]: Exclude<RowKeys<T>, keyof Extract<ContractEvent, { type: T }>> }[ContractEventType];
const _everyRowFieldIsInItsInterface: Extra extends never ? true : never = true;
void _everyRowFieldIsInItsInterface;
