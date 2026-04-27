import { cn } from "@/lib/utils";
import { IconBox } from "@/components/ui/IconBox";
import { getPhaseForAgent } from "@/lib/constants";
import { CheckCircle2 } from "lucide-react";
import { getAgentIcon } from "@/lib/agentIcons";
import type { NodeStateEvent } from "@/lib/types";

interface AgentExecutionCardProps {
  agentType: string;
  nodeState?: NodeStateEvent;
  liveOutput?: string;
  onClick?: () => void;
}

export function AgentExecutionCard({ agentType, nodeState, liveOutput, onClick }: AgentExecutionCardProps) {
  const phase = getPhaseForAgent(agentType);
  const Icon = getAgentIcon(agentType);
  const state = nodeState?.state ?? "pending";
  const isPending = state === "pending" || state === "ready";
  const isRunning = state === "running";
  const isComplete = state === "completed";
  const isFailed = state === "failed";

  const metaParts: string[] = [];
  if (isComplete) metaParts.push("Completed");
  if (isRunning) metaParts.push("Running");
  if (isFailed) metaParts.push("Failed");
  if (isPending) metaParts.push(getWaitingMessage(agentType));
  if (nodeState && !isPending) {
    if (nodeState.duration_seconds > 0) metaParts.push(`${nodeState.duration_seconds.toFixed(1)}s`);
    if (nodeState.cost_usd > 0) metaParts.push(`$${nodeState.cost_usd.toFixed(3)}`);
    if (nodeState.turns > 0) metaParts.push(`${nodeState.turns} turns`);
  }

  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left rounded-[10px] p-3.5 transition-all duration-300 border-l-[3px]",
        isPending && "opacity-35",
        isRunning && "shadow-[0_0_24px_rgba(245,158,11,0.06)]",
      )}
      style={{
        backgroundColor: `${isFailed ? "#ef4444" : phase.color}08`,
        borderLeftColor: isFailed ? "#ef4444" : phase.color,
      }}
    >
      <div className="flex items-center gap-3">
        <IconBox icon={Icon} color={isFailed ? "#ef4444" : phase.color} size="md" />
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-sm" style={{ color: isFailed ? "#ef4444" : phase.color }}>
            {agentType.charAt(0).toUpperCase() + agentType.slice(1)}
          </div>
          <div className="text-[11px] text-white/40 truncate">{metaParts.join(" • ")}</div>
        </div>

        {/* Status indicator */}
        {isComplete && <CheckCircle2 className="w-[18px] h-[18px] text-emerald-500 shrink-0" />}
        {isRunning && <div className="athanor-spinner shrink-0" style={{ color: phase.color }} />}
      </div>

      {/* Live output */}
      {liveOutput && !isPending && (
        <div
          className="mt-2.5 px-2.5 py-1.5 rounded-md text-xs font-mono truncate"
          style={{
            backgroundColor: "rgba(0,0,0,0.3)",
            color: isComplete ? "#10b981" : isFailed ? "#ef4444" : phase.color,
          }}
        >
          {liveOutput}
        </div>
      )}
    </button>
  );
}

function getWaitingMessage(agentType: string): string {
  switch (agentType) {
    case "triage": return "Analyzing issue...";
    case "developer": return "Waiting for triage";
    case "validator": return "Waiting for developer";
    case "reviewer": return "Waiting for validation";
    case "output": return "Final results";
    default: return "Waiting";
  }
}
