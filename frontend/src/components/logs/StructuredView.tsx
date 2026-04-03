import { useMemo, useRef, useEffect, useState, useCallback } from "react";
import { ToolCallCard } from "./ToolCallCard";
import { cn, extractRole } from "@/lib/utils";
import { ROLE_COLORS, ROLE_BG_COLORS, ROLE_LABELS } from "@/lib/constants";
import { ROLE_ICON_MAP } from "@/lib/roleIcons";
import type { LogEvent } from "@/lib/types";

interface StructuredViewProps {
  events: LogEvent[];
}

interface ToolCall {
  tool: string;
  input: Record<string, unknown>;
  output?: string;
  error?: boolean;
  isActive: boolean;
  toolUseId: string;
  role?: string;
}

type StructuredItem =
  | { kind: "tool"; data: ToolCall }
  | { kind: "text"; content: string; timestamp: string }
  | { kind: "system"; message: string; timestamp: string }
  | { kind: "error"; message: string; timestamp: string }
  | { kind: "agent-transition"; role: string; name: string; timestamp: string };

export function StructuredView({ events }: StructuredViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [userScrolledUp, setUserScrolledUp] = useState(false);

  const items = useMemo(() => {
    const result: StructuredItem[] = [];
    const activeTools = new Map<string, number>();
    let currentRole: string | undefined;

    for (const event of events) {
      switch (event.type) {
        case "subagent_start": {
          const role = extractRole(event.name ?? "");
          if (role && role !== currentRole) {
            currentRole = role;
            result.push({
              kind: "agent-transition",
              role,
              name: event.name ?? role,
              timestamp: event.timestamp,
            });
          }
          break;
        }
        case "subagent_end": {
          // Push a system message for subagent completion
          result.push({
            kind: "system",
            message: `Subagent "${event.name}" completed`,
            timestamp: event.timestamp,
          });
          // Clear current role when the matching agent ends
          const endRole = extractRole(event.name ?? "");
          if (endRole === currentRole) {
            currentRole = undefined;
          }
          break;
        }
        case "tool_start": {
          const tc: ToolCall = {
            tool: event.tool ?? "unknown",
            input: (event.input as Record<string, unknown>) ?? {},
            isActive: true,
            toolUseId: event.tool_use_id ?? "",
            role: currentRole,
          };
          const idx = result.length;
          result.push({ kind: "tool", data: tc });
          if (event.tool_use_id) activeTools.set(event.tool_use_id, idx);
          break;
        }
        case "tool_end": {
          const idx = event.tool_use_id ? activeTools.get(event.tool_use_id) : undefined;
          if (idx != null && result[idx]?.kind === "tool") {
            const tc = (result[idx] as { kind: "tool"; data: ToolCall }).data;
            tc.output = event.output;
            tc.error = event.error;
            tc.isActive = false;
          }
          if (event.tool_use_id) activeTools.delete(event.tool_use_id);
          break;
        }
        case "text":
          if (event.content && typeof event.content === "string") {
            result.push({ kind: "text", content: event.content, timestamp: event.timestamp });
          }
          break;
        case "error":
          result.push({ kind: "error", message: event.message ?? "", timestamp: event.timestamp });
          break;
        case "system":
        case "progress":
        case "complete":
          result.push({
            kind: "system",
            message:
              event.type === "complete"
                ? `Agent completed \u2014 ${event.is_error ? "with errors" : "successfully"}`
                : event.type === "progress"
                  ? `${event.step}: ${event.status}`
                  : event.message ?? "",
            timestamp: event.timestamp,
          });
          break;
      }
    }
    return result;
  }, [events]);

  // Detect user scrolling up
  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    // Consider "at bottom" if within 40px of the bottom
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setUserScrolledUp(!atBottom);
  }, []);

  // Auto-scroll to bottom when new events arrive (unless user scrolled up)
  useEffect(() => {
    if (!userScrolledUp && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [items, userScrolledUp]);

  return (
    <div
      ref={containerRef}
      className="space-y-2 overflow-auto h-[calc(100vh-280px)]"
      onScroll={handleScroll}
    >
      {items.map((item, i) => {
        switch (item.kind) {
          case "agent-transition":
            return <AgentTransitionMarker key={i} role={item.role} name={item.name} />;
          case "tool":
            return (
              <ToolCallCard
                key={i}
                tool={item.data.tool}
                input={item.data.input}
                output={item.data.output}
                error={item.data.error}
                isActive={item.data.isActive}
                role={item.data.role}
              />
            );
          case "text":
            return (
              <div key={i} className="px-3 py-2 text-sm whitespace-pre-wrap">
                {item.content}
              </div>
            );
          case "error":
            return (
              <div key={i} className="px-3 py-2 text-sm text-destructive bg-destructive/5 rounded-md">
                {item.message}
              </div>
            );
          case "system":
            return (
              <div key={i} className="px-3 py-1 text-xs text-muted-foreground">
                {item.message}
              </div>
            );
        }
      })}
    </div>
  );
}

function AgentTransitionMarker({ role, name }: { role: string; name: string }) {
  const textColor = ROLE_COLORS[role] ?? "text-muted-foreground";
  const bgColor = ROLE_BG_COLORS[role];
  const label = ROLE_LABELS[role] ?? name;
  const IconComponent = ROLE_ICON_MAP[role];

  return (
    <div className="flex items-center gap-3 py-2">
      <div className={cn("h-px flex-1", bgColor ?? "bg-border")} style={{ opacity: 0.4 }} />
      <div className={cn("inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full", textColor, bgColor ? `${bgColor}/15` : "bg-muted")}>
        {IconComponent && <IconComponent className="h-3.5 w-3.5" />}
        <span>{label} Agent</span>
      </div>
      <div className={cn("h-px flex-1", bgColor ?? "bg-border")} style={{ opacity: 0.4 }} />
    </div>
  );
}
