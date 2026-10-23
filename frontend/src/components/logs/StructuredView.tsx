import { useMemo } from "react";
import { Virtuoso } from "react-virtuoso";
import { ToolCallCard } from "./ToolCallCard";
import { cn } from "@/lib/utils";
import { useAgentIdentity } from "@/hooks/useAgentIdentity";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import type { LogEvent } from "@/lib/types";

interface StructuredViewProps {
  events: LogEvent[];
  /** The job has finished: a call with no result will never get one. */
  isComplete?: boolean;
}

interface ToolCall {
  tool: string;
  input: Record<string, unknown>;
  output?: string;
  error?: boolean;
  isActive: boolean;
  toolUseId: string;
  children: ToolCall[];
}

type StructuredItem =
  | { kind: "tool"; data: ToolCall }
  | { kind: "text"; content: string; timestamp: string }
  | { kind: "thinking"; content: string; timestamp: string }
  | { kind: "turn"; turnNumber: number; timestamp: string }
  | { kind: "system"; message: string; timestamp: string }
  | { kind: "error"; message: string; code?: string; timestamp: string }
  | { kind: "agent-transition"; name: string; timestamp: string };

export function StructuredView({ events, isComplete = false }: StructuredViewProps) {
  const items = useMemo(() => {
    const result: StructuredItem[] = [];
    // Results and subagent names are looked up by tool_id, so build those maps first.
    const results = new Map<string, Extract<LogEvent, { type: "tool_result" }>>();
    const subagentNames = new Map<string, string>();
    const callIds = new Set<string>();
    for (const event of events) {
      if (event.type === "tool_result") results.set(event.tool_id, event);
      else if (event.type === "subagent_start") subagentNames.set(event.tool_id, event.name);
      else if (event.type === "tool_call") callIds.add(event.tool_id);
    }
    const calls = new Map<string, ToolCall>();

    for (const event of events) {
      switch (event.type) {
        case "tool_call": {
          const res = results.get(event.tool_id);
          const call: ToolCall = {
            tool: event.tool_name,
            input: event.tool_input,
            output: res?.content,
            error: res?.is_error,
            // A result lost to a bus overflow or an orphaned run never arrives.
            isActive: !res && !isComplete,
            toolUseId: event.tool_id,
            children: [],
          };
          calls.set(event.tool_id, call);
          const parentId = event.parent_tool_use_id;
          const parent = parentId && callIds.has(parentId) ? calls.get(parentId) : undefined;
          // A child whose parent is unknown (lost to a bus overflow) stays at the top level.
          if (parent) parent.children.push(call);
          else result.push({ kind: "tool", data: call });
          break;
        }
        case "subagent_start":
          result.push({
            kind: "agent-transition",
            name: event.name,
            timestamp: event.timestamp,
          });
          break;
        case "subagent_end": {
          const name = subagentNames.get(event.tool_id) ?? event.tool_id;
          result.push({
            kind: "system",
            message: `Subagent ${name} ${event.is_error ? "failed" : "completed"}`,
            timestamp: event.timestamp,
          });
          break;
        }
        case "turn_start":
          result.push({ kind: "turn", turnNumber: event.turn_number, timestamp: event.timestamp });
          break;
        case "thinking":
          if (event.text) {
            result.push({ kind: "thinking", content: event.text, timestamp: event.timestamp });
          }
          break;
        case "text":
          if (event.text) {
            result.push({ kind: "text", content: event.text, timestamp: event.timestamp });
          }
          break;
        case "error":
          result.push({ kind: "error", message: event.message, code: event.code, timestamp: event.timestamp });
          break;
        case "job_failed":
          result.push({ kind: "error", message: event.message, code: event.kind, timestamp: event.timestamp });
          break;
        case "progress":
          result.push({
            kind: "system",
            message: event.app ? `[${event.app}] ${event.message}` : event.message,
            timestamp: event.timestamp,
          });
          break;
        case "system":
          result.push({ kind: "system", message: event.message, timestamp: event.timestamp });
          break;
        case "job_finished":
          result.push({
            kind: "system",
            message: `Job ${event.status}${event.error_message ? `: ${event.error_message}` : ""}`,
            timestamp: event.timestamp,
          });
          break;
      }
    }
    return result;
  }, [events, isComplete]);

  return (
    <Virtuoso
      className="space-y-2 flex-1 min-h-0 max-h-[70vh] lg:max-h-none overflow-auto overscroll-contain"
      totalCount={items.length}
      itemContent={(i) => {
        const item = items[i];
        switch (item.kind) {
          case "agent-transition":
            return <AgentTransitionMarker name={item.name} />;
          case "tool":
            return <ToolCallNode call={item.data} />;
          case "turn":
            return (
              <div className="px-3 py-1 text-xs font-mono text-muted-foreground">
                Turn {item.turnNumber}
              </div>
            );
          case "thinking":
            return (
              <details className="px-3 py-1 text-xs text-muted-foreground">
                <summary className="cursor-pointer">Thinking</summary>
                <pre className="font-mono whitespace-pre-wrap mt-1">{item.content}</pre>
              </details>
            );
          case "text":
            return (
              <div className="px-3 py-2 text-sm whitespace-pre-wrap">
                {item.content}
              </div>
            );
          case "error":
            return (
              <div className="px-3 py-2 text-sm text-destructive bg-destructive/5 rounded-md">
                {item.message}
                {item.code && <span className="ml-2 font-mono text-xs">{item.code}</span>}
              </div>
            );
          case "system":
            return (
              <div className="px-3 py-1 text-xs text-muted-foreground">
                {item.message}
              </div>
            );
        }
      }}
      followOutput="smooth"
      increaseViewportBy={300}
    />
  );
}

function ToolCallNode({ call }: { call: ToolCall }) {
  return (
    <ToolCallCard
      tool={call.tool}
      input={call.input}
      output={call.output}
      error={call.error}
      isActive={call.isActive}
    >
      {call.children.length > 0 &&
        call.children.map((child) => <ToolCallNode key={child.toolUseId} call={child} />)}
    </ToolCallCard>
  );
}

function AgentTransitionMarker({ name }: { name: string }) {
  const identity = useAgentIdentity(name);
  return (
    <div className="flex items-center gap-3 py-2">
      <div className="h-px flex-1" style={{ background: identity.hex, opacity: 0.4 }} />
      <div className={cn("inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full border",
        identity.classes.text, identity.classes.bg, identity.classes.border)}>
        <AgentAvatar identity={identity} size={16} still />
        <span>{identity.displayName} Agent</span>
      </div>
      <div className="h-px flex-1" style={{ background: identity.hex, opacity: 0.4 }} />
    </div>
  );
}
