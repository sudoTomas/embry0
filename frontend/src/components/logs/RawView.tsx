import { useEffect, useRef, useState, useCallback } from "react";
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
      return `[cost] $${event.cost_usd?.toFixed(4)} | tokens: ${event.tokens_in}\u2193 ${event.tokens_out}\u2191`;
    case "complete":
      return `[complete] cost=$${event.cost_usd?.toFixed(4)} turns=${event.turns} error=${event.is_error}`;
    case "progress":
      return `[progress] ${event.step}: ${event.status} \u2014 ${event.detail ?? ""}`;
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

export function RawView({ events }: RawViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [userScrolledUp, setUserScrolledUp] = useState(false);

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setUserScrolledUp(!atBottom);
  }, []);

  useEffect(() => {
    if (!userScrolledUp && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [events, userScrolledUp]);

  return (
    <div
      ref={containerRef}
      className="bg-background rounded-md border font-mono text-xs p-4 overflow-auto h-[calc(100vh-280px)]"
      onScroll={handleScroll}
    >
      {events.map((event, i) => (
        <div key={i} className="py-0.5 whitespace-pre-wrap">
          <span className="text-muted-foreground select-none">
            {new Date(event.timestamp).toLocaleTimeString()}{" "}
          </span>
          {eventToText(event)}
        </div>
      ))}
    </div>
  );
}
