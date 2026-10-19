import { Gavel, Gem, History, MessageSquareX, Radar, Ruler, Sprout, Workflow, type LucideIcon } from "lucide-react";

/** How one specialist agent shows on the Agents page: its own icon, and the
 * classes of its `--color-role-*` token (index.css) for text, wash and border. */
export interface SpecialistVisual {
  icon: LucideIcon;
  tone: string;
}

/** The eight read-only specialist agents the backend seeds as built-ins
 * (embry0/workflows/issue_to_pr/specialist_agents.py). */
export const SPECIALIST_AGENTS: Readonly<Record<string, SpecialistVisual>> = {
  kintsugi: { icon: Gem, tone: "text-role-kintsugi bg-role-kintsugi/10 border-role-kintsugi/25" },
  gardener: { icon: Sprout, tone: "text-role-gardener bg-role-gardener/10 border-role-gardener/25" },
  interrogator: { icon: Gavel, tone: "text-role-interrogator bg-role-interrogator/10 border-role-interrogator/25" },
  why: { icon: History, tone: "text-role-why bg-role-why/10 border-role-why/25" },
  how: { icon: Workflow, tone: "text-role-how bg-role-how/10 border-role-how/25" },
  "blast-radius": { icon: Radar, tone: "text-role-blast-radius bg-role-blast-radius/10 border-role-blast-radius/25" },
  "comment-sicko": {
    icon: MessageSquareX,
    tone: "text-role-comment-sicko bg-role-comment-sicko/10 border-role-comment-sicko/25",
  },
  "design-auditor": {
    icon: Ruler,
    tone: "text-role-design-auditor bg-role-design-auditor/10 border-role-design-auditor/25",
  },
};

export const SPECIALIST_AGENT_TYPES: readonly string[] = Object.keys(SPECIALIST_AGENTS);

export function specialistVisual(agentType: string): SpecialistVisual | undefined {
  return Object.hasOwn(SPECIALIST_AGENTS, agentType) ? SPECIALIST_AGENTS[agentType] : undefined;
}
