import { useEffect, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { AgentState } from "@/hooks/useAgentStates";
import { ToolCallStream } from "./ToolCallStream";
import { ThinkingBlock } from "./ThinkingBlock";

interface AgentCardProps {
  agent: AgentState;
  expanded?: boolean;
}

const AGENT_COLORS: Record<string, { text: string; bg: string; border: string; icon: string }> = {
  triage: { text: "text-cyan-400", bg: "bg-cyan-500/[0.04]", border: "border-cyan-500/25", icon: "\u25C9" },
  developer: { text: "text-violet-400", bg: "bg-violet-500/[0.04]", border: "border-violet-500/25", icon: "\u270E" },
  review: { text: "text-rose-400", bg: "bg-rose-500/[0.04]", border: "border-rose-500/25", icon: "\u2611" },
};

function getColors(agent: string) {
  return AGENT_COLORS[agent] || { text: "text-white/60", bg: "bg-white/[0.02]", border: "border-white/10", icon: "\u2022" };
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  return `${m}m ${rs}s`;
}

function useLiveDuration(startedAt: string | undefined, isActive: boolean, frozenMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!isActive) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [isActive]);
  if (!isActive) return frozenMs;
  if (!startedAt) return frozenMs;
  return now - new Date(startedAt).getTime();
}

export function AgentCard({ agent, expanded: forceExpanded }: AgentCardProps) {
  const [manualExpand, setManualExpand] = useState(false);
  const isActive = agent.status === "running";
  const expanded = forceExpanded ?? (isActive || manualExpand);
  const colors = getColors(agent.agent);
  const liveDurationMs = useLiveDuration(agent.startedAt, isActive, agent.durationMs);

  // Compact card (completed or pending)
  if (!expanded) {
    return (
      <div
        onClick={() => setManualExpand(!manualExpand)}
        className={`${agent.status === "pending" ? "opacity-40" : ""} cursor-pointer rounded-lg border ${
          agent.status === "pending" ? "border-white/[0.04] bg-white/[0.01]" : "border-white/[0.06] bg-white/[0.02]"
        } px-3 py-2.5 flex items-center gap-3 transition-colors hover:bg-white/[0.04]`}
      >
        <div className={`w-7 h-7 rounded-md ${colors.bg} flex items-center justify-center`}>
          <span className={`${colors.text} text-sm`}>
            {agent.status === "completed" ? "\u2713" : agent.status === "failed" ? "\u2717" : colors.icon}
          </span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={`font-semibold text-xs ${colors.text}`}>{agent.agent}</span>
            {agent.retryLabel && <span className="text-[10px] text-amber-400">{agent.retryLabel}</span>}
            {agent.status === "completed" && <span className="text-[10px] text-green-400">Done</span>}
            {agent.status === "failed" && <span className="text-[10px] text-red-400">Failed</span>}
            {agent.status === "pending" && <span className="text-[10px] text-white/30">Waiting</span>}
          </div>
          {agent.status !== "pending" && (
            <div className="text-[10px] text-white/40 mt-0.5">
              {formatDuration(liveDurationMs)}
              {agent.costUsd > 0 && <span> &middot; ${agent.costUsd.toFixed(2)}</span>}
              {agent.summary && <span> &middot; {agent.summary}</span>}
            </div>
          )}
        </div>
        {agent.status !== "pending" && (
          <ChevronDown className="w-3 h-3 text-white/20" />
        )}
      </div>
    );
  }

  // Expanded card (active or manually expanded)
  return (
    <div className={`rounded-lg border ${colors.border} ${colors.bg} overflow-hidden`}>
      {/* Header */}
      <div
        onClick={() => !isActive && setManualExpand(!manualExpand)}
        className={`px-4 py-3 flex items-center gap-3 border-b ${colors.border.replace("border-", "border-b-")} ${!isActive ? "cursor-pointer" : ""}`}
      >
        <div className={`w-8 h-8 rounded-lg ${colors.bg} flex items-center justify-center`}>
          <span className={`${colors.text} text-base`}>{colors.icon}</span>
        </div>
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className={`font-semibold text-sm ${colors.text}`}>{agent.agent}</span>
            {isActive && (
              <span className="inline-flex items-center gap-1.5 text-[10px] text-amber-400">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
                Running
              </span>
            )}
            {agent.retryLabel && <span className="text-[10px] text-amber-400">{agent.retryLabel}</span>}
            {!isActive && agent.status === "completed" && <span className="text-[10px] text-green-400">\u2713 Completed</span>}
          </div>
          <div className="text-[10px] text-white/40 mt-0.5">
            {formatDuration(liveDurationMs)}
            {agent.costUsd > 0 && <span> &middot; ${agent.costUsd.toFixed(2)}</span>}
            {agent.toolCallCount > 0 && <span> &middot; {agent.toolCallCount} tool calls</span>}
            {agent.model && <span> &middot; {agent.model}</span>}
          </div>
        </div>
        {!isActive && <ChevronUp className="w-4 h-4 text-white/20" />}
      </div>

      {/* Summary headline */}
      {agent.summary && (
        <div className={`px-4 py-2 text-xs ${colors.text} opacity-80 ${colors.bg}`}>
          {agent.summary}
        </div>
      )}

      {/* Thinking blocks */}
      <ThinkingBlock blocks={agent.thinkingBlocks} isStreaming={isActive} />

      {/* Text output */}
      {agent.textBlocks.length > 0 && (
        <div className="px-4 py-2 text-xs text-white/60 leading-relaxed max-h-40 overflow-y-auto border-t border-white/[0.04]">
          {agent.textBlocks.map((text, idx) => (
            <p key={idx} className="mb-1">{text}</p>
          ))}
        </div>
      )}

      {/* Tool call stream */}
      <ToolCallStream toolCalls={agent.toolCalls} />
    </div>
  );
}
