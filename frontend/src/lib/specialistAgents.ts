import { Gavel, Gem, History, MessageSquareX, Radar, Ruler, Sprout, Workflow, type LucideIcon } from "lucide-react";

/** How one specialist agent shows on the Agents page: its own icon on a neutral
 * tile. The icon tells the eight apart; colour stays for status and the one accent. */
const NEUTRAL_TILE = "text-fg-secondary bg-overlay border-border";

export interface SpecialistVisual {
  icon: LucideIcon;
  tone: string;
}

/** The eight read-only specialist agents the backend seeds as built-ins
 * (embry0/workflows/issue_to_pr/specialist_agents.py). */
export const SPECIALIST_AGENTS: Readonly<Record<string, SpecialistVisual>> = {
  kintsugi: { icon: Gem, tone: NEUTRAL_TILE },
  gardener: { icon: Sprout, tone: NEUTRAL_TILE },
  interrogator: { icon: Gavel, tone: NEUTRAL_TILE },
  why: { icon: History, tone: NEUTRAL_TILE },
  how: { icon: Workflow, tone: NEUTRAL_TILE },
  "blast-radius": { icon: Radar, tone: NEUTRAL_TILE },
  "comment-sicko": {
    icon: MessageSquareX,
    tone: NEUTRAL_TILE,
  },
  "design-auditor": {
    icon: Ruler,
    tone: NEUTRAL_TILE,
  },
};

export const SPECIALIST_AGENT_TYPES: readonly string[] = Object.keys(SPECIALIST_AGENTS);

export function specialistVisual(agentType: string): SpecialistVisual | undefined {
  return Object.hasOwn(SPECIALIST_AGENTS, agentType) ? SPECIALIST_AGENTS[agentType] : undefined;
}
