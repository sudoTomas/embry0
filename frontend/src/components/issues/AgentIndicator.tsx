import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { useAgentIdentity } from "@/hooks/useAgentIdentity";

interface AgentIndicatorProps {
  agentType: string;
  size?: "sm" | "md";
}

/** The agent working an issue: its sprite, thinking, from the one identity source; the md size names it too. */
export function AgentIndicator({ agentType, size = "sm" }: AgentIndicatorProps) {
  const identity = useAgentIdentity(agentType);
  return (
    <span className={`inline-flex items-center gap-1 ${identity.classes.text}`}>
      <AgentAvatar identity={identity} size={16} state="thinking" />
      {size === "md" && <span className="text-xs">{identity.displayName}</span>}
    </span>
  );
}
