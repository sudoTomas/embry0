import { useEffect, useRef } from "react";
import type { ToolCallEntry } from "@/hooks/useAgentStates";

interface ToolCallStreamProps {
  toolCalls: ToolCallEntry[];
  maxHeight?: number;
}

const TOOL_COLORS: Record<string, string> = {
  Read: "text-amber-400",
  Write: "text-emerald-400",
  Edit: "text-emerald-400",
  Bash: "text-blue-400",
  Glob: "text-cyan-400",
  Grep: "text-cyan-400",
};

export function ToolCallStream({ toolCalls, maxHeight = 240 }: ToolCallStreamProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

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
      {toolCalls.map((tc, idx) => {
        const color = TOOL_COLORS[tc.tool_name] || "text-white/50";
        const hasResult = tc.result !== undefined;
        return (
          <div key={idx} className="py-0.5 flex items-start gap-2">
            <span className={`${color} shrink-0 w-10`}>{tc.tool_name}</span>
            <span className="text-white/60 flex-1 min-w-0 truncate">{tc.input}</span>
            {hasResult && (
              <span className={tc.is_error ? "text-red-400" : "text-green-400"}>
                {tc.is_error ? "\u2717" : "\u2713"}
              </span>
            )}
            {!hasResult && idx === toolCalls.length - 1 && (
              <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse shrink-0 mt-1" />
            )}
          </div>
        );
      })}
    </div>
  );
}
