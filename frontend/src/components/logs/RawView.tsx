import { Virtuoso } from "react-virtuoso";
import type { LogEvent } from "@/lib/types";

interface RawViewProps {
  events: LogEvent[];
}

/** Fields every event may carry; the fallback JSON leaves them out. */
const BASE_FIELDS = new Set([
  "type", "timestamp", "job_id", "event_seq", "prev_seq", "run_id", "node", "agent", "parent_tool_use_id",
]);

function formatTime(timestamp: string): string {
  if (!timestamp) return "--:--:--";
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? "--:--:--" : date.toLocaleTimeString();
}

function usd(value: number | null): string {
  return value == null ? "unpriced" : `$${value.toFixed(4)}`;
}

/** The headline field of each event type; compact JSON for the rest. */
function eventSummary(event: LogEvent): string {
  switch (event.type) {
    case "text":
    case "thinking":
      return event.text.slice(0, 80);
    case "tool_call":
      return `${event.tool_name} ${event.summary}`;
    case "tool_result":
      return `${event.tool_id} ${event.is_error ? "error" : "ok"}`;
    case "progress":
    case "system":
    case "error":
    case "job_failed":
      return event.message;
    case "turn_start":
      return `turn ${event.turn_number}`;
    case "turn_usage":
      return `tokens ${event.input_tokens}↓ ${event.output_tokens}↑ · ${usd(event.cost_usd)}`;
    case "cost_update":
      return `${usd(event.cost_usd)} · tokens ${event.tokens_in}↓ ${event.tokens_out}↑`;
    case "node_started":
      return `${event.node ?? ""} attempt ${event.attempt}`;
    case "node_completed":
      return `${event.node ?? ""} attempt ${event.attempt}${event.action ? ` ${event.action}` : ""}`;
    case "subagent_start":
      return event.name;
    case "subagent_end":
      return event.tool_id;
    case "job_finished":
      return event.status;
    default: {
      const payload = Object.fromEntries(Object.entries(event).filter(([key]) => !BASE_FIELDS.has(key)));
      return JSON.stringify(payload);
    }
  }
}

function RawEventRow({ event }: { event: LogEvent }) {
  return (
    <div className="py-0.5 whitespace-pre-wrap" data-testid="raw-row">
      <span className="text-muted-foreground select-none">
        {formatTime(event.timestamp)}{" "}
      </span>
      {event.event_seq != null && <span className="text-muted-foreground">#{event.event_seq} </span>}
      {event.type} {eventSummary(event)}
    </div>
  );
}

export function RawView({ events }: RawViewProps) {
  return (
    <Virtuoso
      className="bg-background rounded-md border font-mono text-xs p-4"
      style={{ height: "calc(100vh - 280px)" }}
      totalCount={events.length}
      itemContent={(i) => <RawEventRow event={events[i]} />}
      followOutput="smooth"
    />
  );
}
