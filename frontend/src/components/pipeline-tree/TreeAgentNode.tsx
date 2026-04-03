import { cn } from "@/lib/utils";
import { IconBox } from "@/components/ui/IconBox";
import { getPhaseForAgent } from "@/lib/constants";
import { getAgentIcon } from "@/lib/agentIcons";

export type AgentNodeState = "pending" | "running" | "completed" | "failed";

interface TreeAgentNodeProps {
  agentType: string;
  state: AgentNodeState;
  label?: string;
  onClick?: () => void;
}

export function TreeAgentNode({ agentType, state, label, onClick }: TreeAgentNodeProps) {
  const phase = getPhaseForAgent(agentType);
  const Icon = getAgentIcon(agentType);

  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-2 px-4 py-2.5 rounded-[10px] border transition-all duration-200",
        "cursor-pointer hover:-translate-y-0.5",
        state === "pending" && "opacity-35",
      )}
      style={{
        backgroundColor: `${phase.color}10`,
        borderColor: state === "running" ? `${phase.color}70`
          : state === "failed" ? "#ef444470"
          : `${phase.color}30`,
        boxShadow: state === "running" ? `0 0 16px ${phase.color}15` : "none",
        color: state === "failed" ? "#ef4444" : phase.color,
      }}
    >
      <div
        className={cn("w-2 h-2 rounded-full shrink-0", state === "running" && "animate-pulse")}
        style={{
          backgroundColor: state === "completed" ? "#10b981"
            : state === "running" ? "#f59e0b"
            : state === "failed" ? "#ef4444"
            : "rgba(255,255,255,0.12)",
          boxShadow: state === "completed" ? "0 0 6px rgba(16,185,129,0.5)"
            : state === "running" ? "0 0 6px rgba(245,158,11,0.5)"
            : "none",
        }}
      />
      <IconBox icon={Icon} color={state === "failed" ? "#ef4444" : phase.color} size="sm" />
      <span className="text-sm font-medium">
        {label ?? phase.name.charAt(0).toUpperCase() + phase.name.slice(1)}
      </span>
      <span className="text-[10px] opacity-50 ml-1">
        {state === "completed" ? "Complete" : state === "running" ? "Processing..." : state === "failed" ? "Failed" : "Waiting"}
      </span>
    </button>
  );
}
