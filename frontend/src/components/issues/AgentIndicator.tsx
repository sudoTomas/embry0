import { Eye, Search, ShieldCheck, Wrench, type LucideIcon } from "lucide-react";

interface AgentIndicatorProps {
  agentType: string;
  size?: "sm" | "md";
}

/** The agent working an issue, as a lucide icon in its role colour. Emoji used
 * to stand in and drew empty boxes on a machine without a colour emoji font. */
const AGENT_CHARACTERS: Record<string, { icon: LucideIcon; color: string; label: string }> = {
  triage: { icon: Search, color: "text-purple", label: "Triaging" },
  developer: { icon: Wrench, color: "text-amber-400", label: "Developing" },
  validator: { icon: ShieldCheck, color: "text-success", label: "Validating" },
  reviewer: { icon: Eye, color: "text-purple", label: "Reviewing" },
};

export function AgentIndicator({ agentType, size = "sm" }: AgentIndicatorProps) {
  const agent = AGENT_CHARACTERS[agentType] ?? AGENT_CHARACTERS.developer;
  const Icon = agent.icon;
  const iconClass = size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4";
  return (
    <span className={`inline-flex items-center gap-1 ${agent.color} animate-agent-bounce`} title={agent.label}>
      <Icon aria-hidden="true" className={`${iconClass} shrink-0`} />
      {size === "md" && <span className="text-xs">{agent.label}</span>}
    </span>
  );
}
