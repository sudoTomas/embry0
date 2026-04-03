import { cn } from "@/lib/utils";
import { ROLE_COLORS } from "@/lib/constants";
import type { NodeStateEvent } from "@/lib/types/events";

interface AgentFilterTabsProps {
  nodeStates: Record<string, NodeStateEvent>;
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
            ? "bg-primary text-primary-foreground"
            : "text-muted-foreground hover:text-foreground hover:bg-muted"
        )}
      >
        All
      </button>
      {entries.map(([nodeId, state]) => {
        const role = state.agent_type || nodeId;
        const color = ROLE_COLORS[role] || ROLE_COLORS.default;
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
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted"
            )}
          >
            <span
              className={cn(
                "text-[10px]",
                state.state === "running" && "animate-spin",
                state.state === "completed" && "text-green-400",
                state.state === "failed" && "text-red-400"
              )}
            >
              {icon}
            </span>
            <span style={selectedNodeId === nodeId ? undefined : { color }}>{label}</span>
          </button>
        );
      })}
    </div>
  );
}
