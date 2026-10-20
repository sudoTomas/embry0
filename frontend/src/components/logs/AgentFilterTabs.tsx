import { cn } from "@/lib/utils";
import { ROLE_COLORS } from "@/lib/constants";
import type { NodeLiveState } from "@/lib/pipeline-live";

interface AgentFilterTabsProps {
  nodeStates: Record<string, NodeLiveState>;
  selectedNodeId: string | null;
  onSelect: (nodeId: string | null) => void;
}

const STATE_ICONS: Record<string, string> = {
  completed: "✓",
  running: "⟳",
  failed: "✗",
  pending: "○",
  ready: "○",
};

export function AgentFilterTabs({ nodeStates, selectedNodeId, onSelect }: AgentFilterTabsProps) {
  const entries = Object.entries(nodeStates);
  if (entries.length <= 1) return null;

  return (
    <div className="flex items-center gap-1 px-2 py-1.5 overflow-x-auto border-b border-border/50">
      <button
        onClick={() => onSelect(null)}
        className={cn(
          "px-3 py-1 rounded-md text-xs font-medium transition-colors whitespace-nowrap",
          selectedNodeId === null
            ? "bg-overlay text-foreground"
            : "text-muted-foreground hover:text-foreground hover:bg-muted"
        )}
      >
        All
      </button>
      {entries.map(([nodeId, state]) => {
        const role = state.agent_type || nodeId;
        const icon = STATE_ICONS[state.state] || "○";
        const label = role
          .replace(/-\d+$/, "")
          .split("-")
          .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
          .join(" ");

        return (
          <button
            key={nodeId}
            onClick={() => onSelect(selectedNodeId === nodeId ? null : nodeId)}
            className={cn(
              "px-3 py-1 rounded-md text-xs font-medium transition-colors whitespace-nowrap flex items-center gap-1.5",
              selectedNodeId === nodeId
                ? "bg-overlay text-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted"
            )}
          >
            <span
              className={cn(
                "text-[10px]",
                state.state === "running" && "animate-spin",
                state.state === "completed" && "text-success",
                state.state === "failed" && "text-destructive"
              )}
            >
              {icon}
            </span>
            {/* Unselected, the agent reads in its role's colour, as the
                pipeline graph and the job page's agent cards draw it; a role
                with no colour keeps the chip's muted text. Selected, it reads
                neutral like All. */}
            <span className={selectedNodeId === nodeId ? undefined : ROLE_COLORS[role]}>{label}</span>
          </button>
        );
      })}
    </div>
  );
}
