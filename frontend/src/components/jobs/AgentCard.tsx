import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { AgentState } from "@/hooks/useAgentStates";
import { useLiveDuration } from "@/hooks/useLiveDuration";
import { formatDuration } from "@/lib/utils";
import { useAgentIdentity } from "@/hooks/useAgentIdentity";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { stateFor } from "@/components/roster";
import { ToolCallStream } from "./ToolCallStream";
import { ThinkingBlock } from "./ThinkingBlock";

interface AgentCardProps {
  agent: AgentState;
  expanded?: boolean;
}

export function AgentCard({ agent, expanded: forceExpanded }: AgentCardProps) {
  const [manualExpand, setManualExpand] = useState(false);
  const isActive = agent.status === "running";
  const expanded = forceExpanded ?? (isActive || manualExpand);
  const identity = useAgentIdentity(agent.agent);
  const spriteState = stateFor(agent);
  const colors = identity.classes;
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
        {agent.status === "completed" || agent.status === "failed" ? (
          <div className={`w-7 h-7 rounded-md ${colors.bg} flex items-center justify-center`}>
            {agent.status === "completed" ? (
              <span className={`${colors.text} text-sm`}>{"\u2713"}</span>
            ) : (
              <span className="text-red-400 text-sm">{"\u2717"}</span>
            )}
          </div>
        ) : (
          <AgentAvatar identity={identity} size={28} state={spriteState} />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={`font-semibold text-xs ${colors.text}`} title={agent.agent}>{identity.displayName}</span>
            {agent.node && agent.node !== agent.agent && <span className="text-xs text-muted-foreground">{agent.node}</span>}
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
              {agent.status === "failed" && agent.errorCode && <span> &middot; {agent.errorCode}</span>}
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
        <AgentAvatar identity={identity} size={32} state={spriteState} />
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className={`font-semibold text-sm ${colors.text}`} title={agent.agent}>{identity.displayName}</span>
            {agent.node && agent.node !== agent.agent && <span className="text-sm text-muted-foreground">{agent.node}</span>}
            {isActive && (
              <span className="inline-flex items-center gap-1.5 text-[10px] text-amber-400">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                Running
              </span>
            )}
            {agent.retryLabel && <span className="text-[10px] text-amber-400">{agent.retryLabel}</span>}
            {!isActive && agent.status === "completed" && <span className="text-[10px] text-green-400">{"\u2713"} Completed</span>}
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
          {agent.status === "failed" && agent.errorCode && (
            <span className="text-[10px] text-white/40"> &middot; {agent.errorCode}</span>
          )}
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
