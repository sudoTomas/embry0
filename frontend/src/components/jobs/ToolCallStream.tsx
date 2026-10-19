import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ToolCallEntry } from "@/hooks/useAgentStates";

interface ToolCallStreamProps {
  toolCalls: ToolCallEntry[];
  maxHeight?: number;
}

const TOOL_COLORS: Record<string, string> = {
  Read: "text-amber-400",
  Write: "text-success",
  Edit: "text-success",
  Bash: "text-fg-secondary",
  Glob: "text-fg-secondary",
  Grep: "text-fg-secondary",
};

/** Every call's id, its subagent's calls too. */
function callIds(calls: ToolCallEntry[], into = new Set<string>()): Set<string> {
  for (const c of calls) {
    into.add(c.tool_id);
    callIds(c.children, into);
  }
  return into;
}

/**
 * One call's row, then its subagent's calls indented beneath it. A row that
 * streamed in after the first paint fades in (index.css); the rows of the
 * first paint are there at once.
 */
function renderRow(tc: ToolCallEntry, key: string, depth: number, isLast: boolean, painted: Set<string>): ReactNode {
  const color = TOOL_COLORS[tc.tool_name] || "text-white/50";
  const hasResult = tc.result !== undefined;
  return (
    <div key={key} className={painted.has(tc.tool_id) ? undefined : "tool-call-arrive"}>
      <div className="py-0.5 flex items-start gap-2" style={depth > 0 ? { paddingLeft: depth * 16 } : undefined}>
        <span className={`${color} shrink-0 w-10`}>{tc.tool_name}</span>
        {tc.subagent && <span className="text-[10px] text-white/30 shrink-0">{tc.subagent.name}</span>}
        <span className="text-white/60 flex-1 min-w-0 truncate">{tc.input}</span>
        {hasResult && (
          <span className={tc.is_error ? "text-destructive" : "text-success"}>
            {tc.is_error ? "\u2717" : "\u2713"}
          </span>
        )}
        {!hasResult && isLast && (
          <span className="h-1.5 w-1.5 rounded-full bg-amber-400 motion-safe:animate-pulse shrink-0 mt-1" />
        )}
      </div>
      {tc.children.map((child, idx) => renderRow(child, `${key}.${idx}`, depth + 1, false, painted))}
    </div>
  );
}

export function ToolCallStream({ toolCalls, maxHeight = 240 }: ToolCallStreamProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // The calls of the first paint, which show at once; any other call arrived live.
  const [painted] = useState(() => callIds(toolCalls));

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [toolCalls.length]);

  if (toolCalls.length === 0) return null;

  return (
    <div
      ref={scrollRef}
      className="font-mono text-[11px] leading-relaxed overflow-y-auto bg-black/30 px-4 py-2"
      style={{ maxHeight }}
    >
      {toolCalls.map((tc, idx) => renderRow(tc, `${idx}`, 0, idx === toolCalls.length - 1, painted))}
    </div>
  );
}
