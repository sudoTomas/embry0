interface AgentIndicatorProps {
  agentType: string;
  size?: "sm" | "md";
}

const AGENT_CHARACTERS: Record<string, { char: string; color: string; label: string }> = {
  triage: { char: "🔍", color: "text-purple-400", label: "Triaging" },
  developer: { char: "🔧", color: "text-amber-400", label: "Developing" },
  validator: { char: "🛡️", color: "text-green-400", label: "Validating" },
  reviewer: { char: "👁️", color: "text-cyan-400", label: "Reviewing" },
};

export function AgentIndicator({ agentType, size = "sm" }: AgentIndicatorProps) {
  const agent = AGENT_CHARACTERS[agentType] ?? AGENT_CHARACTERS.developer;
  const sizeClass = size === "sm" ? "text-sm" : "text-base";
  return (
    <span className={`inline-flex items-center gap-1 ${agent.color} ${sizeClass} animate-agent-bounce`} title={agent.label}>
      <span className="inline-block">{agent.char}</span>
      {size === "md" && <span className="text-xs">{agent.label}</span>}
    </span>
  );
}
