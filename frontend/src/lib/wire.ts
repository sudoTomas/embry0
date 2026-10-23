import type { LogEvent } from "@/lib/types/events";
import { EVENT_FIELDS } from "@/lib/types/events";

/** Field names pre-contract rows used (spec section 6). Never part of the contract interfaces. */
interface LegacyWire {
  error?: unknown;
  input?: unknown;
  tool_use_id?: unknown;
  node_id?: unknown;
}

const KNOWN_TYPES = new Set<string>(Object.keys(EVENT_FIELDS));

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Where a parsed JSON object becomes a LogEvent (WebSocket onmessage, REST page).
 * Rows written before the contract are mapped to contract names here, once, so
 * reducers never read a legacy field: `error.error` -> `message`, a string
 * `input` -> `summary`, `tool_use_id` -> `tool_id`, `node_id` -> `node`,
 * agent_completed's nested `result.is_error`, `error_code` and `error_message` ->
 * the top-level fields. A row the
 * API could not decode (`{"type":"raw"}`) and any type outside the contract become
 * a `system` event carrying the text, so they stay visible instead of vanishing.
 */
export function normalizeWireEvent(raw: unknown): LogEvent {
  if (!isRecord(raw) || typeof raw.type !== "string") {
    return { type: "system", message: typeof raw === "string" ? raw : JSON.stringify(raw), timestamp: "" };
  }
  const { input, tool_use_id, node_id, ...rest } = raw as Record<string, unknown> & LegacyWire;
  const ev: Record<string, unknown> = { ...rest };

  if (typeof node_id === "string" && typeof ev.node !== "string") ev.node = node_id;
  if (ev.type === "error") {
    // `error` is a legacy field only on an error row; node_completed's `error` is contract.
    const { error } = ev as LegacyWire;
    delete ev.error;
    if (typeof ev.message !== "string") {
      ev.message = typeof error === "object" && error !== null ? JSON.stringify(error) : String(error ?? "");
    }
  }
  if (ev.type === "agent_completed" && isRecord(ev.result)) {
    // A pre-contract agent_completed carried the run's outcome in a nested
    // `result`; without its error fields the failed run read as a clean exit.
    // Its `output`, the agent's whole final reply, stays where it was: no
    // card ever showed it, and the Raw view still prints the row as stored.
    const result = ev.result;
    for (const key of ["is_error", "error_code", "error_message"] as const) {
      if (ev[key] === undefined && result[key] !== undefined) ev[key] = result[key];
    }
  }
  if (ev.type === "tool_call" && typeof ev.summary !== "string") {
    ev.summary = typeof input === "string" ? input : "";
  }
  if (ev.type === "tool_result" && typeof ev.tool_id !== "string" && typeof tool_use_id === "string") {
    ev.tool_id = tool_use_id;
  }
  if (typeof ev.timestamp !== "string") ev.timestamp = "";

  const seq = typeof ev.event_seq === "number" ? { event_seq: ev.event_seq } : {};
  if (ev.type === "raw") {
    return { type: "system", message: String(ev.message ?? ""), timestamp: "", ...seq };
  }
  if (!KNOWN_TYPES.has(ev.type as string)) {
    return { type: "system", message: `[${String(ev.type)}] ${JSON.stringify(ev)}`, timestamp: String(ev.timestamp), ...seq };
  }
  return ev as unknown as LogEvent;
}

/** The LangGraph node an event belongs to; "" for job-level events. */
export function eventNode(event: LogEvent): string {
  return typeof event.node === "string" ? event.node : "";
}

/** The agent type an event names (the generic node runs whichever agent this says); "" when it names none. */
export function eventAgent(event: LogEvent): string {
  return typeof event.agent === "string" ? event.agent : "";
}
