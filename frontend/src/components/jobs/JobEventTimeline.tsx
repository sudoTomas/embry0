import { formatDate } from "@/lib/utils";
import type { JobEvent } from "@/lib/types/jobs";

interface JobEventTimelineProps {
  events: JobEvent[];
  connected: boolean;
}

const EVENT_ICONS: Record<string, string> = {
  node_started: "\u25B6",
  node_completed: "\u2713",
  agent_started: "\u2699",
  agent_completed: "\u2705",
  tool_call: "\u{1F527}",
  progress: "\u23F3",
  pr_created: "\u{1F517}",
  interrupt: "\u2753",
  error: "\u274C",
  validation_result: "\u{1F9EA}",
  review_decision: "\u{1F4CB}",
};

const EVENT_COLORS: Record<string, string> = {
  node_started: "text-blue-400",
  node_completed: "text-green-400",
  agent_started: "text-cyan-400",
  agent_completed: "text-green-400",
  tool_call: "text-amber-400",
  progress: "text-white/40",
  pr_created: "text-purple-400",
  interrupt: "text-yellow-400",
  error: "text-red-400",
  validation_result: "text-cyan-400",
  review_decision: "text-blue-400",
};

function formatEventMessage(event: JobEvent): string {
  switch (event.type) {
    case "node_started":
      return `${event.node} node started${event.agent ? ` (${event.agent} agent)` : ""}`;
    case "node_completed":
      return `${event.node} node completed${event.action ? ` \u2192 ${event.action}` : ""}`;
    case "agent_started":
      return `${event.agent} agent started (model: ${event.model || "default"})`;
    case "agent_completed":
      return `${event.agent} agent completed (${event.duration_ms ? `${(event.duration_ms / 1000).toFixed(1)}s` : ""}${event.cost_usd ? `, $${event.cost_usd.toFixed(4)}` : ""})`;
    case "tool_call":
      return `Tool: ${event.tool}${event.file_path ? ` \u2192 ${event.file_path}` : ""}`;
    case "progress":
      return event.message || "Processing...";
    case "pr_created":
      return `PR created: ${event.pr_url}`;
    case "interrupt":
      return `Awaiting input: ${event.questions?.length || 0} question(s)`;
    case "error":
      return `Error: ${event.message || "Unknown error"}`;
    case "validation_result": {
      const v = event.validation || {};
      const tests = (v.tests as Record<string, unknown>)?.status ?? "?";
      const lint = (v.lint as Record<string, unknown>)?.status ?? "?";
      return `Validation: tests=${tests}, lint=${lint}`;
    }
    case "review_decision":
      return `Review: ${event.decision} \u2014 ${event.summary || ""}`;
    default:
      return event.message || event.type;
  }
}

export function JobEventTimeline({ events, connected }: JobEventTimelineProps) {
  return (
    <div className="space-y-0">
      {/* Connection indicator */}
      <div className="flex items-center gap-2 mb-3 text-xs">
        <span
          className={`h-2 w-2 rounded-full ${connected ? "bg-green-400 animate-pulse" : "bg-white/20"}`}
        />
        <span className="text-white/40">{connected ? "Live" : "Disconnected"}</span>
        <span className="text-white/40 ml-auto">{events.length} events</span>
      </div>

      {events.length === 0 ? (
        <p className="text-sm text-white/40 py-4 text-center">No events yet.</p>
      ) : (
        events.map((event, idx) => {
          const icon = EVENT_ICONS[event.type] || "\u2022";
          const color = EVENT_COLORS[event.type] || "text-white/40";
          const message = formatEventMessage(event);

          return (
            <div
              key={idx}
              className="flex items-start gap-3 border-l-2 border-white/10 py-2 pl-4"
            >
              <span className={`mt-0.5 text-sm ${color}`}>{icon}</span>
              <div className="flex-1 min-w-0">
                <p className={`text-sm ${color}`}>{message}</p>
                {event.timestamp && (
                  <p className="text-xs text-white/30 mt-0.5">{formatDate(event.timestamp)}</p>
                )}
                {event.type === "pr_created" && event.pr_url && (
                  <a
                    href={event.pr_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-blue-400 hover:underline mt-1 inline-block"
                  >
                    {event.pr_url}
                  </a>
                )}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
