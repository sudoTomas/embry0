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
        className={`cursor-pointer rounded-lg border ${
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
            <span className={`font-semibold text-xs ${agent.status === "pending" ? "text-muted-foreground" : colors.text}`} title={agent.agent}>{identity.displayName}</span>
            {/* The graph node id is an identifier: mono, like code, never sans prose. */}
            {agent.node && agent.node !== agent.agent && <span className="font-mono text-2xs text-muted-foreground">{agent.node}</span>}
            {agent.retryLabel && <span className="text-[10px] text-amber-400">{agent.retryLabel}</span>}
            {agent.status === "completed" && <span className="text-[10px] text-success">Done</span>}
            {agent.status === "failed" && <span className="text-[10px] text-destructive">Failed</span>}
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
      {/* Header. Only its bottom edge has a width, so the card's own border
          class draws the divider in the outline's colour. A border-b- class
          made from it at runtime would be in no source file, and Tailwind
          writes no rule for a class it cannot find there. */}
      <div
        onClick={() => !isActive && setManualExpand(!manualExpand)}
        className={`px-4 py-3 flex items-center gap-3 border-b ${colors.border} ${!isActive ? "cursor-pointer" : ""}`}
      >
        <AgentAvatar identity={identity} size={32} state={spriteState} />
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className={`font-semibold text-sm ${colors.text}`} title={agent.agent}>{identity.displayName}</span>
            {agent.node && agent.node !== agent.agent && <span className="font-mono text-xs text-muted-foreground">{agent.node}</span>}
            {isActive && (
              <span className="inline-flex items-center gap-1.5 text-[10px] text-amber-400">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                Running
              </span>
            )}
            {agent.retryLabel && <span className="text-[10px] text-amber-400">{agent.retryLabel}</span>}
            {!isActive && agent.status === "completed" && <span className="text-[10px] text-success">{"\u2713"} Completed</span>}
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
