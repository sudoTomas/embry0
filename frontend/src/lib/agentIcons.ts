import { Target, Code2, ShieldCheck, ScanEye, Send, type LucideIcon } from "lucide-react";

export const AGENT_ICON_MAP: Record<string, LucideIcon> = {
  triage: Target,
  developer: Code2,
  validator: ShieldCheck,
  reviewer: ScanEye,
  output: Send,
};

export function getAgentIcon(agentType: string): LucideIcon {
  return AGENT_ICON_MAP[agentType] ?? Send;
}
