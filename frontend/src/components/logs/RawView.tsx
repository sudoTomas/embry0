import { Virtuoso } from "react-virtuoso";
import type { LogEvent } from "@/lib/types";

interface RawViewProps {
  events: LogEvent[];
}

function eventToText(event: LogEvent): string {
  switch (event.type) {
    case "tool_start":
      return `[tool] ${event.tool} starting...`;
    case "tool_end":
      return `[tool] ${event.tool} ${event.error ? "FAILED" : "done"}\n${event.output ?? ""}`;
    case "text":
      return event.text ?? "";
    case "system":
      return `[system] ${event.message ?? ""}`;
    case "error":
      return `[ERROR] ${event.message ?? ""}`;
    case "cost_update":
      return `[cost] $${event.cost_usd?.toFixed(4)} | tokens: ${event.tokens_in}↓ ${event.tokens_out}↑`;
    case "complete":
      return `[complete] cost=$${event.cost_usd?.toFixed(4)} turns=${event.turns} error=${event.is_error}`;
    case "progress":
      return `[progress] ${event.step}: ${event.status} — ${event.detail ?? ""}`;
    case "turn_start":
      return `[turn] Turn started`;
    case "subagent_start":
      return `[subagent] ${event.name} starting...`;
    case "subagent_end":
      return `[subagent] ${event.name} completed`;
    default:
      return `[${event.type}] ${JSON.stringify(event)}`;
  }
}

function RawEventRow({ event }: { event: LogEvent }) {
  return (
    <div className="py-0.5 whitespace-pre-wrap">
      <span className="text-muted-foreground select-none">
        {new Date(event.timestamp).toLocaleTimeString()}{" "}
      </span>
      {eventToText(event)}
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
